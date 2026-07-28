# 프롬프트 평가 하네스 — 설계

작성일 2026-07-24 (2026-07-27 확정 반영).
프롬프트 버저닝 + 생성 결과 기록·채점 파이프라인의 설계 기록.

## 배경

1. RCA 프롬프트는 [app/agents/prompts/](../app/agents/prompts/)의 md 6개(router·scan·log·metric·trace·report) + `_common.md`로 관리되고, [load_prompt()](../app/agents/prompts/__init__.py)이 `_common + <name>`을 합쳐 시스템 프롬프트로 쓴다.
2. 프롬프트를 고칠 때 **결과가 어떻게 달라지는지 객관적으로 비교할 수단이 없다** — 눈으로 출력을 보는 수준.
3. SDK(`chok-v2-py-sdk`)에는 정답이 알려진 3시나리오가 이미 정의돼 있다(`demo/replayer/scenarios.py`): `cpu`(Perf_CPU_Contention) · `kill_media`(Svc_Kill_Media) · `code_media`(Code_Stop_MediaService).
4. 정답을 알기에 **자동 채점**이 가능하다.

## 목표

- 프롬프트 버전별로 RCA 결과를 **자동 채점**(정답 대비 O/X) + **원문 보관**
- 프롬프트 버전 ↔ 결과를 정확히 추적 — 커밋 안 한 실험 프롬프트까지(프롬프트 content hash)
- 인프라 0으로 시작 — 스크립트 + CSV + 정적 HTML
- 결정적 입력(고정 번들)으로 프롬프트·모델만 변수화

## 비목표 (YAGNI)

- langfuse·웹서버·DB·실시간 트레이싱·프롬프트 편집 UI — 오프라인 dev-time 튜닝 용도라 불필요
- 앱 런타임 코드 변경 — 하네스는 `orchestrator.run`을 그대로 호출만

## langfuse를 안 쓰는 이유

langfuse의 값어치는 **운영 중 실시간 트레이싱·팀 대시보드·대규모**다. 이 하네스는 별도 스크립트로 도는 **오프라인 프롬프트 튜닝**이라, langfuse는 인프라(SaaS/자체호스팅) + 앱 코드 콜백 래핑을 요구해 문제 대비 과하다. CSV/JSONL + git + 정적 HTML이면 버전→결과 추적이 인프라 0으로 되고 git diff도 된다. 운영 관측이 필요해지면 그때 붙여도 지금 산출물이 걸림돌이 안 된다.

## 진행 순서 (확정)

1. **하네스 먼저** — 현행 프롬프트로 베이스라인 측정. 초기 채점은 자유문자열에 관대(정규화 + 허용 키워드).
2. **프롬프트 정형화 브랜치**(별도, 아래 "연계") — type/service 제약을 하네스로 before/after 측정하는 **첫 실험**. 이후 채점을 exact-enum으로 조임.

근거: 하네스의 존재 이유가 곧 "프롬프트 변경 측정"이라, 정형화를 먼저 넣으면 그 변경을 잴 수단이 없다. 프롬프트 변경은 유닛테스트로 못 잡으니 하네스가 유일한 검증 수단이다.

## 설계

```
eval/
  capture_sink.py       SDK가 POST한 번들을 fixtures/<scenario>.json에 저장 (A)
  fixtures/<scenario>.json   골든 입력(IngestBundle) — 고정, git 추적
  run.py                러너 (python -m eval.run)
  ground_truth.yaml     시나리오별 기대값(type·service)
  scoring.py            채점 로직
  viewer.html           정적 뷰어
  runs/<ts>_<scenario>_<hash>/   실행별 아티팩트 (gitignore)
  index.csv             실행 인덱스 (gitignore, append-only)
```

### A. 골든 입력(픽스처) — 실 번들 캡처

**SDK(main 브랜치)의 실제 파이프라인을 돌려 나온 번들을 캡처**한다. 데이터셋 수동 조립(B)이 아니라 A — main에서 SDK의 collectors→normalization→trigger→assembler→transport가 전부 구현돼 있고, SDK `SnapshotBundle`이 우리 `IngestBundle` 계약과 일치함을 확인함(camelCase·logs/metrics/traces·modalityInfo). B는 SDK 조립을 우리가 재구현하는 격이라 불필요.

절차(시나리오별):
1. **캡처 싱크** 실행 — `python eval/capture_sink.py <scenario>` — `:8000/ingest`에서 대기, 트리거 번들을 `fixtures/<scenario>.json`에 저장. (SDK `scripts/mock_ingest_server.py`에 "본문 저장"만 더한 형태.)
2. **SDK e2e** 실행(SDK 루트, main) — `scripts/run_local_demo.sh <scenario>` — replayer가 데이터셋을 `var/`로 흘리고, `rca-collect`(runner)가 탐지→조립→POST.
3. 트리거 발화 시 POST된 번들 = **실 IngestBundle** → 픽스처.

사전: SDK 체크아웃 `main`, `uv sync`로 `rca-collect` 설치. 트리거 발화 시에만 POST(30초 루프). 타임스탬프는 실행 시각으로 시프트되나 내부 일관.

이후 eval 실행은 이 고정 `fixtures/*.json`을 `orchestrator.run`에 직접 먹임 — SDK·ingest 재실행 불필요, 입력 결정적(프롬프트·모델만 변수).

### B. 러너 — `python -m eval.run`

각 fixture → `orchestrator.run(job_id, bundle)` → RcaResult. 실행 전 프롬프트 6개의 content hash + 합친 **set hash** 기록. 실행별 model·per-node latency·tokens 수집. 시나리오당 N회 반복 옵션(LLM 비결정성 관찰용).

### C. 채점 — `ground_truth.yaml` + `scoring.py`

시나리오별 기대값(type·service) 정의 → 예측 `RcaResult.type/service`와 비교 → per-scenario O/X + 전체 정답률.

- **type** — 정형화 후 고정 enum: `SERVICE_DOWN`·`CODE_STOP`·`PERFORMANCE`·`DEPENDENCY`·`OTHER`·`NONE`. 정형화 전(하네스 초기)엔 자유문자열이라 **허용 키워드 매칭**, 정형화 후 **exact-enum**으로 조임.
- **service** — **데이터 앵커링**: 트레이스 데이터의 service 필드 기준(파이프라인 `origin_service`가 여기서 나옴). 정규명 매칭 + `UNKNOWN` 탈출구.
- **severity** — 기록만, 채점 제외(주관적).
- 기대값: `cpu`→(type=PERFORMANCE, service=UNKNOWN 허용), `kill_media`→(SERVICE_DOWN, media), `code_media`→(CODE_STOP, media). service 정확값은 **트레이스 데이터에서 확인해 확정**.

### D. 기록

- 실행별 아티팩트: `eval/runs/<ts>_<scenario>_<sethash>/` — 프롬프트 스냅샷 6개 + `result.json` + `meta.json`
- 인덱스: `eval/index.csv`(append-only) — run_id·ts·scenario·set_hash·model·pred_type·pred_service·correct·latency_s·tokens·runs_path
- **스칼라 요약은 CSV, 중첩 원문(프롬프트·결과)은 파일** — 중첩 JSON을 CSV 셀에 넣으면 깨지므로 분리

### E. 뷰어 — `eval/viewer.html`

정적 HTML+JS로 `index.csv` fetch → 표(정렬·필터: 시나리오/버전/정답여부). **set_hash로 그룹핑해 버전 간 정답률·latency 비교**. row 클릭 → 해당 run의 result.json·프롬프트로 링크. 인프라 0, 브라우저로 열기만.

## 연계: 프롬프트 정형화 (별도 브랜치, 하네스 이후)

type/service 제약은 순수 프롬프트를 살짝 넘어 **세 곳**을 건드림 → 별도 브랜치(예: `feat/rca-output-taxonomy`)로 하네스 이후 진행:

1. **프롬프트** — `report.md`(+`_common.md`): type 6종 의미·선택 기준, service 규칙(데이터 등장명 그대로/없으면 `UNKNOWN`).
2. **스키마(핵심 강제)** — `app/agents/schemas.py`의 `ReportDraft.type` → `Literal[6종]`. `with_structured_output`이 enum을 강제 → 프롬프트는 "설명", 스키마는 "보장".
3. **코드** — `assemble()`: `service = trace_ev.origin_service or draft.service or "UNKNOWN"`.
4. **Spring 통지** — `RcaResult.type`은 `str` 유지(breaking 아님)이나 값이 고정 6종이 되므로 Spring에 값 집합 공유.

## git 처리

- 추적: `eval/{capture_sink.py, run.py, scoring.py, viewer.html, ground_truth.yaml, fixtures/}`
- 무시: `eval/runs/`, `eval/index.csv` — 실행 생성물

## 비용

1회 eval = 3시나리오 × 6 LLM콜(반복 N이면 ×N) = 실제 OpenAI 과금. 프롬프트 바꿀 때마다 재실행.

## 작업 범위 / 빌드 순서

A(픽스처 캡처, SDK 연동)가 나머지를 막으므로 먼저. 이후 B→C→D→E. 그 다음 별도 브랜치로 프롬프트 정형화(하네스로 측정).

| 단계 | 산출물 |
|---|---|
| A | `capture_sink.py` + `fixtures/*.json` (SDK main e2e 캡처) |
| B | `run.py` — orchestrator 호출·해시·메타 |
| C | `ground_truth.yaml` + `scoring.py` |
| D | 아티팩트·CSV 기록 |
| E | `viewer.html` |
| 후속 | `feat/rca-output-taxonomy` — type/service 정형화 |
