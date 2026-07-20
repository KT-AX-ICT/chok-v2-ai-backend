# feat: RCA 오케스트레이터를 rule-based stub → LangGraph LLM 파이프라인으로 교체

## 요약

- 에이전트 오케스트레이터를 얕은 rule-based stub에서 **LangChain + LangGraph 기반 LLM 심층 분석**으로 교체
- 파이프라인 골격(수신 → 비동기 워커 → 검증 → Spring 전송)의 계약을 깨지 않고 오케스트레이터 내부만 LLM으로 변경하는 작업 실시
- 번들 압축기 추가: LLM 입력 토큰을 줄이기 위한 모달리티별 무손실에 가까운 재표현 작업

설계·계획 문서를 먼저 확정하고 단계별로 구현함:
- [agent-design.md](agent-design.md) — 아키텍처·모델 선정·실패 처리·동시성 설계
- [implementation-plan.md](implementation-plan.md) — 7단계 실행 계획 (단계별 테스트 통과 후 커밋)
- [bundle-compression.md](bundle-compression.md) — 모달리티별 raw 압축 전략

## 아키텍처

```
START → planner → (log · metric · trace 병렬) → report → assemble → END
```

- **planner** (nano): raw 없이 메타데이터(건수·구간 상태·트리거)만 보고 모달리티별 `deep | scan` 결정.
- **모달리티 노드 3종** (병렬): plan에 따라
  - `deep` (mini): 압축된 raw 전체를 심층 분석 → Evidence
  - `scan` (nano): "이상 징후 유무"만 경량 판정 → Evidence (동일 스키마)
- **report** (gpt-5.5): Evidence 3종 + 최소 컨텍스트만 입력받아 `ReportDraft` 생성. raw 재투입 금지.
- **assemble** (코드): `detail.evidence`는 LLM이 재복사하지 않고 코드가 모달리티 산출물을 그대로 주입. trace의 `origin_service`를 대표 `service`로 승격(Q-007).

상태는 `RcaState` TypedDict — 모달리티 노드가 서로 다른 키(`log_ev`/`metric_ev`/`trace_ev`)를 갱신하므로 병렬 충돌이 없다.

## 주요 변경

### 신규 — 에이전트 계층 (`app/agents/`)
| 파일 | 역할 |
|---|---|
| [llm.py](../app/agents/llm.py) | 모델 팩토리 + 전역 세마포어(지연 초기화) + 입력 절단 유틸 |
| [schemas.py](../app/agents/schemas.py) | 내부 스키마 `PlanDecision`·`ReportDraft` (외부 계약과 분리) |
| [planner.py](../app/agents/planner.py) | planner LLM + 코드 가드레일 |
| [modality_agents.py](../app/agents/modality_agents.py) | 심층 3종 + 경량 scan (structured output, 모달리티×모드 팩토리) |
| [report_llm.py](../app/agents/report_llm.py) | report 에이전트 + assemble |
| [graph.py](../app/agents/graph.py) | LangGraph 조립 + `LlmOrchestrator` |
| [prompts/](../app/agents/prompts/) | 에이전트당 md 1개 + `_common.md`, `load_prompt()` 로더 |

### 신규 — 번들 압축기 ([bundle_compression.py](../app/services/bundle_compression.py))
LLM 입력 토큰을 줄이기 위한 모달리티별 무손실에 가까운 재표현:
- **log**: 가변부(req_id·긴 숫자) 마스킹 후 템플릿 dedup — `템플릿·level·×횟수·최초/최후 시각·샘플`
- **metric**: 시리즈별 baseline/incident 통계 + onset·peak 이상점
- **trace**: `(service, operation)` 집계 + 서비스별 볼륨 + exemplar 원문
- 파싱 불가 시 원문 통과 폴백. [bundle_parser.py](../app/services/bundle_parser.py)의 `parse_for_*_agent()`가 이를 사용.

### 변경 — 교체 지점
- [orchestrator.py](../app/agents/orchestrator.py): 앱 전역 오케스트레이터를 `LlmOrchestrator()`로 교체.
- [config.py](../app/core/config.py) / `.env.example`: `OPENAI_*` 설정 7종 추가 (키·모델 3종·동시성·재시도·입력 상한).
- `pyproject.toml` / `uv.lock`: `langgraph`, `langchain-openai` 의존성 추가.

### 제거 — rule-based stub 4종
`log_agent.py` · `metric_agent.py` · `trace_agent.py` · `report_agent.py` (−148줄). 가짜 분석을 끼워넣지 않고 삭제.

## 설계 의사결정

- **토큰 절약 5계층**: ①planner는 메타데이터만 ②비의심 모달리티 nano 강등(mini의 ¼), 0건이면 호출 생략 ③report는 정제 Evidence만 입력 ④evidence는 코드 주입(출력 토큰 절감) ⑤reasoning effort 차등(planner·scan `low` / 심층 `medium` / report `high`).
- **동시성 3밸브**: ①잡 동시성(`rca_worker_concurrency`) ②전역 LLM 세마포어(`OPENAI_MAX_CONCURRENCY`) ③입력 압축·절단. 병목은 RPM이 아니라 TPM이라는 분석에 근거.
- **429 재시도**: `langchain-openai` 내장 지수 백오프(`max_retries`)에 위임 — Retry-After 존중.
- **planner 가드레일(코드 강제 > LLM 판단)**: `triggered_by` 포함 → 무조건 deep(승격 전용, 강등 근거 아님) / 0건 → LLM 생략 / planner 실패 → 전 모달리티 deep 폴백.
- **실패는 정직하게**: 모달리티 부분 실패 → "분석 실패" Evidence로 완주(5키 계약 유지). report 실패 → 예외 전파 → 워커 재시도/FAILED 경로.
- **프롬프트 = md 파일**: 에이전트당 파일 1개. 코드 변경·재배포 없이 리뷰 가능한 단위. 시스템(고정)→user(가변) 배치로 OpenAI 프롬프트 캐싱 활용.

## 테스트

신규 테스트 파일 + 기존 회귀 갱신, 전부 **fake 에이전트/LLM 모킹**으로 실호출 없음:
- `test_llm_layer.py` — 팩토리·세마포어 싱글턴/동시성 상한·절단 유틸
- `test_bundle_compression.py` — dedup 배율·통계값·집계 정합
- `test_prompts.py` — 로더 화이트리스트·공통부 결합·캐시
- `test_agents.py` — 모달리티 에이전트 배선(메타데이터)·structured output
- `test_graph.py` — fan-out·부분 실패·가드레일 경로
- `test_pipeline.py` — 기존 파이프라인 계약을 LLM 모킹으로 유지

`make_llm()`의 `ChatOpenAI`는 **생성 시점에 api_key 존재를 요구**한다(실호출 없어도 객체 생성 자체가 검증). 이 때문에 `OPENAI_API_KEY` 미설정 시 `test_llm_layer`가 `Missing credentials`로 실패했다 →
`conftest.py`에 autouse 픽스처를 추가해 **키가 비어 있을 때만 더미 키를 주입**하도록 해결. 실 키가 있으면(스모크) 덮어쓰지 않는다. CI 시크릿·로컬 env 세팅 없이도 그린이다.

```
$ uv run --extra dev python -m pytest -q     # OPENAI_API_KEY 없어도
71 passed
```

## 후속 / 미결

- **Spring FAILED 수신 계약 확정 필요** (api-spec §6 미결) — 전체 실패 시 `status=FAILED` + `error` 수신 허용, `result` 부재 허용, 프론트 실패 표시 방식 (D-022 재검토).
- 실 API 키 투입 후 스모크(실호출) 수동 검증 — 이 PR 범위 밖.
- 배포 전 `platform.openai.com`에서 모델별 정확한 TPM/RPM 한도 재확인.
- CI 워크플로 미설정(`.github/workflows` 없음) — 별도 작업으로 pytest 워크플로 신설 검토.

## 리뷰 포인트

1. `graph.py`의 병렬 조인(`add_edge(list(MODALITIES), "report")`)과 부분 실패 처리 경로.
2. `bundle_compression.py`의 압축 규칙이 정답 유출(D-020/D-021)을 상위 파서에 위임하는 경계.
3. planner 가드레일이 LLM 판단을 덮어쓰는 우선순위 로직(`plan_with_guardrails`).
