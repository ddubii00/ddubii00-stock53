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

    def to_dict(self) -> dict:
        return asdict(self)


class UniverseProvider(Protocol):
    name: str

    def list_members(
        self,
        market: str,
        min_market_cap_100m: float,
        progress: ProgressCallback | None = None,
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
    ) -> list[UniverseMember]:
        members: list[UniverseMember] = []
        markets = self._markets(market)
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
                below_threshold = False
                for row in stocks:
                    try:
                        market_cap = self._market_cap_100m(row)
                    except (TypeError, ValueError):
                        continue
                    if market_cap < min_market_cap_100m:
                        below_threshold = True
                        continue
                    symbol = str(row.get("itemCode") or "").strip()
                    if len(symbol) != 6 or not symbol.isdigit():
                        continue
                    members.append(
                        UniverseMember(
                            symbol=symbol,
                            name=str(row.get("stockName") or symbol),
                            market=market_name,
                            market_cap_100m=market_cap,
                        )
                    )
                pages_done += 1
                if progress:
                    total_count = int(payload.get("totalCount") or 0)
                    page_total = math.ceil(total_count / self.page_size) if total_count else page
                    progress(pages_done, page_total * len(markets), f"{market_name} 시가총액 목록 {page}페이지")
                if below_threshold or len(stocks) < self.page_size:
                    break
                page += 1
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

    def list_members(
        self,
        market: str,
        min_market_cap_100m: float,
        progress: ProgressCallback | None = None,
    ) -> list[UniverseMember]:
        selected = market.upper()
        rows = [
            UniverseMember(symbol, name, item_market, cap, profit, "DEMO", self.name)
            for symbol, name, item_market, cap, profit in self._members
            if (selected == "ALL" or selected == item_market) and cap >= min_market_cap_100m
        ]
        if progress:
            progress(1, 1, "DEMO 전체시장 목록")
        return rows

    def get_operating_profit(self, member: UniverseMember) -> UniverseMember:
        return member


def build_universe_provider(data_provider: str) -> UniverseProvider:
    return DemoUniverseProvider() if data_provider.strip().lower() == "demo" else NaverUniverseProvider()
