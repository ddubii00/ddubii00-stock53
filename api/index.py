from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.full_scan import FullScanConfig, full_scan_jobs
from app.positions import build_position_guide
from app.providers import (
    build_market_data_provider,
    get_market_snapshot,
    validate_snapshot_price_scale,
)
from app.state import build_position_state_store
from app.store import get_full_market_scan, get_latest_full_market_scan
from app.strategy import Bar, analyze


ROOT = Path(__file__).resolve().parents[1]
SYMBOL_RE = re.compile(r"^\d{6}$")
HISTORY_COUNT = max(120, int(os.getenv("HISTORY_COUNT", "260")))

app = FastAPI(title="Turtle Signal Guide", version="0.6.0")

DEFAULT_SYMBOLS = {
    "000660": "SK하이닉스",
    "005930": "삼성전자",
    "005380": "현대차",
    "012450": "한화에어로스페이스",
    "035420": "NAVER",
}


class BarIn(BaseModel):
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(default=0, ge=0)
    value: float = Field(default=0, ge=0)
    date: str = ""


class ScanIn(BaseModel):
    symbol: str
    name: str = ""
    current: float = Field(gt=0)
    current_volume: float = Field(default=0, ge=0)
    market_return20: float = 0
    market_return60: float = 0
    bars: list[BarIn] = Field(min_length=61)


class GuideIn(BaseModel):
    symbol: str
    entry_price: float = Field(gt=0)
    n_at_entry: float | None = Field(default=None, gt=0)
    filled_units: int = Field(default=0, ge=0, le=4)
    sizing_mode: str = Field(default="fixed", pattern="^(fixed|risk)$")
    fixed_unit_amount: float = Field(default=10_000_000, ge=0)
    account_equity: float = Field(default=100_000_000, ge=0)
    risk_pct: float = Field(default=0.5, ge=0, le=100)
    previous_stop: float | None = Field(default=None, ge=0)
    exit_strategy: str = Field(default="turtle", pattern="^(turtle|ma_staged)$")
    provider: str = "auto"


class SavePositionIn(GuideIn):
    name: str = ""
    n_at_entry: float = Field(gt=0)


class FullMarketScanIn(BaseModel):
    provider: str = "auto"
    market: str = Field(default="ALL", pattern="^(ALL|KOSPI|KOSDAQ)$")
    min_market_cap_100m: float = Field(default=500, ge=0)
    min_operating_profit_100m: float = 50
    signal_mode: str = Field(default="prealert", pattern="^(prealert|breakout|actionable)$")
    prealert_pct: float = Field(default=1.0, ge=0, le=100)
    avg_value10_filter_enabled: bool = True
    min_avg_value10_100m: float = Field(default=500, ge=0)
    investor_filter_enabled: bool = False
    investor_mode: str = Field(default="either", pattern="^(either|foreign|institution|combined)$")
    min_investor_net_buy_100m: float = Field(default=0, ge=0)
    today_change_filter_enabled: bool = False
    min_today_change_pct: float = Field(default=5, ge=0, le=100)


def _valid_symbol(symbol: str) -> str:
    value = symbol.strip()
    if not SYMBOL_RE.fullmatch(value):
        raise ValueError("종목코드는 숫자 6자리여야 합니다")
    return value


@lru_cache(maxsize=8)
def _provider(mode: str):
    return build_market_data_provider(mode)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(ROOT / "index.html")


@app.get("/api/health")
def health():
    mode = os.getenv("DATA_PROVIDER", "auto")
    provider = _provider(mode)
    app_mode = os.getenv("APP_MODE", "vercel")
    return {
        "ok": True,
        "version": "0.6.0",
        "app_mode": app_mode,
        "provider_mode": mode,
        "provider_chain": getattr(provider, "name", provider.__class__.__name__),
        "kis_configured": bool(os.getenv("KIS_APP_KEY") and os.getenv("KIS_APP_SECRET")),
        "realtime_poll_seconds": max(3, int(os.getenv("REALTIME_POLL_SECONDS", "30"))),
        "full_market_scan_supported": app_mode != "vercel",
        "realtime_note": (
            "Vercel realtime uses browser polling; no background worker or WebSocket runs in serverless."
            if app_mode == "vercel"
            else "Oracle defaults to polling and can switch to the KIS realtime price adapter."
        ),
    }


def _sample_bars() -> list[Bar]:
    return build_market_data_provider("demo").get_daily_ohlcv("000660", HISTORY_COUNT)


@app.get("/api/sample")
def sample(stage: str = "prealert"):
    bars = _sample_bars()
    breakout = max(bar.high for bar in bars[-20:])
    current = breakout * (1.002 if stage == "breakout" else 0.995)
    result = analyze(bars, current=current, current_volume=1_250_000, min_score=0)
    payload = result.to_dict()
    payload.update(symbol="000660", name="SK하이닉스 (DEMO)", current=current, source="demo")
    return payload


@app.get("/api/quote/{symbol}")
def quote(symbol: str, provider: str = "auto"):
    try:
        normalized = _valid_symbol(symbol)
        snapshot = get_market_snapshot(_provider(provider), normalized, HISTORY_COUNT)
        return snapshot.quote.__dict__
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/candidates")
def candidates(
    symbols: str | None = None,
    provider: str = "auto",
    include_filtered: bool = True,
    scope: str = "watchlist",
    scan_id: int | None = None,
    prealert_pct: float = 1.0,
):
    if scope == "all":
        if os.getenv("APP_MODE", "vercel") == "vercel":
            return {
                "provider": provider,
                "full_market_scan": True,
                "scan_status": "UNAVAILABLE",
                "note": "Vercel serverless에서는 전체시장 background scan을 실행하지 않습니다. Oracle/로컬 scanner의 저장 결과를 연결하세요.",
                "items": [],
            }
        scan = (
            get_full_market_scan(scan_id, include_items=True)
            if scan_id is not None
            else get_latest_full_market_scan(include_items=True)
        )
        if scan is None:
            return {
                "provider": provider,
                "full_market_scan": True,
                "scan_status": "EMPTY",
                "note": "저장된 전체시장 결과가 없습니다. ‘전체시장 새 검색’을 눌러주세요.",
                "items": [],
            }
        return {
            "provider": scan["provider"],
            "full_market_scan": True,
            "scan_id": scan["id"],
            "scan_status": scan["status"],
            "phase": scan["phase"],
            "processed": scan["processed"],
            "total": scan["total"],
            "universe_count": scan["universe_count"],
            "fundamentals_passed": scan["fundamentals_passed"],
            "error_count": scan["error_count"],
            "started_at": scan["started_at"],
            "finished_at": scan["finished_at"],
            "options": scan.get("options", {}),
            "note": scan["message"],
            "items": scan["items"],
        }
    if scope != "watchlist":
        raise HTTPException(status_code=422, detail="scope must be watchlist or all")
    selected = [item.strip() for item in symbols.split(",") if item.strip()] if symbols else list(DEFAULT_SYMBOLS)
    max_symbols = max(1, int(os.getenv("VERCEL_SCAN_MAX_SYMBOLS", "8")))
    selected = selected[:max_symbols]
    market_provider = _provider(provider)
    rows: list[dict] = []
    for raw_symbol in selected:
        try:
            symbol = _valid_symbol(raw_symbol)
            snapshot = get_market_snapshot(market_provider, symbol, HISTORY_COUNT)
            validate_snapshot_price_scale(snapshot)
            result = analyze(
                snapshot.bars,
                current=snapshot.quote.price,
                current_volume=snapshot.quote.volume,
                min_avg_value20=float(os.getenv("MIN_AVG_VALUE20", "10000000000")),
                prealert_pct=max(0.0, prealert_pct),
                min_score=int(os.getenv("MIN_SCORE", "55")),
            )
            item = result.to_dict()
            item.update(
                symbol=symbol,
                name=DEFAULT_SYMBOLS.get(symbol, symbol),
                current=snapshot.quote.price,
                source=snapshot.quote.source,
            )
            if include_filtered or item["stage"] != "FILTERED":
                rows.append(item)
        except Exception as exc:
            rows.append(
                {"symbol": raw_symbol, "name": DEFAULT_SYMBOLS.get(raw_symbol, raw_symbol), "stage": "ERROR", "error": str(exc)}
            )
    rank = {"BREAKOUT": 0, "PREALERT": 1, "WATCH": 2, "FILTERED": 3, "ERROR": 4}
    rows.sort(key=lambda item: (rank.get(item.get("stage", "ERROR"), 9), -int(item.get("score", 0))))
    return {
        "provider": getattr(market_provider, "name", market_provider.__class__.__name__),
        "full_market_scan": False,
        "note": "Vercel은 소수 watchlist 탐색용입니다. 실전 전체시장 감시는 Oracle + KIS를 사용하세요.",
        "items": rows,
    }


@app.post("/api/full-market-scans", status_code=202)
def start_full_market_scan(payload: FullMarketScanIn):
    if os.getenv("APP_MODE", "vercel") == "vercel":
        raise HTTPException(
            status_code=409,
            detail="Vercel serverless에서는 전체시장 background scan을 시작할 수 없습니다. Oracle 또는 로컬 APP_MODE=oracle에서 실행하세요.",
        )
    config = FullScanConfig(**payload.model_dump())
    try:
        scan_id = full_scan_jobs.start(config)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"scan_id": scan_id, "status": "RUNNING", **config.to_dict()}


@app.get("/api/full-market-scans/{scan_id}")
def full_market_scan_status(scan_id: int):
    if os.getenv("APP_MODE", "vercel") == "vercel":
        raise HTTPException(status_code=404, detail="full market scan is not available on Vercel")
    scan = get_full_market_scan(scan_id, include_items=True)
    if scan is None:
        raise HTTPException(status_code=404, detail="scan not found")
    return scan


@app.post("/api/scan")
def scan(payload: ScanIn):
    bars = [Bar(**bar.model_dump()) for bar in payload.bars]
    result = analyze(
        bars,
        payload.current,
        payload.current_volume,
        payload.market_return60,
        payload.market_return20,
        min_avg_value20=float(os.getenv("MIN_AVG_VALUE20", "10000000000")),
        prealert_pct=float(os.getenv("PREALERT_PCT", "1.0")),
        min_score=int(os.getenv("MIN_SCORE", "55")),
    )
    response = result.to_dict()
    response.update(symbol=payload.symbol, name=payload.name, current=payload.current)
    return response


def _build_guide(payload: GuideIn) -> dict:
    symbol = _valid_symbol(payload.symbol)
    snapshot = get_market_snapshot(_provider(payload.provider), symbol, HISTORY_COUNT)
    validate_snapshot_price_scale(snapshot)
    guide = build_position_guide(
        symbol=symbol,
        bars=snapshot.bars,
        current=snapshot.quote.price,
        entry_price=payload.entry_price,
        n_at_entry=payload.n_at_entry,
        filled_units=payload.filled_units,
        sizing_mode=payload.sizing_mode,
        fixed_unit_amount=payload.fixed_unit_amount,
        account_equity=payload.account_equity,
        risk_pct=payload.risk_pct,
        previous_stop=payload.previous_stop,
        exit_strategy=payload.exit_strategy,
    )
    response = guide.to_dict()
    response.update(name=DEFAULT_SYMBOLS.get(symbol, symbol), source=snapshot.quote.source)
    return response


@app.post("/api/guide")
def guide(payload: GuideIn):
    try:
        return _build_guide(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/guide")
def guide_get(
    symbol: str = "000660",
    entry_price: float = 100_000,
    n_at_entry: float = 5_000,
    filled_units: int = 0,
    exit_strategy: str = "turtle",
    provider: str = "demo",
):
    try:
        return _build_guide(
            GuideIn(
                symbol=symbol,
                entry_price=entry_price,
                n_at_entry=n_at_entry,
                filled_units=filled_units,
                exit_strategy=exit_strategy,
                provider=provider,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/positions")
def positions():
    if os.getenv("APP_MODE", "vercel") == "vercel":
        return {"persistent": False, "note": "Vercel demo는 localStorage에 저장합니다.", "items": []}
    return {"persistent": True, "items": build_position_state_store().list_active()}


@app.post("/api/positions")
def position_save(payload: SavePositionIn):
    if os.getenv("APP_MODE", "vercel") == "vercel":
        raise HTTPException(status_code=409, detail="Vercel에서는 localStorage를 사용하고 Oracle에서만 DB에 저장합니다")
    values = payload.model_dump(exclude={"provider", "previous_stop"})
    values["symbol"] = _valid_symbol(payload.symbol)
    values["common_stop"] = payload.previous_stop or 0
    build_position_state_store().save(values)
    return {"ok": True, "symbol": values["symbol"]}


@app.post("/api/positions/{symbol}/confirm-fill")
def position_confirm_fill(symbol: str):
    if os.getenv("APP_MODE", "vercel") == "vercel":
        raise HTTPException(status_code=409, detail="Vercel에서는 사용자가 localStorage 상태를 직접 확정합니다")
    try:
        return {"ok": True, "position": build_position_state_store().confirm_next_fill(_valid_symbol(symbol))}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ACTIVE position not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
