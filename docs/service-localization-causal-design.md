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

## 하네스 측정 (before/after)

| 시나리오 | 정답 | 인과 교정 `fedb5fcab012` | +생존신호 `c6bf87fb4e6d` |
|---|---|---|---|
| cpu | PERFORMANCE | O | **O** |
| kill_media | SERVICE_DOWN / media | **O** | **O** |
| code_media | CODE_STOP / media | X (service media ✓ / type DEPENDENCY) | X (**service media** ✓ / type **SERVICE_DOWN**) |

**정답률 1/3 → 2/3.** kill_media 완전 정답, code_media는 service를 media로 국소화하고 type을
DEPENDENCY→SERVICE_DOWN으로(둘 다 "media가 문제" 계열).

### code_media `type=CODE_STOP`은 데이터로 결정 불가 (오버핏 금지)

생존신호를 넣어도 CODE_STOP이 안 나오는 건 프롬프트 결함이 아니라 **번들 증거 자체의 모순** 때문:
- `metric=data`(살아있음) ↔ `getaddrinfo … Name or service not known`(이름조차 해석 불가 = 사라짐)이 상충.
- media **자체 코드 에러 로그는 없음**(`log=missing`).
모델은 이 모순을 인지하고("metric=data이지만 log·trace 없음… 실제 원인이 프로세스 종료인지…") 더 강한
"소실" 신호(getaddrinfo)를 근거로 **SERVICE_DOWN**을 택함 — **합리적 판단**이다. 여기서 CODE_STOP을
강제하려면 "metric=data가 이름해석실패를 이긴다"는 데이터 편향 룰이 필요하므로 **하지 않는다**. code_media의
CODE_STOP 라벨은 **주입 방식에서 온 것**이며 관측 증거로는 SERVICE_DOWN이 더 방어 가능. (픽스처에 media
자체 코드에러 로그가 있었다면 구분 가능 — 데이터 한계.)

## 테스트

228개 통과. 신규/변경: `test_report_service_takes_precedence_over_trace_origin`,
`test_orchestrator_returns_valid_rca_result`(대표 service=report 종합), `test_report_liveness`(서비스별
모달리티 생존 신호 교차 집계·주입).

## 산출물 / 브랜치

- 브랜치 `feat/trace-origin-guard` (base `feat/incident-signal-highlight`). 1차(service 국소화)+2차(생존신호 type).
- 커밋 대상: `app/agents/report_llm.py`, `app/agents/prompts/report.md`·`trace.md`,
  `tests/test_pipeline.py`·`test_report_liveness.py`, 본 문서.
