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


def _message(item: dict) -> str:
    quantity, amount, risk_budget = calculate_unit_qty(
        price=float(item["breakout20"]),
        n_at_entry=float(item["atr20"]),
        sizing_mode=os.getenv("SIZING_MODE", "fixed"),
        fixed_unit_amount=float(os.getenv("FIXED_UNIT_AMOUNT", "10000000")),
        account_equity=float(os.getenv("ACCOUNT_EQUITY", "100000000")),
        risk_pct=float(os.getenv("RISK_PCT", "0.5")),
    )
    return (
        f"[TURTLE {item['stage']}]\n"
        f"{item.get('name') or item['symbol']} ({item['symbol']})\n"
        f"현재가 {_fmt(item.get('current'))} / 조건가 {_fmt(item.get('breakout20'))}\n"
        f"다음 ADD {_fmt(item.get('add2'))} / STOP {_fmt(item.get('initial_stop'))} / EXIT {_fmt(item.get('exit10'))}\n"
        f"시총 {item.get('market_cap_100m', 0):,.0f}억원 / "
        f"영업이익 {item.get('operating_profit_100m', 0):,.0f}억원\n"
        f"제안 {quantity:,}주 · 약 {_fmt(amount)} / Risk budget {_fmt(risk_budget)}\n"
        "읽기 전용 신호이며 실제 주문은 전송하지 않습니다."
    )


def scan_once() -> dict:
    config = FullScanConfig(
        provider=os.getenv("DATA_PROVIDER", "auto"),
        market=os.getenv("FULL_SCAN_MARKET", "ALL"),
        min_market_cap_100m=float(os.getenv("MIN_MARKET_CAP_100M", "500")),
        min_operating_profit_100m=float(os.getenv("MIN_OPERATING_PROFIT_100M", "50")),
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
    for item in scan["items"]:
        event_key = f"candidate:{item['symbol']}:{float(item['breakout20']):.4f}:{item['stage']}"
        if event_once(event_key, item["symbol"], item["stage"]):
            notifier.send(_message(item))
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
