"""KOSPI/KOSDAQ symbol-name search backed by the official KIS master files."""

from __future__ import annotations

import os
import threading
import time
import zipfile
from io import BytesIO

import requests

from app.universe import DemoUniverseProvider, NaverUniverseProvider


MASTER_URLS = {
    "KOSPI": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "KOSDAQ": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
}
_CACHE: list[dict] = []
_CACHE_AT = 0.0
_LOCK = threading.Lock()


def parse_kis_master(content: bytes, market: str) -> list[dict]:
    """Parse KIS's EUC-KR fixed-width domestic symbol master format."""

    rows: list[dict] = []
    for line in content.splitlines():
        if len(line) < 61:
            continue
        code = line[0:9].decode("euc-kr", errors="ignore").strip()
        name = line[21:61].decode("euc-kr", errors="ignore").strip()
        if len(code) > 6:
            code = code[-6:]
        code = code.upper()
        if len(code) != 6 or not code.isalnum() or not name:
            continue
        rows.append(
            {
                "symbol": code,
                "name": name,
                "market": market,
                "source": "kis-master",
            }
        )
    return rows


def _download_master(timeout: float = 20.0) -> list[dict]:
    rows: list[dict] = []
    for market, url in MASTER_URLS.items():
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            names = archive.namelist()
            if not names:
                raise RuntimeError(f"KIS {market} master archive is empty")
            rows.extend(parse_kis_master(archive.read(names[0]), market))
    if not rows:
        raise RuntimeError("KIS symbol master returned no rows")
    return rows


def _kis_symbols() -> list[dict]:
    global _CACHE, _CACHE_AT
    try:
        ttl = max(60, int(os.getenv("SYMBOL_MASTER_CACHE_SECONDS", "21600")))
    except ValueError:
        ttl = 21600
    if _CACHE and time.time() - _CACHE_AT < ttl:
        return _CACHE
    with _LOCK:
        if _CACHE and time.time() - _CACHE_AT < ttl:
            return _CACHE
        _CACHE = _download_master()
        _CACHE_AT = time.time()
        return _CACHE


def _fallback_symbols(mode: str) -> list[dict]:
    provider = DemoUniverseProvider() if mode == "demo" else NaverUniverseProvider()
    members = provider.list_members("ALL", 0, include_etf=True)
    return [
        {
            "symbol": member.symbol,
            "name": member.name,
            "market": member.market,
            "asset_type": member.asset_type,
            "source": provider.name,
        }
        for member in members
    ]


def search_symbols(query: str, provider_mode: str = "auto", limit: int = 20) -> tuple[list[dict], str]:
    """Search by code/name. KIS master is primary; Naver/Demo is fallback."""

    term = query.strip().casefold()
    if not term:
        return [], "none"
    mode = provider_mode.strip().lower()
    if mode == "demo":
        rows = _fallback_symbols("demo")
        source = "demo"
    else:
        try:
            rows = _kis_symbols()
            source = "kis-master"
        except Exception:
            rows = _fallback_symbols("naver")
            source = "naver"
    matches = [
        row
        for row in rows
        if term in str(row.get("symbol", "")).casefold()
        or term in str(row.get("name", "")).casefold()
    ]
    matches.sort(
        key=lambda row: (
            str(row.get("symbol", "")).casefold() != term,
            not str(row.get("name", "")).casefold().startswith(term),
            len(str(row.get("name", ""))),
            str(row.get("name", "")),
        )
    )
    return matches[: max(1, min(50, int(limit)))], source
