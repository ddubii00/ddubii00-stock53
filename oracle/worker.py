"""Oracle polling worker for read-only Turtle signals and Telegram alerts."""

from __future__ import annotations

import os
import time

from app.notifiers import Notifier, build_notifier
from app.positions import build_position_guide, calculate_unit_qty
from app.providers import (
    MarketDataProvider,
    build_market_data_provider,
    get_market_snapshot,
    validate_snapshot_price_scale,
)
from app.state import build_position_state_store
from app.store import event_once
from app.strategy import TurtleResult, analyze


DEFAULT_WATCHLIST = "000660,005930,005380,012450,035420"
HISTORY_COUNT = max(120, int(os.getenv("HISTORY_COUNT", "260")))


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}원"


def _sizing() -> dict:
    return {
        "sizing_mode": os.getenv("SIZING_MODE", "fixed"),
        "fixed_unit_amount": float(os.getenv("FIXED_UNIT_AMOUNT", "10000000")),
        "account_equity": float(os.getenv("ACCOUNT_EQUITY", "100000000")),
        "risk_pct": float(os.getenv("RISK_PCT", "0.5")),
    }


def _candidate_message(
    symbol: str, price: float, day_high: float | None, result: TurtleResult
) -> str:
    sizing = _sizing()
    quantity, amount, risk_budget = calculate_unit_qty(
        price=result.breakout20,
        n_at_entry=result.atr20,
        **sizing,
    )
    return (
        f"[TURTLE {result.stage}]\n"
        f"{symbol}\n"
        f"현재가 {fmt(price)} / 오늘 고가 {fmt(day_high)} / 조건가 {fmt(result.breakout20)}\n"
        f"다음 ADD {fmt(result.add2)} / STOP {fmt(result.initial_stop)} / EXIT {fmt(result.exit10)}\n"
        f"제안 {quantity:,}주 · 약 {fmt(amount)} / Risk budget {fmt(risk_budget)}\n"
        f"거리 {result.distance_pct:.2f}% · ATR20 {fmt(result.atr20)} · Quality {result.score}"
    )


def _position_message(name: str, guide) -> str:
    condition = guide.action_price or guide.current
    side = "LONG" if guide.side == "long" else "SHORT"
    return (
        f"[TURTLE {side} {guide.action}]\n"
        f"{name}\n"
        f"현재가 {fmt(guide.current)} / 조건가 {fmt(condition)}\n"
        f"다음 ADD {fmt(guide.next_add_price)} / STOP {fmt(guide.common_stop)} / EXIT {fmt(guide.exit10)}\n"
        f"제안 {guide.action_qty:,}주 · 약 {fmt(guide.action_amount)} / Risk budget {fmt(guide.risk_budget)}\n"
        "체결 여부는 자동 반영하지 않습니다. 앱에서 진입/추매 완료를 직접 확정하세요."
    )


def monitor_once(
    provider: MarketDataProvider | None = None,
    notifier: Notifier | None = None,
) -> None:
    """Run one complete scan. No function in this worker sends an order."""

    market_provider = provider or build_market_data_provider(os.getenv("DATA_PROVIDER", "auto"))
    signal_notifier = notifier or build_notifier()
    symbols = [item.strip() for item in os.getenv("WATCHLIST", DEFAULT_WATCHLIST).split(",") if item.strip()]

    for symbol in symbols:
        try:
            snapshot = get_market_snapshot(market_provider, symbol, HISTORY_COUNT)
            validate_snapshot_price_scale(snapshot)
            result = analyze(
                snapshot.bars,
                current=snapshot.quote.price,
                current_volume=snapshot.quote.volume,
                prealert_pct=float(os.getenv("PREALERT_PCT", "1.0")),
                min_avg_value20=float(os.getenv("MIN_AVG_VALUE20", "10000000000")),
                min_score=int(os.getenv("MIN_SCORE", "55")),
                today_high=snapshot.quote.day_high,
            )
            if result.stage in {"PREALERT", "BREAKOUT"}:
                event_key = f"candidate:{symbol}:{result.breakout20:.4f}:{result.stage}"
                if event_once(event_key, symbol, result.stage):
                    signal_notifier.send(
                        _candidate_message(
                            symbol, snapshot.quote.price, snapshot.quote.day_high, result
                        )
                    )
        except Exception as exc:
            print(f"candidate {symbol}: {exc}", flush=True)

    sizing_keys = {"sizing_mode", "fixed_unit_amount", "account_equity", "risk_pct"}
    for position in build_position_state_store().list_active():
        symbol = position["symbol"]
        try:
            snapshot = get_market_snapshot(market_provider, symbol, HISTORY_COUNT)
            validate_snapshot_price_scale(snapshot)
            guide = build_position_guide(
                symbol=symbol,
                bars=snapshot.bars,
                current=snapshot.quote.price,
                entry_price=position["entry_price"],
                n_at_entry=position["n_at_entry"],
                filled_units=position["filled_units"],
                side=position.get("side", "long"),
                fill_prices=position.get("fill_prices", []),
                previous_stop=position.get("common_stop"),
                exit_strategy=position.get("exit_strategy", "turtle"),
                **{key: position[key] for key in sizing_keys},
            )
            if guide.action in {"ADD_NOW", "STOP_NOW", "EXIT_NOW"}:
                marker = guide.action_price or snapshot.quote.price
                event_key = f"position:{symbol}:{guide.action}:{guide.filled_units}:{marker:.4f}"
                if event_once(event_key, symbol, guide.action):
                    signal_notifier.send(_position_message(position.get("name") or symbol, guide))
        except Exception as exc:
            print(f"position {symbol}: {exc}", flush=True)


def main() -> None:
    interval = max(5, int(os.getenv("ORACLE_POLL_SECONDS", "20")))
    print(f"Turtle polling worker started. interval={interval}s (read-only, no orders)", flush=True)
    while True:
        monitor_once()
        time.sleep(interval)


if __name__ == "__main__":
    main()
