from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import requests

from app.strategy import Bar


SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    volume: float = 0.0
    source: str = ""


@dataclass(frozen=True)
class MarketSnapshot:
    bars: list[Bar]
    quote: Quote


class MarketDataProvider(Protocol):
    name: str

    def get_daily_ohlcv(self, symbol: str, count: int = 260) -> list[Bar]: ...
    def get_current_price(self, symbol: str) -> Quote: ...


def _today_kst() -> str:
    return datetime.now(SEOUL).strftime("%Y%m%d")


def _today_kst_date() -> date:
    return datetime.now(SEOUL).date()


def _completed(bars: list[Bar], count: int) -> list[Bar]:
    """Remove the current Korean trading date to enforce D-1 strategy windows."""

    today = _today_kst()
    return [bar for bar in bars if not bar.date or bar.date != today][-count:]


def _number(value: object) -> float:
    return float(str(value or "0").replace(",", ""))


def get_market_snapshot(provider: MarketDataProvider, symbol: str, count: int = 260) -> MarketSnapshot:
    """Fetch bars and quote from one provider, including across fallback chains."""

    method = getattr(provider, "get_snapshot", None)
    if callable(method):
        return method(symbol, count)
    bars = provider.get_daily_ohlcv(symbol, count)
    quote = provider.get_current_price(symbol)
    return MarketSnapshot(bars=bars, quote=quote)


class DemoMarketDataProvider:
    name = "demo"

    @staticmethod
    def _dates(total: int) -> list[str]:
        cursor = _today_kst_date() - timedelta(days=1)
        values: list[str] = []
        while len(values) < total:
            if cursor.weekday() < 5:
                values.append(cursor.strftime("%Y%m%d"))
            cursor -= timedelta(days=1)
        return list(reversed(values))

    def get_daily_ohlcv(self, symbol: str, count: int = 260) -> list[Bar]:
        total = max(count, 260)
        dates = self._dates(total)
        bars: list[Bar] = []
        base = 100_000 + (int(symbol[-2:]) if symbol[-2:].isdigit() else 1) * 1_000
        for i in range(total):
            close = base + i * 120
            bars.append(
                Bar(
                    high=close + 1_500,
                    low=close - 1_400,
                    close=close,
                    volume=700_000 + i * 500,
                    value=(700_000 + i * 500) * close,
                    date=dates[i],
                )
            )
        level = bars[-22].high + 8_000
        for idx in range(len(bars) - 21, len(bars)):
            close = level - 3_500 + (idx - (len(bars) - 21)) * 80
            bars[idx] = Bar(
                high=level - 1_000,
                low=close - 1_200,
                close=close,
                volume=900_000,
                value=900_000 * close,
                date=dates[idx],
            )
        bars[-1] = Bar(
            high=level - 1_200,
            low=level - 5_000,
            close=level - 3_000,
            volume=930_000,
            value=930_000 * (level - 3_000),
            date=dates[-1],
        )
        return bars[-count:]

    def get_current_price(self, symbol: str) -> Quote:
        bars = self.get_daily_ohlcv(symbol, 260)
        breakout = max(b.high for b in bars[-20:])
        bucket = sum(int(ch) for ch in symbol if ch.isdigit()) % 3
        price = breakout * (1.002 if bucket == 0 else 0.994 if bucket == 1 else 0.97)
        return Quote(symbol=symbol, price=price, volume=1_100_000, source=self.name)


class NaverMarketDataProvider:
    """Best-effort public data for Vercel UI/exploration, never Oracle truth."""

    name = "naver"
    chart_url = "https://fchart.stock.naver.com/sise.nhn"
    quote_url = "https://polling.finance.naver.com/api/realtime"

    def __init__(self, timeout: float = 7.0):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; TurtleSignalGuide/0.4)",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
                "Referer": "https://finance.naver.com/",
            }
        )

    def get_daily_ohlcv(self, symbol: str, count: int = 260) -> list[Bar]:
        response = self.session.get(
            self.chart_url,
            params={"symbol": symbol, "timeframe": "day", "count": max(count + 1, 140), "requestType": "0"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        bars: list[Bar] = []
        for item in root.findall(".//item"):
            parts = item.attrib.get("data", "").split("|")
            if len(parts) < 6:
                continue
            session_date, _open, high, low, close, volume = parts[:6]
            try:
                h, low_value, c, v = map(_number, (high, low, close, volume))
            except ValueError:
                continue
            if min(h, low_value, c) <= 0:
                continue
            bars.append(
                Bar(high=h, low=low_value, close=c, volume=v, value=v * c, date=session_date)
            )
        bars = _completed(bars, count)
        if len(bars) < 61:
            raise RuntimeError(f"Naver returned only {len(bars)} completed daily bars for {symbol}")
        return bars

    def get_current_price(self, symbol: str) -> Quote:
        response = self.session.get(
            self.quote_url,
            params={"query": f"SERVICE_ITEM:{symbol}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        try:
            row = response.json()["result"]["areas"][0]["datas"][0]
            price = _number(row.get("nv") or row.get("closePrice") or row.get("nowVal"))
            volume = _number(row.get("aq") or row.get("accQuant") or 0)
        except Exception as exc:
            raise RuntimeError(f"Unexpected Naver quote response for {symbol}") from exc
        if price <= 0:
            raise RuntimeError(f"Naver returned an invalid quote for {symbol}")
        return Quote(symbol=symbol, price=price, volume=volume, source=self.name)


class KrxMarketDataProvider:
    """Optional pykrx adapter for exploration/fallback."""

    name = "krx"

    def __init__(self):
        try:
            from pykrx import stock  # type: ignore
        except Exception as exc:
            raise RuntimeError("pykrx is not installed; install requirements-krx.txt") from exc
        self.stock = stock

    def get_daily_ohlcv(self, symbol: str, count: int = 260) -> list[Bar]:
        end = _today_kst_date()
        start = end - timedelta(days=max(550, count * 2))
        frame = self.stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), symbol)
        if frame.empty:
            raise RuntimeError(f"KRX returned no data for {symbol}")
        bars: list[Bar] = []
        for index, row in frame.iterrows():
            close = _number(row["종가"])
            volume = _number(row["거래량"])
            value = _number(row["거래대금"]) if "거래대금" in row else close * volume
            session_date = index.strftime("%Y%m%d") if hasattr(index, "strftime") else str(index).replace("-", "")[:8]
            bars.append(
                Bar(
                    high=_number(row["고가"]),
                    low=_number(row["저가"]),
                    close=close,
                    volume=volume,
                    value=value,
                    date=session_date,
                )
            )
        bars = _completed(bars, count)
        if len(bars) < 61:
            raise RuntimeError(f"KRX returned only {len(bars)} completed daily bars for {symbol}")
        return bars

    def get_current_price(self, symbol: str) -> Quote:
        end = _today_kst_date()
        frame = self.stock.get_market_ohlcv_by_date((end - timedelta(days=10)).strftime("%Y%m%d"), end.strftime("%Y%m%d"), symbol)
        if frame.empty:
            raise RuntimeError(f"KRX returned no quote for {symbol}")
        row = frame.iloc[-1]
        return Quote(symbol=symbol, price=_number(row["종가"]), volume=_number(row["거래량"]), source=self.name)


class KisMarketDataProvider:
    """KIS REST provider for Oracle production market data (read-only)."""

    name = "kis"

    def __init__(self, timeout: float = 8.0):
        self.app_key = os.getenv("KIS_APP_KEY", "").strip()
        self.app_secret = os.getenv("KIS_APP_SECRET", "").strip()
        self.base_url = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")
        if not self.app_key or not self.app_secret:
            raise RuntimeError("KIS_APP_KEY/KIS_APP_SECRET are required")
        self.timeout = timeout
        self.session = requests.Session()
        self._token = ""
        self._token_expiry = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        response = self.session.post(
            f"{self.base_url}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _headers(self, tr_id: str) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self._access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def get_current_price(self, symbol: str) -> Quote:
        response = self.session.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._headers("FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("rt_cd", "0")) != "0":
            raise RuntimeError(payload.get("msg1", "KIS quote error"))
        out = payload.get("output") or {}
        return Quote(
            symbol=symbol,
            price=_number(out.get("stck_prpr")),
            volume=_number(out.get("acml_vol")),
            source=self.name,
        )

    def get_daily_ohlcv(self, symbol: str, count: int = 260) -> list[Bar]:
        end = _today_kst_date()
        start = end - timedelta(days=max(550, count * 2))
        response = self.session.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=self._headers("FHKST03010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("rt_cd", "0")) != "0":
            raise RuntimeError(payload.get("msg1", "KIS daily chart error"))
        parsed: list[tuple[str, Bar]] = []
        for row in payload.get("output2") or []:
            try:
                session_date = str(row.get("stck_bsop_date") or "")
                close = _number(row.get("stck_clpr"))
                volume = _number(row.get("acml_vol"))
                value = _number(row.get("acml_tr_pbmn")) or close * volume
                bar = Bar(
                    high=_number(row.get("stck_hgpr")),
                    low=_number(row.get("stck_lwpr")),
                    close=close,
                    volume=volume,
                    value=value,
                    date=session_date,
                )
                if min(bar.high, bar.low, bar.close) > 0:
                    parsed.append((session_date, bar))
            except (TypeError, ValueError):
                continue
        parsed.sort(key=lambda item: item[0])
        bars = _completed([bar for _, bar in parsed], count)
        if len(bars) < 61:
            raise RuntimeError(
                f"KIS returned only {len(bars)} completed bars for {symbol}; configure Oracle history cache/pagination"
            )
        return bars


class FallbackMarketDataProvider:
    def __init__(self, providers: list[MarketDataProvider]):
        if not providers:
            raise ValueError("providers cannot be empty")
        self.providers = providers
        self.name = ">".join(getattr(provider, "name", provider.__class__.__name__) for provider in providers)

    def _call(self, method: str, *args, **kwargs):
        errors: list[str] = []
        for provider in self.providers:
            try:
                return getattr(provider, method)(*args, **kwargs)
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        raise RuntimeError("All market-data providers failed: " + " | ".join(errors))

    def get_daily_ohlcv(self, symbol: str, count: int = 260) -> list[Bar]:
        return self._call("get_daily_ohlcv", symbol, count)

    def get_current_price(self, symbol: str) -> Quote:
        return self._call("get_current_price", symbol)

    def get_snapshot(self, symbol: str, count: int = 260) -> MarketSnapshot:
        errors: list[str] = []
        for provider in self.providers:
            try:
                bars = provider.get_daily_ohlcv(symbol, count)
                quote = provider.get_current_price(symbol)
                return MarketSnapshot(bars=bars, quote=quote)
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        raise RuntimeError("All market-data providers failed: " + " | ".join(errors))


def _try_krx() -> MarketDataProvider | None:
    if os.getenv("ENABLE_KRX_FALLBACK", "0").lower() not in {"1", "true", "yes"}:
        return None
    try:
        return KrxMarketDataProvider()
    except Exception:
        return None


def build_market_data_provider(mode: str | None = None) -> MarketDataProvider:
    """Build KIS -> Naver -> optional KRX -> Demo according to environment."""

    selected = (mode or os.getenv("DATA_PROVIDER", "auto")).strip().lower()
    if selected == "demo":
        return DemoMarketDataProvider()
    if selected == "naver":
        return NaverMarketDataProvider()
    if selected == "krx":
        return KrxMarketDataProvider()
    if selected == "kis":
        return KisMarketDataProvider()
    if selected != "auto":
        raise ValueError(f"Unknown DATA_PROVIDER={selected}")

    providers: list[MarketDataProvider] = []
    if os.getenv("KIS_APP_KEY") and os.getenv("KIS_APP_SECRET"):
        providers.append(KisMarketDataProvider())
    providers.append(NaverMarketDataProvider())
    krx = _try_krx()
    if krx is not None:
        providers.append(krx)
    providers.append(DemoMarketDataProvider())
    return FallbackMarketDataProvider(providers)
