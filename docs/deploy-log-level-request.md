# 배포 환경변수 요청 — `LOG_LEVEL` 전달 — 인계 문서

작성일 2026-07-28. 배경·설계: [schema-mismatch-logging.md](schema-mismatch-logging.md)

컨테이너에 `LOG_LEVEL`을
전달하는 줄 추가. 현재 배포 환경은 로그 레벨 조정 수단이 없고 항상 기본 `INFO`로 뜬다.

| # | 대상 | 저장소 | 형태 | 상태 |
|---|---|---|---|---|
| 0 | 스키마 불일치 진단 로그 · `LOG_LEVEL` 지원 | chok-v2-ai-backend | PR | 완료(코드·문서·테스트) |
| 1 | `fastapi` 서비스에 `LOG_LEVEL` 전달 줄 추가 | **chok-v2-deploy** | **PR(수동)** | **요청** |ㅇ

## 요청 내용 — 2줄

### 1-1. `docker-compose.yml` — `fastapi.environment`

`RCA_WORKER_CONCURRENCY` 다음 줄에 추가:

```yaml
     # 로그 레벨(DEBUG/INFO/WARNING/ERROR). 이 줄이 없으면 기본값인 INFO로 설정됨.
     LOG_LEVEL: ${LOG_LEVEL:-INFO}
```

기본값 `INFO`를 둬 미설정 상태에서도 기동에 영향이 없다(`:?` 아님).

### 1-2. `.env.example`

`RCA_WORKER_CONCURRENCY=2` 다음에 추가 — 배포 시작이 `cp .env.example .env`이므로, 이 항목이
없으면 무엇을 설정할 수 있는지 알 방법이 없다.

```dotenv
# FastAPI log level (DEBUG/INFO/WARNING/ERROR).
LOG_LEVEL=INFO
```

## 검증

로컬에서 `docker compose config` 렌더 확인 완료(더미 `.env` 사용):

| `.env` | 렌더 결과 |
|---|---|
| 미설정 | `LOG_LEVEL: INFO` |
| `LOG_LEVEL=DEBUG` | `LOG_LEVEL: DEBUG` |

배포 후 확인:

```bash
docker compose exec fastapi env | grep LOG_LEVEL   # 컨테이너 도달 확인
docker compose logs fastapi | head                 # 포맷: 시각 LEVEL 로거명 | 메시지
```

## 영향·롤백

- 기본값이 현행 동작과 같은 `INFO`이므로 **값 미설정 시 무영향**.
- 롤백 = `.env`에서 `LOG_LEVEL` 줄 제거 후 재기동. 이미지·코드 변경 불필요.
- AWS 주입 방식이 compose가 아닌 경로(예: ECS task definition)로 확정되면, 같은 요건이
  그 경로에 적용된다 — 주입 경로에 `LOG_LEVEL` 키 등록.

## DEBUG 사용 시 주의

`INFO`에서 스키마 불일치·전송 실패 로그는 모두 보인다(`WARNING` 이상). `DEBUG`가 필요한
경우는 드물고, 부작용이 크다.

- 앱 자체 `logger.debug`는 2곳뿐 — 얻는 정보가 거의 없다.
- 루트가 `DEBUG`로 내려가면 **SQLAlchemy가 SQL과 바인딩 파라미터를 기록**한다. 파라미터에
  `job.bundle` JSON이 실리므로 로그가 번들 사본이 된다(부피·유출).
  [schema-mismatch-logging.md](schema-mismatch-logging.md)에서 로그에 입력값을 일부러 제외한
  방침과 어긋난다.
- `httpx`·`httpcore`는 `WARNING` 고정([logging_config.py](../app/core/logging_config.py) `_NOISY`)이라
  Spring·OpenAI HTTP 상세는 `DEBUG`로도 열리지 않는다.

권고 — 전역 `DEBUG` 대신 필요한 로거만 내리는 방식. 대상 로거가 정해지면 FastAPI 쪽 수정으로
처리한다(별건).

## 참고 — 현재 compose가 전달하지 않는 나머지 노브

지금 요청 대상 아님. 조정이 필요해질 때 같은 방식으로 한 줄씩 추가하면 된다.

| 환경변수 | 기본값 | 비고 |
|---|---|---|
| `SPRING_SIGNAL_LIMIT` | 200 | Spring 전송 모달리티별 항목 상한 |
| `DB_AUTO_CREATE` | false | 운영은 Alembic 소유 — 기본값 유지가 정답 |
| `DB_POOL_RECYCLE_SECONDS` | 3600 | **공유 MySQL의 `wait_timeout`이 이보다 짧으면 낮춰야 함** |
| `DB_CONNECT_TIMEOUT_SECONDS` | 10 | |
| `STUCK_JOB_AFTER_SECONDS` / `_INTERVAL_SECONDS` | 1500 / 300 | 중단 job 회수 임계 |
| `MAX_JOB_REQUEUE` | 1 | 재투입 허용 횟수 |
| `BUNDLE_ORPHAN_MAX_AGE_HOURS` | 24 | 고아 원본 파일 회수 기준 |
| `LLM_TIMEOUT_LOW`/`MEDIUM`/`HIGH_SECONDS` | 60 / 180 / 300 | |
| `RCA_OVERALL_TIMEOUT_SECONDS` | 600 | `STUCK_JOB_AFTER_SECONDS`와 연동 — 함께 조정 |
