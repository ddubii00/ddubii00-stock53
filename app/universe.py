from __future__ import annotations

import math
import threading
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ProgressCallback = Callable[[int, int, str], None]


@dataclass
class UniverseMember:
    symbol: str
    name: str
    market: str
    market_cap_100m: float
    operating_profit_100m: float | None = None
    fiscal_period: str | None = None
    source: str = "naver"
    asset_type: str = "STOCK"

    def to_dict(self) -> dict:
        return asdict(self)


class UniverseProvider(Protocol):
    name: str

    def list_members(
        self,
        market: str,
        min_market_cap_100m: float,
        progress: ProgressCallback | None = None,
        include_etf: bool = False,
    ) -> list[UniverseMember]: ...

    def get_operating_profit(self, member: UniverseMember) -> UniverseMember: ...


def _number(value: object) -> float:
    text = str(value or "").replace(",", "").strip()
    if text in {"", "-", "N/A"}:
        raise ValueError("number is unavailable")
    return float(text)


class NaverUniverseProvider:
    """Best-effort KOSPI/KOSDAQ universe and annual fundamentals metadata."""

    name = "naver"
    list_url = "https://m.stock.naver.com/api/stocks/marketValue/{market}"
    finance_url = "https://m.stock.naver.com/api/stock/{symbol}/finance/annual"

    def __init__(self, timeout: float = 8.0, page_size: int = 100):
        self.timeout = timeout
        self.page_size = max(1, min(page_size, 100))
        self._local = threading.local()
        self.last_listed_count = 0
        self.last_market_counts: dict[str, int] = {"KOSPI": 0, "KOSDAQ": 0}
        self.last_stock_count = 0
        self.last_etf_count = 0

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (compatible; TurtleSignalGuide/0.5)",
                    "Accept": "application/json",
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
                    "Referer": "https://m.stock.naver.com/",
                }
            )
            session.mount(
                "https://",
                HTTPAdapter(
                    max_retries=Retry(
                        total=2,
                        backoff_factor=0.3,
                        status_forcelist=(429, 500, 502, 503, 504),
                        allowed_methods=("GET",),
                    )
                ),
            )
            self._local.session = session
        return session

    @staticmethod
    def _markets(market: str) -> list[str]:
        selected = market.strip().upper()
        if selected == "ALL":
            return ["KOSPI", "KOSDAQ"]
        if selected not in {"KOSPI", "KOSDAQ"}:
            raise ValueError("market must be ALL, KOSPI, or KOSDAQ")
        return [selected]

    @staticmethod
    def _market_cap_100m(row: dict) -> float:
        raw = row.get("marketValueRaw")
        if raw not in {None, ""}:
            return _number(raw) / 100_000_000
        return _number(row.get("marketValue"))

    def list_members(
        self,
        market: str,
        min_market_cap_100m: float,
        progress: ProgressCallback | None = None,
        include_etf: bool = False,
    ) -> list[UniverseMember]:
        members: list[UniverseMember] = []
        markets = self._markets(market)
        seen_symbols: set[str] = set()
        market_symbols: dict[str, set[str]] = {"KOSPI": set(), "KOSDAQ": set()}
        stock_symbols: set[str] = set()
        etf_symbols: set[str] = set()
        pages_done = 0
        for market_name in markets:
            page = 1
            while True:
                response = self._session().get(
                    self.list_url.format(market=market_name),
                    params={"page": page, "pageSize": self.page_size},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                stocks = payload.get("stocks") or []
                if not stocks:
                    break
                for row in stocks:
                    end_type = str(row.get("stockEndType") or "stock").strip().lower()
                    if end_type not in ({"stock", "etf"} if include_etf else {"stock"}):
                        continue
                    symbol = str(row.get("itemCode") or "").strip().upper()
                    if len(symbol) != 6 or not symbol.isalnum():
                        continue
                    market_symbols[market_name].add(symbol)
                    (etf_symbols if end_type == "etf" else stock_symbols).add(symbol)
                    try:
                        market_cap = self._market_cap_100m(row)
                    except (TypeError, ValueError):
                        continue
                    if end_type == "stock" and market_cap < min_market_cap_100m:
                        continue
                    if symbol in seen_symbols:
                        continue
                    seen_symbols.add(symbol)
                    members.append(
                        UniverseMember(
                            symbol=symbol,
                            name=str(row.get("stockName") or symbol),
                            market=market_name,
                            market_cap_100m=market_cap,
                            asset_type="ETF" if end_type == "etf" else "STOCK",
                        )
                    )
                pages_done += 1
                if progress:
                    total_count = int(payload.get("totalCount") or 0)
                    page_total = math.ceil(total_count / self.page_size) if total_count else page
                    label = "주식·ETF" if include_etf else "주식"
                    progress(pages_done, page_total * len(markets), f"{market_name} {label} 목록 {page}페이지")
                if len(stocks) < self.page_size:
                    break
                page += 1
        self.last_market_counts = {
            market_name: len(market_symbols[market_name]) for market_name in ("KOSPI", "KOSDAQ")
        }
        self.last_listed_count = sum(self.last_market_counts.values())
        self.last_stock_count = len(stock_symbols)
        self.last_etf_count = len(etf_symbols)
        return members

    def get_operating_profit(self, member: UniverseMember) -> UniverseMember:
        response = self._session().get(
            self.finance_url.format(symbol=member.symbol), timeout=self.timeout
        )
        response.raise_for_status()
        payload = response.json()
        finance = payload.get("financeInfo") or {}
        actual_periods = {
            str(item.get("key")): str(item.get("title") or item.get("key"))
            for item in finance.get("trTitleList") or []
            if str(item.get("isConsensus", "N")).upper() == "N" and item.get("key")
        }
        latest_key = max(actual_periods, default=None)
        value: float | None = None
        if latest_key:
            for row in finance.get("rowList") or []:
                if str(row.get("title") or "").replace(" ", "") != "영업이익":
                    continue
                cell = (row.get("columns") or {}).get(latest_key) or {}
                try:
                    value = _number(cell.get("value"))
                except (TypeError, ValueError):
                    value = None
                break
        member.operating_profit_100m = value
        member.fiscal_period = actual_periods.get(latest_key) if latest_key else None
        return member


class DemoUniverseProvider:
    name = "demo"

    _members = [
        ("000660", "SK하이닉스", "KOSPI", 900_000, 260_000),
        ("005930", "삼성전자", "KOSPI", 3_000_000, 320_000),
        ("005380", "현대차", "KOSPI", 450_000, 140_000),
        ("012450", "한화에어로스페이스", "KOSPI", 350_000, 85_000),
        ("035420", "NAVER", "KOSPI", 300_000, 20_000),
        ("051910", "LG화학", "KOSPI", 250_000, 18_000),
        ("006400", "삼성SDI", "KOSPI", 220_000, 12_000),
        ("068270", "셀트리온", "KOSPI", 400_000, 70_000),
        ("196170", "알테오젠", "KOSDAQ", 180_000, 3_000),
        ("086520", "에코프로", "KOSDAQ", 120_000, 4_000),
        ("247540", "에코프로비엠", "KOSDAQ", 110_000, 6_000),
        ("277810", "레인보우로보틱스", "KOSDAQ", 80_000, 2_000),
        ("035900", "JYP Ent.", "KOSDAQ", 30_000, 1_500),
        ("041510", "에스엠", "KOSDAQ", 25_000, 1_000),
        ("145020", "휴젤", "KOSDAQ", 35_000, 1_200),
    ]
    _etfs = [
        ("069500", "KODEX 200", "KOSPI", 65_000),
        ("229200", "KODEX 코스닥150", "KOSPI", 25_000),
    ]

    def __init__(self):
        self.last_listed_count = 0
        self.last_market_counts: dict[str, int] = {"KOSPI": 0, "KOSDAQ": 0}
        self.last_stock_count = 0
        self.last_etf_count = 0

    def list_members(
        self,
        market: str,
        min_market_cap_100m: float,
        progress: ProgressCallback | None = None,
        include_etf: bool = False,
    ) -> list[UniverseMember]:
        selected = market.upper()
        selected_etfs = [row for row in self._etfs if selected in {"ALL", row[2]}] if include_etf else []
        self.last_market_counts = {
            market_name: (
                sum(1 for row in self._members if row[2] == market_name)
                + sum(1 for row in selected_etfs if row[2] == market_name)
            ) if selected in {"ALL", market_name} else 0
            for market_name in ("KOSPI", "KOSDAQ")
        }
        self.last_listed_count = sum(self.last_market_counts.values())
        self.last_stock_count = sum(
            1 for row in self._members if selected in {"ALL", row[2]}
        )
        self.last_etf_count = len(selected_etfs)
        rows = [
            UniverseMember(symbol, name, item_market, cap, profit, "DEMO", self.name)
            for symbol, name, item_market, cap, profit in self._members
            if (selected == "ALL" or selected == item_market) and cap >= min_market_cap_100m
        ]
        rows.extend(
            UniverseMember(
                symbol=symbol,
                name=name,
                market=item_market,
                market_cap_100m=cap,
                operating_profit_100m=None,
                fiscal_period=None,
                source=self.name,
                asset_type="ETF",
            )
            for symbol, name, item_market, cap in selected_etfs
        )
        if progress:
            progress(1, 1, "DEMO 전체시장 주식·ETF 목록" if include_etf else "DEMO 전체시장 주식 목록")
        return rows

    def get_operating_profit(self, member: UniverseMember) -> UniverseMember:
        return member


def build_universe_provider(data_provider: str) -> UniverseProvider:
    return DemoUniverseProvider() if data_provider.strip().lower() == "demo" else NaverUniverseProvider()
