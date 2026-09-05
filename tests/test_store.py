import sqlite3

import pytest

from app import store


def test_vercel_default_database_uses_writable_tmp(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert store.db_path() == "/tmp/turtle.db"


def test_signal_event_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "turtle.db"))
    assert store.event_once("abc", "000660", "ADD_NOW") is True
    assert store.event_once("abc", "000660", "ADD_NOW") is False


def test_fill_is_only_incremented_by_explicit_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "positions.db"))
    store.save_position(
        {
            "symbol": "000660",
            "entry_price": 300_000,
            "n_at_entry": 12_000,
            "filled_units": 1,
        }
    )
    assert store.get_position("000660")["filled_units"] == 1
    updated = store.confirm_next_fill("000660")
    assert updated["filled_units"] == 2
    assert updated["common_stop"] == 282_000


def test_multiple_actual_fills_are_persisted_and_used_for_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "actual-multiple.db"))
    store.save_position(
        {
            "symbol": "000660",
            "entry_price": 300_000,
            "n_at_entry": 12_000,
            "filled_units": 0,
            "side": "long",
        }
    )
    updated = store.confirm_fills("000660", [303_000, 311_000, 320_000])
    assert updated["entry_price"] == 303_000
    assert updated["fill_prices"] == [303_000, 311_000, 320_000]
    assert updated["filled_units"] == 3
    assert updated["common_stop"] == 296_000


def test_first_actual_fill_replaces_preentry_theoretical_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "first-actual.db"))
    store.save_position(
        {
            "symbol": "000660",
            "entry_price": 300_000,
            "n_at_entry": 12_000,
            "filled_units": 0,
            "side": "long",
        }
    )
    updated = store.confirm_fills("000660", [295_000])
    assert updated["entry_price"] == 295_000
    assert updated["common_stop"] == 271_000


def test_short_position_direction_and_actual_fills_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "short.db"))
    store.save_position(
        {
            "symbol": "000660",
            "entry_price": 100_000,
            "n_at_entry": 5_000,
            "filled_units": 0,
            "side": "short",
        }
    )
    updated = store.confirm_fills("000660", [99_000, 96_000])
    assert updated["side"] == "short"
    assert updated["fill_prices"] == [99_000, 96_000]
    assert updated["filled_units"] == 2
    assert updated["common_stop"] == 106_000


def test_explicit_confirmation_can_reach_six_units_but_not_seven(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "positions-six.db"))
    store.save_position(
        {
            "symbol": "000660",
            "entry_price": 300_000,
            "n_at_entry": 12_000,
            "filled_units": 4,
        }
    )
    assert store.confirm_next_fill("000660")["filled_units"] == 5
    assert store.confirm_next_fill("000660")["filled_units"] == 6
    with pytest.raises(ValueError, match="six Units"):
        store.confirm_next_fill("000660")


def test_existing_four_unit_sqlite_schema_is_migrated(tmp_path, monkeypatch):
    path = tmp_path / "positions-v4.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE positions (
              symbol TEXT PRIMARY KEY,
              name TEXT NOT NULL DEFAULT '',
              entry_price REAL NOT NULL,
              n_at_entry REAL NOT NULL,
              filled_units INTEGER NOT NULL DEFAULT 0 CHECK(filled_units BETWEEN 0 AND 4),
              sizing_mode TEXT NOT NULL DEFAULT 'fixed',
              fixed_unit_amount REAL NOT NULL DEFAULT 10000000,
              account_equity REAL NOT NULL DEFAULT 100000000,
              risk_pct REAL NOT NULL DEFAULT 0.5,
              exit_strategy TEXT NOT NULL DEFAULT 'turtle',
              common_stop REAL NOT NULL DEFAULT 0,
              status TEXT NOT NULL DEFAULT 'ACTIVE',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO positions(symbol,entry_price,n_at_entry,filled_units)
            VALUES('000660',300000,12000,4);
            """
        )
    monkeypatch.setenv("DB_PATH", str(path))
    store.init_db()
    migrated = store.confirm_next_fill("000660")
    assert migrated["filled_units"] == 5
    with sqlite3.connect(path) as conn:
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='positions'"
        ).fetchone()[0]
    assert "BETWEEN 0 AND 6" in schema


def test_position_persists_selected_exit_strategy(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "positions-exit.db"))
    store.save_position(
        {
            "symbol": "000660",
            "entry_price": 300_000,
            "n_at_entry": 12_000,
            "filled_units": 1,
            "exit_strategy": "ma_staged",
        }
    )
    assert store.get_position("000660")["exit_strategy"] == "ma_staged"
