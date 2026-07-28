# 진원 후보 하이라이트 (lifecycle 마커 + 트리거 로그) 설계

작성 2026-07-28. 브랜치 `feat/incident-signal-highlight` (base: `feat/baseline-injection`).
출처: baseline 주입 측정 후속 — kill_media 정답(SERVICE_DOWN/media) 미도달 원인 분석.

## 배경

baseline 주입(PR #26)이 만성 노이즈 오진은 없앴으나 kill_media 정답엔 미도달. 정밀 진단 결과
**정답 신호는 데이터에 있으나 surprise 정렬이 그것을 뒤로 밀어** 못 보는 것이 원인:

- kill_media: `triggered_by=['log']`, 트리거 시각 `08:35:31.961753`에 **`Starting the media-service server`**
  로그(= media가 죽었다 살아난 재시작). 그러나 같은 마커가 base에도 1번(초기 부팅), baseline=1이라
  surprise = 1/(1+1+1) = 0.33 → 하단에 묻힘. log 에이전트는 "재시작 신호 없음"으로 결론.
- code_media: `triggered_by=['log','trace']`, 트리거 시각에 **`getaddrinfo media-service` / `Failed to
  connect media-service-client`**(진원=media) 존재. 그러나 최종 service는 nginx로 샘.

즉 **저빈도지만 결정적인 신호(서비스 재시작·연결 실패)를 surprise·정렬과 무관하게 진원 후보로
부각**해야 한다.

## 목표

- `compress_logs` 출력 맨 위에 **"진원 후보(하이라이트)"** 섹션 추가:
  - **lifecycle 마커** — 서비스 시작/재시작/종료 로그를 base/incid/baseline 카운트와 함께 노출
    (재시작한 서비스가 드러나게).
  - **트리거 로그** — `triggered_by`에 `log`가 있을 때, 트리거 시각 ±윈도의 로그(에러/lifecycle 우선,
    dedup, 상한). 로그 트리거 사건의 진원을 콕 집음.
- 프롬프트로 "재시작/시작 로그가 평소보다 늘거나 incid쪽에 있으면 그 서비스가 다운 → SERVICE_DOWN/
  그 서비스", "트리거 로그의 특정 서비스 연결 실패 → 그 대상이 진원" 유도.
- 하네스로 before(baseline 주입 `193a0899982c`)/after 측정. 목표: kill_media → SERVICE_DOWN/media,
  가능하면 code_media service→media.

## 비목표 (YAGNI)

- metric/trace 압축 변경 — 이번 레버는 로그의 lifecycle·트리거 신호. metric/trace는 baseline 브랜치 상태 유지.
- 트리거 라인 원문을 SDK가 알려주는 계약 신설 — `trigger_time` + `triggered_by`(모달리티)만으로 충분.
- lifecycle 완전 일반화 — 서버 생애주기 키워드셋(휴리스틱)으로 시작. 문서에 한계 명시.

## 설계 상세

### 1. lifecycle 마커 탐지 (`bundle_compression.py`)

- 정규식 `_LIFECYCLE_RE` — 서버 생애주기 특정(일반 "start" 회피):
  `starting the .*server` · `server (started|starting|stopped|listening)` · `shutting down|shutdown` ·
  `received sig(kill|term)` · `out of memory|OOM` · `killed|panic|terminated|exiting`.
- kill_media 실측: 전 서비스가 08:33에 1회 부팅(정상), **media만 2회**(재시작). 총 12건 — 플러딩 아님.

### 2. 하이라이트 섹션 (`compress_logs`, trigger_time 있을 때만)

정상 dedup 목록 **앞에** 별도 섹션. 두 그룹(있는 것만):

- **lifecycle 마커**: lifecycle 매칭 라인을 기존 그룹핑 재사용해 `서비스 · ×count(base/incid[/baseline])
  · 시각 · 샘플`로. 정렬 `(incid desc, count desc)`. → media 재시작(incid=1)이 남들(incid=0) 위로.
- **트리거 로그** (`'log' in triggered_by`): 트리거 시각 ±`_TRIGGER_WINDOW_S`(기본 3초) 라인.
  (서비스,템플릿) dedup, **ERROR/WARN 우선** 후 INFO, 상한 `_HIGHLIGHT_CAP`(기본 8). → code_media의
  `getaddrinfo`/`Failed to connect media` 노출, cpu(metric 트리거)는 섹션 자체 생략.

둘 다 비면 섹션 생략. 정상 목록·surprise 정렬·base/incid·baseline은 그대로(회귀 없음).

### 3. 프롬프트 (`log.md`)

`## 입력 형식`에 하이라이트 섹션 설명 추가. 지침:
- "**진원 후보 하이라이트**에 나온 서비스를 최우선 검토한다."
- "서비스 **시작/재시작 로그**가 평소(baseline)보다 늘었거나 트리거 이후(incid)에 찍혔으면, 그 서비스가
  다운됐다 살아난 것 → 장애 유형 SERVICE_DOWN, 진원 service=그 서비스."
- "트리거 로그에 특정 서비스로의 **연결 실패**(`getaddrinfo <svc>`·`Failed to connect <svc>`)가 있으면
  그 대상 서비스가 진원."

(report.md는 손대지 않음 — log evidence가 진원을 명확히 짚으면 assemble의 service 앵커링으로 전달됨.)

## 테스트

유닛(`tests/test_bundle_compression.py`):
- lifecycle 라인(예: `Starting the media-service server`)이 낮은 surprise여도 **하이라이트 섹션에 노출**,
  base/incid 카운트 포함.
- `'log' in triggered_by`일 때 트리거 시각 라인이 하이라이트에, 아닐 때(metric만) 트리거 로그 그룹 생략.
- lifecycle·트리거 신호 없으면 하이라이트 섹션 자체 없음.
- 트리거 없으면(하위호환) 하이라이트 없음 + 기존 정렬 폴백(기존 테스트 회귀 없음).
- 상한(`_HIGHLIGHT_CAP`) 초과 시 절단.

## 측정 (하네스)

`python -m eval.run` → before `193a0899982c`(baseline 주입) / after. 목표: kill_media SERVICE_DOWN/media
전환, code_media service→media 근접. 결과를 본 문서 표에 기입.

## 리스크

- **lifecycle 키워드 휴리스틱** — SN 로그 기준. 다른 시스템은 매칭 누락 가능 → 섹션 없으면 기존 동작으로
  안전 degrade(신호 손실 없음, 부각만 안 됨).
- **트리거 윈도 노이즈** — `'log' in triggered_by` 게이트 + 에러 우선 + 상한으로 억제. 그래도 정상 트래픽이
  섞일 수 있으나 "후보"로만 제시.
- **출력 토큰 증가** — 상한으로 제한(수 줄). 프롬프트 캐싱 접두엔 영향 없음(user 메시지측).

## 산출물 / 브랜치

- 브랜치 `feat/incident-signal-highlight` (base `feat/baseline-injection`, PR #26 머지 후 main 리베이스).
- 커밋 대상: `app/services/bundle_compression.py`, `app/agents/prompts/log.md`, 유닛테스트, 본 문서.
