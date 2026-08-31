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
- `KrxMarketDataProvider`: `pykrx` 선택 fallback
- `KisMarketDataProvider`: Oracle 실전 신호 우선 REST 데이터
- `FallbackMarketDataProvider`: 동일 provider snapshot 단위 fallback

Naver/KRX는 탐색·테스트용입니다. 실전 Oracle 판단은 KIS를 기준으로 운영하세요.

## 로컬 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
DATA_PROVIDER=demo .venv/bin/uvicorn api.index:app --host 127.0.0.1 --port 8000
```

브라우저: `http://127.0.0.1:8000`

검증:

```bash
.venv/bin/python -m pytest -q
curl -s http://127.0.0.1:8000/api/health
curl -s "http://127.0.0.1:8000/api/candidates?provider=demo&symbols=000660"
curl -s "http://127.0.0.1:8000/api/guide?provider=demo"
```

## UI 동작

- `↻ 새로고침`: 후보와 저장된 추적종목 가이드를 각각 한 번 조회
- `실시간`: ON이면 초록색 `● 검색중`, 기본 10초 browser polling
- poll 간격: `REALTIME_POLL_SECONDS`
- 중복 fetch cycle 방지
- 후보 행 클릭: 상세·Entry/ATR 기본값 표시
- Vercel 상태: `localStorage`
- `진입/추매 완료`: 확인창 이후에만 `filled_units` 1 증가

worker는 가격 조건이 충족되어도 `filled_units`를 자동 변경하지 않습니다.

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | UI |
| GET | `/api/health` | provider chain, poll 설정 |
| GET | `/api/candidates` | watchlist 후보 |
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

GitHub 저장소 root를 Vercel에서 Import하면 됩니다. `api/index.py`의 `app`이 FastAPI entrypoint이고 root `index.html`은 정적 UI입니다.

권장 환경변수:

```text
APP_MODE=vercel
DATA_PROVIDER=auto
REALTIME_POLL_SECONDS=10
ENABLE_KRX_FALLBACK=0
```

KIS 키 없이도 Naver 실패 시 Demo까지 fallback되어 화면이 열립니다. Vercel에는 worker, 영구 loop, WebSocket, SQLite 영속화를 두지 않습니다. Preview에서 `/`, `/api/health`, `/api/candidates?provider=demo`, `/api/guide?provider=demo`를 확인하세요.

## Oracle + KIS + Telegram

```bash
cp .env.example .env
# .env에 실제 값을 넣되 commit하지 않음
docker compose up -d --build
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

포지션 영속화는 `app.state.PositionStateStore` 경계 뒤에 있습니다. 현재 `SqlitePositionStateStore`가 구현되어 있고 API/worker 모두 이 인터페이스를 사용하므로, PostgreSQL adapter는 전략·알림 코드를 바꾸지 않고 교체할 수 있습니다.

`app.realtime.KisRealtimeWebSocketAdapter`는 KIS WebSocket approval key와 국내주식 실시간체결가 `H0STCNT0` 구독을 구현한 **읽기 전용 adapter skeleton**입니다. 재접속/backoff, 장 시작·종료, 구독 제한, 일봉 캐시 갱신을 운영 환경에 맞게 보강한 뒤 별도 event-driven runner에 연결하세요.

## 테스트 범위

- today 제외 20D breakout / PREALERT 1% / BREAKOUT
- yesterday breakout 제외
- ATR20 / 10D exit
- 0.5N / 1.0N / 1.5N add
- fixed / risk quantity
- Unit별 실제 수량 합
- common stop ratchet
- WAIT / ENTRY / ADD / STOP / EXIT
- SQLite signal dedup / 수동 fill 확정
- provider snapshot 일관성
- Vercel 필수 route smoke

## 남은 운영 TODO

- KIS 120/252 거래일 history cache와 pagination 운영 검증
- KIS WebSocket reconnect/backoff 및 장중 soak test
- PostgreSQL `PositionStateStore` adapter와 인증된 Oracle 관리 UI
- 전체 KOSPI/KOSDAQ universe 수집·rate limit·배치 스케줄
- Telegram 운영 chat에서 end-to-end 알림 검증
- 휴장일/관리종목/거래정지 데이터 정책

## Secret

실제 `.env`, KIS key/secret, 계좌번호, Telegram token/chat id를 commit하지 마세요. `.env.example`에는 빈 placeholder만 있습니다.
