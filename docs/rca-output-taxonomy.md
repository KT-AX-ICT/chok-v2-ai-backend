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

고정 픽스처 3종에 대한 정답률. before set_hash `8f9de8214700`, after set_hash `743c2fb15d0e`(report_model gpt-5.5).

| 시나리오 | 정답(type/service) | before | after(pred) |
|---|---|---|---|
| cpu | PERFORMANCE / (service 무관) | O | **O** — PERFORMANCE / UNKNOWN |
| kill_media | SERVICE_DOWN / media | X | X — CODE_STOP / nginx |
| code_media | CODE_STOP / media | X | X — DEPENDENCY / nginx |

**결과: 1/3 → 1/3 (정답률 불변).** 정형화의 **구조적** 목표는 달성:
- type이 6종 enum 값으로만 출력됨(자유문자열 제거).
- confidence가 실제 산출물에서 사라짐(rca 키 = rootCause·propagation).
- cpu에서 UNKNOWN 폴백 정상 동작.

그러나 **진단 정확도는 개선되지 않음.** 측정으로 드러난 두 급소:

1. **service 앵커링의 실제 급소는 `trace.md`.** `service: nginx`는 report가 아니라 **trace 에이전트의 `origin_service`**가 말단 프록시(nginx)를 진원으로 뽑아 발생. `assemble()`이 `trace_ev.origin_service`를 최우선하므로 `report.md`의 "nginx 승격 금지" 규칙은 도달하지 못한다. → 진원 추출 규칙을 `trace.md`에 넣어야 함(후속).
2. **type 오분류는 진단 품질 문제.** kill_media가 배경 노이즈(user MongoDB 중복키)에 latch되어 미디어 kill(SERVICE_DOWN)을 CODE_STOP으로, code_media를 DEPENDENCY로 오판. enum 강제와 무관한 상관분석·우선순위 판단 이슈(후속 프롬프트 개선).

즉 이 브랜치는 "출력 형식 고정"까지를 완결하고, "정확도 개선"은 별도 후속(1: trace.md 진원 규칙, 2: report 상관분석 강화)으로 분리한다. 하네스가 이 분리를 정량적으로 확인해 준 셈.
