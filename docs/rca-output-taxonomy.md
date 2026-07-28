# RCA 출력 정형화 — type enum · service 앵커링 · confidence 제거

작성일 2026-07-28. 브랜치 `feat/rca-output-taxonomy`.
RCA 산출물의 `type`·`service` 값을 고정·앵커링하고 미사용 `confidence`를 제거한 사양.

## 배경

자유문자열 `type`(`Svc_Kill`·`Perf_Contention` 등 임의 라벨)과 말단 서비스로 새는 `service` 때문에 프론트 매핑·집계가 불안정. 하네스 베이스라인(1/3)의 오답 2건이 정확히 이 지점.

## type — 고정 6종 enum

`ReportDraft.type: Literal[...]`로 강제(`app/agents/schemas.py`의 `RcaType`). `with_structured_output`이 값 자체를 보장하고, 프롬프트(`report.md` 지침 4)는 의미·선택 기준을 설명. 선택 우선순위: **다운 > 코드중단 > 성능 > 의존**.

| 값 | 의미 |
|---|---|
| `SERVICE_DOWN` | 서비스 프로세스/인스턴스 사망·무응답(컨테이너 kill·OOM 종료 등) |
| `CODE_STOP` | 프로세스는 생존하나 코드 결함으로 처리 중단(예외·배포 버그·무한루프) |
| `PERFORMANCE` | 자원 경합·포화로 지연·타임아웃(CPU·메모리·커넥션풀). 다운 아님 |
| `DEPENDENCY` | 외부/하위 의존(DB·큐·써드파티) 장애 전파 |
| `OTHER` | 위 어디에도 안 맞는 장애 |
| `NONE` | 장애 근거 불충분·정상 |

`RcaResult.type`은 **`str` 유지**(Spring 계약 non-breaking) — 값만 위 6종으로 고정.

## service — 데이터 앵커링

`assemble()`: `trace.origin_service → draft.service → "UNKNOWN"`(`app/agents/report_llm.py`). 프롬프트: 데이터 등장명 그대로, 말단 프록시(nginx 등) 진원 승격 금지, 불명·상충 시 리터럴 `UNKNOWN`.

## confidence — 제거

`Rca.confidence` 삭제(`app/schemas/contracts.py`). 생성·Spring 전송되던 값이나 프론트 계약(`chok-v2-react-frontend` `src/types/reportDetail.ts` `RcaData`)에 필드 부재 → 미표시, 프롬프트에 척도 정의도 없어 모델이 임의로 찍던 값. 재도입 시 프론트 계약·척도를 먼저 정의.

## Spring 통지 (값 집합 공유)

- `result.type` — 타입은 문자열 그대로이나 **값이 위 6종으로 고정**. 프론트 표시 라벨·필터·집계 매핑을 이 집합에 맞춰 갱신.
- `result.service` — 불명 시 `"UNKNOWN"`이 올 수 있음(신규 상수값).
- `detail.rca.confidence` — **제거**. 더 이상 전송되지 않음(프론트 미사용이라 breaking 아님).

## 측정 (하네스 before/after)

고정 픽스처 3종에 대한 정답률. (Task 5 실행 후 수치·set_hash 기입.)

| 시나리오 | 정답(type/service) | before | after |
|---|---|---|---|
| cpu | PERFORMANCE / (service 무관) | O | _TBD_ |
| kill_media | SERVICE_DOWN / media | X | _TBD_ |
| code_media | CODE_STOP / media | X | _TBD_ |

before set_hash `8f9de8214700` (1/3). after set_hash: _TBD_.
