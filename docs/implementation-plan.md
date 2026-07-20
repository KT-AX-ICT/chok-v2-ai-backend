# LLM 에이전트 구현 계획

[에이전트 설계](agent-design.md)·[번들 raw 압축 전략](bundle-compression.md)을 코드로 옮기기 위한 단계별 실행 계획.
단계마다 테스트 통과 확인 후 커밋·푸시. 최종 단계 전까지 기본 동작(stub)은 유지 — 파이프라인이 항상 돌아가는 상태로 증분.

## 전체 구조 (신규/변경 파일)

```
app/
├── agents/
│   ├── llm.py               # [신규] 모델 팩토리 + 전역 세마포어 + 입력 절단
│   ├── schemas.py           # [신규] 에이전트 내부 스키마 (RouteDecision, ReportDraft)
│   ├── prompts/             # [신규] 시스템 프롬프트 (md 파일 + 로더)
│   │   ├── __init__.py      #   load_prompt(name)
│   │   ├── _common.md · router.md · scan.md · log.md · metric.md · trace.md · report.md
│   ├── modality_agents.py   # [신규] 심층 3종 + 경량 스캔 (LLM)
│   ├── router.py           # [신규] router + 가드레일
│   ├── graph.py             # [신규] LangGraph 그래프 조립 + LlmOrchestrator
│   ├── report_llm.py        # [신규] report 에이전트 + assemble
│   ├── orchestrator.py      # [변경] 기본 구현을 LlmOrchestrator로 교체
│   └── log_agent.py 등 4종  # [제거] rule-based stub (최종 단계)
├── services/
│   ├── bundle_compression.py # [신규] log dedup · metric 통계 · trace 집계
│   └── bundle_parser.py      # [변경] 심층 입력에 압축기 연결
└── core/config.py            # [변경] OPENAI_* 설정 추가
```

## 단계별 계획

### 1단계 — 의존성·설정 (`chore`)

- `uv add langgraph langchain-openai` → pyproject·lock 갱신.
- [config.py](../app/core/config.py) 추가 항목:

| 키 | 기본값 | 용도 |
|---|---|---|
| `OPENAI_API_KEY` | `""` (빈 값 허용 — 미설정 시 LLM 미기동) | 인증 |
| `OPENAI_MODEL_REPORT` | `gpt-5.5-2026-04-23` | report |
| `OPENAI_MODEL_ANALYSIS` | `gpt-5.4-mini-2026-03-17` | 심층 3종 |
| `OPENAI_MODEL_LIGHT` | `gpt-5.4-nano-2026-03-17` | router·scan |
| `OPENAI_MAX_CONCURRENCY` | `4` | 전역 세마포어 상한 |
| `OPENAI_MAX_RETRIES` | `3` | 429 지수 백오프 횟수 |
| `OPENAI_MAX_INPUT_CHARS` | `120000` | 모달리티 입력 절단 상한 |

- `.env.example` 동기화.
- **완료 기준**: `uv run pytest` 전체 통과 (동작 무변경).

### 2단계 — LLM 공통 계층 (`feat`)

- `app/agents/llm.py`:
  - (1) `make_llm(model, effort)` — `ChatOpenAI(model, reasoning_effort, max_retries)` 팩토리. reasoning effort 차등(router·scan `low` / 심층 `medium` / report `high`).
  - (2) `llm_semaphore` — `asyncio.Semaphore(OPENAI_MAX_CONCURRENCY)` 전역 1개. 모든 호출을 `async with`로 감쌈.
  - (3) `truncate_input(text, max_chars, trigger_time)` — 절단 최후 방어선. 트리거 시각 주변 우선 보존 + 절단 사실 문구 삽입.
- **완료 기준**: 절단 유틸 단위 테스트 통과 (LLM 호출 없음).

### 3단계 — 시스템 프롬프트 (`feat`)

- `app/agents/prompts/` 에 md 7종 작성 — `_common`(출력 언어·정답 유출 금지·근거 없는 단정 금지), `router`, `scan`, `log`, `metric`, `trace`, `report`.
- 로더 `load_prompt(name)` — `_common.md` + 해당 파일 결합, `functools.cache`.
- 변수 자리는 `{window_start}` 플레이스홀더 — `ChatPromptTemplate` 바인딩.
- **완료 기준**: 로더 단위 테스트 (7종 로드·공통부 결합·캐시) 통과.

### 4단계 — 번들 압축기 (`feat`)

- `app/services/bundle_compression.py` — [압축 전략 문서](bundle-compression.md)의 규칙 구현:
  - (1) `compress_logs(items)` — 템플릿화 dedup. 가변부(`req_id`·긴 숫자) 마스킹, `템플릿 · level · ×횟수 · 최초/최후 시각 · 샘플 1줄`. 희귀 라인 원문 유지.
  - (2) `compress_metrics(items, trigger_time)` — 시리즈별 baseline/incident 통계 + onset·peak. 파싱 불가 시 원문 통과 폴백.
  - (3) `compress_traces(items)` — `(service, operation)` 집계 + 서비스별 볼륨 + exemplar 소수 원문.
  - 공통: 절대 시각 축약(`HH:MM:SS.mmm`), 서비스별 그룹핑, TSV 직렬화, D-020/D-021 유지.
- `bundle_parser.parse_for_*_agent()` 가 압축 산출물을 사용하도록 확장 (기존 키 유지).
- **완료 기준**: 압축기 단위 테스트 (dedup 배율·통계값·집계 정합) + 기존 parser 테스트 통과.

### 5단계 — 에이전트 구현 (`feat`)

- `app/agents/schemas.py` — `RouteDecision`(모달리티별 `deep|scan` + reason), `ReportDraft`(RcaResult에서 evidence 제외).
- `app/agents/modality_agents.py` — 심층 log/metric/trace + 경량 scan. structured output(`LogEvidence` 등), 기존 주입 시그니처(`(bundle) → Evidence`) 준수.
- `app/agents/router.py` — 메타데이터만 입력, structured output. 가드레일(코드 강제):
  - (1) `triggered_by` 포함 모달리티 → 무조건 `deep` (승격 전용).
  - (2) 데이터 0건 → LLM 생략, "데이터 없음" Evidence.
  - (3) router 실패 → 전 모달리티 `deep`.
- **완료 기준**: 가드레일 단위 테스트 (fake LLM 주입) 통과.

### 6단계 — LangGraph 조립 (`feat`)

- `app/agents/report_llm.py` — report 에이전트(Evidence 3종 + 최소 컨텍스트 → `ReportDraft`) + assemble(코드가 evidence 주입, `origin_service` 승격).
- `app/agents/graph.py` — `StateGraph` 조립:
  - 노드: `router → (log · metric · trace 병렬) → report → assemble`.
  - 각 모달리티 노드가 plan에 따라 deep/scan 선택, 실패 시 "분석 실패" Evidence로 완주.
  - `LlmOrchestrator.run(job_id, bundle) → RcaResult` — 기존 `RcaRunner` 시그니처 유지, 노드 에이전트 생성자 주입(테스트 대체용).
- **완료 기준**: fake 에이전트 주입 그래프 테스트 (fan-out·부분 실패·가드레일 경로) 통과.

### 7단계 — 교체·정리 (`feat` + `refactor`)

- [orchestrator.py](../app/agents/orchestrator.py) 기본 구현을 `LlmOrchestrator`로 교체 — [job_queue](../app/services/job_queue.py) 무변경.
- rule-based stub 4종(`log_agent`·`metric_agent`·`trace_agent`·`report_agent`) 제거.
- 테스트 갱신: LLM 모킹(fake 주입)으로 기존 `test_pipeline` 계약 유지.
- **완료 기준**: `uv run pytest` 전체 통과. API 키 없이도 테스트 그린.

## 검증 방식

1. **단계별** — 해당 단계 단위 테스트 + 전체 pytest 회귀.
2. **LLM 호출 배제** — 테스트는 전부 fake 주입. 실 호출 검증은 API 키 투입 후 별도 수동 확인(스모크).
3. **계약 고정** — `RcaRunner` 시그니처·detail 5키·D-020/D-021은 전 단계 불변 조건.

## 커밋 단위

| 단계 | 커밋 예시 |
|---|---|
| 계획 | `docs: LLM 에이전트 구현 계획 문서 추가` |
| 1 | `chore: langgraph·langchain-openai 의존성 및 OPENAI 설정 추가` |
| 2 | `feat: LLM 공통 계층 추가 (모델 팩토리·세마포어·입력 절단)` |
| 3 | `feat: 시스템 프롬프트 폴더 및 로더 추가` |
| 4 | `feat: 번들 raw 압축기 구현 (log dedup·metric 통계·trace 집계)` |
| 5 | `feat: router·경량 스캔·심층 에이전트 구현` |
| 6 | `feat: LangGraph 오케스트레이션 그래프 조립` |
| 7 | `feat: LLM 오케스트레이터 교체 및 stub 에이전트 제거` |
