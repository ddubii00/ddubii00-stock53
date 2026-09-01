from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


_INIT_LOCK = threading.Lock()
_INITIALIZED_PATHS: set[str] = set()


def db_path() -> str:
    configured = os.getenv("DB_PATH")
    if configured:
        return configured
    if os.getenv("VERCEL"):
        return "/tmp/turtle.db"
    return "./data/turtle.db"


def connect() -> sqlite3.Connection:
    path = Path(db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    resolved = str(Path(db_path()).resolve())
    with _INIT_LOCK:
        if resolved in _INITIALIZED_PATHS:
            return
        with closing(connect()) as conn:
            conn.executescript(
                """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS positions (
              symbol TEXT PRIMARY KEY,
              name TEXT NOT NULL DEFAULT '',
              entry_price REAL NOT NULL,
              n_at_entry REAL NOT NULL,
              filled_units INTEGER NOT NULL DEFAULT 0 CHECK(filled_units BETWEEN 0 AND 4),
              sizing_mode TEXT NOT NULL DEFAULT 'fixed',
              fixed_unit_amount REAL NOT NULL DEFAULT 10000000,
              account_equity REAL NOT NULL DEFAULT 100000000,
              risk_pct REAL NOT NULL DEFAULT 0.5,
              exit_strategy TEXT NOT NULL DEFAULT 'turtle',
              common_stop REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'ACTIVE',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS signal_events (
              event_key TEXT PRIMARY KEY,
              symbol TEXT NOT NULL,
              signal_type TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS universe_fundamentals (
              symbol TEXT PRIMARY KEY,
              name TEXT NOT NULL DEFAULT '',
              market TEXT NOT NULL,
              market_cap_100m REAL NOT NULL,
              operating_profit_100m REAL,
              fiscal_period TEXT,
              source TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS full_market_scans (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              status TEXT NOT NULL,
              phase TEXT NOT NULL DEFAULT 'queued',
              provider TEXT NOT NULL,
              market TEXT NOT NULL,
              min_market_cap_100m REAL NOT NULL,
              min_operating_profit_100m REAL NOT NULL,
              signal_mode TEXT NOT NULL,
              options_json TEXT NOT NULL DEFAULT '{}',
              processed INTEGER NOT NULL DEFAULT 0,
              total INTEGER NOT NULL DEFAULT 0,
              listed_count INTEGER NOT NULL DEFAULT 0,
              stock_count INTEGER NOT NULL DEFAULT 0,
              etf_count INTEGER NOT NULL DEFAULT 0,
              kospi_count INTEGER NOT NULL DEFAULT 0,
              kosdaq_count INTEGER NOT NULL DEFAULT 0,
              universe_count INTEGER NOT NULL DEFAULT 0,
              fundamentals_passed INTEGER NOT NULL DEFAULT 0,
              stock_fundamentals_passed INTEGER NOT NULL DEFAULT 0,
              etf_scanned INTEGER NOT NULL DEFAULT 0,
              error_count INTEGER NOT NULL DEFAULT 0,
              message TEXT NOT NULL DEFAULT '',
              started_at TEXT NOT NULL,
              finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS full_market_scan_items (
              scan_id INTEGER NOT NULL,
              ordinal INTEGER NOT NULL,
              payload TEXT NOT NULL,
              PRIMARY KEY(scan_id, ordinal),
              FOREIGN KEY(scan_id) REFERENCES full_market_scans(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_full_market_scans_finished
              ON full_market_scans(status, id DESC);
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
            if "common_stop" not in columns:
                conn.execute("ALTER TABLE positions ADD COLUMN common_stop REAL NOT NULL DEFAULT 0")
            if "exit_strategy" not in columns:
                conn.execute(
                    "ALTER TABLE positions ADD COLUMN exit_strategy TEXT NOT NULL DEFAULT 'turtle'"
                )
            scan_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(full_market_scans)").fetchall()
            }
            if "options_json" not in scan_columns:
                conn.execute(
                    "ALTER TABLE full_market_scans ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'"
                )
            for column in (
                "listed_count",
                "stock_count",
                "etf_count",
                "kospi_count",
                "kosdaq_count",
                "stock_fundamentals_passed",
                "etf_scanned",
            ):
                if column not in scan_columns:
                    conn.execute(
                        f"ALTER TABLE full_market_scans ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            conn.commit()
        _INITIALIZED_PATHS.add(resolved)


def list_positions() -> list[dict]:
    init_db()
    with closing(connect()) as conn:
        rows = conn.execute("SELECT * FROM positions WHERE status='ACTIVE' ORDER BY symbol").fetchall()
        return [dict(row) for row in rows]


def get_position(symbol: str) -> dict | None:
    init_db()
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
        return dict(row) if row else None


def _derived_stop(entry_price: float, n_at_entry: float, filled_units: int) -> float:
    latest = entry_price + max(0, filled_units - 1) * 0.5 * n_at_entry
    return latest - 2.0 * n_at_entry


def save_position(payload: dict) -> None:
    init_db()
    entry_price = float(payload["entry_price"])
    n_at_entry = float(payload["n_at_entry"])
    filled_units = int(payload.get("filled_units", 0))
    if entry_price <= 0 or n_at_entry <= 0 or not 0 <= filled_units <= 4:
        raise ValueError("invalid position values")
    requested_stop = float(payload.get("common_stop") or 0)
    common_stop = max(requested_stop, _derived_stop(entry_price, n_at_entry, filled_units))
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO positions(
              symbol,name,entry_price,n_at_entry,filled_units,sizing_mode,
              fixed_unit_amount,account_equity,risk_pct,exit_strategy,common_stop,status,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',CURRENT_TIMESTAMP)
            ON CONFLICT(symbol) DO UPDATE SET
              name=excluded.name,
              entry_price=excluded.entry_price,
              n_at_entry=excluded.n_at_entry,
              filled_units=excluded.filled_units,
              sizing_mode=excluded.sizing_mode,
              fixed_unit_amount=excluded.fixed_unit_amount,
              account_equity=excluded.account_equity,
              risk_pct=excluded.risk_pct,
              exit_strategy=excluded.exit_strategy,
              common_stop=MAX(positions.common_stop, excluded.common_stop),
              status='ACTIVE',
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                payload["symbol"],
                payload.get("name", ""),
                entry_price,
                n_at_entry,
                filled_units,
                payload.get("sizing_mode", "fixed"),
                payload.get("fixed_unit_amount", 10_000_000),
                payload.get("account_equity", 100_000_000),
                payload.get("risk_pct", 0.5),
                payload.get("exit_strategy", "turtle"),
                common_stop,
            ),
        )
        conn.commit()


def confirm_next_fill(symbol: str) -> dict:
    """Manually confirm one fill. Workers never call this function."""

    init_db()
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM positions WHERE symbol=? AND status='ACTIVE'", (symbol,)
        ).fetchone()
        if row is None:
            raise KeyError(symbol)
        current_units = int(row["filled_units"])
        if current_units >= 4:
            raise ValueError("all four Units are already confirmed")
        next_units = current_units + 1
        ratcheted_stop = max(
            float(row["common_stop"] or 0),
            _derived_stop(float(row["entry_price"]), float(row["n_at_entry"]), next_units),
        )
        conn.execute(
            "UPDATE positions SET filled_units=?, common_stop=?, updated_at=CURRENT_TIMESTAMP WHERE symbol=?",
            (next_units, ratcheted_stop, symbol),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM positions WHERE symbol=?", (symbol,)).fetchone()
        return dict(updated)


def event_once(event_key: str, symbol: str, signal_type: str) -> bool:
    """Return True only for the first durable insertion of an event key."""

    init_db()
    with closing(connect()) as conn:
        try:
            conn.execute(
                "INSERT INTO signal_events(event_key,symbol,signal_type) VALUES(?,?,?)",
                (event_key, symbol, signal_type),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def get_cached_fundamental(symbol: str, max_age_days: int = 7) -> dict | None:
    """Return a recent fundamentals row, including a cached NULL profit."""

    init_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, max_age_days))
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM universe_fundamentals WHERE symbol=?", (symbol,)
        ).fetchone()
        if row is None:
            return None
        try:
            updated_at = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return dict(row) if updated_at >= cutoff else None


def get_cached_fundamentals(symbols: list[str], max_age_days: int = 7) -> dict[str, dict]:
    """Bulk cache lookup to avoid opening one SQLite connection per stock."""

    if not symbols:
        return {}
    init_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, max_age_days))
    result: dict[str, dict] = {}
    with closing(connect()) as conn:
        for start in range(0, len(symbols), 900):
            chunk = symbols[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT * FROM universe_fundamentals WHERE symbol IN ({placeholders})", chunk
            ).fetchall()
            for row in rows:
                try:
                    updated_at = datetime.fromisoformat(
                        str(row["updated_at"]).replace("Z", "+00:00")
                    )
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if updated_at >= cutoff:
                    result[str(row["symbol"])] = dict(row)
    return result


def save_fundamental(payload: dict) -> None:
    init_db()
    updated_at = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO universe_fundamentals(
              symbol,name,market,market_cap_100m,operating_profit_100m,
              fiscal_period,source,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
              name=excluded.name,
              market=excluded.market,
              market_cap_100m=excluded.market_cap_100m,
              operating_profit_100m=excluded.operating_profit_100m,
              fiscal_period=excluded.fiscal_period,
              source=excluded.source,
              updated_at=excluded.updated_at
            """,
            (
                payload["symbol"],
                payload.get("name", ""),
                payload["market"],
                float(payload["market_cap_100m"]),
                payload.get("operating_profit_100m"),
                payload.get("fiscal_period"),
                payload.get("source", "naver"),
                updated_at,
            ),
        )
        conn.commit()


def create_full_market_scan(payload: dict) -> int:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as conn:
        cursor = conn.execute(
            """
            INSERT INTO full_market_scans(
              status,phase,provider,market,min_market_cap_100m,
              min_operating_profit_100m,signal_mode,options_json,started_at,message
            ) VALUES('RUNNING','universe',?,?,?,?,?,?,?,?)
            """,
            (
                payload["provider"],
                payload["market"],
                float(payload["min_market_cap_100m"]),
                float(payload["min_operating_profit_100m"]),
                payload["signal_mode"],
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                now,
                "전체시장 종목목록 조회중",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_full_market_scan(scan_id: int, **fields) -> None:
    allowed = {
        "status",
        "phase",
        "processed",
        "total",
        "listed_count",
        "stock_count",
        "etf_count",
        "kospi_count",
        "kosdaq_count",
        "universe_count",
        "fundamentals_passed",
        "stock_fundamentals_passed",
        "etf_scanned",
        "error_count",
        "message",
        "finished_at",
    }
    values = {key: value for key, value in fields.items() if key in allowed}
    if not values:
        return
    assignments = ",".join(f"{key}=?" for key in values)
    with closing(connect()) as conn:
        conn.execute(
            f"UPDATE full_market_scans SET {assignments} WHERE id=?",
            (*values.values(), scan_id),
        )
        conn.commit()


def finish_full_market_scan(scan_id: int, items: list[dict], **summary) -> None:
    """Atomically publish a completed candidate snapshot."""

    finished_at = datetime.now(timezone.utc).isoformat()
    with closing(connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM full_market_scan_items WHERE scan_id=?", (scan_id,))
        conn.executemany(
            "INSERT INTO full_market_scan_items(scan_id,ordinal,payload) VALUES(?,?,?)",
            [
                (scan_id, ordinal, json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                for ordinal, item in enumerate(items)
            ],
        )
        conn.execute(
            """
            UPDATE full_market_scans
            SET status='COMPLETED',phase='completed',processed=?,total=?,
                listed_count=?,stock_count=?,etf_count=?,kospi_count=?,kosdaq_count=?,
                universe_count=?,fundamentals_passed=?,stock_fundamentals_passed=?,
                etf_scanned=?,error_count=?,message=?,finished_at=?
            WHERE id=?
            """,
            (
                int(summary.get("processed", 0)),
                int(summary.get("total", 0)),
                int(summary.get("listed_count", 0)),
                int(summary.get("stock_count", 0)),
                int(summary.get("etf_count", 0)),
                int(summary.get("kospi_count", 0)),
                int(summary.get("kosdaq_count", 0)),
                int(summary.get("universe_count", 0)),
                int(summary.get("fundamentals_passed", 0)),
                int(summary.get("stock_fundamentals_passed", 0)),
                int(summary.get("etf_scanned", 0)),
                int(summary.get("error_count", 0)),
                summary.get("message", f"후보 {len(items)}개 선정"),
                finished_at,
                scan_id,
            ),
        )
        conn.commit()


def fail_full_market_scan(scan_id: int, message: str) -> None:
    update_full_market_scan(
        scan_id,
        status="FAILED",
        phase="failed",
        message=message[:1000],
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


def get_full_market_scan(scan_id: int, include_items: bool = True) -> dict | None:
    init_db()
    with closing(connect()) as conn:
        row = conn.execute("SELECT * FROM full_market_scans WHERE id=?", (scan_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["options"] = json.loads(result.get("options_json") or "{}")
        except json.JSONDecodeError:
            result["options"] = {}
        result["items"] = []
        if include_items and result["status"] == "COMPLETED":
            item_rows = conn.execute(
                "SELECT payload FROM full_market_scan_items WHERE scan_id=? ORDER BY ordinal",
                (scan_id,),
            ).fetchall()
            result["items"] = [json.loads(item["payload"]) for item in item_rows]
        return result


def get_latest_full_market_scan(include_items: bool = True) -> dict | None:
    init_db()
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id FROM full_market_scans WHERE status='COMPLETED' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return get_full_market_scan(int(row["id"]), include_items) if row else None
