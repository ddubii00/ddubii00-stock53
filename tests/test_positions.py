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
    assert g.add_levels == [300_000, 306_000, 312_000, 318_000]
    assert g.unit_quantities == [33, 32, 32, 31]
    assert g.total_qty == 97
    assert g.next_unit_qty == 31
    assert g.next_unit_amount == 9_858_000


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
