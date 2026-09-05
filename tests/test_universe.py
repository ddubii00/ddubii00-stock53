from __future__ import annotations

from app.full_scan import FullScanConfig, scan_full_market
from app.providers import DemoMarketDataProvider, Quote
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


class RoutedUniverseSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, **kwargs):
        market = url.rsplit("/", 1)[-1]
        page = int((params or {}).get("page", 1))
        self.calls.append((market, page))
        rows = {
            ("KOSPI", 1): [
                {"itemCode": "005930", "stockName": "삼성전자", "stockEndType": "stock", "marketValueRaw": "60000000000"},
                {"itemCode": "069500", "stockName": "KODEX 200", "stockEndType": "etf", "marketValueRaw": "10000000000"},
            ],
            ("KOSPI", 2): [
                {"itemCode": "0126Z0", "stockName": "영문혼합주", "stockEndType": "stock", "marketValueRaw": "55000000000"},
                {"itemCode": "005930", "stockName": "삼성전자 중복", "stockEndType": "stock", "marketValueRaw": "60000000000"},
            ],
            ("KOSDAQ", 1): [
                {"itemCode": "247540", "stockName": "에코프로비엠", "stockEndType": "stock", "marketValueRaw": "49900000000"},
                {"itemCode": "0009K0", "stockName": "신규코드", "stockEndType": "stock", "marketValueRaw": "80000000000"},
            ],
            ("KOSDAQ", 2): [],
        }.get((market, page), [])
        return FakeResponse({"stocks": rows, "totalCount": 4})


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


def test_naver_universe_reads_every_market_page_and_filters_only_listed_stocks(monkeypatch):
    provider = NaverUniverseProvider(page_size=2)
    session = RoutedUniverseSession()
    monkeypatch.setattr(provider, "_session", lambda: session)

    members = provider.list_members("ALL", 500)

    assert session.calls == [("KOSPI", 1), ("KOSPI", 2), ("KOSPI", 3), ("KOSDAQ", 1), ("KOSDAQ", 2)]
    assert [member.symbol for member in members] == ["005930", "0126Z0", "0009K0"]
    assert all(member.symbol != "069500" for member in members)
    assert provider.last_listed_count == 4
    assert provider.last_market_counts == {"KOSPI": 2, "KOSDAQ": 2}


def test_naver_universe_adds_etf_without_market_cap_filter_when_enabled(monkeypatch):
    provider = NaverUniverseProvider(page_size=2)
    session = RoutedUniverseSession()
    monkeypatch.setattr(provider, "_session", lambda: session)

    members = provider.list_members("ALL", 500, include_etf=True)

    assert [member.symbol for member in members] == ["005930", "069500", "0126Z0", "0009K0"]
    etf = next(member for member in members if member.symbol == "069500")
    assert etf.asset_type == "ETF"
    assert etf.market_cap_100m == 100
    assert provider.last_stock_count == 4
    assert provider.last_etf_count == 1
    assert provider.last_listed_count == 5


class FilterUniverse:
    name = "fake"

    def list_members(self, market, min_market_cap_100m, progress=None, include_etf=False):
        members = [
            UniverseMember("000660", "A", "KOSPI", 1_000, 100, "2025"),
            UniverseMember("005930", "B", "KOSPI", 900, 49, "2025"),
            UniverseMember("005380", "C", "KOSPI", 800, 50, "2025"),
        ]
        if include_etf:
            members.append(
                UniverseMember("229200", "KODEX 코스닥150", "KOSPI", 100, asset_type="ETF")
            )
        return members

    def get_operating_profit(self, member):
        raise AssertionError("prefilled fundamentals should not be fetched")


class CountingDemo(DemoMarketDataProvider):
    def __init__(self):
        self.symbols = []

    def get_daily_ohlcv(self, symbol, count=260):
        self.symbols.append(symbol)
        return super().get_daily_ohlcv(symbol, count)


class NoInvestorFlowDemo(CountingDemo):
    def get_investor_flow(self, symbol):
        raise RuntimeError("investor data is not finalized")


class IntradayNearButCurrentDistantDemo(CountingDemo):
    def get_current_price(self, symbol):
        bars = self.get_daily_ohlcv(symbol, 260)
        target = max(bar.high for bar in bars[-20:])
        return Quote(
            symbol=symbol,
            price=target * (1 - 0.0647),
            volume=1_100_000,
            source=self.name,
            day_high=target * 1.005,
        )


class ShortSignalDemo(CountingDemo):
    def __init__(self, ratio):
        super().__init__()
        self.ratio = ratio

    def get_current_price(self, symbol):
        bars = self.get_daily_ohlcv(symbol, 260)
        target = min(bar.low for bar in bars[-19:])
        price = target * self.ratio
        return Quote(
            symbol=symbol,
            price=price,
            volume=1_100_000,
            source=self.name,
            day_high=price,
            change_pct=(price / bars[-1].close - 1) * 100,
        )

    def get_daily_ohlcv(self, symbol, count=260):
        bars = super().get_daily_ohlcv(symbol, count)
        floor = min(bar.low for bar in bars[-20:])
        for index in range(len(bars) - 20, len(bars)):
            bar = bars[index]
            bars[index] = type(bar)(
                high=max(bar.high, floor + 2_000),
                low=floor,
                close=max(bar.close, floor + 1_000),
                volume=bar.volume,
                value=bar.value,
                date=bar.date,
            )
        return bars


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


def test_full_scan_visible_stage_uses_current_price_not_intraday_high(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "strict-prealert.db"))
    items, _ = scan_full_market(
        FullScanConfig(
            provider="demo",
            market="KOSPI",
            min_market_cap_100m=500,
            min_operating_profit_100m=50,
            signal_mode="actionable",
            prealert_pct=1.5,
            avg_value10_filter_enabled=False,
        ),
        market_provider=IntradayNearButCurrentDistantDemo(),
        universe_provider=FilterUniverse(),
    )
    assert items == []


def test_full_scan_returns_short_prealert_and_breakout_candidates(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "short-signals.db"))
    common = dict(
        provider="demo",
        market="KOSPI",
        min_market_cap_100m=500,
        min_operating_profit_100m=50,
        avg_value10_filter_enabled=False,
    )
    prealerts, _ = scan_full_market(
        FullScanConfig(**common, signal_mode="prealert", prealert_pct=1),
        market_provider=ShortSignalDemo(1.005),
        universe_provider=FilterUniverse(),
    )
    breakouts, _ = scan_full_market(
        FullScanConfig(**common, signal_mode="breakout", prealert_pct=1),
        market_provider=ShortSignalDemo(0.999),
        universe_provider=FilterUniverse(),
    )
    assert {item["symbol"] for item in prealerts} == {"000660", "005380"}
    assert all(item["short_stage"] == "SHORT_PREALERT" for item in prealerts)
    assert {item["symbol"] for item in breakouts} == {"000660", "005380"}
    assert all(item["short_stage"] == "SHORT_BREAKOUT" for item in breakouts)


def test_full_scan_breakout_and_optional_filters(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "filter-scan.db"))
    provider = CountingDemo()
    base = dict(
        provider="demo",
        market="KOSPI",
        min_market_cap_100m=500,
        min_operating_profit_100m=50,
        signal_mode="breakout",
    )

    items, _ = scan_full_market(
        FullScanConfig(**base),
        market_provider=provider,
        universe_provider=FilterUniverse(),
    )
    assert [item["symbol"] for item in items] == ["000660"]
    assert items[0]["stage"] == "BREAKOUT"
    assert items[0]["avg_value10"] >= 500 * 100_000_000
    assert items[0]["investor_date"]
    assert items[0]["foreign_net_buy_100m"] is not None
    assert items[0]["institution_net_buy_100m"] is not None
    assert items[0]["investor_availability"] == "post_close"

    no_large_move, _ = scan_full_market(
        FullScanConfig(**base, today_change_filter_enabled=True, min_today_change_pct=5),
        market_provider=provider,
        universe_provider=FilterUniverse(),
    )
    assert no_large_move == []

    no_large_flow, _ = scan_full_market(
        FullScanConfig(
            **base,
            investor_filter_enabled=True,
            investor_mode="either",
            min_investor_net_buy_100m=100,
        ),
        market_provider=provider,
        universe_provider=FilterUniverse(),
    )
    assert no_large_flow == []

    value_filter_disabled, _ = scan_full_market(
        FullScanConfig(
            **base,
            avg_value10_filter_enabled=False,
            min_avg_value10_100m=1_000_000,
        ),
        market_provider=provider,
        universe_provider=FilterUniverse(),
    )
    assert [item["symbol"] for item in value_filter_disabled] == ["000660"]


def test_full_scan_keeps_candidate_when_optional_investor_display_is_unavailable(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "investor-unavailable.db"))
    items, _ = scan_full_market(
        FullScanConfig(
            provider="demo",
            market="KOSPI",
            min_market_cap_100m=500,
            min_operating_profit_100m=50,
            signal_mode="breakout",
            investor_filter_enabled=False,
        ),
        market_provider=NoInvestorFlowDemo(),
        universe_provider=FilterUniverse(),
    )

    assert [item["symbol"] for item in items] == ["000660"]
    assert items[0]["foreign_net_buy_100m"] is None
    assert "not finalized" in items[0]["investor_error"]


def test_full_scan_etf_bypasses_fundamentals_market_cap_and_investor_filters(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "etf-scan.db"))
    provider = CountingDemo()
    items, summary = scan_full_market(
        FullScanConfig(
            provider="demo",
            market="KOSPI",
            min_market_cap_100m=500,
            min_operating_profit_100m=1_000_000,
            include_etf=True,
            signal_mode="actionable",
            avg_value10_filter_enabled=False,
            investor_filter_enabled=True,
            min_investor_net_buy_100m=1_000_000,
        ),
        market_provider=provider,
        universe_provider=FilterUniverse(),
    )

    assert "229200" in provider.symbols
    assert summary["stock_fundamentals_passed"] == 0
    assert summary["etf_scanned"] == 1
    assert summary["etf_count"] == 1
    assert [item["symbol"] for item in items] == ["229200"]
    assert items[0]["asset_type"] == "ETF"
    assert items[0]["operating_profit_100m"] is None
