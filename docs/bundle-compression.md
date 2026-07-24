# 번들 raw 압축 (bundle_compression)

모달리티 원본(logs/metrics/traces)을 LLM에 넘기기 전에 **집계·요약해 재표현**하는 규칙과 구현.
코드: [app/services/bundle_compression.py](../app/services/bundle_compression.py).

## 목적

- 원본은 한 건이 수만~30만 줄까지 커져 그대로는 LLM에 못 넣음(TPM 병목).
- 관측 데이터는 중복이 심해, **압축이 곧 정확도 향상**임 — 반복에 묻힌 희귀 신호가 드러남.
- 버리는 게 아니라 **무손실에 가까운 재표현** — 패턴·이상점·대표 원문을 남김.

## 위치와 흐름

```
원본 3종 → bundle_parser.parse_for_*_agent()
              └ compress_logs / compress_metrics / compress_traces  ← 이 모듈
          → LLM 프롬프트 (초과 시 llm.truncate_input이 최후 절단)
```

`bundle_parser`가 모달리티별 파서에서 이 압축기를 호출하고, 산출물(문자열)을 user 메시지의 `## 데이터 (압축 표현)`에 싣는다.

## 공통 유틸

- **`parse_ts(ts)`** — ISO-8601 파싱(`Z` 허용, 3.11+ `fromisoformat`). 실패 시 `None`. 시각 비교가 필요한 곳에서만 사용.
- **`short_ts(ts)`** — 날짜부 생략, 시각부(`HH:MM:SS[.fff]`)만. 번들이 단일 윈도 내라 날짜 중복이 불필요. 날짜·기준시각은 프롬프트 상단 윈도/트리거에 전체 형식으로 한 번만 싣는다. (자정을 넘어 시작·끝 날짜가 갈리는 구간의 `MM-DD` 병기는 `bundle_parser.render_interval`이 담당.)
- **판정 상수·함수는 `signal_selector`와 공유** — `parse_ts` · `LEVEL_RE` · `LEVEL_ORDER` · `make_miner` · `metric_pairs` · `span_fields` · `TRACE_ERR_RE`. 압축(LLM 입력)과 Spring 전송 선별이 같은 판정을 쓰도록 두 벌로 관리하지 않음.

## 1. log — `compress_logs` (Drain 템플릿 dedup)

**방식**: [drain3](https://github.com/logpai/Drain3) 템플릿 마이닝으로 같은 패턴을 하나로 묶음. 정규식 하드코딩이 아니라 **데이터에서 템플릿을 학습**하므로, 사전에 모르는 로그 형식도 가변부를 학습해 dedup한다.

- **마스킹(클러스터링 전)** — 고카디널리티 토큰을 먼저 치환해 템플릿을 안정화: 타임스탬프(2종)·IP·HEX(8자 이상)·숫자(4자리 이상). 나머지 가변부(유저ID·경로·호스트명 등)는 Drain이 학습.
- **그룹 키** = `(서비스, 레벨, Drain 클러스터 ID)`. 레벨은 `FATAL|CRITICAL|ERROR|WARN(ING)|INFO|DEBUG|TRACE` 정규식 매칭, 없으면 `-`.
- **그룹당 1줄** = `서비스 · 레벨 · ×횟수 · 최초~최후 시각 · 원문 샘플 1건`(TSV). 건수 1이면 시각 하나만.
- **정렬** — 레벨 우선순위(`FATAL/CRITICAL/ERROR=0 → WARN=1 → 기타=2`) 후 횟수 내림차순. 에러·경고 패턴이 위로.
- **희귀 라인** — 저빈도 패턴은 자기 클러스터로 남아 원문 샘플이 보존됨. 압축 대상이 아니라 부각 대상.
- **효과** — 30만 줄 → N개 패턴 줄. 동일 에러 200건은 `×200` 1줄로 축약되며 정보 손실 없음.

## 2. metric — `compress_metrics` (통계 + 이상점)

**방식**: `metric_pairs`로 `(라벨, 값)`을 뽑아 `(서비스, 라벨)` 시리즈로 묶고, **트리거 시각 기준 baseline/incident**로 나눠 통계를 낸다.

- **파싱 형식(넓은 순)** — ① 평면 JSON `{"cpu":53.5}`(숫자 필드 전부) ② name·value JSON `{"metric":"cpu","value":53.5}`(우선) ③ Prometheus 노출형 `node_cpu{...} 2.22`(라벨셋 드롭) ④ `key=value` 텍스트. 모두 실패하면 **원문 통과**(아래).
- **구간 분리** — 트리거 이전 = baseline, 이후(또는 트리거 시각 파싱 불가 시 전량) = incident.
- **시리즈당 통계** — `n · mean · min · max`를 baseline·incident 각각.
- **이상점** — baseline 평균 `mu`, 표준편차 `sigma`(표본 1개면 0). incident 중 `|v-mu| > 3·sigma`(또는 `sigma=0`이면 평균과 다르기만 하면) 이탈로 보고, **onset**(첫 이탈)·**peak**(편차 최대)의 값·시각만 표기.
- **미파싱 폴백** — 파싱 불가 항목은 버리지 않고 `[시각] 원문`으로 말미에 통과. 손실보다 안전 우선.
- **효과** — 시계열 나열 제거, LLM 없이 결정적 산출. 서비스별 시리즈로 범인 후보를 좁힘.

## 3. trace — `compress_traces` (집계 + exemplar)

**방식**: `span_fields`로 `(오퍼레이션, 지연ms, 에러여부)`를 뽑아 `(서비스, 오퍼레이션)`별로 집계한다.

- **필드 추출(`span_fields`)** — 오퍼레이션은 `operation/operationName/name/to` 순, 지연은 `duration_us`(÷1000)/`duration_ms`/`duration` 순, 에러는 `status`·`http_status_code`에 `ERROR|TIMEOUT|FAIL*|5xx` 매칭. JSON 실패 시 텍스트에서 지연 정규식 + 에러 정규식 폴백.
- **집계 표** — `(서비스, 오퍼레이션)`별 `호출수 · 에러수 · p50/p95/max(ms)`. 에러수 → 호출수 내림차순 정렬.
- **볼륨 타임라인** — 서비스별 분단위(`HH:MM`) 스팬 수. **급감·소실 구간이 구조적 장애 신호**(kill 계열은 5xx가 아니라 스팬 소실로 드러남).
- **exemplar** — 가장 느린 스팬 상위 3 + 에러 스팬 상위 3(중복 제거)의 **원문 전체**를 `[시각] 서비스 원문`으로 첨부.
- **주의 — 압축과 판정의 분리** — 이 룰베이스는 raw→구조화 표까지만. "media-service가 사라졌으니 원인"이라는 **해석은 trace 심층 에이전트의 몫**. 단순 5xx 필터로는 범인을 못 짚음.

## 정답 유출 방지 (D-020/D-021)

- **제외** — 시나리오 제목·번들 ID는 프롬프트에서 뺌.
- **파일명은 포함** (PR #12에서 정정) — `modality_info` 구간의 `fileName`은 프롬프트에 실음. 없으면 `status=missing`이 "어딘가의 어떤 파일이 없었다"가 되어 진원 국소화 불가. 차폐 효과도 제한적이었음 — 서비스명은 `compress_logs`가 이미 노출 중. (구간 렌더링은 `bundle_parser.render_interval`이 담당.)
- **raw 보존** — 원본 raw는 파싱해 버리지 않고 무손실에 가깝게 재표현(D-021). 파싱 불가 시 원문 통과.

## 시각 표기

- **절대 시각** — 상대 오프셋(`T-42s`) 금지. 리포트 근거를 사람이 이해하려면 정확한 시각이 우선.
- **표기 축약** — `short_ts`로 날짜 생략, `HH:MM:SS[.fff]` 유지. 핵심 시각(dedup 최초/최후, metric onset/peak, trace exemplar)은 정밀 절대값 그대로.

## 공통 표현 규칙

1. **서비스별 그룹핑** — 원인 서비스 귀속을 도움.
2. **JSON 대신 TSV** — 키 반복을 없애 토큰 절감.
3. **프롬프트 캐싱 배치** — `시스템(고정) → raw(가변)` 순서 유지로 OpenAI 자동 캐싱 수혜(가변 데이터는 user 메시지).

## 절단 (truncate_input) — 최후 방어선

- 압축 후에도 입력 상한(`openai_max_input_chars`, 기본 120k자) 초과 시에만 발동. 코드: [app/agents/llm.py](../app/agents/llm.py).
- **트리거 시각 주변을 우선 보존**하며 절단, 경계에 `TRUNCATION_NOTICE`를 삽입해 부분 관측임을 프롬프트에 명시.

## 실데이터 검증 근거

SDK 데이터셋(`chok-v2-py-sdk/datasets/sn`, MVP 3종 시나리오)으로 규칙 적합성 확인.

| 모달리티 | 관측 사실 | 압축 적합성 |
|---|---|---|
| log | UserService 15만 줄, 에러 200건이 타임스탬프만 빼고 완전 동일. info도 `req_id`만 가변 | 매우 적합 — 수십 패턴으로 축약 |
| metric | `timestamp,value,metric,instance` 15초 간격. CPU가 특정 시각에 `2%→80%` 급변 | 매우 적합 — 순수 수치 통계 |
| trace | `parent_span_id·duration_us·http_status_code·service/operation` 완비. status는 `200`·공란뿐(5xx 없음) | 압축 적합 / 판정은 에이전트 몫 |
