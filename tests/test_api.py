from fastapi.testclient import TestClient

from api.index import app


client = TestClient(app)


def test_required_vercel_routes_work_without_kis_keys(monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    assert client.get("/").status_code == 200
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["kis_configured"] is False
    candidates = client.get("/api/candidates", params={"provider": "demo", "symbols": "000660"})
    assert candidates.status_code == 200
    assert candidates.json()["items"][0]["source"] == "demo"


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


def test_get_guide_route_is_deploy_smoke_test():
    response = client.get("/api/guide", params={"provider": "demo"})
    assert response.status_code == 200
    assert response.json()["source"] == "demo"
