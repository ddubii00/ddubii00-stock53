"""Durable KOSPI/KOSDAQ full-market scanner for Oracle/local operation."""

from __future__ import annotations

import os
import time

from app.full_scan import FullScanConfig, run_full_market_scan
from app.notifiers import build_notifier
from app.positions import calculate_unit_qty
from app.store import event_once, get_full_market_scan


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}원"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _message(item: dict, *, label: str, target_key: str, add_key: str, stop_key: str, exit_key: str) -> str:
    quantity, amount, risk_budget = calculate_unit_qty(
        price=float(item[target_key]),
        n_at_entry=float(item["atr20"]),
        sizing_mode=os.getenv("SIZING_MODE", "fixed"),
        fixed_unit_amount=float(os.getenv("FIXED_UNIT_AMOUNT", "10000000")),
        account_equity=float(os.getenv("ACCOUNT_EQUITY", "100000000")),
        risk_pct=float(os.getenv("RISK_PCT", "0.5")),
    )
    return (
        f"[TURTLE {label}]\n"
        f"{item.get('name') or item['symbol']} ({item['symbol']})\n"
        f"현재가 {_fmt(item.get('current'))} / 오늘 고가 {_fmt(item.get('today_high'))} "
        f"/ 오늘 저가 {_fmt(item.get('today_low'))} / 조건가 {_fmt(item.get(target_key))}\n"
        f"다음 ADD {_fmt(item.get(add_key))} / STOP {_fmt(item.get(stop_key))} / EXIT {_fmt(item.get(exit_key))}\n"
        f"시총 {float(item.get('market_cap_100m') or 0):,.0f}억원 / "
        f"영업이익 {float(item.get('operating_profit_100m') or 0):,.0f}억원\n"
        f"제안 {quantity:,}주 · 약 {_fmt(amount)} / Risk budget {_fmt(risk_budget)}\n"
        "읽기 전용 신호이며 실제 주문은 전송하지 않습니다."
    )


def scan_once() -> dict:
    config = FullScanConfig(
        provider=os.getenv("DATA_PROVIDER", "auto"),
        market=os.getenv("FULL_SCAN_MARKET", "ALL"),
        min_market_cap_100m=float(os.getenv("MIN_MARKET_CAP_100M", "500")),
        min_operating_profit_100m=float(os.getenv("MIN_OPERATING_PROFIT_100M", "50")),
        short_max_market_cap_100m=float(os.getenv("SHORT_MAX_MARKET_CAP_100M", "5000")),
        short_max_operating_profit_100m=float(
            os.getenv("SHORT_MAX_OPERATING_PROFIT_100M", "50")
        ),
        long_min_avg_volume20_10k=float(
            os.getenv("LONG_MIN_AVG_VOLUME20_10K", "0")
        ),
        short_min_avg_volume20_10k=float(
            os.getenv("SHORT_MIN_AVG_VOLUME20_10K", "0")
        ),
        signal_mode=os.getenv("FULL_SCAN_SIGNAL_MODE", "actionable"),
        prealert_pct=float(os.getenv("PREALERT_PCT", "1")),
        avg_value10_filter_enabled=_env_bool("AVG_VALUE10_FILTER_ENABLED", True),
        min_avg_value10_100m=float(os.getenv("MIN_AVG_VALUE10_100M", "500")),
        investor_filter_enabled=_env_bool("INVESTOR_FILTER_ENABLED", False),
        investor_mode=os.getenv("INVESTOR_MODE", "either"),
        min_investor_net_buy_100m=float(os.getenv("MIN_INVESTOR_NET_BUY_100M", "0")),
        today_change_filter_enabled=_env_bool("TODAY_CHANGE_FILTER_ENABLED", False),
        min_today_change_pct=float(os.getenv("MIN_TODAY_CHANGE_PCT", "5")),
    )
    scan_id = run_full_market_scan(config)
    scan = get_full_market_scan(scan_id, include_items=True)
    if scan is None or scan["status"] != "COMPLETED":
        raise RuntimeError(scan["message"] if scan else "scan result was not saved")

    notifier = build_notifier()
    signal_specs = (
        ("stage", "breakout20", "add2", "initial_stop", "exit10", "LONG 20D"),
        ("short_stage", "short_entry20", "short_add2", "short_initial_stop", "short_exit10", "SHORT 20D"),
        ("stage55", "breakout55", "add2_55", "initial_stop55", "exit20", "LONG 55D"),
        ("short_stage55", "short_entry55", "short_add2_55", "short_initial_stop55", "short_exit20", "SHORT 55D"),
    )
    for item in scan["items"]:
        for stage_key, target_key, add_key, stop_key, exit_key, system_label in signal_specs:
            stage = str(item.get(stage_key) or "")
            if stage not in {"PREALERT", "BREAKOUT", "SHORT_PREALERT", "SHORT_BREAKOUT"}:
                continue
            target = item.get(target_key)
            if target is None:
                continue
            signal_type = f"{system_label} {stage}"
            event_key = f"candidate:{item['symbol']}:{target_key}:{float(target):.4f}:{stage}"
            if event_once(event_key, item["symbol"], signal_type):
                notifier.send(
                    _message(
                        item,
                        label=signal_type,
                        target_key=target_key,
                        add_key=add_key,
                        stop_key=stop_key,
                        exit_key=exit_key,
                    )
                )
    return scan


def main() -> None:
    interval = max(0, int(os.getenv("FULL_SCAN_INTERVAL_SECONDS", "0")))
    while True:
        scan = scan_once()
        print(f"Full market scan #{scan['id']}: {scan['message']}", flush=True)
        if interval <= 0:
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
