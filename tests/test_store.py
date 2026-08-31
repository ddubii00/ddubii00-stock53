from app import store


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
