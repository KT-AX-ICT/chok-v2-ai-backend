# 스키마 불일치 진단 로그

작성일 2026-07-28. 브랜치 `feat/schema-mismatch-logging`.
스키마 불일치 발생 시 **어느 필드가 왜 틀렸는지** 서버 로그로 판별 가능하게 하는 변경.

## 배경 — 검증 경계 3곳의 진단 공백

| 경계 | 검증 주체 | 변경 전 로그 | 판정 |
|---|---|---|---|
| SDK → FastAPI `POST /ingest` | `IngestBundle` (FastAPI 자동) | access log `422 Unprocessable Entity` 한 줄. 필드 정보는 **응답 본문에만** | 공백 |
| RCA 산출물 → `RcaResult` (job 경계) | [rca_validation.py](../app/services/rca_validation.py) | 필드 경로만 (`불일치 필드: detail.actions`) | 부분 |
| FastAPI → Spring `/api/internal/reports` | Spring 측 | 4xx 응답 본문 500자 ([spring_client.py](../app/services/spring_client.py)) | Spring 형식 의존 |

1번이 실질 공백. `RequestValidationError` 핸들러가 없어 FastAPI 기본 422가 필드 정보를
응답 본문으로만 반환하고, SDK가 그 본문을 로깅하지 않으면 단서가 소멸한다. 본문이 수 MB
번들이라 사후 재현도 불가. [ingest.py](../app/api/ingest.py)의 수신 로그는 검증 통과
이후 실행이라 실패 요청에는 도달하지 않는다.

2번은 필드 경로만 남아 누락(`missing`)과 타입 불일치 구별 불가.

## 변경 내역

### 1. 공용 포맷 — [app/core/schema_errors.py](../app/core/schema_errors.py) *(신규)*

`summarize_validation_errors(errors, *, limit)` — pydantic `ValidationError.errors()` →
로그 한 줄.

```
필드경로[오류코드] 사유 | 필드경로[오류코드] 사유 | (…외 N건 생략, 총 M건)
```

- 수록: `loc` · `type` · `msg`
- 제외: `input`(입력값 원본) · `ctx`
- 상한: `MSG_MAX = 120`(사유 길이) · `MAX_ERRORS = 20`(건수, 초과 시 생략 건수 명시)

세 경계가 같은 포맷을 공유 — 로그를 읽는 절차를 한 벌로 유지.

### 2. 요청 422 서버 로그 — [app/core/error_handlers.py](../app/core/error_handlers.py) *(신규)*

`RequestValidationError` 핸들러 등록([main.py](../app/main.py)). 사유를 `WARNING`으로
남기고 **응답 생성은 FastAPI 기본 핸들러에 위임**.

```
WARNING app.core.error_handlers | 요청 스키마 불일치 422: POST /ingest (본문 24 bytes)
  — body.window[missing] Field required | body.triggerInfo[missing] Field required
```

본문 크기는 `content-length` 헤더 값 그대로(재직렬화 비용 0) — 대용량 요청이 원인인지 판별용.

### 3. RCA 산출물 사유 보강 — [app/services/rca_validation.py](../app/services/rca_validation.py)

```
변경 전: RcaResult 스키마 불일치 필드: detail.actions
변경 후: RcaResult 스키마 불일치: detail.actions[missing] Field required
```

전파 경로 불변 — `job.error`(DB 전문 저장) → `GET /ingest/{job_id}`의 `error` →
Spring 실패 페이로드 `reason`. 공용 포맷의 건수·길이 상한이 이 경로 전체에 적용된다.

## 로그 읽는 법

- 검색 키워드: `요청 스키마 불일치` (1번) · `RcaResult 스키마 불일치` (2번)
- `body.` 접두 = 요청 본문. 배열 원소는 인덱스 포함 — `body.logs.0.timestamp`
- 필드 경로는 **계약 별칭(camelCase)** 표기. `populate_by_name=True`라 입력은
  snake/camel 양쪽을 받지만 `loc`은 별칭으로 나온다 — 422 응답 본문의 `loc`과 동일 표기라
  응답·로그 대조 가능. SDK가 `trigger_info`로 보내도 로그는 `body.triggerInfo`
- 오류 코드: `missing`(누락) · `string_type`/`int_parsing`(타입) · `literal_error`(허용값
  위반) · `value_error`(커스텀 검증기 — 타임스탬프 형식 등)
- `(…외 N건 생략)` 문구 = 전량 아님

## 로그 레벨

이번 변경으로 추가된 로그는 `WARNING`(요청 422). 기존 지점은 `WARNING`(RCA 시도별 실패,
Spring 5xx) / `ERROR`(RCA 최종 실패 — 스택 포함, Spring 4xx·401). 기본값 `INFO`이므로
전부 노출되고, `WARNING`으로 올려도 스키마 불일치 로그는 남는다(정상 수신 `INFO`만 사라짐).

설정 위치 — 기본값 [config.py](../app/core/config.py) `log_level`, 적용
[logging_config.py](../app/core/logging_config.py) `setup_logging()`, 로컬 `.env`의
`LOG_LEVEL`([.env.example](../.env.example)에 항목 추가).

미해결 — 배포 환경은 현재 `LOG_LEVEL` 조정 수단이 없다(항상 기본 `INFO`).

- `chok-v2-deploy`의 compose `fastapi` 서비스에 `LOG_LEVEL` 전달 줄이 없다(`env_file`도
  없음). 배포 설정 이관(#18, `37d3681`)에서 옛 `docker-compose.deploy.yml`의
  `LOG_LEVEL: ${LOG_LEVEL:-INFO}`가 따라가지 않은 유실.
- AWS 실배포의 환경변수 주입 방식은 별도 확정 대상(chok-v2-deploy README: "PR 병합 이후의
  실제 배포 방식은 AWS 구성 확정 후 별도로 작성").

방식과 무관한 조건 — 앱은 프로세스 환경변수 `LOG_LEVEL`을 읽으므로(pydantic-settings,
접두어 없음) **주입 경로에 키가 등록**돼야 한다. compose 경유면 `environment:` 줄,
ECS 등이면 task definition의 환경변수 항목. 이 레포 [.env.example](../.env.example)의
항목은 "설정해야 할 값" 목록일 뿐 주입 경로가 아니다.

복구는 별 레포 작업이므로 요청 절차를 분리했다 — [deploy-log-level-request.md](deploy-log-level-request.md).
서비스 CI 자동화는 이미지 digest만 갱신하므로(`reusable-docker-build.yml`의 `sed`) 수동 PR이 필요하다.

## 설계 판단

| 항목 | 결정 | 근거 |
|---|---|---|
| 422 응답 형식 | 기본 핸들러 위임(무변경) | SDK 오류 처리와 맞물린 계약 — 로깅 추가가 형식을 건드릴 이유 없음 |
| `input` 값 | 제외 | 번들 본문(수천 건 배열·원본 로그 라인)이 로그로 복사됨 → 부피·유출 표면 |
| `msg` | 포함 + 길이 상한 | 값 자체가 진단 정보인 경우 존재([contracts.py](../app/schemas/contracts.py) `_valid_iso8601`이 값을 메시지에 넣음) |
| 건수 상한 | 20 + 총계 표기 | 배열 전량 불일치 시 오류도 수천 건. 잘림을 은닉하지 않음 |
| 배치 | `app/core/` | 세 경계 공용 유틸 — 특정 서비스 소유 아님 |
| 로그 레벨 | `WARNING` | 클라이언트 측 오류(서버 결함 아님)이나 무시 대상도 아님 |

## 비목표

- **Spring 전송 페이로드의 우리 쪽 필드 매핑** — 3번 경계의 필드 정보는 Spring 응답 본문
  형식에 의존(통제 밖). 필요 시 전송 직전 자체 검증(Spring 계약 모델 보유)이 별건 과제.
- **요청 본문 원본 저장** — 재현에는 유효하나 유출·용량 위험이 이득을 상회.
- **응답 본문 커스터마이즈** — 위 표 참조.

## 테스트

| 파일 | 건수 | 범위 |
|---|---|---|
| [tests/test_schema_errors.py](../tests/test_schema_errors.py) | 10 | 경로 결합·오류 코드·`input` 미수록·길이/건수 상한·빈 목록 |
| [tests/test_validation_logging.py](../tests/test_validation_logging.py) | 5 | 422 경로 로그 기록·메서드/경로 표기·배열 인덱스·**응답 형식 불변**·정상 요청 무로그 |
| [tests/test_rca_validation.py](../tests/test_rca_validation.py) | +2 | 오류 코드 포함·누락 ↔ 타입 불일치 구별 |

전체 회귀 `uv run pytest` — 208 passed.
