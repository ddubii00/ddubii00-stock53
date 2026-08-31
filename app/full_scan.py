from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from app.providers import MarketDataProvider, build_market_data_provider, get_market_snapshot
from app.store import (
    create_full_market_scan,
    fail_full_market_scan,
    finish_full_market_scan,
    get_cached_fundamentals,
    get_full_market_scan,
    save_fundamental,
    update_full_market_scan,
)
from app.strategy import analyze
from app.universe import UniverseMember, UniverseProvider, build_universe_provider


HISTORY_COUNT = max(120, int(os.getenv("HISTORY_COUNT", "260")))


@dataclass(frozen=True)
class FullScanConfig:
    provider: str = "auto"
    market: str = "ALL"
    min_market_cap_100m: float = 500.0
    min_operating_profit_100m: float = 50.0
    signal_mode: str = "prealert"

    def validate(self) -> "FullScanConfig":
        market = self.market.upper()
        if market not in {"ALL", "KOSPI", "KOSDAQ"}:
            raise ValueError("market must be ALL, KOSPI, or KOSDAQ")
        if self.min_market_cap_100m < 0:
            raise ValueError("min market cap cannot be negative")
        if self.signal_mode not in {"prealert", "actionable"}:
            raise ValueError("signal_mode must be prealert or actionable")
        return FullScanConfig(
            provider=self.provider.strip().lower(),
            market=market,
            min_market_cap_100m=float(self.min_market_cap_100m),
            min_operating_profit_100m=float(self.min_operating_profit_100m),
            signal_mode=self.signal_mode,
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "market": self.market,
            "min_market_cap_100m": self.min_market_cap_100m,
            "min_operating_profit_100m": self.min_operating_profit_100m,
            "signal_mode": self.signal_mode,
        }


ProgressHook = Callable[[str, int, int, str], None]


def _worker_count(provider: MarketDataProvider) -> int:
    provider_name = getattr(provider, "name", "")
    if provider_name == "kis" or provider_name.startswith("kis>"):
        return 1
    return max(1, min(16, int(os.getenv("FULL_SCAN_WORKERS", "8"))))


def _eligible_stages(signal_mode: str) -> set[str]:
    return {"PREALERT"} if signal_mode == "prealert" else {"PREALERT", "BREAKOUT"}


def scan_full_market(
    config: FullScanConfig,
    market_provider: MarketDataProvider | None = None,
    universe_provider: UniverseProvider | None = None,
    progress: ProgressHook | None = None,
) -> tuple[list[dict], dict]:
    """Filter fundamentals first, then calculate Turtle signals for survivors."""

    config = config.validate()
    data = market_provider or build_market_data_provider(config.provider)
    universe = universe_provider or build_universe_provider(config.provider)

    def report(phase: str, processed: int, total: int, message: str) -> None:
        if progress:
            progress(phase, processed, total, message)

    members = universe.list_members(
        config.market,
        config.min_market_cap_100m,
        lambda done, total, message: report("universe", done, total, message),
    )
    universe_count = len(members)
    report("fundamentals", 0, universe_count, f"시총 통과 {universe_count:,}개 · 영업이익 확인중")

    errors = 0
    ready: list[UniverseMember] = []
    uncached: list[UniverseMember] = []
    cache_days = max(0, int(os.getenv("FUNDAMENTALS_CACHE_DAYS", "7")))
    cached_rows = get_cached_fundamentals([member.symbol for member in members], cache_days)
    for member in members:
        if member.operating_profit_100m is not None:
            ready.append(member)
            continue
        cached = cached_rows.get(member.symbol)
        if cached is None:
            uncached.append(member)
            continue
        member.operating_profit_100m = cached.get("operating_profit_100m")
        member.fiscal_period = cached.get("fiscal_period")
        ready.append(member)

    completed = len(ready)
    if uncached:
        workers = max(1, min(_worker_count(data), len(uncached)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fundamentals") as pool:
            futures = {pool.submit(universe.get_operating_profit, member): member for member in uncached}
            for future in as_completed(futures):
                member = futures[future]
                try:
                    enriched = future.result()
                    save_fundamental(enriched.to_dict())
                    ready.append(enriched)
                except Exception:
                    errors += 1
                completed += 1
                report(
                    "fundamentals",
                    completed,
                    universe_count,
                    f"영업이익 확인 {completed:,}/{universe_count:,}",
                )

    eligible = [
        member
        for member in ready
        if member.operating_profit_100m is not None
        and member.operating_profit_100m >= config.min_operating_profit_100m
    ]
    total = len(eligible)
    report("signals", 0, total, f"재무 필터 통과 {total:,}개 · PREALERT 계산중")

    min_avg_value20 = float(os.getenv("MIN_AVG_VALUE20", "10000000000"))
    prealert_pct = float(os.getenv("PREALERT_PCT", "1.0"))
    min_score = int(os.getenv("MIN_SCORE", "55"))
    stages = _eligible_stages(config.signal_mode)

    def inspect(member: UniverseMember) -> dict | None:
        snapshot = get_market_snapshot(data, member.symbol, HISTORY_COUNT)
        if config.provider != "demo" and snapshot.quote.source == "demo":
            raise RuntimeError("demo fallback is excluded from a real full-market scan")
        result = analyze(
            snapshot.bars,
            current=snapshot.quote.price,
            current_volume=snapshot.quote.volume,
            min_avg_value20=min_avg_value20,
            prealert_pct=prealert_pct,
            min_score=min_score,
        )
        if result.stage not in stages:
            return None
        item = result.to_dict()
        item.update(
            symbol=member.symbol,
            name=member.name,
            market=member.market,
            market_cap_100m=member.market_cap_100m,
            operating_profit_100m=member.operating_profit_100m,
            fiscal_period=member.fiscal_period,
            current=snapshot.quote.price,
            source=snapshot.quote.source,
        )
        return item

    items: list[dict] = []
    completed = 0
    if eligible:
        workers = max(1, min(_worker_count(data), len(eligible)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="signals") as pool:
            futures = {pool.submit(inspect, member): member for member in eligible}
            for future in as_completed(futures):
                try:
                    item = future.result()
                    if item is not None:
                        items.append(item)
                except Exception:
                    errors += 1
                completed += 1
                report("signals", completed, total, f"신호 계산 {completed:,}/{total:,}")

    rank = {"BREAKOUT": 0, "PREALERT": 1}
    items.sort(
        key=lambda item: (
            rank.get(item.get("stage", "PREALERT"), 9),
            float(item.get("distance_pct", 999)),
            -int(item.get("score", 0)),
        )
    )
    summary = {
        "processed": completed,
        "total": total,
        "universe_count": universe_count,
        "fundamentals_passed": total,
        "error_count": errors,
        "message": f"시총 통과 {universe_count:,}개 → 재무 통과 {total:,}개 → 후보 {len(items):,}개",
    }
    return items, summary


def run_full_market_scan(
    config: FullScanConfig,
    market_provider: MarketDataProvider | None = None,
    universe_provider: UniverseProvider | None = None,
) -> int:
    """Run synchronously and publish a durable scan snapshot."""

    config = config.validate()
    scan_id = create_full_market_scan(config.to_dict())

    def progress(phase: str, processed: int, total: int, message: str) -> None:
        update_full_market_scan(
            scan_id, phase=phase, processed=processed, total=total, message=message
        )

    try:
        items, summary = scan_full_market(
            config,
            market_provider=market_provider,
            universe_provider=universe_provider,
            progress=progress,
        )
        finish_full_market_scan(scan_id, items, **summary)
    except Exception as exc:
        fail_full_market_scan(scan_id, str(exc))
    return scan_id


class FullScanJobManager:
    """In-process launcher for local UI only; Vercel never calls it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._scan_id: int | None = None

    def active_scan_id(self) -> int | None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._scan_id
            return None

    def start(self, config: FullScanConfig) -> int:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError(f"full market scan {self._scan_id} is already running")
            config = config.validate()
            scan_id = create_full_market_scan(config.to_dict())
            self._scan_id = scan_id

            def target() -> None:
                def progress(phase: str, processed: int, total: int, message: str) -> None:
                    update_full_market_scan(
                        scan_id,
                        phase=phase,
                        processed=processed,
                        total=total,
                        message=message,
                    )

                try:
                    items, summary = scan_full_market(config, progress=progress)
                    finish_full_market_scan(scan_id, items, **summary)
                except Exception as exc:
                    fail_full_market_scan(scan_id, str(exc))

            self._thread = threading.Thread(
                target=target, name=f"full-market-scan-{scan_id}", daemon=True
            )
            self._thread.start()
            return scan_id


full_scan_jobs = FullScanJobManager()


def get_scan(scan_id: int) -> dict | None:
    return get_full_market_scan(scan_id, include_items=True)
