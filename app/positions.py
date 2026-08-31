from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

from app.strategy import Bar, atr


@dataclass
class PositionGuide:
    symbol: str
    current: float
    entry_price: float
    n_at_entry: float
    filled_units: int
    sizing_mode: str
    risk_budget: float
    add_levels: list[float]
    unit_quantities: list[int]
    unit_amounts: list[float]
    total_qty: int
    total_cost: float
    next_add_price: float | None
    next_unit_qty: int
    next_unit_amount: float
    common_stop: float
    exit10: float
    ma5: float
    ma10: float
    exit_strategy: str
    sell_action: str
    sell_price: float | None
    sell_pct: int
    sell_reasons: list[str]
    action: str
    action_price: float | None
    action_qty: int
    action_amount: float
    pnl_pct: float
    reasons: list[str]
    # v0.3 compatibility aliases. They now always describe the next Unit.
    unit_qty: int
    unit_amount: float

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_unit_qty(
    *,
    price: float,
    n_at_entry: float,
    sizing_mode: str = "fixed",
    fixed_unit_amount: float = 10_000_000,
    account_equity: float = 100_000_000,
    risk_pct: float = 0.5,
) -> tuple[int, float, float]:
    """Return ``(quantity, expected amount, risk budget)`` for one Unit.

    Risk sizing is a conservative personal guide: ``equity * risk% / (2N)``.
    It does not reproduce the original futures contract-sizing system.
    """

    if price <= 0 or n_at_entry <= 0:
        raise ValueError("price and n_at_entry must be positive")
    if account_equity < 0 or risk_pct < 0 or fixed_unit_amount < 0:
        raise ValueError("sizing inputs cannot be negative")

    mode = sizing_mode.lower().strip()
    risk_budget = account_equity * risk_pct / 100.0
    if mode == "risk":
        qty = max(0, math.floor(risk_budget / (2.0 * n_at_entry)))
        return qty, qty * price, risk_budget
    if mode != "fixed":
        raise ValueError("sizing_mode must be fixed or risk")
    qty = max(0, math.floor(fixed_unit_amount / price))
    return qty, qty * price, risk_budget


def build_position_guide(
    *,
    symbol: str,
    bars: Sequence[Bar],
    current: float,
    entry_price: float,
    n_at_entry: float | None = None,
    filled_units: int = 1,
    sizing_mode: str = "fixed",
    fixed_unit_amount: float = 10_000_000,
    account_equity: float = 100_000_000,
    risk_pct: float = 0.5,
    previous_stop: float | None = None,
    exit_strategy: str = "turtle",
) -> PositionGuide:
    if len(bars) < 21:
        raise ValueError("Need at least 21 completed bars")
    if entry_price <= 0 or current <= 0:
        raise ValueError("entry_price and current must be positive")
    if not 0 <= filled_units <= 4:
        raise ValueError("filled_units must be 0..4")
    exit_mode = exit_strategy.strip().lower()
    if exit_mode not in {"turtle", "ma_staged"}:
        raise ValueError("exit_strategy must be turtle or ma_staged")

    n = float(n_at_entry or atr(bars, 20))
    if n <= 0:
        raise ValueError("n_at_entry must be positive")

    add_levels = [entry_price + offset * n for offset in (0.0, 0.5, 1.0, 1.5)]
    unit_quantities: list[int] = []
    unit_amounts: list[float] = []
    risk_budget = account_equity * risk_pct / 100.0
    for level in add_levels:
        qty, amount, risk_budget = calculate_unit_qty(
            price=level,
            n_at_entry=n,
            sizing_mode=sizing_mode,
            fixed_unit_amount=fixed_unit_amount,
            account_equity=account_equity,
            risk_pct=risk_pct,
        )
        unit_quantities.append(qty)
        unit_amounts.append(amount)

    next_add_price = add_levels[filled_units] if filled_units < 4 else None
    next_unit_qty = unit_quantities[filled_units] if filled_units < 4 else 0
    next_unit_amount = unit_amounts[filled_units] if filled_units < 4 else 0.0
    total_qty = sum(unit_quantities[:filled_units])
    total_cost = sum(unit_amounts[:filled_units])

    last_filled_price = add_levels[filled_units - 1] if filled_units else entry_price
    calculated_stop = last_filled_price - 2.0 * n
    common_stop = max(calculated_stop, float(previous_stop or calculated_stop))
    exit10 = min(b.low for b in bars[-10:])
    ma5 = sum(b.close for b in bars[-5:]) / 5.0
    ma10 = sum(b.close for b in bars[-10:]) / 10.0

    reasons: list[str] = []
    action = "HOLD"
    action_price: float | None = None
    action_qty = 0
    action_amount = 0.0

    if filled_units == 0:
        action_price = entry_price
        if current >= entry_price:
            action = "ENTRY_NOW"
            action_qty = unit_quantities[0]
            action_amount = unit_amounts[0]
            reasons.append("신규 Entry 가격 도달")
        else:
            action = "WAIT_ENTRY"
            reasons.append("Entry 가격 미도달")
    elif current <= common_stop:
        action = "STOP_NOW"
        action_price = common_stop
        action_qty = total_qty
        action_amount = action_qty * current
        reasons.append("보호손절선(최근 체결 Unit - 2N) 이탈")
    elif current <= exit10:
        action = "EXIT_NOW"
        action_price = exit10
        action_qty = total_qty
        action_amount = action_qty * current
        reasons.append("System 1 직전 10일 저가 청산선 이탈")
    elif next_add_price is not None and current >= next_add_price:
        action = "ADD_NOW"
        action_price = next_add_price
        action_qty = next_unit_qty
        action_amount = next_unit_amount
        reasons.append(f"Unit #{filled_units + 1} 추가매수 레벨 도달")
    else:
        if next_add_price is not None:
            reasons.append(f"다음 추가매수까지 {max(0.0, (next_add_price / current - 1) * 100):.2f}%")
        reasons.append(
            f"손절선까지 {max(0.0, (current / common_stop - 1) * 100) if common_stop > 0 else 0:.2f}%"
        )

    avg_fill = total_cost / total_qty if total_qty else entry_price
    pnl_pct = (current / avg_fill - 1.0) * 100.0 if avg_fill else 0.0

    sell_action = "SELL_WAIT"
    sell_price: float | None = None
    sell_pct = 0
    sell_reasons: list[str] = []
    if filled_units == 0:
        sell_reasons.append("진입 확정 전: 매도 가이드 비활성")
    elif current <= common_stop:
        sell_action = "STOP_NOW"
        sell_price = common_stop
        sell_pct = 100
        sell_reasons.append("보호손절 우선: 최근 체결 Unit - 2N 이탈")
    elif current <= exit10:
        sell_action = "EXIT_NOW"
        sell_price = exit10
        sell_pct = 100
        sell_reasons.append("정통 System 1: 직전 10거래일 최저가 이탈, 전량 청산")
    elif exit_mode == "ma_staged" and current <= ma10:
        sell_action = "REDUCE_2"
        sell_price = ma10
        sell_pct = 100
        sell_reasons.append("MA10 이탈: 1차 매도 후 잔여 포지션 정리 검토")
    elif exit_mode == "ma_staged" and current <= ma5:
        sell_action = "REDUCE_1"
        sell_price = ma5
        sell_pct = 50
        sell_reasons.append("MA5 이탈: 1차 50% 분할매도 검토")
    elif exit_mode == "ma_staged":
        sell_reasons.append("MA5·MA10 위: 보유, 10D Low 전량청산선 추적")
    else:
        sell_reasons.append("정통 Turtle: 고정 익절 없이 10D Low 전량청산선 추적")

    return PositionGuide(
        symbol=symbol,
        current=current,
        entry_price=entry_price,
        n_at_entry=n,
        filled_units=filled_units,
        sizing_mode=sizing_mode,
        risk_budget=risk_budget,
        add_levels=add_levels,
        unit_quantities=unit_quantities,
        unit_amounts=unit_amounts,
        total_qty=total_qty,
        total_cost=total_cost,
        next_add_price=next_add_price,
        next_unit_qty=next_unit_qty,
        next_unit_amount=next_unit_amount,
        common_stop=common_stop,
        exit10=exit10,
        ma5=ma5,
        ma10=ma10,
        exit_strategy=exit_mode,
        sell_action=sell_action,
        sell_price=sell_price,
        sell_pct=sell_pct,
        sell_reasons=sell_reasons,
        action=action,
        action_price=action_price,
        action_qty=action_qty,
        action_amount=action_amount,
        pnl_pct=pnl_pct,
        reasons=reasons,
        unit_qty=next_unit_qty,
        unit_amount=next_unit_amount,
    )
