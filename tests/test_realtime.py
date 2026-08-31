from app.realtime import KisRealtimeWebSocketAdapter


def test_kis_execution_tick_parser(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "test-key")
    monkeypatch.setenv("KIS_APP_SECRET", "test-secret")
    adapter = KisRealtimeWebSocketAdapter()
    fields = ["0"] * adapter.field_count
    fields[0] = "005930"
    fields[1] = "101530"
    fields[2] = "70000"
    fields[13] = "1234567"
    ticks = adapter._parse("0|H0STCNT0|1|" + "^".join(fields))
    assert len(ticks) == 1
    assert ticks[0].symbol == "005930"
    assert ticks[0].price == 70_000
    assert ticks[0].accumulated_volume == 1_234_567
