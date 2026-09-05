import pytest

from app.positions import build_position_guide, calculate_unit_qty
from app.strategy import Bar


def bars(n: int = 140):
    out = []
    for i in range(n):
        c = 100_000 + i * 10
        out.append(Bar(high=c + 2_000, low=c - 2_000, close=c, volume=1_000_000, value=100_000_000_000))
    return out


def test_fixed_unit_qty():
    qty, amount, _ = calculate_unit_qty(price=300_000, n_at_entry=12_000, fixed_unit_amount=10_000_000)
    assert qty == 33
    assert amount == 9_900_000


def test_risk_unit_qty():
    qty, amount, risk = calculate_unit_qty(
        price=300_000,
        n_at_entry=12_000,
        sizing_mode="risk",
        account_equity=100_000_000,
        risk_pct=0.5,
    )
    assert risk == 500_000
    assert qty == 20  # floor(500,000 / 24,000)
    assert amount == 6_000_000


def test_textbook_risk_sizing_example_floors_fractional_quantity():
    qty, _, risk = calculate_unit_qty(
        price=10_000,
        n_at_entry=700,
        sizing_mode="risk",
        account_equity=150_000,
        risk_pct=2,
    )
    assert risk == 3_000
    assert risk / (2 * 700) == pytest.approx(2.142857)
    assert qty == 2


def test_add_now_at_half_n():
    g = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=306_100,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=1,
        fixed_unit_amount=10_000_000,
    )
    assert g.next_add_price == 306_000
    assert g.action == "ADD_NOW"
    assert g.action_qty == 32  # sized at next unit price


def test_all_pyramid_levels_and_fixed_quantities_are_independent():
    g = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=320_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=3,
        fixed_unit_amount=10_000_000,
    )
    assert g.add_levels == [300_000, 306_000, 312_000, 318_000, 324_000, 330_000]
    assert g.unit_quantities == [33, 32, 32, 31, 30, 30]
    assert g.total_qty == 97
    assert g.next_unit_qty == 31
    assert g.next_unit_amount == 9_858_000


def test_unit_five_and_six_are_available_as_explicit_extension():
    fifth = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=324_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=4,
    )
    sixth = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=330_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=5,
    )
    complete = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=340_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=6,
    )
    assert fifth.action == "ADD_NOW"
    assert fifth.next_add_price == 324_000
    assert sixth.action == "ADD_NOW"
    assert sixth.next_add_price == 330_000
    assert complete.next_add_price is None
    assert complete.next_unit_qty == 0
    assert complete.max_units == 6
    assert complete.original_max_units == 4


def test_current_atr_updates_but_position_math_uses_entry_n():
    guide = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=306_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=1,
    )
    assert guide.current_atr == 4_000
    assert guide.n_at_entry == 12_000
    assert guide.next_add_price == 306_000
    assert guide.common_stop == 276_000


def test_short_perspective_uses_twenty_day_low_and_current_atr():
    history = bars()
    guide = build_position_guide(
        symbol="000660",
        bars=history,
        current=99_000,
        entry_price=100_000,
        n_at_entry=12_000,
        filled_units=0,
        prealert_pct=1.5,
    )
    short_entry = min(bar.low for bar in history[-20:])
    assert guide.short_entry20 == short_entry
    assert guide.short_stop == short_entry + 2 * guide.current_atr
    assert guide.short_exit10 == max(bar.high for bar in history[-10:])
    assert guide.short_add_levels == [short_entry - offset * guide.current_atr for offset in (0, .5, 1, 1.5, 2, 2.5)]
    assert guide.short_prealert_pct == 1.5
    assert guide.short_yesterday_broke is False


def test_common_stop_ratchets_after_add():
    g = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=310_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=2,
    )
    assert g.common_stop == 282_000  # 306,000 - 24,000


def test_common_stop_never_moves_down():
    g = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=310_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=1,
        previous_stop=290_000,
    )
    assert g.common_stop == 290_000


def test_stop_has_priority():
    g = build_position_guide(
        symbol="000660",
        bars=bars(),
        current=281_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=2,
    )
    assert g.action == "STOP_NOW"
    assert g.action_qty > 0


def test_exit_now_when_ten_day_low_is_above_stop():
    history = [Bar(high=310_000, low=290_000, close=300_000) for _ in range(140)]
    g = build_position_guide(
        symbol="000660",
        bars=history,
        current=289_000,
        entry_price=300_000,
        n_at_entry=12_000,
        filled_units=1,
    )
    assert g.common_stop == 276_000
    assert g.exit10 == 290_000
    assert g.action == "EXIT_NOW"
    assert g.sell_action == "EXIT_NOW"
    assert g.sell_pct == 100


def staged_exit_bars():
    history = [Bar(high=105, low=80, close=100) for _ in range(135)]
    history.extend(Bar(high=115, low=80, close=110) for _ in range(5))
    return history


def test_orthodox_turtle_ignores_moving_average_exit():
    guide = build_position_guide(
        symbol="000660",
        bars=staged_exit_bars(),
        current=108,
        entry_price=100,
        n_at_entry=10,
        filled_units=1,
        exit_strategy="turtle",
    )
    assert guide.ma5 == 110
    assert guide.ma10 == 105
    assert guide.sell_action == "SELL_WAIT"


def test_staged_exit_guides_half_at_ma5_and_remainder_at_ma10():
    ma5_guide = build_position_guide(
        symbol="000660",
        bars=staged_exit_bars(),
        current=108,
        entry_price=100,
        n_at_entry=10,
        filled_units=1,
        exit_strategy="ma_staged",
    )
    ma10_guide = build_position_guide(
        symbol="000660",
        bars=staged_exit_bars(),
        current=104,
        entry_price=100,
        n_at_entry=10,
        filled_units=1,
        exit_strategy="ma_staged",
    )
    assert ma5_guide.sell_action == "REDUCE_1"
    assert ma5_guide.sell_pct == 50
    assert ma10_guide.sell_action == "REDUCE_2"
    assert ma10_guide.sell_pct == 100


def test_protective_stop_has_priority_over_all_sell_rules():
    guide = build_position_guide(
        symbol="000660",
        bars=staged_exit_bars(),
        current=79,
        entry_price=100,
        n_at_entry=10,
        filled_units=1,
        exit_strategy="ma_staged",
    )
    assert guide.common_stop == 80
    assert guide.sell_action == "STOP_NOW"
    assert guide.sell_pct == 100


def test_wait_entry_and_entry_now():
    waiting = build_position_guide(
        symbol="000660", bars=bars(), current=299_000, entry_price=300_000,
        n_at_entry=12_000, filled_units=0,
    )
    entering = build_position_guide(
        symbol="000660", bars=bars(), current=300_000, entry_price=300_000,
        n_at_entry=12_000, filled_units=0,
    )
    assert waiting.action == "WAIT_ENTRY"
    assert entering.action == "ENTRY_NOW"
