from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.providers import (
    FallbackMarketDataProvider,
    KisMarketDataProvider,
    MarketSnapshot,
    NaverMarketDataProvider,
    Quote,
    _completed,
    get_market_snapshot,
    validate_snapshot_price_scale,
)
from app.strategy import Bar, analyze


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


class HolidaySessionProvider:
    name = "holiday-test"

    def get_daily_ohlcv(self, symbol, count=260):
        bars = []
        for index in range(61):
            session_date = (date(2026, 6, 1) + timedelta(days=index)).strftime("%Y%m%d")
            bars.append(
                Bar(
                    high=100,
                    low=90,
                    close=95,
                    volume=1_000,
                    value=95_000,
                    date=session_date,
                )
            )
        bars.append(
            Bar(
                high=120,
                low=94,
                close=101,
                volume=2_000,
                value=202_000,
                date="20260904",
            )
        )
        return bars[-count:]

    def get_current_price(self, symbol):
        return Quote(
            symbol=symbol,
            price=101,
            volume=2_000,
            source=self.name,
            day_high=120,
            date="20260904",
        )


def test_holiday_snapshot_uses_latest_open_session_without_lookahead(monkeypatch):
    monkeypatch.setattr("app.providers._today_kst", lambda: "20260906")
    snapshot = get_market_snapshot(HolidaySessionProvider(), "005930", 61)

    assert snapshot.as_of_date == "20260904"
    assert snapshot.latest_completed_session is True
    assert len(snapshot.bars) == 61
    assert all(bar.date != "20260904" for bar in snapshot.bars)
    assert max(bar.high for bar in snapshot.bars[-20:]) == 100

    result = analyze(
        snapshot.bars,
        current=snapshot.quote.price,
        current_volume=snapshot.quote.volume,
        today_high=snapshot.quote.day_high,
        min_avg_value20=0,
        min_score=0,
    )
    assert result.stage == "BREAKOUT"
    assert result.breakout20 == 100


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


class InvestorSession:
    def get(self, *args, **kwargs):
        return JsonResponse(
            [
                {
                    "bizdate": "20260902",
                    "foreignerPureBuyQuant": "-1,000",
                    "organPureBuyQuant": "+2,000",
                    "closePrice": "50,000",
                }
            ]
        )


class KisRetrySession:
    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return JsonResponse({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "temporary limit"})
        return JsonResponse(
            {
                "rt_cd": "0",
                "output": {
                    "stck_prpr": "337000",
                    "acml_vol": "222733",
                    "stck_hgpr": "345000",
                    "prdy_ctrt": "-1.46",
                },
            }
        )


def test_kis_retries_api_error_before_fallback(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "test-key")
    monkeypatch.setenv("KIS_APP_SECRET", "test-secret")
    monkeypatch.setenv("KIS_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("KIS_RETRY_BACKOFF_SECONDS", "0")
    provider = KisMarketDataProvider()
    session = KisRetrySession()
    provider.session = session
    provider._token = "cached-test-token"
    provider._token_expiry = 9_999_999_999

    quote = provider.get_current_price("005490")

    assert session.calls == 2
    assert quote.source == "kis"
    assert quote.price == 337_000


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


def test_naver_investor_flow_exposes_latest_completed_date_and_estimated_amount(monkeypatch):
    provider = NaverMarketDataProvider()
    monkeypatch.setattr(provider, "_session", lambda: InvestorSession())

    flow = provider.get_investor_flow("005930")

    assert flow.date == "20260902"
    assert flow.foreign_net_amount == -50_000_000
    assert flow.institution_net_amount == 100_000_000
    assert flow.estimated_amount is True


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
