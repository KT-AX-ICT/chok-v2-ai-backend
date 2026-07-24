# Spring 전송 시 모달리티별 신호 선별 (PR C) — 설계

작성일 2026-07-24. 구현 전 결정 기록.

## 배경

PR B(#11)로 번들 원본 3종(`logs`/`metrics`/`traces`)을 **우리 DB 밖 파일로** 이전해 `ingest_job` 저장 시의 용량 초과를 제거함. 다만 **Spring 전송 경로는 그대로**임.

- `spring_client._result_payload()`가 `bundle.model_dump(by_alias=True)`로 3종 배열을 **전량** 적재
- `raw_normalizer.normalize_payload_signals()`는 각 항목의 `raw` 문자열만 변환할 뿐 **개수를 줄이지 않음**
- Spring은 FastAPI와 **같은 MySQL 인스턴스**를 사용 → `max_allowed_packet` 한계가 동일

즉 로그 30만 줄 번들에서 PR B로 고친 증상이 Spring 쪽에서 재현 가능한 상태.

추가로, 이 3종 배열은 단순 보관용이 아니라 **Spring이 상세 화면 evidence(`lines`/`spans`/`items`)를 조립하는 원천**(`docs/spring-contract.md`). 따라서 선별 기준이 최종 사용자가 보는 근거 목록을 결정함.

## 목표

- 모달리티별 전송 항목을 **상한(기본 200) 이내로 축소**해 Spring 저장 실패 위험 제거
- 남기는 항목은 **장애 확인에 유효한 것** 우선 — 화면 evidence의 진단 가치 유지
- 대량 중복 패턴이 상한을 독식하지 않도록 **유형 다양성 확보**
- 잘린 사실을 사용자가 **오해 없이 인지**하도록 고지

## 비목표

- LLM 분석 입력 축소 — 압축기(`bundle_compression`)는 전량 집계가 전제이며 이번 변경과 무관
- Spring 계약 필드 추가 — 기존 필드만 사용, 타팀 작업 유발 없음
- DB 스키마 변경 — 마이그레이션 없음

## 결정사항

| 항목 | 결정 | 근거 |
|---|---|---|
| 선별 기준 | **진단 가치 우선** | 에러·이상점·느린 스팬이 장애 확인의 핵심. 시간 균등 분포는 정작 에러 구간을 희석 |
| 중복 처리 | **그룹 라운드로빈** | 고정 쿼터는 그룹 수에 따라 튜닝 필요. 라운드로빈은 자동 조정 |
| 구현 위치 | **전송 직전**(`spring_client`) | 스키마 변경 0, PR B 방향(무거운 건 DB 밖)과 일관, LLM은 여전히 전량 관측 |
| 절단 고지 | **`evidence.*.source`에 코드가 삽입** | 모달리티별 `source`가 이미 분리돼 있어 개별 표기 가능. 선별은 LLM 실행 **이후**라 LLM에게 맡길 수 없음 |
| 고지 범위 | **항목이 있으면 항상 표기**(잘림·전량 모두) | 잘림/전량이 구분되어, 문구 부재를 "정보 없음"으로 오해할 여지 제거. 빈 배열은 표기할 건수가 없어 예외 |
| 상한 설정 | **`spring_signal_limit: int = 200`** 공통 1개 | 세 모달리티 동일 적용. 운영 중 환경변수로 조정 가능 |

### 채택하지 않은 안

- **분석 직후 선별해 DB 저장** — 컬럼 추가(마이그레이션) 필요하고, 600행을 다시 DB에 넣어 PR B 방향과 역행
- **ingest 시점에 줄여 파일 저장** — 압축기의 "30만 건 → N패턴", "3σ 이상점" 통계 근거가 소실되어 LLM 분석 품질 저하
- **Spring 계약에 건수 필드 추가** — 가장 정확하나 Spring 스키마·API·프론트 작업이 따라붙어 타팀 일정에 묶임
- **`truncated` 유사 플래그** — 전체/전송 건수로 완전히 유도되는 중복

## 설계

### 데이터 흐름

```
job_queue / delivery_reconciler
    ↓ bundle_store.restore_bundle()      원본 전량 (파일에서 복원)
spring_client._result_payload()
    ↓ signal_selector.select_signals()   ← 신설: 전량 → 상한 이내 + 건수
    ↓ 고지 문구를 evidence.*.source에 삽입
    ↓ normalize_payload_signals()        기존: raw → 모달리티별 JSON
    → POST /api/internal/reports
```

### 신설 모듈 — `app/services/signal_selector.py`

**책임**: "원본 항목 리스트 → 진단 가치 상위 N개". 순수 함수, I/O·부작용 없음.

**판정 로직은 `bundle_compression`에서 재사용** — 중복 구현 금지. 필요한 것만 공개 승격:

| 재사용 대상 | 현재 이름 | 용도 |
|---|---|---|
| 레벨 파싱·우선순위 | `_LEVEL_RE`, `_LEVEL_ORDER` | log 항목 등급 |
| Drain 마이너 | `_make_miner` | log 패턴 그룹핑 |
| 스팬 필드 추출 | `_span_fields` | trace 에러 여부·지연 |
| 시각 파싱 | `_parse_ts` | metric baseline 분리 |

압축기 자체는 변경 없음 — LLM 입력용(전량 집계)과 Spring 전송용(부분집합)은 목적이 다르므로 분리 유지.

**반환 형태**: 선별 결과와 건수를 함께 반환해 호출부가 고지 문구를 만들 수 있게 함.

```python
class Selection(NamedTuple):
    items: list[dict]   # 선별된 항목 (timestamp 오름차순)
    total: int          # 원본 건수
```

### 선별 알고리즘 — 그룹 라운드로빈

```
1) 그룹핑        log    → Drain 클러스터 (압축기와 동일 기준)
                 metric → (서비스, 라벨) 시리즈
                 trace  → (서비스, 오퍼레이션)

2) 그룹 정렬     에러/이상 포함 그룹 우선 → 그룹 크기 내림차순

3) 그룹 내 정렬  log    : ERROR/FATAL > WARN > 기타, 동급은 시간순
                 metric : 3σ 이상점(onset·peak) 우선, 나머지는 시간순
                 trace  : 에러 스팬 > 느린 스팬 > 기타

4) 라운드로빈    그룹을 순회하며 1건씩 추출, 상한 도달까지 반복
                 (소진된 그룹은 건너뜀)

5) 최종 정렬     timestamp 오름차순 — 화면이 시간 흐름을 따르도록
```

**다양성 보장**: 25만 건이 한 패턴이어도 한 바퀴에 1건씩만 가져가므로, **그룹 수가 상한 이하인 한 모든 패턴이 최소 1건씩 포함**됨. 그룹 수가 상한을 넘으면 2단계 우선순위(에러 포함 → 크기) 순으로 앞에서부터 채우고 나머지는 누락 — 이 경우 누락 그룹 수를 서버 로그에 남김. 반대로 그룹 수가 상한보다 적으면 남는 자리를 계속 돌며 채워 상한을 모두 사용.

**결정성**: 모든 정렬에 tie-breaker로 원본 인덱스를 포함 → 같은 입력이면 항상 같은 결과. 재전송 시에도 동일 200개가 나가 멱등키(`triggerTime`) 정책과 무충돌.

**metric 이상점 입력**: 3σ 판정은 트리거 시각 기준 baseline 분리가 전제이므로 `select_signals()`에 `trigger_time`을 전달. 파싱 불가 시 이상점 우선순위만 생략하고 나머지 규칙으로 동작.

### 절단 고지

`spring_client`가 payload 조립 시 모달리티별로 `evidence.<modality>.source` 끝에 덧붙임.

```
잘림  : "{기존 source} (전체 300000건 중 주요 200건 수록)"
전량  : "{기존 source} (전체 1200건 전량 수록)"
```

- `source`가 없으면(LLM 미기재, optional 필드) 고지 문구만으로 새로 생성
- `conclusion`은 손대지 않음 — LLM 결론과 코드가 쓴 사실을 섞지 않음
- 문구는 코드가 실제 선별 결과로 작성 → 항상 정확. LLM은 선별 이후 단계를 알 수 없으므로 위임 불가

### 엣지 케이스

| 상황 | 처리 |
|---|---|
| 항목이 상한 이하 | 선별·그룹핑 생략, 전량 통과. 고지는 "전량 수록" |
| 빈 배열 | 빈 배열 유지. 고지 생략(표기할 건수가 없음) |
| FAILED 경로(`_failure_payload`) | 선별 **동일 적용**(크기 위험 동일). `result`가 없어 고지할 자리가 없으므로 서버 로그에만 기록 |
| 원본 파일 소실 후 재전송 | 경량 번들이라 배열이 이미 비어 있음 → 선별이 무해하게 통과 |
| 재전송 | 결정적 선별이라 같은 결과 |

## 작업 범위

| 파일 | 작업 |
|---|---|
| `app/services/signal_selector.py` | 신설 — 그룹핑·정렬·라운드로빈 선별 |
| `app/services/bundle_compression.py` | 재사용 대상 4종을 공개 함수·상수로 승격 |
| `app/services/spring_client.py` | 두 payload 조립부에 선별 적용 + 고지 문구 삽입 |
| `app/core/config.py` | `spring_signal_limit: int = 200` 추가 |
| `tests/test_signal_selector.py` | 신설 |
| `tests/test_spring_client.py` | 고지 문구·FAILED 경로 상한 검증 보강 |

## 테스트

| 검증 | 내용 |
|---|---|
| 상한 이하 전량 | 상한 미만이면 입력 그대로 반환 |
| **다양성** | 동일 패턴 대량 + 희귀 패턴 3종 → 희귀 패턴이 반드시 포함 |
| 우선순위 | ERROR가 INFO보다, 에러 스팬이 정상 스팬보다 우선 |
| 결정성 | 동일 입력 2회 호출 → 완전히 같은 결과 |
| 정렬 | 반환값이 timestamp 오름차순 |
| 고지 문구 | 잘림/전량 각각의 표기, `source` 부재 시 신규 생성 |
| FAILED 경로 | `_failure_payload`도 상한 적용 |
| 빈 입력 | 빈 배열에 예외 없음, 고지 생략 |
