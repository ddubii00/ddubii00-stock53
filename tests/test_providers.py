from datetime import datetime
from zoneinfo import ZoneInfo

from app.providers import FallbackMarketDataProvider, MarketSnapshot, Quote, _completed, get_market_snapshot
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
