from fastapi.testclient import TestClient
import json
from pathlib import Path
import time
import tomllib

from api.index import app


client = TestClient(app)


def test_vercel_fastapi_entrypoint_is_explicit():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["vercel"]["entrypoint"] == "api.index:app"
    deployment = json.loads(Path("vercel.json").read_text(encoding="utf-8"))
    assert {build["src"] for build in deployment["builds"]} == {"api/index.py", "index.html"}
    assert deployment["routes"][0] == {"src": "/api/(.*)", "dest": "api/index.py"}
    assert deployment["routes"][1]["headers"]["Cache-Control"] == "no-store, max-age=0"


def test_required_vercel_routes_work_without_kis_keys(monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    monkeypatch.delenv("REALTIME_POLL_SECONDS", raising=False)
    home = client.get("/")
    assert home.status_code == 200
    assert home.headers["cache-control"] == "no-store, max-age=0"
    assert home.headers["pragma"] == "no-cache"
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["kis_configured"] is False
    assert health.json()["realtime_poll_seconds"] == 30
    assert health.json()["quote_poll_seconds"] == 3
    assert health.json()["manual_full_market_scan_supported"] is True
    candidates = client.get("/api/candidates", params={"provider": "demo", "symbols": "000660"})
    assert candidates.status_code == 200
    assert candidates.json()["items"][0]["source"] == "demo"
    alphanumeric_quote = client.get("/api/quote/0126z0", params={"provider": "demo"})
    assert alphanumeric_quote.status_code == 200
    assert alphanumeric_quote.json()["symbol"] == "0126Z0"
    batch_quotes = client.get("/api/quotes", params={"provider": "demo", "symbols": "000660,005930"})
    assert batch_quotes.status_code == 200
    assert len(batch_quotes.json()["items"]) == 2
    assert all(item["change_pct"] is not None for item in batch_quotes.json()["items"])
    investor_flows = client.get(
        "/api/investor-flows", params={"provider": "demo", "symbols": "000660,005930"}
    )
    assert investor_flows.status_code == 200
    assert len(investor_flows.json()["items"]) == 2
    assert all(item["investor_date"] for item in investor_flows.json()["items"])
    assert all(item["foreign_net_buy_100m"] is not None for item in investor_flows.json()["items"])


def test_oracle_live_candidate_subset_is_not_limited_to_vercel_default(monkeypatch):
    symbols = ",".join(f"{index:06d}" for index in range(1, 13))
    monkeypatch.setenv("APP_MODE", "oracle")
    monkeypatch.delenv("ORACLE_LIVE_SCAN_MAX_SYMBOLS", raising=False)
    response = client.get(
        "/api/candidates",
        params={"provider": "demo", "scope": "watchlist", "symbols": symbols},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_count"] == 12
    assert payload["scanned_count"] == 12
    assert payload["truncated"] is False
    assert len(payload["items"]) == 12
    assert all("today_change_pct" in item for item in payload["items"])


def test_vercel_watchlist_keeps_small_request_limit(monkeypatch):
    symbols = ",".join(f"{index:06d}" for index in range(1, 13))
    monkeypatch.setenv("APP_MODE", "vercel")
    monkeypatch.delenv("VERCEL_SCAN_MAX_SYMBOLS", raising=False)
    response = client.get(
        "/api/candidates",
        params={"provider": "demo", "scope": "watchlist", "symbols": symbols},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_count"] == 12
    assert payload["scanned_count"] == 8
    assert payload["truncated"] is True
    assert len(payload["items"]) == 8


def test_oracle_position_can_be_closed_after_sell(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "oracle")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "close-position.db"))
    saved = client.post(
        "/api/positions",
        json={
            "symbol": "090460",
            "name": "비에이치",
            "entry_price": 20_000,
            "n_at_entry": 1_000,
            "filled_units": 1,
            "provider": "demo",
        },
    )
    assert saved.status_code == 200
    closed = client.delete("/api/positions/090460")
    assert closed.status_code == 200
    assert client.get("/api/positions").json()["items"] == []


def test_guide_route_returns_position_action():
    response = client.post(
        "/api/guide",
        json={
            "symbol": "000660",
            "entry_price": 150_000,
            "n_at_entry": 5_000,
            "filled_units": 1,
            "provider": "demo",
        },
    )
    assert response.status_code == 200
    assert response.json()["action"] in {"HOLD", "ADD_NOW", "STOP_NOW", "EXIT_NOW"}


def test_guide_route_returns_selected_sell_strategy():
    response = client.post(
        "/api/guide",
        json={
            "symbol": "000660",
            "entry_price": 150_000,
            "n_at_entry": 5_000,
            "filled_units": 1,
            "exit_strategy": "ma_staged",
            "provider": "demo",
        },
    )
    assert response.status_code == 200
    assert response.json()["exit_strategy"] == "ma_staged"
    assert response.json()["sell_action"] in {"SELL_WAIT", "REDUCE_1", "REDUCE_2", "STOP_NOW", "EXIT_NOW"}


def test_get_guide_route_is_deploy_smoke_test():
    response = client.get("/api/guide", params={"provider": "demo"})
    assert response.status_code == 200
    assert response.json()["source"] == "demo"


def test_full_market_scan_demo_is_persisted_and_read_by_candidates(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "oracle")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "api-scan.db"))
    response = client.post(
        "/api/full-market-scans",
        json={
            "provider": "demo",
            "market": "ALL",
            "min_market_cap_100m": 500,
            "min_operating_profit_100m": 50,
            "signal_mode": "prealert",
        },
    )
    assert response.status_code == 202
    scan_id = response.json()["scan_id"]
    for _ in range(100):
        status = client.get(f"/api/full-market-scans/{scan_id}")
        assert status.status_code == 200
        if status.json()["status"] != "RUNNING":
            break
        time.sleep(0.02)
    assert status.json()["status"] == "COMPLETED"
    assert status.json()["listed_count"] == 15
    assert status.json()["kospi_count"] == 8
    assert status.json()["kosdaq_count"] == 7
    assert status.json()["universe_count"] == 15
    assert status.json()["fundamentals_passed"] == 15

    candidates = client.get("/api/candidates", params={"scope": "all", "scan_id": scan_id})
    assert candidates.status_code == 200
    payload = candidates.json()
    assert payload["full_market_scan"] is True
    assert payload["scan_status"] == "COMPLETED"
    assert payload["listed_count"] == 15
    assert payload["kospi_count"] == 8
    assert payload["kosdaq_count"] == 7
    assert all(item["stage"] == "PREALERT" for item in payload["items"])


def test_vercel_refuses_background_full_market_worker(monkeypatch):
    monkeypatch.setenv("APP_MODE", "vercel")
    response = client.post("/api/full-market-scans", json={"provider": "demo"})
    assert response.status_code == 409


def test_vercel_allows_manual_one_request_full_market_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "vercel")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "manual-scan.db"))
    response = client.post(
        "/api/full-market-scan-once",
        json={"provider": "demo", "signal_mode": "prealert"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["manual_once"] is True
    assert payload["scan_status"] == "COMPLETED"
    assert payload["listed_count"] == 15
    assert payload["kospi_count"] == 8
    assert payload["kosdaq_count"] == 7
    assert all(item["stage"] == "PREALERT" for item in payload["items"])


def test_manual_full_market_scan_can_include_etf(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_MODE", "vercel")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "manual-etf-scan.db"))
    response = client.post(
        "/api/full-market-scan-once",
        json={
            "provider": "demo",
            "include_etf": True,
            "signal_mode": "actionable",
            "avg_value10_filter_enabled": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["options"]["include_etf"] is True
    assert payload["stock_count"] == 15
    assert payload["etf_count"] == 2
    assert payload["listed_count"] == 17
    assert any(item["asset_type"] == "ETF" for item in payload["items"])
