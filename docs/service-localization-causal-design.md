# 진원 service 국소화 — 인과 추적 프롬프트 교정

작성 2026-07-29. 브랜치 `feat/trace-origin-guard` (base: `feat/incident-signal-highlight`).
출처: 하이라이트 측정 후속 — service가 진원(media)이 아니라 **호출자/프록시**로 새는 문제.

## 배경

하이라이트 PR 이후에도 service가 틀렸다:
- kill_media: log·report `rootCause`는 media를 맞게 짚는데 최종 `service=trace`(모달리티명) 또는
  `composepost`(호출자)로 나감.
- 원인 **둘**: (1) `assemble()`이 `trace_ev.origin_service`를 report 종합보다 코드로 **우선 승격**,
  (2) `report.md` item 6도 *"trace origin_service 우선"* 이라 지시 → 코드·프롬프트 둘 다 trace에 양보.

**밴드에이드 지양**: trace 출력을 코드로 걸러내는 방어가 아니라, **각 에이전트가 인과를 사람처럼
추적하게** 하고 **report가 검증·보완**하게 한다.

## 변경

역할 재정립:
- **`trace.md` — 진원을 제대로 추출(1차).** origin_service를 인과로: 피호출·최하류(가장 깊은 실패)
  서비스를 짚고, 에러를 관측·중계한 **호출자**나 **말단 프록시(nginx)**를 올리지 않는다. 실제 서비스명만.
- **`report.md` — 검증·보완(2차).** 새 원칙: *"진원은 신호의 원인이지 신호를 남긴 쪽이 아니다"*
  (`A: Failed to connect B` → 진원은 B). service(item 7)는 report 상관분석 결과(rootCause 일치)로,
  trace origin은 **참고·보정 대상**.
- **`assemble()` (코드).** `service = draft.service or trace_ev.origin_service or "UNKNOWN"` —
  검증 끝낸 report 우선, trace는 폴백. 이전의 `_promote_origin`(쓰레기값 거부) 밴드에이드 제거.

전부 **분산 시스템 공통 인과 원칙**이라 특정 데이터에 편향되지 않는다.

## 하네스 측정 (before/after)

before = 하이라이트 `0b87554a18bd`, after = 인과 교정 `fedb5fcab012`.

| 시나리오 | 정답 | before | after |
|---|---|---|---|
| cpu | PERFORMANCE | O | **O** |
| kill_media | SERVICE_DOWN / media | X (service=trace) | **O (SERVICE_DOWN / media)** |
| code_media | CODE_STOP / media | service만 O | X (**service media** ✓ / type DEPENDENCY ✗) |

**정답률 1/3 → 2/3 (첫 상승).** kill_media 완전 정답 — report가 인과로 media를 확정하고 trace의
호출자(composepost) 오귀속을 보정. code_media도 service를 media로 국소화.

**남은 한 필드**: code_media `type=DEPENDENCY`(정답 CODE_STOP). "media 연결 실패"를 밖에서 보면 의존
장애로 보이나, 실제론 media 자체 코드 결함(CODE_STOP). 구분하려면 media **자체의 코드 에러 로그**가
근거로 올라와야 함 — report의 type 분류 기준 보강 또는 진원 서비스 자체 로그 부각(별도 후속).

## 테스트

225개 통과. 신규/변경: `test_report_service_takes_precedence_over_trace_origin`(report service가 trace
origin보다 우선), `test_orchestrator_returns_valid_rca_result`(대표 service=report 종합).

## 산출물 / 브랜치

- 브랜치 `feat/trace-origin-guard` (base `feat/incident-signal-highlight`).
- 커밋 대상: `app/agents/report_llm.py`, `app/agents/prompts/report.md`·`trace.md`, `tests/test_pipeline.py`,
  본 문서.
