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


def test_required_vercel_routes_work_without_kis_keys(monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    monkeypatch.delenv("REALTIME_POLL_SECONDS", raising=False)
    assert client.get("/").status_code == 200
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["kis_configured"] is False
    assert health.json()["realtime_poll_seconds"] == 30
    candidates = client.get("/api/candidates", params={"provider": "demo", "symbols": "000660"})
    assert candidates.status_code == 200
    assert candidates.json()["items"][0]["source"] == "demo"
    alphanumeric_quote = client.get("/api/quote/0126z0", params={"provider": "demo"})
    assert alphanumeric_quote.status_code == 200
    assert alphanumeric_quote.json()["symbol"] == "0126Z0"


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
