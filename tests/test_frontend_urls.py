from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


INDEX = Path("index.html")
API_PATHS = [
    "/api/health",
    "/api/candidates?scope=all",
    "/api/quotes?symbols=000660",
    "/api/investor-flows?symbols=000660",
    "/api/symbol-search?q=005930",
    "/api/full-market-scans",
    "/api/full-market-scans/7",
    "/api/full-market-scan-once",
    "/api/guide",
    "/api/positions",
    "/api/positions/000660",
]


def _resolver_source() -> str:
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("function resolveApiUrl")
    end = source.index("async function fetchJson", start)
    return source[start:end]


def _resolve_with_frontend(base_url: str) -> list[str]:
    script = "\n".join(
        [
            f"const document={{baseURI:{json.dumps(base_url)}}};",
            _resolver_source(),
            f"process.stdout.write(JSON.stringify({json.dumps(API_PATHS)}.map(path=>resolveApiUrl(path))));",
        ]
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for frontend JS test")
@pytest.mark.parametrize(
    ("base_url", "prefix"),
    [
        ("https://stock53.vercel.app/", "https://stock53.vercel.app"),
        (
            "http://158.179.192.139/stock53-7/",
            "http://158.179.192.139/stock53-7",
        ),
    ],
)
def test_api_urls_resolve_for_root_and_oracle_subpath(base_url: str, prefix: str):
    assert _resolve_with_frontend(base_url) == [prefix + path for path in API_PATHS]


def test_every_frontend_fetch_uses_the_common_api_resolver():
    source = INDEX.read_text(encoding="utf-8")
    script = source[source.index("<script>") : source.index("</script>")]
    assert re.findall(r"\bfetch\s*\(", script) == ["fetch("]
    assert "fetch(resolveApiUrl(url),options)" in script
    assert "Vercel 배포의 FastAPI 함수 설정" not in script
    assert "배포 경로와 FastAPI/Nginx 연결을 확인하세요" in script


def test_oracle_health_capability_selects_the_full_market_scan_api():
    source = INDEX.read_text(encoding="utf-8")
    assert "fullScanSupported=Boolean(data.full_market_scan_supported)" in source
    assert "if(!fullScanSupported&&manualFullScanSupported)" in source
    assert "fetchJson('/api/full-market-scans'" in source


def test_live_mode_rechecks_only_the_initial_full_market_candidates():
    source = INDEX.read_text(encoding="utf-8")
    cycle_start = source.index("async function realtimeCandidateCycle")
    cycle_end = source.index("function toggleRealtime", cycle_start)
    cycle = source[cycle_start:cycle_end]
    assert "return refreshLiveCandidateSignals()" in cycle
    assert "startFullMarketScan" not in cycle
    assert "symbols:batch.map(item=>item.symbol).join(',')" in source
    assert "batchSize=appMode==='vercel'?8:200" in source
    assert "include_filtered:'true'" in source
    assert "조회 오류 ${errorCount}개(마지막 값 유지·다음 주기 재시도)" in source
    assert "전체시장·재무 재검색 없음" in source
    assert "candidateItems=liveSeedItems.map(item=>({...item}))" in source
    assert "숏 PREALERT ${shortPrealerts} / BREAKOUT ${shortBreakouts} / 이탈 ${shortWatches}" in source


def test_live_button_is_next_to_manual_full_market_scan_button():
    source = INDEX.read_text(encoding="utf-8")
    controls = source[source.index('id="marketScanBtn"') : source.index('id="providerNote"')]
    assert 'id="liveBtn"' in controls
    assert controls.index('id="marketScanBtn"') < controls.index('id="liveBtn"')
    assert '>실시간</button>' in controls


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for frontend JS test")
def test_live_stage_and_target_distance_follow_current_price():
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("function changeClass")
    end = source.index("function renderShortGuide", start)
    helpers = source[start:end]
    script = "\n".join(
        [
            helpers,
            "const below={stage:'BREAKOUT',current:99.5,breakout20:100,yesterday_broke:false};",
            "const above={stage:'PREALERT',current:100.5,breakout20:100,yesterday_broke:false};",
            "const far={stage:'PREALERT',current:98,breakout20:100,yesterday_broke:false};",
            "const retraced={stage:'PREALERT',current:93.5,today_high:100.1,breakout20:100,yesterday_broke:false};",
            "const retracedNear={stage:'BREAKOUT',current:99.5,today_high:100.1,breakout20:100,yesterday_broke:false};",
            "process.stdout.write(JSON.stringify({",
            "belowDistance:signedPct(targetDistancePct(below)),belowClass:changeClass(targetDistancePct(below)),",
            "aboveDistance:signedPct(targetDistancePct(above)),aboveClass:changeClass(targetDistancePct(above)),",
            "down:classifyLiveStage(below,1),up:classifyLiveStage(above,1),farBelow:classifyLiveStage(far,1),retraced:classifyLiveStage(retraced,1),retracedNear:classifyLiveStage(retracedNear,1),intradayFlag:applyLiveStage(retracedNear,1).intraday_broke",
            "}));",
        ]
    )
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == {
        "belowDistance": "-0.50%",
        "belowClass": "changeDown",
        "aboveDistance": "+0.50%",
        "aboveClass": "changeUp",
        "down": "PREALERT",
        "up": "BREAKOUT",
        "farBelow": "WATCH",
        "retraced": "WATCH",
        "retracedNear": "PREALERT",
        "intradayFlag": True,
    }


def test_out_of_range_live_candidates_are_separate_from_prealert():
    source = INDEX.read_text(encoding="utf-8")
    assert 'id="watchSection"' in source
    assert 'id="watchBody"' in source
    assert "prealerts=rows.filter(item=>item.stage==='PREALERT')" in source
    assert "watches=rows.filter(item=>item._track_long&&item.stage==='WATCH')" in source
    assert "renderGroup('prealertBody',prealerts" in source


def test_investor_flow_is_split_and_labeled_with_its_basis_date():
    source = INDEX.read_text(encoding="utf-8")
    assert 'data-sort="foreign_net_buy_100m">외인(억)' in source
    assert 'data-sort="institution_net_buy_100m">기관(억)' in source
    assert "<th>수급 기준</th>" in source
    assert "장 종료 후 확정" in source
    assert "item.investor_date" in source
    assert "fetchJson('/api/investor-flows?'" in source


def test_candidate_table_shows_atr_and_current_state_badges():
    source = INDEX.read_text(encoding="utf-8")
    assert source.count('data-sort="atr20">ATR20(N)') == 6
    assert "fmt(item.atr20)" in source
    assert "장중 돌파 후 하회" in source
    assert "접근범위 이탈" in source
    assert "if(current>=target)return 'BREAKOUT'" in source
    assert "todayHigh>=target" in source


def test_oracle_labels_naver_as_kis_fallback():
    source = INDEX.read_text(encoding="utf-8")
    assert "function marketSourceText(item)" in source
    assert "NAVER · KIS대체" in source
    assert "kisConfigured=Boolean(data.kis_configured)" in source


def test_candidate_requires_explicit_select_button_and_keeps_market_details():
    source = INDEX.read_text(encoding="utf-8")
    assert "choose.textContent=(isShort?'숏':'롱')+' 선택'" in source
    assert "selectCandidate(item,{side:perspective})" in source
    assert "detailPerspective:perspective" in source
    assert "selectionSide:side,detailPerspective:side" in source
    assert "function persistCandidateSelection(item,side='long')" in source
    assert "function syncTrackedCandidateSnapshots(items)" in source
    assert "20D ${short?'신저가':'돌파가'}" in source


def test_selected_guide_supports_symbol_autocomplete_and_six_units():
    source = INDEX.read_text(encoding="utf-8")
    assert 'id="symbolSuggestions"' in source
    assert "fetchJson('/api/symbol-search?'" in source
    assert "function chooseSymbolSuggestion(item)" in source
    assert "{refresh:false,side:el('positionSide').value}" in source
    assert 'id="gUnits"' in source
    assert '<option value="6">6 Units · 확장 한도</option>' in source
    assert 'id="u5"' in source and 'id="u6"' in source
    assert "for(let i=0;i<6;i++)" in source
    assert "if(units>=6)" in source
    assert 'id="newFillPrices"' in source
    assert "json:JSON.stringify({fill_prices:newPrices})" not in source
    assert "JSON.stringify({fill_prices:newPrices})" in source


def test_amounts_are_entered_and_displayed_in_manwon():
    source = INDEX.read_text(encoding="utf-8")
    assert "const manwon=" in source
    assert "const wonFromManwon=" in source
    assert "총 투자 한도 (만원 · 6 Units)" in source
    assert "계좌 잔고 (만원)" in source
    assert "위험한도 (%)" in source
    assert "el('unitAmount').value=Math.max(0,Math.floor(num('totalInvestment')/6))" in source
    assert "total_investment:Number(item.fixed_unit_amount||10000000)*6" in source
    assert "el('gRiskBudget').textContent=manwon(data.risk_budget)" in source


def test_long_badge_short_guide_and_atr_basis_are_visible():
    source = INDEX.read_text(encoding="utf-8")
    assert "badge.textContent=isShort?'숏':'롱'" in source
    assert "숏 포지션 · 하락 매매 가이드" in source
    assert "최초 숏 Entry" in source
    assert "현재 ATR20" in source
    assert "진입 당시 고정 N" in source
    assert 'id="gActionName"' in source and 'id="sActionName"' in source
    assert "renderShortGuide({...data,name:trackingName||data.name})" in source
    assert "if(data.side!=='short')return" in source
    assert "document.querySelectorAll('.shortOnly')" in source


def test_quality_explanation_is_visible_and_separate_from_breakout():
    source = INDEX.read_text(encoding="utf-8")
    assert "Quality란?" in source
    assert "후보의 거래 용이성과 추세 품질을 비교하는 0~100점 참고 점수" in source
    assert "PREALERT/BREAKOUT의 20일 돌파가는 바꾸지 않습니다" in source


def test_prealert_table_is_rendered_before_breakout_table():
    source = INDEX.read_text(encoding="utf-8")
    prealert = '<section class="signalSection"><h3 class="signalSectionTitle yellow">롱 PREALERT'
    breakout = '<section class="signalSection"><h3 class="signalSectionTitle green">롱 BREAKOUT'
    assert source.index(prealert) < source.index(breakout)


def test_short_prealert_and_breakout_tables_follow_long_tables():
    source = INDEX.read_text(encoding="utf-8")
    long_breakout = 'class="signalSectionTitle green">롱 BREAKOUT'
    short_prealert = 'class="signalSectionTitle yellow">숏 PREALERT'
    short_breakout = 'class="signalSectionTitle red">숏 BREAKOUT'
    assert source.index(long_breakout) < source.index(short_prealert) < source.index(short_breakout)
    assert 'id="shortPrealertBody"' in source
    assert 'id="shortBreakoutBody"' in source
    assert 'id="shortWatchBody"' in source
    assert 'data-sort="short_entry20">20D 신저가' in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for frontend JS test")
def test_short_live_stage_moves_between_prealert_breakout_and_watch():
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("function changeClass")
    end = source.index("function renderShortGuide", start)
    helpers = source[start:end]
    script = "\n".join(
        [
            helpers,
            "const base={current:100.5,short_entry20:100,short_stage:'SHORT_PREALERT',short_yesterday_broke:false};",
            "process.stdout.write(JSON.stringify({",
            "near:classifyLiveShortStage(base,1),",
            "broke:classifyLiveShortStage({...base,current:99.9},1),",
            "far:classifyLiveShortStage({...base,current:102},1),",
            "old:classifyLiveShortStage({...base,current:99.9,short_yesterday_broke:true},1)",
            "}));",
        ]
    )
    completed = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout) == {
        "near": "SHORT_PREALERT",
        "broke": "SHORT_BREAKOUT",
        "far": "SHORT_WATCH",
        "old": "SHORT_FILTERED",
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is needed for frontend JS test")
def test_resolver_does_not_modify_external_links():
    external = "https://stock.naver.com/domestic/stock/005930"
    script = "\n".join(
        [
            "const document={baseURI:'http://158.179.192.139/stock53-7/'};",
            _resolver_source(),
            f"process.stdout.write(resolveApiUrl({json.dumps(external)}));",
        ]
    )
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout == external
