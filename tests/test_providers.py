from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.providers import (
    FallbackMarketDataProvider,
    MarketSnapshot,
    NaverMarketDataProvider,
    Quote,
    _completed,
    get_market_snapshot,
    validate_snapshot_price_scale,
)
from app.strategy import Bar


class BrokenQuoteProvider:
    name = "broken"

    def get_daily_ohlcv(self, symbol, count=260):
        return [Bar(2, 1, 1.5)] * count

    def get_current_price(self, symbol):
        raise RuntimeError("quote failed")


class WorkingProvider:
    name = "working"

    def get_daily_ohlcv(self, symbol, count=260):
        return [Bar(20, 10, 15)] * count

    def get_current_price(self, symbol):
        return Quote(symbol=symbol, price=15, source=self.name)


def test_fallback_snapshot_never_mixes_provider_data():
    provider = FallbackMarketDataProvider([BrokenQuoteProvider(), WorkingProvider()])
    snapshot = get_market_snapshot(provider, "000660", 61)
    assert isinstance(snapshot, MarketSnapshot)
    assert snapshot.bars[0].high == 20
    assert snapshot.quote.source == "working"


def test_provider_removes_todays_partial_daily_bar():
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    bars = [Bar(2, 1, 1.5, date="20260828"), Bar(3, 2, 2.5, date=today)]
    completed = _completed(bars, 10)
    assert len(completed) == 1
    assert completed[0].date == "20260828"


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class QuoteSession:
    def __init__(self, row):
        self.row = row

    def get(self, *args, **kwargs):
        return JsonResponse({"result": {"areas": [{"datas": [self.row]}]}})


def test_naver_quote_prefers_open_nxt_session_price(monkeypatch):
    provider = NaverMarketDataProvider()
    session = QuoteSession(
        {
            "nv": 1_674_000,
            "hv": 1_680_000,
            "pcv": 1_600_000,
            "aq": 0,
            "nxtOverMarketPriceInfo": {
                "overPrice": "1,670,000",
                "highPrice": "1,700,000",
                "accumulatedTradingVolumeRaw": "84351",
                "tradeStopType": {"name": "TRADING"},
                "tradableStatus": "tradable",
            },
        }
    )
    monkeypatch.setattr(provider, "_session", lambda: session)
    quote = provider.get_current_price("000660")
    assert quote.price == 1_670_000
    assert quote.volume == 84_351
    assert quote.day_high == 1_700_000
    assert quote.change_pct == pytest.approx(4.375)


def test_naver_quote_uses_regular_price_when_nxt_is_closed(monkeypatch):
    provider = NaverMarketDataProvider()
    session = QuoteSession(
        {
            "nv": 1_674_000,
            "hv": 1_680_000,
            "pcv": 1_600_000,
            "aq": 123,
            "nxtOverMarketPriceInfo": {
                "overPrice": "1,670,000",
                "tradeStopType": {"name": "CLOSING"},
                "tradableStatus": "notTradable",
            },
        }
    )
    monkeypatch.setattr(provider, "_session", lambda: session)
    quote = provider.get_current_price("000660")
    assert quote.price == 1_674_000
    assert quote.volume == 123
    assert quote.day_high == 1_680_000
    assert quote.change_pct == pytest.approx(4.625)


def test_quote_history_scale_mismatch_is_rejected():
    snapshot = MarketSnapshot(
        bars=[Bar(high=820, low=780, close=783)] * 61,
        quote=Quote(symbol="011370", price=3_915, source="naver"),
    )
    try:
        validate_snapshot_price_scale(snapshot)
    except RuntimeError as exc:
        assert "possible split/consolidation" in str(exc)
    else:
        raise AssertionError("corporate-action price scale mismatch must be rejected")
