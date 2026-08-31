from __future__ import annotations

from app.full_scan import FullScanConfig, scan_full_market
from app.providers import DemoMarketDataProvider
from app.universe import NaverUniverseProvider, UniverseMember


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *args, **kwargs):
        return FakeResponse(self.responses.pop(0))


def test_naver_universe_applies_market_cap_and_uses_latest_actual_profit(monkeypatch):
    provider = NaverUniverseProvider(page_size=100)
    session = FakeSession(
        [
            {
                "stocks": [
                    {
                        "itemCode": "005930",
                        "stockName": "삼성전자",
                        "marketValueRaw": "60000000000",
                    },
                    {
                        "itemCode": "000001",
                        "stockName": "소형주",
                        "marketValueRaw": "49900000000",
                    },
                ],
                "totalCount": 2,
            },
            {
                "financeInfo": {
                    "trTitleList": [
                        {"key": "202412", "title": "2024.12.", "isConsensus": "N"},
                        {"key": "202512", "title": "2025.12.", "isConsensus": "N"},
                        {"key": "202612", "title": "2026.12.", "isConsensus": "Y"},
                    ],
                    "rowList": [
                        {
                            "title": "영업이익",
                            "columns": {
                                "202412": {"value": "40"},
                                "202512": {"value": "55"},
                                "202612": {"value": "9,999"},
                            },
                        }
                    ],
                }
            },
        ]
    )
    monkeypatch.setattr(provider, "_session", lambda: session)

    members = provider.list_members("KOSPI", 500)
    assert [member.symbol for member in members] == ["005930"]
    enriched = provider.get_operating_profit(members[0])
    assert enriched.market_cap_100m == 600
    assert enriched.operating_profit_100m == 55
    assert enriched.fiscal_period == "2025.12."


class FilterUniverse:
    name = "fake"

    def list_members(self, market, min_market_cap_100m, progress=None):
        return [
            UniverseMember("000660", "A", "KOSPI", 1_000, 100, "2025"),
            UniverseMember("005930", "B", "KOSPI", 900, 49, "2025"),
            UniverseMember("005380", "C", "KOSPI", 800, 50, "2025"),
        ]

    def get_operating_profit(self, member):
        raise AssertionError("prefilled fundamentals should not be fetched")


class CountingDemo(DemoMarketDataProvider):
    def __init__(self):
        self.symbols = []

    def get_daily_ohlcv(self, symbol, count=260):
        self.symbols.append(symbol)
        return super().get_daily_ohlcv(symbol, count)


def test_full_scan_filters_fundamentals_before_fetching_price_history(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "scan.db"))
    provider = CountingDemo()
    items, summary = scan_full_market(
        FullScanConfig(
            provider="demo",
            market="KOSPI",
            min_market_cap_100m=500,
            min_operating_profit_100m=50,
            signal_mode="actionable",
        ),
        market_provider=provider,
        universe_provider=FilterUniverse(),
    )
    assert sorted(set(provider.symbols)) == ["000660", "005380"]
    assert "005930" not in provider.symbols
    assert summary["universe_count"] == 3
    assert summary["fundamentals_passed"] == 2
    assert all(item["operating_profit_100m"] >= 50 for item in items)
