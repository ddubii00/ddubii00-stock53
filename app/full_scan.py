from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from app.providers import (
    MarketDataProvider,
    build_market_data_provider,
    get_market_snapshot,
    validate_snapshot_price_scale,
)
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
    short_max_market_cap_100m: float = 5_000.0
    short_max_operating_profit_100m: float = 50.0
    long_min_avg_volume20_10k: float = 0.0
    short_min_avg_volume20_10k: float = 0.0
    include_etf: bool = False
    signal_mode: str = "prealert"
    prealert_pct: float = 1.0
    avg_value10_filter_enabled: bool = True
    min_avg_value10_100m: float = 500.0
    investor_filter_enabled: bool = False
    investor_mode: str = "either"
    min_investor_net_buy_100m: float = 0.0
    today_change_filter_enabled: bool = False
    min_today_change_pct: float = 5.0

    def validate(self) -> "FullScanConfig":
        market = self.market.upper()
        if market not in {"ALL", "KOSPI", "KOSDAQ"}:
            raise ValueError("market must be ALL, KOSPI, or KOSDAQ")
        if self.min_market_cap_100m < 0:
            raise ValueError("min market cap cannot be negative")
        if self.short_max_market_cap_100m < 0:
            raise ValueError("short max market cap cannot be negative")
        if self.long_min_avg_volume20_10k < 0:
            raise ValueError("long minimum 20-day average volume cannot be negative")
        if self.short_min_avg_volume20_10k < 0:
            raise ValueError("short minimum 20-day average volume cannot be negative")
        if self.signal_mode not in {"prealert", "breakout", "actionable"}:
            raise ValueError("signal_mode must be prealert, breakout, or actionable")
        if self.prealert_pct < 0:
            raise ValueError("prealert_pct cannot be negative")
        if self.min_avg_value10_100m < 0:
            raise ValueError("minimum 10-day average value cannot be negative")
        if self.investor_mode not in {"either", "foreign", "institution", "combined"}:
            raise ValueError("invalid investor_mode")
        if self.min_investor_net_buy_100m < 0:
            raise ValueError("minimum investor net buy cannot be negative")
        if self.min_today_change_pct < 0:
            raise ValueError("minimum today change cannot be negative")
        return FullScanConfig(
            provider=self.provider.strip().lower(),
            market=market,
            min_market_cap_100m=float(self.min_market_cap_100m),
            min_operating_profit_100m=float(self.min_operating_profit_100m),
            short_max_market_cap_100m=float(self.short_max_market_cap_100m),
            short_max_operating_profit_100m=float(
                self.short_max_operating_profit_100m
            ),
            long_min_avg_volume20_10k=float(self.long_min_avg_volume20_10k),
            short_min_avg_volume20_10k=float(self.short_min_avg_volume20_10k),
            include_etf=bool(self.include_etf),
            signal_mode=self.signal_mode,
            prealert_pct=float(self.prealert_pct),
            avg_value10_filter_enabled=bool(self.avg_value10_filter_enabled),
            min_avg_value10_100m=float(self.min_avg_value10_100m),
            investor_filter_enabled=bool(self.investor_filter_enabled),
            investor_mode=self.investor_mode,
            min_investor_net_buy_100m=float(self.min_investor_net_buy_100m),
            today_change_filter_enabled=bool(self.today_change_filter_enabled),
            min_today_change_pct=float(self.min_today_change_pct),
        )

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "market": self.market,
            "min_market_cap_100m": self.min_market_cap_100m,
            "min_operating_profit_100m": self.min_operating_profit_100m,
            "short_max_market_cap_100m": self.short_max_market_cap_100m,
            "short_max_operating_profit_100m": self.short_max_operating_profit_100m,
            "long_min_avg_volume20_10k": self.long_min_avg_volume20_10k,
            "short_min_avg_volume20_10k": self.short_min_avg_volume20_10k,
            "include_etf": self.include_etf,
            "signal_mode": self.signal_mode,
            "prealert_pct": self.prealert_pct,
            "avg_value10_filter_enabled": self.avg_value10_filter_enabled,
            "min_avg_value10_100m": self.min_avg_value10_100m,
            "investor_filter_enabled": self.investor_filter_enabled,
            "investor_mode": self.investor_mode,
            "min_investor_net_buy_100m": self.min_investor_net_buy_100m,
            "today_change_filter_enabled": self.today_change_filter_enabled,
            "min_today_change_pct": self.min_today_change_pct,
        }


ProgressHook = Callable[[str, int, int, str], None]


def _worker_count(provider: MarketDataProvider) -> int:
    provider_name = getattr(provider, "name", "")
    if provider_name == "kis" or provider_name.startswith("kis>"):
        return 1
    return max(1, min(16, int(os.getenv("FULL_SCAN_WORKERS", "8"))))


def _eligible_stages(signal_mode: str) -> tuple[set[str], set[str]]:
    if signal_mode == "prealert":
        return {"PREALERT"}, {"SHORT_PREALERT"}
    if signal_mode == "breakout":
        return {"BREAKOUT"}, {"SHORT_BREAKOUT"}
    return {"PREALERT", "BREAKOUT"}, {"SHORT_PREALERT", "SHORT_BREAKOUT"}


def _investor_filter_passes(config: FullScanConfig, foreign: float, institution: float) -> bool:
    threshold = config.min_investor_net_buy_100m * 100_000_000
    positive_threshold = max(0.0, threshold)
    if config.investor_mode == "foreign":
        value = foreign
    elif config.investor_mode == "institution":
        value = institution
    elif config.investor_mode == "combined":
        value = foreign + institution
    else:
        value = max(foreign, institution)
    return value > 0 if positive_threshold == 0 else value >= positive_threshold


def _fundamental_sides(
    config: FullScanConfig, member: UniverseMember
) -> tuple[bool, bool]:
    """Return long/short fundamental eligibility without changing signals."""

    if member.asset_type == "ETF":
        return True, True
    profit = member.operating_profit_100m
    if profit is None:
        return False, False
    long_pass = (
        member.market_cap_100m >= config.min_market_cap_100m
        and profit >= config.min_operating_profit_100m
    )
    short_pass = (
        member.market_cap_100m <= config.short_max_market_cap_100m
        and profit <= config.short_max_operating_profit_100m
    )
    return long_pass, short_pass


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
        0,
        lambda done, total, message: report("universe", done, total, message),
        include_etf=config.include_etf,
    )
    universe_count = len(members)
    listed_count = int(getattr(universe, "last_listed_count", universe_count))
    stock_count = int(
        getattr(
            universe,
            "last_stock_count",
            sum(1 for member in members if member.asset_type != "ETF"),
        )
    )
    etf_count = int(
        getattr(
            universe,
            "last_etf_count",
            sum(1 for member in members if member.asset_type == "ETF"),
        )
    )
    market_counts = getattr(universe, "last_market_counts", {})
    kospi_count = int(market_counts.get("KOSPI", 0))
    kosdaq_count = int(market_counts.get("KOSDAQ", 0))
    report(
        "fundamentals",
        0,
        universe_count,
        (
            f"일반주식 {stock_count:,}개 + ETF {etf_count:,}개 확인 · "
            f"롱·숏 독립 시총 범위 판정 대상 + ETF {universe_count:,}개 · 영업이익 확인중"
            if config.include_etf
            else f"상장주식 {listed_count:,}개 전체 확인 · 롱·숏 독립 시총 범위 판정 · 영업이익 확인중"
        ),
    )

    errors = 0
    ready: list[UniverseMember] = [member for member in members if member.asset_type == "ETF"]
    uncached: list[UniverseMember] = []
    cache_days = max(0, int(os.getenv("FUNDAMENTALS_CACHE_DAYS", "7")))
    cached_rows = get_cached_fundamentals([member.symbol for member in members], cache_days)
    for member in members:
        if member.asset_type == "ETF":
            continue
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

    eligible = [member for member in ready if any(_fundamental_sides(config, member))]
    stock_fundamentals_passed = sum(
        1
        for member in ready
        if member.asset_type != "ETF" and _fundamental_sides(config, member)[0]
    )
    short_fundamentals_passed = sum(
        1
        for member in ready
        if member.asset_type != "ETF" and _fundamental_sides(config, member)[1]
    )
    etf_scanned = sum(1 for member in eligible if member.asset_type == "ETF")
    total = len(eligible)
    signal_label = "PREALERT/BREAKOUT" if config.signal_mode == "actionable" else config.signal_mode.upper()
    signal_progress = (
        f"롱 재무 통과 {stock_fundamentals_passed:,}개 · 숏 재무 통과 {short_fundamentals_passed:,}개 + ETF {etf_scanned:,}개"
        if config.include_etf
        else f"롱 재무 통과 {stock_fundamentals_passed:,}개 · 숏 재무 통과 {short_fundamentals_passed:,}개"
    )
    report("signals", 0, total, f"{signal_progress} · {signal_label} 계산중")

    min_avg_value20 = float(os.getenv("MIN_AVG_VALUE20", "10000000000"))
    min_score = int(os.getenv("MIN_SCORE", "55"))
    long_stages, short_stages = _eligible_stages(config.signal_mode)

    def inspect(member: UniverseMember) -> dict | None:
        snapshot = get_market_snapshot(data, member.symbol, HISTORY_COUNT)
        if config.provider != "demo" and snapshot.quote.source == "demo":
            raise RuntimeError("demo fallback is excluded from a real full-market scan")
        today_change_pct = validate_snapshot_price_scale(snapshot)
        result = analyze(
            snapshot.bars,
            current=snapshot.quote.price,
            current_volume=snapshot.quote.volume,
            min_avg_value20=min_avg_value20,
            prealert_pct=config.prealert_pct,
            min_score=min_score,
            today_high=snapshot.quote.day_high,
        )
        long_fundamental_pass, short_fundamental_pass = _fundamental_sides(config, member)
        long_volume_pass = result.avg_volume20 >= config.long_min_avg_volume20_10k * 10_000
        short_volume_pass = result.avg_volume20 >= config.short_min_avg_volume20_10k * 10_000
        long_signal = long_fundamental_pass and long_volume_pass and result.stage in long_stages
        short_signal = short_fundamental_pass and short_volume_pass and result.short_stage in short_stages
        if (
            long_signal
            and result.stage == "BREAKOUT"
            and config.today_change_filter_enabled
            and today_change_pct < config.min_today_change_pct
        ):
            long_signal = False
        if not long_signal and not short_signal:
            return None
        if (
            config.avg_value10_filter_enabled
            and result.avg_value10 < config.min_avg_value10_100m * 100_000_000
        ):
            return None
        flow = None
        investor_error = None
        if member.asset_type != "ETF":
            try:
                candidate_flow = data.get_investor_flow(member.symbol)
                if config.provider != "demo" and candidate_flow.source == "demo":
                    raise RuntimeError(
                        "demo investor flow is excluded from a real full-market scan"
                    )
                flow = candidate_flow
            except Exception as exc:
                if config.investor_filter_enabled:
                    raise
                investor_error = str(exc)
            if (
                flow is not None
                and config.investor_filter_enabled
                and not _investor_filter_passes(
                    config, flow.foreign_net_amount, flow.institution_net_amount
                )
            ):
                return None
        item = result.to_dict()
        if not long_signal:
            item["stage"] = "FILTERED"
        if not short_signal:
            item["short_stage"] = "SHORT_FILTERED"
        item.update(
            symbol=member.symbol,
            name=member.name,
            market=member.market,
            market_cap_100m=member.market_cap_100m,
            operating_profit_100m=member.operating_profit_100m,
            fiscal_period=member.fiscal_period,
            asset_type=member.asset_type,
            long_fundamental_pass=long_fundamental_pass,
            short_fundamental_pass=short_fundamental_pass,
            long_volume_pass=long_volume_pass,
            short_volume_pass=short_volume_pass,
            current=snapshot.quote.price,
            today_high=snapshot.quote.day_high,
            today_change_pct=today_change_pct,
            source=snapshot.quote.source,
            signal_date=snapshot.as_of_date,
            latest_completed_session=snapshot.latest_completed_session,
            investor_date=flow.date if flow else None,
            foreign_net_buy_100m=(flow.foreign_net_amount / 100_000_000) if flow else None,
            institution_net_buy_100m=(flow.institution_net_amount / 100_000_000) if flow else None,
            investor_source=flow.source if flow else None,
            investor_amount_estimated=flow.estimated_amount if flow else None,
            investor_availability="post_close" if member.asset_type != "ETF" else "not_applicable",
            investor_error=investor_error,
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

    rank = {"PREALERT": 0, "BREAKOUT": 1}
    short_rank = {"SHORT_PREALERT": 0, "SHORT_BREAKOUT": 1}
    items.sort(
        key=lambda item: (
            min(
                rank.get(item.get("stage", ""), 9),
                short_rank.get(item.get("short_stage", ""), 9),
            ),
            min(
                abs(float(item.get("distance_pct", 999))),
                abs(float(item.get("short_distance_pct", 999))),
            ),
            -int(item.get("score", 0)),
        )
    )
    signal_dates = [str(item.get("signal_date")) for item in items if item.get("signal_date")]
    summary = {
        "processed": completed,
        "total": total,
        "listed_count": listed_count,
        "stock_count": stock_count,
        "etf_count": etf_count,
        "kospi_count": kospi_count,
        "kosdaq_count": kosdaq_count,
        "universe_count": universe_count,
        "fundamentals_passed": total,
        "stock_fundamentals_passed": stock_fundamentals_passed,
        "short_fundamentals_passed": short_fundamentals_passed,
        "etf_scanned": etf_scanned,
        "error_count": errors,
        "signal_date": max(signal_dates) if signal_dates else None,
        "latest_completed_session": any(
            bool(item.get("latest_completed_session")) for item in items
        ),
        "message": (
            (
                f"일반주식 {stock_count:,}개 + ETF {etf_count:,}개(ETN 제외) "
                f"→ 롱·숏 독립 시총 범위 판정 + ETF {universe_count:,}개 "
                f"→ 롱 재무 통과 {stock_fundamentals_passed:,}개 · 숏 재무 통과 {short_fundamentals_passed:,}개 + ETF {etf_scanned:,}개 "
                f"→ 선택 옵션 통과 후보 {len(items):,}개"
            )
            if config.include_etf
            else (
                f"상장주식 전체 {listed_count:,}개(ETF/ETN 제외) → 롱·숏 독립 시총 범위 판정 {universe_count:,}개 "
                f"→ 롱 재무 통과 {stock_fundamentals_passed:,}개 · 숏 재무 통과 {short_fundamentals_passed:,}개 "
                f"→ 합집합 {total:,}개 → 선택 옵션 통과 후보 {len(items):,}개"
            )
        ),
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
