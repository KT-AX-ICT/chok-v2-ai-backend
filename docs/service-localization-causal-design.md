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

## 2차 개선 — 모달리티 생존 신호 + type 판정

service를 맞춘 뒤에도 남은 code_media `type`(DEPENDENCY, 정답 CODE_STOP)을 위해:
- **룰베이스 교차 신호 주입** — `modalityInfo`에서 서비스별 `log/metric/trace` status(data/empty/missing)를
  뽑아 **종합 에이전트 입력에 실어준다**(`report_llm.service_liveness`). 어느 단일 모달리티 에이전트도 못
  보는 신호라, 모달리티끼리 넘겨보게 하지 않고 **report가 종합**한다.
- **`report.md` 규칙**: `metric=data`(프로세스 생존)인데 `log/trace=missing/empty`(작업 신호 없음) →
  **CODE_STOP**; `metric`까지 소실·이름해석실패·재시작 → **SERVICE_DOWN**. **DEPENDENCY는 외부 인프라로
  한정**(내부 서비스 연결 실패는 그 대상의 down/code_stop).

## 3차 개선 — SERVICE_DOWN vs CODE_STOP 판정을 "liveness"로 날카롭게

2차(생존신호 주입)만으론 code_media가 CODE_STOP이 아니라 SERVICE_DOWN으로 갔다 — 모델이 `metric=data`
(살아있음)와 `getaddrinfo not known`(소실)을 상충으로 보고 후자를 택함. 핵심 원칙을 명시해 해소:

- **metric은 liveness 프록시다. 진짜로 죽은(kill·OOM·소실) 서비스는 metric도 끊긴다.** 따라서 `metric=data`면
  프로세스는 살아있는 것 → **SERVICE_DOWN이 아니다.**
- `metric=data` + `log`·`trace` 침묵 + 죽음/재시작 신호 없음 → **CODE_STOP**. 호출자들의 연결·이름 해석
  실패는 "앱이 응답을 멈춰 등록이 빠진" **결과**일 뿐, metric이 살아있으면 프로세스 소실로 넘기지 않는다.
- 반대로 명시적 죽음/재시작 신호 또는 `metric`까지 소실이면 **SERVICE_DOWN**.

일반 원칙(metric=생존 프록시)이며 특정 데이터 편향 아님. kill_media(재시작 로그=명시적 죽음)와 code_media
(metric 생존+침묵)가 이 기준으로 깔끔히 갈린다.

## 하네스 측정 (before/after)

| 시나리오 | 정답 | 하이라이트 `0b87554a18bd` | 인과 교정 `fedb5fcab012` | +생존신호 `c6bf87fb4e6d` | +liveness 판정 `67de21706651` |
|---|---|---|---|---|---|
| cpu | PERFORMANCE | O | O | O | **O** |
| kill_media | SERVICE_DOWN / media | X | **O** | O | **O** |
| code_media | CODE_STOP / media | X | X (service✓) | X (SERVICE_DOWN) | **O** |

**정답률 1/3 → 3/3 (완전 정답).** service 국소화(1차 인과)로 kill_media를, type 판정(2·3차 생존신호+liveness
원칙)으로 code_media를 정답화. 모두 분산 시스템 공통 원칙(호출자≠원인·metric=liveness)이라 비편향.

## 테스트

228개 통과. 신규/변경: `test_report_service_takes_precedence_over_trace_origin`,
`test_orchestrator_returns_valid_rca_result`(대표 service=report 종합), `test_report_liveness`(서비스별
모달리티 생존 신호 교차 집계·주입).

## 산출물 / 브랜치

- 브랜치 `feat/trace-origin-guard` (base `feat/incident-signal-highlight`). 1차(service 국소화)+2차(생존신호 type).
- 커밋 대상: `app/agents/report_llm.py`, `app/agents/prompts/report.md`·`trace.md`,
  `tests/test_pipeline.py`·`test_report_liveness.py`, 본 문서.
