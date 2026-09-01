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
