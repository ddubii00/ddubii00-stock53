# stock53 Turtle Signal Guide

한국 주식용 Turtle System 1 **시점·수량 가이드**입니다. 후보 탐색, 신규 20일 돌파, 피라미딩, 보호손절, 10일 채널 청산, Oracle Telegram 알림을 다룹니다.

> 실제 주문 API는 없습니다. 모든 결과는 사용자가 판단하고 체결을 직접 확정하는 읽기 전용 가이드입니다.

## 핵심 흐름

```text
후보검색 → 1% PREALERT → 20D BREAKOUT → 사용자 진입 확정
        → 0.5N 추매 → 사용자 추매 확정 → 2N 보호손절 / 10D Low Exit
        → Oracle Telegram
```

### look-ahead 방지

오늘을 D라고 할 때 모든 채널은 완료된 일봉만 사용합니다.

- 신규 돌파가: `MAX(High[D-1] ... High[D-20])`
- 어제 돌파 기준: `MAX(High[D-2] ... High[D-21])`
- 어제 돌파 여부: `High[D-1] > 어제 돌파 기준`
- System 1 Exit: `MIN(Low[D-1] ... Low[D-10])`

Naver/KRX/KIS provider는 날짜가 오늘인 부분 일봉을 전략 입력에서 제거합니다. 어제 이미 돌파한 종목은 오늘 신규 Unit #1 후보가 아닙니다.

### 포지션

Entry 체결가 `E`와 Entry 당시 `ATR20 = N`은 포지션 수명 동안 고정합니다.

| Unit | 기준가 |
|---|---:|
| #1 | `E` |
| #2 | `E + 0.5N` |
| #3 | `E + 1.0N` |
| #4 | `E + 1.5N` |

- fixed: 각 Unit마다 `floor(Unit 목표금액 / 해당 Unit 가격)`을 별도로 계산
- risk: `floor((account equity × risk %) / (2N))`
- 공통 보호손절: 최근 확정 Unit 가격 `- 2N`
- stop은 이전 저장값보다 내려가지 않음
- STOP과 EXIT가 동시에 충족되면 STOP 우선

매도 가이드는 두 모드를 제공합니다.

- `turtle`: 정통 System 1. 고정 익절 없이 직전 10거래일 최저가 이탈 시 전량청산
- `ma_staged`: 조기 수익보호용 변형. MA5 이탈 시 50%, MA10 이탈 시 잔여 포지션 정리
- 어느 모드든 `최근 체결 Unit - 2N` 보호손절과 10D Low 전량청산이 이동평균 기준보다 우선

risk 방식은 개인용 보수적 가이드이며 원조 futures contract sizing 전체를 복제하는 모델이 아닙니다.

## Data provider

`DATA_PROVIDER=auto` 정책:

```text
KIS credentials 있음: KIS → Naver → optional KRX → Demo
KIS credentials 없음: Naver → optional KRX → Demo
```

fallback은 일봉과 현재가를 같은 provider에서 성공시킨 뒤 하나의 snapshot으로 반환합니다. 서로 다른 공급자의 데이터를 섞지 않습니다.

- `DemoMarketDataProvider`: 키 없는 UI/API 테스트
- `NaverMarketDataProvider`: Vercel 소수 watchlist 탐색
- `NaverUniverseProvider`: KOSPI/KOSDAQ 상장주식 전체 페이지(ETF/ETN 제외, 영문 혼합 종목코드 포함)와 최근 확정 연간 영업이익
- `DemoUniverseProvider`: 키 없는 전체검색 API/UI 테스트
- `KrxMarketDataProvider`: `pykrx` 선택 fallback
- `KisMarketDataProvider`: Oracle 실전 신호 우선 REST 데이터
- `FallbackMarketDataProvider`: 동일 provider snapshot 단위 fallback

Naver/KRX는 탐색·테스트용입니다. 실전 Oracle 판단은 KIS를 기준으로 운영하세요.

Naver 현재가는 정규장 live quote를 사용하고, NXT 프리/애프터마켓 세션이 실제 거래 중이면 NXT 체결가를 우선합니다. 과거 DEMO로 저장된 추적 설정은 화면 재접속 시 AUTO 실제 시세로 전환하며, 사용자가 입력해야 하는 Entry 체결가와 ATR은 임의로 바꾸지 않습니다.

현재가와 D-1 종가의 차이가 국내 일일 가격제한폭을 명백히 넘으면 액면분할·병합 미반영 가능성이 있으므로 해당 snapshot의 돌파/ATR/손절 계산을 건너뜁니다. 실제 현재가만 맞고 과거 가격 스케일이 다른 상태에서 거짓 BREAKOUT을 만드는 것을 방지하기 위한 안전장치입니다.

## KOSPI/KOSDAQ 전체 PREALERT 검색

전체검색은 Naver의 KOSPI/KOSDAQ 시가총액 목록을 마지막 페이지까지 조회하되 ETF/ETN은 제외합니다. 시가총액·영업이익을 먼저 적용하므로 모든 종목의 차트를 무조건 요청하지 않습니다.

```text
KOSPI/KOSDAQ 종목목록
→ 최소 시가총액(억원)
→ 최근 확정 연간 영업이익(억원, 컨센서스 제외)
→ 통과 종목만 KIS/Naver 일봉·현재가 조회
→ PREALERT 또는 당일 첫 BREAKOUT
→ 선택 필터: 10D 평균거래대금 / 당일 외인·기관 수급 / BREAKOUT 당일 상승률
→ SQLite 결과 snapshot
```

기본값은 시가총액 500억원 이상, 영업이익 50억원 이상, 10일 평균거래대금 500억원 이상이며 UI에서 숫자를 바꿀 수 있습니다. 각 선택 필터의 `×`를 누르면 그 조건을 제외할 수 있습니다. 외인/기관은 각각 또는 합산 순매수액을 설정할 수 있고, `0억원`은 순매수 여부만 확인합니다. Naver 수급액은 순매수수량×가격의 추정값이며 KIS 공식 투자자 데이터는 장 종료 후 확정되는 데이터입니다. ETF·ETN은 종목목록 단계에서 제외하고, 신규·우선주에 쓰이는 영문 혼합 6자리 종목코드는 포함합니다. 화면에는 KOSPI/KOSDAQ 원천 상장주식 수와 각 필터 통과 수를 함께 표시합니다. 재무값은 기본 7일 캐시하고 시세 신호는 새 검색 때 다시 계산합니다.

PREALERT 접근률은 기본 1%이고 숫자로 변경할 수 있습니다. BREAKOUT은 `현재가 >= 직전 완료 20거래일 High`이면서 어제 이미 돌파하지 않은, 오늘 최초 돌파만 반환합니다. BREAKOUT의 `당일 5% 이상` 필터는 선택 사항이며 제거하면 상승률과 관계없이 정상 20D 돌파를 찾습니다.

전체검색은 로컬/Oracle에서는 진행률을 저장하는 장기 작업입니다. Vercel에서는 background thread를 실행하지 않고, 사용자가 버튼을 누른 단일 요청 안에서 수동 1회 검색합니다. 완료 결과는 해당 브라우저 `localStorage`에 보관합니다.

## 로컬 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
APP_MODE=oracle DATA_PROVIDER=demo .venv/bin/uvicorn api.index:app --host 127.0.0.1 --port 8000
```

브라우저: `http://127.0.0.1:8000`

검증:

```bash
.venv/bin/python -m pytest -q
curl -s http://127.0.0.1:8000/api/health
curl -s "http://127.0.0.1:8000/api/candidates?provider=demo&symbols=000660"
curl -s "http://127.0.0.1:8000/api/guide?provider=demo"
curl -s -X POST http://127.0.0.1:8000/api/full-market-scans \
  -H 'Content-Type: application/json' \
  -d '{"provider":"demo","market":"ALL","min_market_cap_100m":500,"min_operating_profit_100m":50}'
```

## UI 동작

- `↻ 새로고침`: 현재 범위의 후보 snapshot과 저장된 추적종목 가이드를 각각 한 번 조회. 전체 상장주식 목록·재무·신호를 처음부터 다시 계산하지 않음
- `실시간`: ON이면 초록색 `● 검색중`, 기본 30초 browser polling
- poll 간격: `REALTIME_POLL_SECONDS`
- 중복 fetch cycle 방지
- 후보 행 클릭: 상세·Entry/ATR 기본값 표시. 종목명 클릭 시 `stock.naver.com` 해당 종목을 새 탭으로 엶. 저장된 추적 종목은 자동 후보 선택으로 덮어쓰지 않음
- 검색 범위: `KOSPI/KOSDAQ 전체` 또는 `직접 입력 종목`
- PREALERT/BREAKOUT 버튼: 신호를 전환하고 전체시장에서는 새 검색 실행
- 전체시장 필터: 시장, 최소 시가총액, 최소 영업이익, 10D 평균거래대금, 외인/기관 수급, BREAKOUT 당일 상승률
- 표 머리글: 첫 클릭 내림차순, 두 번째 클릭 오름차순
- `전체시장 새 검색`: 로컬/Oracle에서 KOSPI/KOSDAQ 상장주식 목록부터 시총·영업이익·선택 필터·신호를 전부 다시 계산하고 진행률 표시
- Vercel의 `전체시장 새 검색`: 클릭한 요청 안에서 Naver 전체시장을 수동 1회 계산하고 결과를 브라우저에 보관. background worker나 반복 전체검색은 실행하지 않음
- 전체검색 중복 실행 방지, 일반 실시간 polling은 마지막 snapshot만 재조회
- Vercel 상태: `localStorage`
- `진입/추매 완료`: 확인창 이후에만 `filled_units` 1 증가
- 매도 전략: 정통 10D Low 전량청산 또는 MA5 50%/MA10 잔여 분할 가이드

worker는 가격 조건이 충족되어도 `filled_units`를 자동 변경하지 않습니다.

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | UI |
| GET | `/api/health` | provider chain, poll 설정 |
| GET | `/api/candidates` | watchlist 또는 저장된 전체시장 후보 snapshot |
| POST | `/api/full-market-scan-once` | Vercel용 수동 1회 전체시장 검색 |
| POST | `/api/full-market-scans` | 로컬/Oracle 전체시장 검색 시작 |
| GET | `/api/full-market-scans/{id}` | 검색 진행률·결과 |
| POST | `/api/scan` | 완료 일봉 직접 분석 |
| GET/POST | `/api/guide` | 선택종목 행동 가이드 |
| GET/POST | `/api/positions` | Oracle position state |
| POST | `/api/positions/{symbol}/confirm-fill` | 사용자의 체결 수동 확정 |

행동 상태는 `WAIT_ENTRY`, `ENTRY_NOW`, `HOLD`, `ADD_NOW`, `STOP_NOW`, `EXIT_NOW`입니다.

Quality Score는 다음 지표로 후보를 정렬하기 위한 정보이며 돌파가를 수정하지 않습니다.

- 20일 평균거래대금
- `현재가 > MA20 > MA60`
- `MA60 > MA120`
- RS20 / RS60
- ATR%
- 52주 신고가 거리
- 거래량 강도

## Vercel

GitHub 저장소 root를 Vercel에서 Import하면 됩니다. `pyproject.toml`의 `[tool.vercel] entrypoint = "api.index:app"`이 FastAPI 진입점을 명시합니다. `vercel.json`은 `index.html` 정적 UI와 `api/index.py` Python 함수를 각각 빌드하고 `/api/*`를 FastAPI로 전달합니다. 따라서 Vercel 프로젝트가 `Other` 프리셋으로 잡혀도 API 함수가 누락되지 않습니다.

Vercel에서도 `전체시장 새 검색`을 누르면 Naver 전체 상장주식 목록을 기준으로 수동 1회 검색합니다. 서버리스 background 작업은 만들지 않으며, 응답이 끝날 때 후보 결과를 브라우저에 저장합니다. 최초 검색은 재무·일봉 요청이 많아 오래 걸릴 수 있습니다. 제한 시간에 걸리면 시가총액·영업이익·거래대금 기준을 높여 대상 수를 줄인 뒤 다시 실행하는 것이 안전하며, 지속적인 전 종목 감시는 Oracle + KIS worker를 사용합니다.

권장 환경변수:

```text
APP_MODE=vercel
DATA_PROVIDER=auto
REALTIME_POLL_SECONDS=30
ENABLE_KRX_FALLBACK=0
```

KIS 키 없이도 Naver 실패 시 Demo까지 fallback되어 화면이 열립니다. `↻ 새로고침`은 마지막 수동 전체검색 결과 또는 직접 입력 종목과 선택 종목 가이드를 다시 표시·조회하고, `전체시장 새 검색`만 전 종목 계산을 새로 시작합니다. Vercel에는 worker, 영구 loop, WebSocket, SQLite 영속화를 두지 않습니다. 전체검색 중의 `/tmp` SQLite는 동일 요청의 임시 재무 캐시일 뿐이며, 결과는 브라우저에 저장됩니다. Preview에서 `/`, `/api/health`, `/api/candidates?provider=demo`, `/api/full-market-scan-once`(POST, demo), `/api/guide?provider=demo`를 확인하세요.

여러 브라우저에서 동일한 전체시장 최신 결과를 공유하려면 Oracle scanner 결과를 PostgreSQL/Supabase 등 외부 영속 store로 옮기고 `full_market_scans` repository adapter를 연결하세요. 기본 공유 저장소는 로컬/Oracle SQLite이며, Vercel 수동 결과는 실행한 브라우저에만 남습니다.

## Oracle + KIS + Telegram

```bash
cp .env.example .env
# .env에 실제 값을 넣되 commit하지 않음
docker compose up -d --build

# 필요할 때 KOSPI/KOSDAQ 전체 1회 검색
docker compose --profile full-scan run --rm turtle-scanner
```

필수 운영 설정:

```text
APP_MODE=oracle
DATA_PROVIDER=auto
KIS_APP_KEY=...
KIS_APP_SECRET=...
NOTIFY_PROVIDER=telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
DB_PATH=./data/turtle.db
```

`oracle.worker`는 기본 polling worker입니다. PREALERT, BREAKOUT, ADD, STOP, EXIT를 SQLite event key로 한 번만 Telegram 전송합니다. 메시지는 현재가, 조건가, next add, stop, exit, 제안 수량·금액, risk budget을 포함합니다.

`oracle.scanner`는 시총·영업이익 필터 뒤 전체시장 신호를 계산하고 같은 dedup 규칙으로 Telegram을 전송합니다. `FULL_SCAN_INTERVAL_SECONDS=0`은 1회 실행이며, cron/Oracle scheduler로 장중 주기를 관리하는 방식을 권장합니다. 체결 Unit은 scanner/worker 어느 쪽도 자동 증가시키지 않습니다.

포지션 영속화는 `app.state.PositionStateStore` 경계 뒤에 있습니다. 현재 `SqlitePositionStateStore`가 구현되어 있고 API/worker 모두 이 인터페이스를 사용하므로, PostgreSQL adapter는 전략·알림 코드를 바꾸지 않고 교체할 수 있습니다.

`app.realtime.KisRealtimeWebSocketAdapter`는 KIS WebSocket approval key와 국내주식 실시간체결가 `H0STCNT0` 구독을 구현한 **읽기 전용 adapter skeleton**입니다. 재접속/backoff, 장 시작·종료, 구독 제한, 일봉 캐시 갱신을 운영 환경에 맞게 보강한 뒤 별도 event-driven runner에 연결하세요.

## 테스트 범위

- today 제외 20D breakout / PREALERT 1% / BREAKOUT
- yesterday breakout 제외
- ATR20 / 10D exit
- 10D 평균거래대금 / BREAKOUT 당일 상승률 / 투자자 수급 선택 필터
- 0.5N / 1.0N / 1.5N add
- fixed / risk quantity
- Unit별 실제 수량 합
- common stop ratchet
- WAIT / ENTRY / ADD / STOP / EXIT
- 정통 10D Low 매도 / MA5·MA10 분할 매도 / 보호손절 우선순위
- SQLite signal dedup / 수동 fill 확정
- provider snapshot 일관성
- Naver NXT 거래 세션 현재가 선택
- 전체시장 시가총액 filter와 최신 확정 영업이익 선택
- 재무 filter 선적용 후 시세 history 조회
- 전체시장 background scan API와 snapshot 조회
- Vercel 필수 route smoke

## 남은 운영 TODO

- KIS 120/252 거래일 history cache와 pagination 운영 검증
- KIS WebSocket reconnect/backoff 및 장중 soak test
- PostgreSQL `PositionStateStore` adapter와 인증된 Oracle 관리 UI
- Naver universe 응답 변경 감시와 KIS 호출 rate-limit 운영 튜닝
- 전체검색 snapshot용 PostgreSQL adapter 및 Vercel read path
- Telegram 운영 chat에서 end-to-end 알림 검증
- 휴장일/관리종목/거래정지 데이터 정책

## Secret

실제 `.env`, KIS key/secret, 계좌번호, Telegram token/chat id를 commit하지 마세요. `.env.example`에는 빈 placeholder만 있습니다.
