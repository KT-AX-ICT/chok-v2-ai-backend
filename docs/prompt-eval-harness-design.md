# 프롬프트 평가 하네스 — 설계

작성일 2026-07-24. 프롬프트 버저닝 + 생성 결과 기록·채점 파이프라인의 설계 기록.
※ 아래 "확정 필요" 두 항목(채점 기준·픽스처 생성)은 합의 전 추천안이며, 확정 시 갱신.

## 배경

RCA 프롬프트는 [app/agents/prompts/](../app/agents/prompts/)의 md 6개(router·scan·log·metric·trace·report) + `_common.md`로 관리되고, [load_prompt()](../app/agents/prompts/__init__.py)이 `_common + <name>`을 합쳐 시스템 프롬프트로 쓴다. 프롬프트를 고칠 때 **결과가 어떻게 달라지는지 객관적으로 비교할 수단이 없다** — 눈으로 출력을 보는 수준.

SDK(`chok-v2-py-sdk`)에는 정답이 알려진 3시나리오가 이미 정의돼 있다(`demo/replayer/scenarios.py`): `cpu`(Perf_CPU_Contention) · `kill_media`(Svc_Kill_Media) · `code_media`(Code_Stop_MediaService). 정답을 알기에 **자동 채점**이 가능하다.

## 목표

- 프롬프트 버전별로 RCA 결과를 **자동 채점**(정답 대비 O/X) + **원문 보관**
- 프롬프트 버전 ↔ 결과를 정확히 추적 — 커밋 안 한 실험 프롬프트까지
- 인프라 0으로 시작 — 스크립트 + CSV + 정적 HTML
- 결정적 입력(고정 번들)으로 프롬프트·모델만 변수화

## 비목표 (YAGNI)

- langfuse·웹서버·DB·실시간 트레이싱·프롬프트 편집 UI — 오프라인 dev-time 튜닝 용도라 불필요
- 앱 런타임 코드 변경 — 하네스는 `orchestrator.run`을 그대로 호출만

## langfuse를 안 쓰는 이유

langfuse의 값어치는 **운영 중 실시간 트레이싱·팀 대시보드·대규모**다. 이 하네스는 별도 스크립트로 도는 **오프라인 프롬프트 튜닝**이라, langfuse는 인프라(SaaS/자체호스팅) + 앱 코드 콜백 래핑을 요구해 문제 대비 과하다. CSV/JSONL + git + 정적 HTML이면 버전→결과 추적이 인프라 0으로 되고 git diff도 된다. 운영 관측이 필요해지면 그때 붙여도 지금 산출물이 걸림돌이 안 된다.

## 설계

```
eval/
  fixtures/<scenario>.json     골든 입력(IngestBundle) — 고정, git 추적
  gen_fixtures.py              SDK 3시나리오 → 번들 JSON 덤프(1회성)
  run.py                       러너 (python -m eval.run)
  ground_truth.yaml            시나리오별 기대값(service·type)
  viewer.html                  정적 뷰어
  runs/<ts>_<scenario>_<hash>/ 실행별 아티팩트 (gitignore)
  index.csv                    실행 인덱스 (gitignore, append-only)
```

### A. 골든 입력 — `eval/fixtures/<scenario>.json`

3시나리오의 IngestBundle JSON을 **한 번 뽑아 고정**. SDK 리플레이어가 /ingest로 보내는 payload가 곧 IngestBundle 계약이므로, 그것을 파일로 덤프(`gen_fixtures.py`). 이후 실행은 이 고정 JSON을 `orchestrator.run`에 직접 먹인다 — ingest·HTTP·SDK 재실행 불필요, 입력 결정적.

리포에 재사용할 번들 샘플은 없음(테스트는 인라인 합성). 그래서 픽스처는 신규 생성.

### B. 러너 — `python -m eval.run`

각 fixture → `orchestrator.run(job_id, bundle)` → RcaResult. 실행 전 프롬프트 6개의 content hash + 합친 **set hash** 기록. 실행별 model·per-node latency·tokens 수집. 시나리오당 N회 반복 옵션(LLM 비결정성 관찰용).

### C. 채점 — `eval/ground_truth.yaml`

시나리오별 기대값(expected service·type)을 정의 → 예측 `RcaResult.service/type`과 비교 → per-scenario O/X + 전체 정답률. **추천 기준: service 일치 + type 카테고리 일치를 정답**으로(severity는 기록만, 채점 제외 — 주관적). ground_truth 값은 시나리오 의미로 초안(예: `kill_media` → service=media·type=서비스 중단) 후 확정.

### D. 기록

- 실행별 아티팩트: `eval/runs/<ts>_<scenario>_<sethash>/` — 프롬프트 스냅샷 6개 + `result.json` + `meta.json`
- 인덱스: `eval/index.csv`(append-only) — run_id·ts·scenario·set_hash·model·pred_service·pred_type·correct·latency_s·tokens·runs_path
- **스칼라 요약은 CSV, 중첩 원문(프롬프트·결과)은 파일** — 중첩 JSON을 CSV 셀에 넣으면 깨지므로 분리

### E. 뷰어 — `eval/viewer.html`

정적 HTML+JS로 `index.csv` fetch → 표(정렬·필터: 시나리오/버전/정답여부). **set_hash로 그룹핑해 버전 간 정답률·latency 비교**. row 클릭 → 해당 run의 result.json·프롬프트로 링크. 인프라 0, 브라우저로 열기만.

## git 처리

- 추적: `eval/{gen_fixtures.py, run.py, viewer.html, ground_truth.yaml, fixtures/}`
- 무시: `eval/runs/`, `eval/index.csv` — 실행 생성물

## 비용

1회 eval = 3시나리오 × 6 LLM콜(반복 N이면 ×N) = 실제 OpenAI 과금. 프롬프트 바꿀 때마다 재실행.

## 작업 범위 / 빌드 순서

A(픽스처 생성)가 나머지를 막으므로 먼저. A는 SDK를 건드리는 유일한 부분이라 별도 sub-step으로 분리. 이후 B→C→D→E.

| 단계 | 산출물 |
|---|---|
| A | `gen_fixtures.py`, `fixtures/*.json` (SDK 연동) |
| B | `run.py` — orchestrator 호출·해시·메타 |
| C | `ground_truth.yaml` + 채점 로직 |
| D | 아티팩트·CSV 기록 |
| E | `viewer.html` |

## 확정 필요

1. **채점 기준** — service + type 일치를 정답으로 (severity 제외). 확정 대기.
2. **픽스처 생성 경로** — SDK 리플레이어로 3시나리오 번들 덤프. 기존 샘플 번들 보유 시 대체. 확정 대기.
