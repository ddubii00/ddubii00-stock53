from app.symbols import parse_kis_master, search_symbols


def test_kis_master_parser_reads_code_name_and_market():
    line = (
        b"000005930"
        + b"KR7005930003"
        + "삼성전자".encode("euc-kr").ljust(40, b" ")
        + b"ignored trailing fields"
    )
    assert parse_kis_master(line + b"\n", "KOSPI") == [
        {
            "symbol": "005930",
            "name": "삼성전자",
            "market": "KOSPI",
            "source": "kis-master",
        }
    ]


def test_demo_symbol_search_supports_korean_partial_name():
    items, source = search_symbols("삼성", "demo", 20)
    assert source == "demo"
    assert any(item["symbol"] == "005930" and "삼성" in item["name"] for item in items)
