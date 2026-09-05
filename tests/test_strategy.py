from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.strategy import Bar, analyze, atr


def fresh_bars(n: int = 130):
    out = []
    for i in range(n):
        c = 100 + i * 0.2
        out.append(Bar(high=c + 1, low=c - 1, close=c, volume=1000, value=20_000_000_000))
    for i in range(-21, 0):
        out[i] = Bar(high=150, low=145, close=147, volume=1000, value=20_000_000_000)
    out[-1] = Bar(high=149, low=145, close=147, volume=1000, value=20_000_000_000)
    return out


def test_prealert():
    r = analyze(fresh_bars(), 149.0, 1300, 0, min_score=0)
    assert r.stage == "PREALERT"
    assert r.breakout20 == 150
    assert r.distance_pct == pytest.approx(2 / 3)


def test_prealert_includes_exactly_one_percent_below():
    r = analyze(fresh_bars(), 148.5, 1300, min_score=0)
    assert r.distance_pct == pytest.approx(1.0)
    assert r.stage == "PREALERT"


def test_breakout():
    r = analyze(fresh_bars(), 150.5, 1300, 0, min_score=0)
    assert r.stage == "BREAKOUT"


def test_short_breakout_and_prealert_use_previous_twenty_lows():
    breaking = analyze(fresh_bars(), 144.0, 1300, min_score=0, prealert_pct=1.0)
    approaching = analyze(fresh_bars(), 145.725, 1300, min_score=0, prealert_pct=1.0)
    assert breaking.short_entry20 == 145
    assert breaking.short_stage == "SHORT_NOW"
    assert approaching.short_distance_pct == pytest.approx(0.5)
    assert approaching.short_stage == "SHORT_PREALERT"


def test_yesterday_short_breakout_is_not_a_fresh_signal():
    b = fresh_bars()
    b[-1] = Bar(high=149, low=144, close=145, volume=1000, value=20_000_000_000)
    result = analyze(b, 143.5, 1300, min_score=0)
    assert result.yesterday_short_broke is True
    assert result.short_stage == "SHORT_FILTERED"


def test_long_and_short_levels_include_two_expansion_units():
    result = analyze(fresh_bars(), 149.0, 1300, min_score=0)
    assert result.add5 == result.breakout20 + 2 * result.atr20
    assert result.add6 == result.breakout20 + 2.5 * result.atr20
    assert result.short_add5 == result.short_entry20 - 2 * result.atr20
    assert result.short_add6 == result.short_entry20 - 2.5 * result.atr20
    assert result.short_initial_stop == result.short_entry20 + 2 * result.atr20
    assert result.short_exit10 == max(bar.high for bar in fresh_bars()[-10:])


def test_intraday_breakout_reverts_to_prealert_when_current_price_retraces():
    r = analyze(
        fresh_bars(),
        current=149.0,
        current_volume=1300,
        min_score=0,
        today_high=150.5,
    )
    assert r.breakout20 == 150
    assert r.intraday_broke is True
    assert r.stage == "PREALERT"
    assert r.distance_pct == pytest.approx(2 / 3)


def test_intraday_high_near_target_does_not_make_distant_current_price_prealert():
    r = analyze(
        fresh_bars(),
        current=140.0,
        current_volume=1300,
        min_score=0,
        prealert_pct=1.5,
        today_high=149.5,
    )
    assert r.breakout20 == 150
    assert r.distance_pct == pytest.approx(100 * (150 - 140) / 150)
    assert r.stage == "WATCH"


def test_prealert_uses_configured_current_price_range():
    inside = analyze(fresh_bars(), current=147.75, current_volume=1300, min_score=0, prealert_pct=1.5)
    outside = analyze(fresh_bars(), current=147.74, current_volume=1300, min_score=0, prealert_pct=1.5)
    assert inside.distance_pct == pytest.approx(1.5)
    assert inside.stage == "PREALERT"
    assert outside.distance_pct > 1.5
    assert outside.stage == "WATCH"


def test_breakout_window_is_exactly_previous_twenty_completed_sessions():
    b = fresh_bars()
    b[-20] = Bar(high=160, low=145, close=147, volume=1000, value=20_000_000_000)
    b[-21] = Bar(high=999, low=145, close=147, volume=1000, value=20_000_000_000)
    r = analyze(b, current=159.0, current_volume=1300, min_score=0, prealert_pct=1.0)
    assert r.breakout20 == 160
    assert r.stage == "PREALERT"


def test_quality_does_not_block_turtle_breakout():
    b = fresh_bars()
    b = [Bar(x.high, x.low, x.close, x.volume, 0) for x in b]
    r = analyze(b, 150.5, 100, 0, min_score=100)
    assert r.hard_pass is False
    assert r.stage == "BREAKOUT"


def test_yesterday_breakout_is_not_fresh_signal():
    b = fresh_bars()
    b[-1] = Bar(high=151, low=145, close=150, volume=1000, value=20_000_000_000)
    r = analyze(b, 151.5, 1300, 0, min_score=0)
    assert r.yesterday_broke is True
    assert r.stage == "FILTERED"


def test_today_is_not_in_breakout_window():
    b = fresh_bars()
    r = analyze(b, 200.0, 1300, 0, min_score=0)
    assert r.breakout20 == 150
    assert r.stage == "BREAKOUT"


def test_dated_today_bar_is_rejected():
    b = fresh_bars()
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    b[-1] = Bar(high=149, low=145, close=147, volume=1000, value=20_000_000_000, date=today)
    with pytest.raises(ValueError, match="completed sessions"):
        analyze(b, 149, 1300, min_score=0)


def test_atr20_uses_true_range_from_completed_bars():
    b = [Bar(high=110, low=90, close=100)]
    b.extend(Bar(high=112, low=92, close=102) for _ in range(20))
    assert atr(b, 20) == 20


def test_exit10_is_previous_ten_lows():
    b = fresh_bars()
    for index in range(-10, 0):
        b[index] = Bar(high=150, low=140 + abs(index), close=147, volume=1000, value=20_000_000_000)
    r = analyze(b, 149, 1300, min_score=0)
    assert r.exit10 == 141


def test_avg_value10_uses_only_last_ten_completed_bars():
    b = fresh_bars()
    for index in range(-20, -10):
        b[index] = Bar(high=150, low=145, close=147, volume=1000, value=1)
    for index in range(-10, 0):
        b[index] = Bar(high=150, low=145, close=147, volume=1000, value=60_000_000_000)
    r = analyze(b, 149, 1300, min_score=0)
    assert r.avg_value10 == 60_000_000_000
