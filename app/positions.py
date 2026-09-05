from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Sequence

from app.strategy import Bar, atr


ORIGINAL_MAX_UNITS = 4
MAX_UNITS = 6
UNIT_OFFSETS = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5)


@dataclass
class PositionGuide:
    symbol: str
    side: str
    current: float
    entry_price: float
    n_at_entry: float
    current_atr: float
    filled_units: int
    max_units: int
    original_max_units: int
    sizing_mode: str
    fixed_unit_amount: float
    account_equity: float
    risk_pct: float
    risk_budget: float
    unit_risk_budget: float
    risk_per_share: float
    risk_qty_raw: float
    add_levels: list[float]
    unit_prices: list[float]
    fill_prices: list[float]
    fill_price_basis: str
    unit_quantities: list[int]
    unit_amounts: list[float]
    total_qty: int
    total_cost: float
    next_add_price: float | None
    next_unit_qty: int
    next_unit_amount: float
    reached_unit_count: int
    pending_reached_units: int
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
    short_stage: str
    short_prealert_pct: float
    short_entry20: float
    short_distance_pct: float
    short_yesterday_broke: bool
    short_stop: float
    short_exit10: float
    short_add_levels: list[float]
    # v0.3 compatibility aliases. They always describe the next Unit.
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
    risk_units: int = 1,
) -> tuple[int, float, float]:
    """Return ``(quantity, expected amount, total risk budget)`` for one Unit.

    With ``risk_units=1`` this is the textbook personal-stock approximation
    ``equity * risk% / (2N)``. A six-Unit position guide passes six so the
    user-entered risk limit is divided across the complete pyramid rather than
    being silently repeated six times.
    """

    if price <= 0 or n_at_entry <= 0:
        raise ValueError("price and n_at_entry must be positive")
    if account_equity < 0 or risk_pct < 0 or fixed_unit_amount < 0:
        raise ValueError("sizing inputs cannot be negative")
    if risk_units <= 0:
        raise ValueError("risk_units must be positive")

    mode = sizing_mode.lower().strip()
    risk_budget = account_equity * risk_pct / 100.0
    if mode == "risk":
        qty = max(0, math.floor((risk_budget / risk_units) / (2.0 * n_at_entry)))
        return qty, qty * price, risk_budget
    if mode != "fixed":
        raise ValueError("sizing_mode must be fixed or risk")
    qty = max(0, math.floor(fixed_unit_amount / price))
    return qty, qty * price, risk_budget


def _confirmed_fill_prices(
    fill_prices: Sequence[float] | None, filled_units: int
) -> list[float]:
    values = [float(value) for value in (fill_prices or [])]
    if len(values) > MAX_UNITS:
        raise ValueError(f"fill_prices can contain at most {MAX_UNITS} values")
    if any(value <= 0 for value in values):
        raise ValueError("fill prices must be positive")
    return values[:filled_units]


def _ratcheted_stop(
    *, side: str, calculated: float, previous_stop: float | None
) -> float:
    previous = float(previous_stop or 0)
    if previous <= 0:
        return calculated
    return max(previous, calculated) if side == "long" else min(previous, calculated)


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
    prealert_pct: float = 1.0,
    side: str = "long",
    fill_prices: Sequence[float] | None = None,
) -> PositionGuide:
    if len(bars) < 21:
        raise ValueError("Need at least 21 completed bars")
    if entry_price <= 0 or current <= 0:
        raise ValueError("entry_price and current must be positive")
    if not 0 <= filled_units <= MAX_UNITS:
        raise ValueError(f"filled_units must be 0..{MAX_UNITS}")
    if prealert_pct < 0:
        raise ValueError("prealert_pct cannot be negative")
    position_side = side.strip().lower()
    if position_side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    exit_mode = exit_strategy.strip().lower()
    if exit_mode not in {"turtle", "ma_staged"}:
        raise ValueError("exit_strategy must be turtle or ma_staged")

    current_atr = atr(bars, 20)
    n = float(n_at_entry or current_atr)
    if n <= 0:
        raise ValueError("n_at_entry must be positive")

    direction = 1.0 if position_side == "long" else -1.0
    add_levels = [entry_price + direction * offset * n for offset in UNIT_OFFSETS]
    confirmed_fills = _confirmed_fill_prices(fill_prices, filled_units)
    unit_prices = [
        confirmed_fills[index] if index < len(confirmed_fills) else level
        for index, level in enumerate(add_levels)
    ]
    if filled_units == 0:
        fill_price_basis = "none"
    elif len(confirmed_fills) == filled_units:
        fill_price_basis = "actual"
    elif confirmed_fills:
        fill_price_basis = "mixed"
    else:
        fill_price_basis = "theoretical_fallback"

    risk_budget = account_equity * risk_pct / 100.0
    unit_risk_budget = risk_budget / MAX_UNITS
    risk_per_share = 2.0 * n
    risk_qty_raw = unit_risk_budget / risk_per_share if risk_per_share > 0 else 0.0
    risk_units = MAX_UNITS if sizing_mode == "risk" else 1
    unit_quantities: list[int] = []
    unit_amounts: list[float] = []
    for price in unit_prices:
        qty, amount, _ = calculate_unit_qty(
            price=price,
            n_at_entry=n,
            sizing_mode=sizing_mode,
            fixed_unit_amount=fixed_unit_amount,
            account_equity=account_equity,
            risk_pct=risk_pct,
            risk_units=risk_units,
        )
        unit_quantities.append(qty)
        unit_amounts.append(amount)

    next_add_price = add_levels[filled_units] if filled_units < MAX_UNITS else None
    next_unit_qty = unit_quantities[filled_units] if filled_units < MAX_UNITS else 0
    next_unit_amount = unit_amounts[filled_units] if filled_units < MAX_UNITS else 0.0
    total_qty = sum(unit_quantities[:filled_units])
    total_cost = sum(unit_amounts[:filled_units])
    reached_unit_count = sum(
        current >= level if position_side == "long" else current <= level for level in add_levels
    )
    pending_reached_units = max(0, reached_unit_count - filled_units)

    last_filled_price = unit_prices[filled_units - 1] if filled_units else entry_price
    calculated_stop = (
        last_filled_price - 2.0 * n
        if position_side == "long"
        else last_filled_price + 2.0 * n
    )
    common_stop = _ratcheted_stop(
        side=position_side, calculated=calculated_stop, previous_stop=previous_stop
    )
    exit10 = (
        min(bar.low for bar in bars[-10:])
        if position_side == "long"
        else max(bar.high for bar in bars[-10:])
    )
    ma5 = sum(bar.close for bar in bars[-5:]) / 5.0
    ma10 = sum(bar.close for bar in bars[-10:]) / 10.0

    # Search-preview fields are separate from the selected position. The UI
    # shows them only in candidate tables unless short is explicitly selected.
    short_entry20 = min(bar.low for bar in bars[-19:])
    short_yesterday_level = min(bar.low for bar in bars[-20:-1])
    short_yesterday_broke = bars[-1].low < short_yesterday_level
    short_distance_pct = (current / short_entry20 - 1.0) * 100.0
    if short_yesterday_broke:
        short_stage = "SHORT_FILTERED"
    elif current <= short_entry20:
        short_stage = "SHORT_BREAKOUT"
    elif 0.0 < short_distance_pct <= prealert_pct + 1e-9:
        short_stage = "SHORT_PREALERT"
    else:
        short_stage = "SHORT_WATCH"
    short_stop = short_entry20 + 2.0 * current_atr
    short_exit10 = max(bar.high for bar in bars[-10:])
    short_add_levels = [short_entry20 - offset * current_atr for offset in UNIT_OFFSETS]

    reasons: list[str] = []
    action = "HOLD"
    action_price: float | None = None
    action_qty = 0
    action_amount = 0.0
    stop_now = current <= common_stop if position_side == "long" else current >= common_stop
    exit_now = current <= exit10 if position_side == "long" else current >= exit10
    entry_now = current >= entry_price if position_side == "long" else current <= entry_price
    add_now = next_add_price is not None and (
        current >= next_add_price if position_side == "long" else current <= next_add_price
    )

    if filled_units == 0:
        action_price = entry_price
        if entry_now:
            action = "ENTRY_NOW"
            action_qty = unit_quantities[0]
            action_amount = unit_amounts[0]
            reasons.append(
                "신규 롱 Entry 가격 도달"
                if position_side == "long"
                else "신규 숏 Entry 가격 도달"
            )
        else:
            action = "WAIT_ENTRY"
            reasons.append("Entry 가격 미도달")
    elif stop_now:
        action = "STOP_NOW"
        action_price = common_stop
        action_qty = total_qty
        action_amount = action_qty * current
        reasons.append("실제 최근 체결가 기준 2N 보호손절선 도달")
    elif exit_now:
        action = "EXIT_NOW"
        action_price = exit10
        action_qty = total_qty
        action_amount = action_qty * current
        reasons.append("System 1 직전 10일 채널 청산선 도달")
    elif add_now:
        action = "ADD_NOW"
        action_price = next_add_price
        action_qty = next_unit_qty
        action_amount = next_unit_amount
        if pending_reached_units > 1:
            reasons.append(
                f"Unit #{filled_units + 1}~#{reached_unit_count} 레벨 동시 도달 · "
                "실제 체결가를 모두 입력해 한 번에 확정 가능"
            )
        else:
            reasons.append(f"Unit #{filled_units + 1} 추가 레벨 도달")
    else:
        if next_add_price is not None:
            distance = (
                (next_add_price / current - 1.0) * 100.0
                if position_side == "long"
                else (current / next_add_price - 1.0) * 100.0
            )
            reasons.append(f"다음 추가까지 {max(0.0, distance):.2f}%")
        reasons.append("실제 주문 없음 · 체결 후 실제 가격을 입력해 직접 확정")

    avg_fill = total_cost / total_qty if total_qty else entry_price
    if not avg_fill:
        pnl_pct = 0.0
    elif position_side == "long":
        pnl_pct = (current / avg_fill - 1.0) * 100.0
    else:
        pnl_pct = (avg_fill / current - 1.0) * 100.0

    sell_action = "SELL_WAIT" if position_side == "long" else "COVER_WAIT"
    sell_price: float | None = None
    sell_pct = 0
    sell_reasons: list[str] = []
    if filled_units == 0:
        sell_reasons.append("진입 확정 전: 청산 가이드 비활성")
    elif stop_now:
        sell_action = "STOP_NOW"
        sell_price = common_stop
        sell_pct = 100
        sell_reasons.append("보호손절 우선: 실제 최근 체결 Unit 기준 2N 도달")
    elif exit_now:
        sell_action = "EXIT_NOW"
        sell_price = exit10
        sell_pct = 100
        channel = (
            "10거래일 최저가 이탈"
            if position_side == "long"
            else "10거래일 최고가 상향 돌파"
        )
        sell_reasons.append(f"정통 System 1 대칭 청산: {channel}, 전량 청산")
    elif position_side == "short":
        sell_reasons.append("숏 포지션: 2N 상단 손절과 10D High 환매 청산선을 추적")
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
        side=position_side,
        current=current,
        entry_price=entry_price,
        n_at_entry=n,
        current_atr=current_atr,
        filled_units=filled_units,
        max_units=MAX_UNITS,
        original_max_units=ORIGINAL_MAX_UNITS,
        sizing_mode=sizing_mode,
        fixed_unit_amount=fixed_unit_amount,
        account_equity=account_equity,
        risk_pct=risk_pct,
        risk_budget=risk_budget,
        unit_risk_budget=unit_risk_budget,
        risk_per_share=risk_per_share,
        risk_qty_raw=risk_qty_raw,
        add_levels=add_levels,
        unit_prices=unit_prices,
        fill_prices=confirmed_fills,
        fill_price_basis=fill_price_basis,
        unit_quantities=unit_quantities,
        unit_amounts=unit_amounts,
        total_qty=total_qty,
        total_cost=total_cost,
        next_add_price=next_add_price,
        next_unit_qty=next_unit_qty,
        next_unit_amount=next_unit_amount,
        reached_unit_count=reached_unit_count,
        pending_reached_units=pending_reached_units,
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
        short_stage=short_stage,
        short_prealert_pct=prealert_pct,
        short_entry20=short_entry20,
        short_distance_pct=short_distance_pct,
        short_yesterday_broke=short_yesterday_broke,
        short_stop=short_stop,
        short_exit10=short_exit10,
        short_add_levels=short_add_levels,
        unit_qty=next_unit_qty,
        unit_amount=next_unit_amount,
    )
