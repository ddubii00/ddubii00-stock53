import os

import pytest

from oracle import server


def test_oracle_server_reads_host_and_port(monkeypatch):
    called = {}
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8003")
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **options: called.update(app=app, **options))
    server.main()
    assert called == {"app": "api.index:app", "host": "127.0.0.1", "port": 8003}


def test_oracle_server_rejects_invalid_port(monkeypatch):
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValueError, match="PORT"):
        server.main()


def test_oracle_server_defaults_to_oracle_mode(monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.setenv("PORT", "8124")
    monkeypatch.setattr(server.uvicorn, "run", lambda *args, **kwargs: None)

    server.main()

    assert os.environ["APP_MODE"] == "oracle"


def test_oracle_server_preserves_explicit_mode(monkeypatch):
    monkeypatch.setenv("APP_MODE", "vercel")
    monkeypatch.setenv("PORT", "8125")
    monkeypatch.setattr(server.uvicorn, "run", lambda *args, **kwargs: None)

    server.main()

    assert os.environ["APP_MODE"] == "vercel"
