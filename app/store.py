from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path


def db_path() -> str:
    return os.getenv("DB_PATH", "./data/turtle.db")


def connect() -> sqlite3.Connection:
    path = Path(db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
        if "common_stop" not in columns:
            conn.execute("ALTER TABLE positions ADD COLUMN common_stop REAL NOT NULL DEFAULT 0")
        conn.commit()


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
              fixed_unit_amount,account_equity,risk_pct,common_stop,status,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,'ACTIVE',CURRENT_TIMESTAMP)
            ON CONFLICT(symbol) DO UPDATE SET
              name=excluded.name,
              entry_price=excluded.entry_price,
              n_at_entry=excluded.n_at_entry,
              filled_units=excluded.filled_units,
              sizing_mode=excluded.sizing_mode,
              fixed_unit_amount=excluded.fixed_unit_amount,
              account_equity=excluded.account_equity,
              risk_pct=excluded.risk_pct,
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
