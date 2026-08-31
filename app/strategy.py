from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Sequence
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Bar:
    """A completed daily bar.

    Providers must exclude the current trading day before handing bars to the
    strategy. ``date`` is optional so deterministic tests can stay lightweight.
    """

    high: float
    low: float
    close: float
    volume: float = 0.0
    value: float = 0.0
    date: str = ""


@dataclass
class TurtleResult:
    breakout20: float
    distance_pct: float
    atr20: float
    atr_pct: float
    ma20: float
    ma60: float
    ma120: float | None
    avg_value10: float
    avg_value20: float
    volume_ratio: float
    rs20: float
    rs60: float
    high52: float
    high52_distance_pct: float
    score: int
    hard_pass: bool
    yesterday_broke: bool
    stage: str
    target_buy: float
    add2: float
    add3: float
    add4: float
    initial_stop: float
    exit10: float
    reasons: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def _avg(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("cannot average an empty sequence")
    return sum(values) / len(values)


def atr(bars: Sequence[Bar], period: int = 20) -> float:
    """Simple average True Range over completed bars only."""

    if period <= 0:
        raise ValueError("period must be positive")
    if len(bars) < period + 1:
        raise ValueError("ATR needs at least period+1 completed bars")
    trs: list[float] = []
    for i in range(len(bars) - period, len(bars)):
        bar = bars[i]
        prev = bars[i - 1]
        trs.append(max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close)))
    return _avg(trs)


def _return(closes: Sequence[float], period: int) -> float:
    if len(closes) <= period or closes[-period - 1] <= 0:
        return 0.0
    return closes[-1] / closes[-period - 1] - 1.0


def analyze(
    bars: Sequence[Bar],
    current: float,
    current_volume: float = 0.0,
    market_return60: float = 0.0,
    market_return20: float = 0.0,
    min_avg_value20: float = 10_000_000_000,
    prealert_pct: float = 1.0,
    min_score: int = 55,
) -> TurtleResult:
    """Analyze a fresh Turtle System-1 entry without look-ahead bias.

    ``bars`` contains completed daily bars only, oldest to newest. Quality
    metrics rank candidates but never modify the breakout calculation.
    """

    if len(bars) < 61:
        raise ValueError("Need at least 61 completed daily bars")
    if current <= 0:
        raise ValueError("current must be positive")
    if prealert_pct < 0:
        raise ValueError("prealert_pct cannot be negative")
    today_kst = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    if any(bar.date == today_kst for bar in bars if bar.date):
        raise ValueError("bars must contain completed sessions only; today's partial bar was found")

    breakout20 = max(b.high for b in bars[-20:])
    yesterday_breakout_level = max(b.high for b in bars[-21:-1])
    yesterday_broke = bars[-1].high > yesterday_breakout_level

    n = atr(bars, 20)
    distance_pct = (breakout20 - current) / breakout20 * 100.0
    atr_pct = n / current * 100.0
    closes = [b.close for b in bars]
    ma20 = _avg(closes[-20:])
    ma60 = _avg(closes[-60:])
    ma120 = _avg(closes[-120:]) if len(closes) >= 120 else None
    avg_value10 = _avg([b.value for b in bars[-10:]])
    avg_value20 = _avg([b.value for b in bars[-20:]])
    avg_vol20 = _avg([b.volume for b in bars[-20:]])
    volume_ratio = current_volume / avg_vol20 if avg_vol20 > 0 else 0.0
    rs20 = _return(closes, 20) - market_return20
    rs60 = _return(closes, 60) - market_return60
    high52 = max(b.high for b in bars[-min(252, len(bars)):])
    high52_distance_pct = (high52 - current) / high52 * 100.0 if high52 else 0.0

    score = 0
    reasons: list[str] = []
    if current > ma20 > ma60:
        score += 20
        reasons.append("현재가 > MA20 > MA60")
    if ma120 is not None and ma60 > ma120:
        score += 10
        reasons.append("MA60 > MA120")
    if rs20 > 0:
        score += 10
        reasons.append("RS20 양호")
    if rs60 > 0:
        score += 10
        reasons.append("RS60 양호")
    if avg_value20 >= min_avg_value20:
        score += 15
        reasons.append("20일 평균거래대금 통과")
    if volume_ratio >= 1.2:
        score += 10
        reasons.append("거래량 강도 1.2배 이상")
    elif volume_ratio >= 0.8:
        score += 5
        reasons.append("거래량 강도 보통")
    if 1.5 <= atr_pct <= 8.0:
        score += 10
        reasons.append("ATR% 범위 양호")
    if high52_distance_pct <= 15.0:
        score += 15
        reasons.append("52주 고가 15% 이내")

    # Backwards-compatible quality metadata; never used to gate the signal.
    hard_pass = avg_value20 >= min_avg_value20 and score >= min_score

    if yesterday_broke:
        stage = "FILTERED"
        reasons.append("어제 이미 20일 고가 돌파: 신규 Unit #1 제외")
    elif current >= breakout20:
        stage = "BREAKOUT"
    elif 0.0 < distance_pct <= prealert_pct + 1e-9:
        stage = "PREALERT"
    else:
        stage = "WATCH"

    return TurtleResult(
        breakout20=breakout20,
        distance_pct=distance_pct,
        atr20=n,
        atr_pct=atr_pct,
        ma20=ma20,
        ma60=ma60,
        ma120=ma120,
        avg_value10=avg_value10,
        avg_value20=avg_value20,
        volume_ratio=volume_ratio,
        rs20=rs20,
        rs60=rs60,
        high52=high52,
        high52_distance_pct=high52_distance_pct,
        score=score,
        hard_pass=hard_pass,
        yesterday_broke=yesterday_broke,
        stage=stage,
        target_buy=breakout20,
        add2=breakout20 + 0.5 * n,
        add3=breakout20 + 1.0 * n,
        add4=breakout20 + 1.5 * n,
        initial_stop=breakout20 - 2.0 * n,
        exit10=min(b.low for b in bars[-10:]),
        reasons=reasons,
    )
