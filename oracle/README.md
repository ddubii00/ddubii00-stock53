# Oracle 운영 구조 (v0.4)

Vercel은 화면/공개데이터 테스트용이며, 실제 장중 감시는 Oracle + KIS를 기본으로 합니다.

## 권장 구성

- `turtle-api`: FastAPI + 웹 UI API
- `turtle-worker`: 후보/보유종목 반복 감시
- `turtle-scanner`: KOSPI/KOSDAQ 전체를 시총·확정 영업이익으로 먼저 거른 뒤 PREALERT/BREAKOUT 계산

전체시장 1회 검색:

```bash
docker compose --profile full-scan run --rm turtle-scanner
```

`FULL_SCAN_INTERVAL_SECONDS`가 0이면 한 번 실행하고 종료합니다. Oracle scheduler/cron에서 위 명령을 장중 주기적으로 호출하거나 양수로 설정해 반복할 수 있습니다. 검색 결과와 재무 캐시는 API와 같은 SQLite volume에 저장됩니다.
- `KIS`: 실전 데이터 1순위
- `Telegram`: PREALERT / BREAKOUT / ADD / STOP / EXIT 알림
- `SQLite`: 초기 상태/중복신호 저장 (`DB_PATH`)
- 이후 PostgreSQL로 교체 가능

현재 `oracle.worker`는 안정적인 polling worker입니다. `ORACLE_POLL_SECONDS=20`이면 20초마다 감시합니다. `app.realtime.KisRealtimeWebSocketAdapter`는 KIS `H0STCNT0` 체결가를 읽는 확장용 skeleton이며 주문 API는 포함하지 않습니다.

## 실행

```bash
cp .env.example .env
# .env에 Oracle 서버의 기존 KIS/Telegram 값 설정

docker compose up -d --build
```

`.env` 예:

```text
APP_MODE=oracle
DATA_PROVIDER=auto
KIS_APP_KEY=...
KIS_APP_SECRET=...
NOTIFY_PROVIDER=telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ORACLE_POLL_SECONDS=20
WATCHLIST=000660,005930
DB_PATH=./data/turtle.db
```

`DATA_PROVIDER=auto`에서 KIS 키가 있으면 KIS가 가장 먼저 사용됩니다. 실패 시 Naver/KRX(optional)/Demo fallback 구조입니다.

## 포지션 신호

DB에 ACTIVE position이 등록되면 worker가 다음을 계산합니다.

- 다음 추가매수: `Entry + 0.5N`, `+1.0N`, `+1.5N`
- 공통 보호손절: 가장 최근 체결 Unit 가격 - `2N`
- System 1 Exit: 직전 10거래일 Low 최저값 이탈
- 추가매수/손절/청산 발생 시 동일 event는 1회만 Telegram 전송

실제 주문은 실행하지 않습니다. worker는 가격이 ADD level에 도달해도 `filled_units`를 변경하지 않습니다. 사용자가 실제 체결 후 `POST /api/positions/{symbol}/confirm-fill`을 호출하거나 UI의 `진입/추매 완료` 버튼으로 확정해야 합니다.

## WebSocket 확장 전 체크

- `requirements-oracle.txt` 설치
- approval key 발급과 `KIS_WEBSOCKET_URL` 연결 확인
- 재접속 exponential backoff, 장 종료 처리, 구독 수 제한 구현
- 완료 일봉 cache를 자정이 아니라 KRX 거래일 종료 기준으로 갱신
- polling worker와 WebSocket runner를 동시에 켜지 않거나 동일 event DB를 공유
