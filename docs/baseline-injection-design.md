# RCA 정확도 개선 — baseline 주입 + 이상치 정렬 (log·metric) 설계

작성 2026-07-28. 브랜치 `feat/baseline-injection` (base: main, #25 정형화 머지 후).
출처: Notion "⚠️ 프롬프트 개선사항" 항목 2·3 + 팀원 실험 `experiment/baseline-injection`.

## 배경

RCA가 **만성 노이즈를 근본 원인으로 오판**한다. 예: kill_media(media 강제 종료) 시나리오에서
평소에도 상시 발생하는 `E11000 duplicate key error(user_id:444)`(정상 운영 중에도 다수)가
근본 원인으로 잡히고, 정작 media 재시작 신호(저빈도 INFO)는 묻힌다.

원인은 **압축 단계의 정렬**이다.
- `compress_logs` — 레벨(ERROR 우선)·count 내림차순 정렬. "평소에도 흔한 에러"와 "이번에만
  생긴 신호"를 구분 못 하고, INFO는 항상 후순위라 재시작 로그 같은 이상 반복이 구조적으로 안 보인다.
- `compress_metrics` — `(service, label)` 알파벳순 정렬. 3σ 이상점을 **계산은 하나 정렬에 미반영**해,
  media 고유 지표보다 무관한 호스트 지표(`__node__`, `cadvisor`)가 먼저 노출 → "광범위 리소스 경합"
  으로 오도.

하네스(`eval/`) 베이스라인 측정에서도 동일 확인: kill_media·code_media가 `service=nginx`(말단 프록시)·
오분류로 실패(1/3). service=nginx는 trace 진원 추출 문제이나, type 오판·node-level 편향의 뿌리가
이 정렬 문제다.

팀원 실험 `experiment/baseline-injection`(커밋 1개)이 **log 쪽 해결책을 이미 검증**했다(job3/4/5 재실행,
만성 오판 3건 모두 개선). metric 쪽은 "다음 단계"로 미착수. 본 작업은 그 실험에서 **쓸 것만 추려**
프로덕션화하고, metric 정렬을 추가하며, 하네스로 정량 측정한다.

## 목표

- `compress_logs`: 만성 노이즈를 뒤로, 저빈도-이상 신호를 앞으로 (surprise 정렬) + base/incid 건수 표기
  + 정상 baseline 프로파일 대조.
- `compress_metrics`: 알파벳순 → **이상점 크기순** 정렬(이미 계산 중인 3σ 편차 활용).
- 정상 운영 baseline 로그 프로파일을 오프라인 생성·**JSON만 커밋**(원본 로그 제외).
- 하네스로 전/후 정답률 측정. 목표: kill_media→`SERVICE_DOWN`/media, code_media→`CODE_STOP`/media,
  cpu 유지.

## 비목표 (YAGNI / 명시적 제외)

- **프롬프트 버전 주석 인프라** (`prompts/__init__.py`의 `_strip_version_comments`·`prompt_version`) —
  정확도와 무관한 로딩 변경. 제외.
- **`rerun_experiment.py`·실험 결과 JSON 3개** — 하네스가 상위 호환(픽스처·채점·뷰어). 제외.
- Notion 항목 1(영향 카드 자연어화)·4(errors 누락)·5(멀티 리포트) — 별도 테마.
- SDK 감지 error 트리거 파일 list 주입 — 정답 유출 위험, 보류(실험 문서 제안).
- baseline 프로파일의 rate 정규화(분당 환산) — 상대 비교엔 원시 count로 충분. YAGNI.

## 실험에서 가져오는 변경 (검토 결과)

| 변경 | 파일 | 성격 | 결정 |
|---|---|---|---|
| surprise 정렬 (레벨/count → 평소 대비 튄 정도) | `bundle_compression.py` `compress_logs` | 🔴 핵심 | **유지** |
| base/incid 분할 (트리거 전/후 건수) | `bundle_compression.py` `compress_logs` + `bundle_parser.py`(trigger_time 전달 1줄) | 🔴 핵심 | **유지** |
| 서비스별 Drain miner (전엔 번들 1개 공유) | `bundle_compression.py` `compress_logs` | 🟠 버그픽스 | **유지** |
| 대괄호 타임스탬프 마스킹 | `bundle_compression.py` `_MASKING` | 🟠 버그픽스 | **유지** |
| baseline 프로파일 조회 (`log_profile.json`) | `bundle_compression.py` `_load_baseline_profile` | 🟢 선택적 | **유지** |
| log.md v3 (base/incid·만성 판단 지침) | `app/agents/prompts/log.md` | 🟢 프롬프트 | **유지**(명명 보정) |
| 오프라인 프로파일 생성 | `scripts/analyze_baseline.py` | ⚪ 비런타임 | **유지** |
| 프롬프트 버전 주석 인프라 | `prompts/__init__.py` | ⚪ 무관 | **제외** |
| 수동 재실행 스크립트·결과 JSON | `scripts/rerun_experiment.py`, `*_rca_result_*.json` | ⚪ 중복/잔재 | **제외** |

## 설계 상세

### 1. baseline 프로파일 (오프라인)

- **입력**: `AnoMod.zip`(`C:\Users\user\Desktop\개인폴더\AnoMod-main`)의
  `SN_data/log_data/Normal_Baseline_20251103_220228_*` — 정상 운영 로그(약 20분, 서비스별 파일).
  → `datasets/baseline/log/**/*.log`로 추출(gitignore).
- **스크립트**: `scripts/analyze_baseline.py` — 서비스별 Drain 클러스터링으로 `(service, level, template)`별
  발생 횟수 집계 → `datasets/baseline/log_profile.json`.
- **커밋 범위**: `log_profile.json`(작음)만 커밋. 원본 로그는 gitignore. 프로파일이 있으면 런타임이
  원본 없이 만성 판별을 한다.
- **명명 보정**: 데이터가 24h가 아니라 ~20분이므로, 실험의 "평소24h" 표기를 **"정상 baseline"**
  (예: `baseline=N`)으로 바꾼다. 상대 비교(만성 여부)엔 원시 count로 충분하되, 표현이 데이터와 어긋나지
  않게 한다.
- **재생성 절차**를 스크립트 docstring + 본 문서에 명시(경로 인자/환경변수).

### 2. `compress_logs` (bundle_compression.py)

실험 로직을 이식하되 명명만 보정.
- **서비스별 miner**: `miners: dict[service, TemplateMiner]` — 서로 다른 서비스의 유사 로그가 한
  클러스터로 뭉개져 서비스명이 `<*>`로 일반화되는 것 방지(프로파일 매칭 정확도).
- **대괄호 TS 마스킹**: `[YYYY-Mon-DD HH:MM:SS.ffffff]` 통째 마스킹 규칙을 최우선 추가(월 약어 미마스킹
  → 수집월 다르면 다른 템플릿으로 갈리는 문제).
- **base/incid 집계**: trigger_time 기준 전/후 건수. `bundle_parser.parse_for_log_agent`가 trigger_time
  전달(1줄).
- **surprise 정렬**: `surprise = incid / (base + baseline + 1)`. 내림차순 1순위, 레벨·count는 2·3순위.
  프로파일 없으면 `baseline` 항 0(하위 호환), trigger_time 없으면 기존 레벨/count 정렬로 폴백.
- **안전 degrade**: `_load_baseline_profile`은 파일 없으면 빈 dict(운영·CI 기본 상태에서 무동작).

### 3. `compress_metrics` (신규 — 이상점 정렬)

- 현재 `sorted(series.items())`(알파벳순)을 **이상점 크기순**으로 교체.
- 각 시리즈에 이미 base/incid + 3σ deviants(onset/peak) 계산이 있으므로, **anomaly score**를 정의:
  `score = |peak_value - base_mean| / base_sigma`(base_sigma=0이면 편차 유무로 이진). 이상점 없는 시리즈는
  score=0.
- 정렬: score 내림차순 1순위, 동점은 기존 알파벳순. → media처럼 실제 이상치가 난 서비스 지표가 상단.
- trigger_time/base 부재 등으로 score 계산 불가 시 기존 알파벳순 폴백.

### 4. 프롬프트

- `log.md` — 실험 v3 이식. base/incid/baseline 숫자 의미, "평소와 다른 정도순 정렬" 명시, **"ERROR라도
  baseline이 크면(만성) 단정 금지"**, **"재시작 로그가 평소 1회인데 이번 2회↑면 강한 신호"** 지침 유지.
  단 "평소24h" → "정상 baseline" 명명.
- `metric.md` — 지표 블록이 **이상점 크기순** 정렬임을 명시하고 상위=원인 후보로 우선 검토하도록 안내.

## 테스트

유닛(`tests/test_bundle_compression*.py`):
- `compress_logs`: (a) 만성 패턴(높은 baseline)이 낮은 surprise로 뒤로, (b) 저빈도-이상(base 0·baseline 1·
  incid 2)이 앞으로, (c) 프로파일 없을 때 degrade, (d) trigger_time 없을 때 레벨/count 폴백, (e) 서비스별
  miner 분리로 서비스명 보존, (f) 대괄호 TS 마스킹으로 같은 로그가 한 템플릿.
- `compress_metrics`: 이상점 있는 시리즈가 알파벳 뒤여도 상단, score 계산 불가 시 폴백.
- `analyze_baseline.py`: 소형 픽스처 로그 → 예상 `(service, level, template)` count.
회귀: 기존 compression 테스트 전량 통과(서비스별 miner·마스킹으로 dedup count가 바뀌는 곳 확인·갱신).

## 측정 (하네스)

1. `Normal_Baseline` 추출 → `analyze_baseline.py` → `log_profile.json` 커밋.
2. `python -m eval.run` 실행. before = main(현행, 정형화 포함) set_hash / after = 본 변경 set_hash.
3. 목표: kill_media `SERVICE_DOWN`/media, code_media `CODE_STOP`/media, cpu 유지. 최소 만성 오판 제거 확인.
4. 결과를 본 문서 측정 표에 기입.

## 리스크

- **프로덕션 로그 순서가 항상 바뀜**(surprise 정렬은 trigger_time 있으면 상시 동작) — 안전장치: 프로파일
  없어도 degrade, 유닛테스트로 고정, 하네스 전/후 검증 후에만 채택.
- **~20분 baseline**은 24h보다 커버리지 낮아 일부 만성 패턴을 놓칠 수 있음 — 더 긴 baseline 확보 시
  프로파일만 재생성(코드 불변).
- **dedup count 변화**(서비스별 miner·마스킹) — 회귀 테스트로 확인, 필요 시 기대값 갱신.

## 산출물 / 브랜치

- 브랜치 `feat/baseline-injection` (주 폴더 `C:\chok-v2-ai-backend`에서 작업, base main).
- 커밋 대상: `bundle_compression.py`, `bundle_parser.py`, `log.md`, `metric.md`, `scripts/analyze_baseline.py`,
  `datasets/baseline/log_profile.json`, 유닛테스트, 본 설계 문서, `.gitignore`(원본 baseline 로그 제외).
