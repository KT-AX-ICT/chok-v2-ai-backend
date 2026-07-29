# FastAPI 백엔드 테스트 케이스

RCA 파이프라인(수집 → 압축 → LLM 오케스트레이션 → 검증 → Spring 전송) FastAPI 백엔드의 pytest 스위트 전수 기록.

## 개요

- **총계**: 231개 테스트, 전부 통과(0 실패), 실행 ~15초.
- **러너**: pytest, `asyncio_mode=auto`(설정은 `pyproject.toml [tool.pytest.ini_options]`) — async 테스트에 데코레이터 불필요.
- **외부 의존 없음**: 실제 LLM 호출·네트워크·라이브 서버 없이 목(monkeypatch/MockTransport)·인메모리 SQLite로 동작(무과금).
- **실행**: `uv run pytest` (특정 파일: `uv run pytest tests/test_ingest.py -v`).
- 파일 26개 = 테스트 함수 222개 + 파라미터라이즈 확장 9개(`test_prompts` 프롬프트 6종, `test_agents` 팩토리 5조합) = 231.
- 그룹별 합계: 수집 API·인증 21 · 번들 처리·압축 74 · RCA 에이전트·그래프 46 · 계약·스키마 검증 25 · 잡 처리·전송(Spring) 60 · 인프라 5 = **231**.

### 표 컬럼 정의

각 파일의 표는 4열이며, 행은 그 파일의 테스트 함수(파라미터라이즈는 1행으로 묶음, 총 222행)에 대응한다.

- **기능명**: 그 테스트가 검증하는 기능/시나리오의 한국어 명사구(함수명 아님).
- **검증 내용**: 무엇을 어떤 조건/입력으로 확인하는지(행위).
- **기대 결과**: 그래서 무엇이 나와야 하는지(단언 대상).
- **실제 결과**: 전부 `통과`.

---

## 1. 수집 API·인증 (21)

### tests/test_ingest.py (11)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 정상 번들 수집 | 정상 번들을 POST /ingest | 201 + 정수 job_id | 통과 |
| job_id 순차 발급 | 번들 2회 연속 수집 | 뒤 job_id > 앞 job_id | 통과 |
| 잡 상태 조회 | 수집 후 GET /ingest/{id} | 200 + status가 5종 enum 내 | 통과 |
| 없는 잡 조회 | 미존재 job_id 조회 | 404 | 통과 |
| 필수 필드 누락 거부 | 필수 필드 빠진 번들 수집 | 422 | 통과 |
| triggered_by 값 제약 | triggered_by에 비허용 값 수집 | 422 | 통과 |
| 타임스탬프 형식 검증 | window.start 비 ISO-8601 수집 | 422 | 통과 |
| DB 오류 응답 정리 | commit 중 DB 예외 발생 | 500 아닌 503 | 통과 |
| 레거시 필드 관용 | 구형 present 필드 포함 수집 | 201(무시) | 통과 |
| 경량 번들·원본 분리 저장 | 수집 후 DB/파일 저장 형태 확인 | DB엔 경량+파일명, 파일 복원 시 원문 일치 | 통과 |
| 빈 모달리티 수집 | 모달리티 배열 없는 최소 번들 | 201 | 통과 |

### tests/test_ingest_auth.py (7)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 인증 헤더 누락 차단 | 키 설정 상태에서 X-API-Key 없이 POST | 401 | 통과 |
| 잘못된 키 차단 | 틀린 키로 POST | 401 | 통과 |
| 올바른 키 허용 | 정상 키로 POST | 201 | 통과 |
| 무중단 키 교체 | 쉼표 목록 중 두 번째 키로 POST | 201 | 통과 |
| 키 미설정 통과 | 키 미설정 상태에서 헤더 없이 POST | 201 | 통과 |
| 조회 API 인증 보호 | 키 설정 상태에서 미존재 id GET 조회 | 404 아닌 401 | 통과 |
| 헬스 무인증 접근 | 키 설정 상태에서 GET /health | 200 | 통과 |

### tests/test_health.py (3)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 헬스 정상 | DB 도달 가능 상태에서 GET /health | 200 + status=ok | 통과 |
| 헬스 DB 장애 | DB execute 예외 상태에서 GET /health | 503 | 통과 |
| 무키 기동 차단 | OPENAI_API_KEY 없이 lifespan 진입 | RuntimeError(OPENAI_API_KEY) | 통과 |

---

## 2. 번들 처리·압축 (74)

### tests/test_bundle_compression.py (25)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 반복 에러 dedup | 동일 에러 200회(시각·req_id만 변) 압축 | 1패턴 ×200, 최초~최후 시각·원문 샘플 유지 | 통과 |
| 희귀 라인 보존·에러 우선 | INFO 다수+ERROR 1건 압축 | 에러 패턴 상단·희귀 원문 유지·×2 | 통과 |
| 미학습 형식 일반화 | 하드코딩 안 된 sshd/포트/호스트 로그 30건 | 1패턴 ×30 수렴 | 통과 |
| 빈 로그 처리 | 빈 로그 리스트 압축 | (없음) | 통과 |
| 급성 우선 정렬 | 만성·급성 패턴 혼재 압축(surprise 기준) | 급성 패턴이 만성보다 상단 | 통과 |
| 트리거 전후 건수 표기 | trigger_time 기준 전/후 로그 압축 | base=1 incid=2 표기 | 통과 |
| 트리거 없을 때 정렬 회귀 | trigger_time 없이 압축 | 레벨/건수 정렬(ERROR 먼저) | 통과 |
| baseline 부재 안전 | 프로파일 파일 없는 상태 로드 | 빈 dict | 통과 |
| 재시작 하이라이트 | surprise 낮은 서비스 재시작 로그 압축 | 진원 하이라이트에 재시작 노출 | 통과 |
| 트리거 시각 로그 하이라이트 | log 트리거 시 트리거 시각 연결실패 로그 압축 | 하이라이트에 트리거 시각·해당 로그 | 통과 |
| 비-log 트리거 그룹 생략 | metric만 트리거 시 압축 | 트리거 로그 그룹 미노출 | 통과 |
| 신호 없으면 하이라이트 없음 | lifecycle·트리거 신호 없는 로그 압축 | 하이라이트 섹션 부재 | 통과 |
| 트리거 없으면 하이라이트 없음 | trigger_time 없이 압축 | 하이라이트 부재 | 통과 |
| 이상 시리즈 우선 | 알파벳 뒤지만 이탈 큰 시리즈 포함 압축 | 이상 시리즈 상단 | 통과 |
| 이탈 onset·peak 검출 | baseline→급변 메트릭 압축 | onset·peak 지점, base/incid n 표기 | 통과 |
| 평면 JSON 메트릭 파싱 | {"cpu_usage":..} 형식 압축 | 통계 산출(원문 통과 아님)·peak 표기 | 통과 |
| name/value JSON 파싱 | {"metric","value"} 쌍 압축 | metric 값이 라벨 | 통과 |
| Prometheus 노출형 파싱 | 'node_cpu{labels} value' 압축 | 라벨셋 드롭·지표명 시리즈 | 통과 |
| bool 오인 방지 | {"healthy":true} 압축 | 숫자 없음→미파싱 원문 통과 | 통과 |
| 미파싱 메트릭 폴백 | 형식 불명 메트릭 압축 | 원문 통과 표기·원문 유지 | 통과 |
| 빈 메트릭 처리 | 빈 리스트 압축 | (없음) | 통과 |
| 트레이스 집계 | 오퍼레이션 11스팬(에러 1) 압축 | ×11·err=1·max지연·분단위 타임라인+에러 exemplar 원문 | 통과 |
| OTel name 인식 | operation 없이 name 키 스팬 압축 | name을 오퍼레이션 승격(? 강등 없음) | 통과 |
| 평문 스팬 폴백 | 평문 'span 15000ms TIMEOUT' 압축 | ?·×1·err=1 집계+원문 exemplar | 통과 |
| 빈 트레이스 처리 | 빈 리스트 압축 | (없음) | 통과 |

### tests/test_bundle_parser.py (14)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| log 에이전트 입력 구성 | 번들을 log 에이전트용 파싱 | logs·trigger_time·window_start 포함 | 통과 |
| 구간 요약 포함 | log 파싱의 log_intervals | status(missing) 포함 | 통과 |
| 구간 파일명 포함 | log 파싱의 구간 요약 | 파일명 포함 | 통과 |
| 빈 필드 생략 | 값 없는 필드 구간 렌더 | 해당 필드 줄에서 생략 | 통과 |
| 양쪽 건수 표기 | record/total 있는 구간 렌더 | 1523/20000건·시각만(날짜·파일명 없음) | 통과 |
| 단일 건수 구분 | 한쪽 건수만 있는 구간 렌더 | '1523건'/'원본 20000건' 구분 | 통과 |
| 임의 status 금지 | status 없는 구간 렌더 | status·'ok' 미출력 | 통과 |
| 자정 넘김 날짜 병기 | 시작·끝 날짜 다른 구간 렌더 | MM-DD 병기 | 통과 |
| 동일자 날짜 생략 | 같은 날 구간 렌더 | 시각만·날짜 없음 | 통과 |
| metric 에이전트 입력 | metric 파싱 결과 | 라벨·값 통계 유지 | 통과 |
| trace 에이전트 입력 | trace 파싱 결과 | 스팬 원문 포함 | 통과 |
| 빈 모달리티 플레이스홀더 | logs 빈 번들 파싱 | (없음) | 통과 |
| 빈 구간 플레이스홀더 | trace 구간 빈 파싱 | (없음) | 통과 |
| triggered_by 타입 제약 | 비허용 triggered_by로 TriggerInfo 생성 | ValidationError | 통과 |

### tests/test_bundle_store.py (10)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 경량 번들 분리 | split_and_save 결과 경량 측 확인 | 3종 배열 제거·메타 보존 | 통과 |
| 입력 불변성 | split 후 원본 dict 확인 | 원본 배열 유지 | 통과 |
| 파일 시그널 전용 | 저장 파일 내용 확인 | 3종 시그널만·메타 제외 | 통과 |
| 원본 복원 왕복 | 경량+파일 결합 복원 | 원본 IngestBundle과 동일 | 통과 |
| 레거시 인라인 복원 | 파일경로 없이 복원 | 번들 인라인 배열 사용 | 통과 |
| 파일 소실 감지 | 파일 삭제 후 복원 | SignalsMissing | 통과 |
| 파일 손상 감지 | 깨진 파일 복원 | SignalsMissing | 통과 |
| 파일 삭제 멱등 | discard 2회+None 인자 | 삭제되고 예외 없음 | 통과 |
| 사용 중 파일 보존 | keep 집합 포함 고령 파일 sweep | 사용 중 보존·나머지 회수 | 통과 |
| 고아 파일 회수 | 임계 초과·이하 파일 sweep | 초과분만 삭제 | 통과 |

### tests/test_raw_normalizer.py (12)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| JSON 로그 정규화 | level/message JSON normalize_log | {level, msg} | 통과 |
| 텍스트 로그 폴백 | 평문 로그 정규화 | 레벨 추출+원문 한 줄 msg | 통과 |
| 레벨 불명 처리 | 레벨 없는 평문 정규화 | 빈 level+원문 msg | 통과 |
| name/value 메트릭 정규화 | metric/value/threshold/exceeded JSON | 4필드 정규화 | 통과 |
| 평면 JSON 메트릭 | {"cpu_usage":53.5} 정규화 | label/value 매핑·exceeded=None | 통과 |
| Prometheus 텍스트 메트릭 | 노출형 텍스트 정규화 | label·value 추출 | 통과 |
| 미파싱 메트릭 키 유지 | 형식 불명 메트릭 정규화 | 빈 키 구조 | 통과 |
| JSON 트레이스 정규화 | traceId/from/to/duration/status JSON | 필드 매핑 | 통과 |
| OTel 트레이스 정규화 | name·duration_us 정규화 | name→to, us→ms 환산 | 통과 |
| 평문 트레이스 폴백 | 평문 스팬 정규화 | duration·status 추출·traceId 빈값 | 통과 |
| 페이로드 raw 일괄 정규화 | 3종 배열 raw 정규화 | 각 raw 정규화 치환 | 통과 |
| 키 부재 관용 | 3종 키 없는 payload 정규화 | 변화 없음·무해 | 통과 |

### tests/test_signal_selector.py (13)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 상한 이하 통과 | 10건, limit 200 선별 | 입력 그대로·truncated=False | 통과 |
| 상한 절단 | 500건, limit 200 선별 | 200건·total=500·truncated=True | 통과 |
| 빈 입력 안전 | 빈 리스트 선별 | 빈 결과·total 0 | 통과 |
| 희귀 패턴 생존 | 지배 5000건+희귀 3건 선별 | 희귀 3종 잔존·지배는 독식 못함 | 통과 |
| 에러 우선 선별 | INFO 다수+ERROR 5건, limit 50 | ERROR 5건 모두 포함 | 통과 |
| 에러 스팬 우선 | 정상 400+에러 3스팬 선별 | 에러 3건 포함 | 통과 |
| 이상 메트릭 우선 | baseline+급등 3점 선별 | 급등 3건 선별 | 통과 |
| 미파싱 후보 잔존 | baseline+파싱불가 1건 선별 | 미파싱 항목 포함 | 통과 |
| 선별 결정성 | 동일 입력 2회 선별 | 동일 결과 | 통과 |
| 시간순 정렬 | 선별 결과 timestamp 확인 | 오름차순 | 통과 |
| 혼합 형식 시간 정렬 | 정밀도·오프셋 혼재 timestamp 선별 | 실제 시각 기준 정렬 | 통과 |
| 동일 시각 순서 유지 | 동일 시각 300건 선별 | 수집기 순서 유지·결정적 | 통과 |
| 그룹 초과 로깅 | 그룹 수 > 상한 선별 | '누락' 로그 기록 | 통과 |

---

## 3. RCA 에이전트·그래프 (46)

### tests/test_agents.py (12 · 함수 8개, `test_agent_factory_wiring` 5조합)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 트리거 모달리티 deep 승격 | LLM이 scan 반환·triggered 모달리티 존재 | triggered는 deep 승격 | 통과 |
| 비트리거 결정 존중 | 비트리거 모달리티에 LLM deep 결정 | deep 유지(역방향 강등 없음) | 통과 |
| router 실패 폴백 | router 예외 발생 | 전 모달리티 deep | 통과 |
| router 메타 전용 입력 | router 메시지 구성 | 건수·트리거 시각 포함·raw 미포함 | 통과 |
| 수집/원본 건수 병기 | totalCount 있는 구간 메시지 | 받은 건수+원본 합 병기 | 통과 |
| 구간 파일명 노출 | 파일명 있는 구간 메시지 | 파일명 포함 | 통과 |
| 모달리티 에이전트 배선 | (모달리티,모드) 5조합 팩토리 | deep→mini·모달리티 프롬프트, scan→nano·scan 프롬프트 | 통과 |
| user 메시지 구성 | log 파싱 후 user 메시지 | 모달리티·트리거 시각·압축 원문 샘플 포함 | 통과 |

### tests/test_graph.py (5)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 정상 오케스트레이션 | plan대로 라우팅·조립 실행 | plan 라우팅+origin 승격·evidence 주입, 5키 계약 통과 | 통과 |
| 빈 모달리티 LLM 생략 | trace 0건 실행 | LLM 미호출·'데이터 없음' evidence, service=draft | 통과 |
| 부분 실패 완주 | log 에이전트 예외 | '분석 실패' evidence·나머지 정상 결론 | 통과 |
| report 실패 전파 | report 에이전트 예외 | RuntimeError 전파 | 통과 |
| router 실패 완주 | router 예외 발생 실행 | 전 모달리티 deep 완주·type 산출 | 통과 |

### tests/test_pipeline.py (7)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 오케스트레이터 결과 계약 | fake 오케스트레이터 실행 | 5키 계약 통과·service=draft 우선 | 통과 |
| 빈 모달리티 계약 유지 | 전 모달리티 0건 실행 | 계약 유지·service=draft·'데이터 없음' | 통과 |
| 노드 교체 가능 | draft service/type 교체 실행 | 교체값 반영(service·type) | 통과 |
| 대표 서비스 폴백 | trace origin·draft.service 모두 빈 값 | service=UNKNOWN | 통과 |
| report 서비스 우선 | trace origin과 draft.service 상충 | draft.service 채택 | 통과 |
| 큐 통합 완주 | 큐→오케스트레이터→검증→전송 성공 | status=DONE·detail 5키 | 통과 |
| 전송 실패 상태 유지 | 전송 실패 실행 | DELIVERING·result 저장 | 통과 |

### tests/test_report_llm.py (6)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 교차 생존 신호 | 서비스별 log/metric/trace status 산출 | {log:missing, metric:data, trace:empty} | 통과 |
| 최강 관측 대표 | 동일 서비스 다구간 관측 | data>empty>missing 중 강한 값 | 통과 |
| report 신호 라인 | report 메시지 구성 | 서비스별 관측 신호 라인 포함 | 통과 |
| 에러 레벨 집계 | 로그 레벨별 카운트 | error/fatal만 집계(warn·info 제외) | 통과 |
| affected 에러 수 주입 | draft assemble 시 로그 에러 수 매칭 | 서비스별 errors 채움(정규화 매칭) | 통과 |
| 로그 없는 서비스 처리 | 로그 없는 서비스 assemble | errors=None | 통과 |

### tests/test_prompts.py (8 · 함수 3개, `test_loads_all_prompts...` 6종)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 프롬프트 로딩 | 6개 프롬프트 각각 로드 | '# 공통 규칙' 접두+'# 역할:' 본문 결합 | 통과 |
| 미등록 프롬프트 거부 | 화이트리스트 외 이름 로드 | ValueError | 통과 |
| 프롬프트 캐시 | 동일명 2회 로드 | 동일 객체 반환 | 통과 |

### tests/test_llm_layer.py (7)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| LLM 생성 파라미터 | make_llm 모델·effort 적용 | 모델·reasoning_effort·max_retries=3 | 통과 |
| effort별 타임아웃 | low/medium/high effort | 60/180/300초 | 통과 |
| 세마포어 싱글턴 | llm_limit 2회 호출 | 동일 객체·용량 4 | 통과 |
| 동시성 상한 | 세마포어(2)로 6작업 동시 실행 | 피크 ≤2 | 통과 |
| 상한 이하 무변경 | 짧은 입력 truncate | 원본 그대로 반환 | 통과 |
| 트리거 주변 보존 절단 | 트리거 시각 포함 긴 입력 절단 | 길이 상한·트리거 보존·고지 2회 | 통과 |
| 중앙부 보존 절단 | 트리거 미매칭 긴 입력 절단 | 길이 상한·중앙 보존·고지 삽입 | 통과 |

### tests/test_analyze_baseline.py (1)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| baseline 프로파일 집계 | 로그 디렉터리에서 build_profile | user ERROR count=5 | 통과 |

---

## 4. 계약·스키마 검증 (25)

### tests/test_contracts_taxonomy.py (4)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| confidence 필드 제거 | Rca 모델 필드 확인 | confidence 없음 | 통과 |
| 페이로드 confidence 제거 | 결과 dump 확인 | rca에 confidence 없음 | 통과 |
| RcaType enum 값 | RcaType 인자 확인 | 6종(SERVICE_DOWN/CODE_STOP/PERFORMANCE/DEPENDENCY/OTHER/NONE) 일치 | 통과 |
| 자유 type 거부 | enum 외 type로 ReportDraft 생성 | ValidationError | 통과 |

### tests/test_rca_validation.py (6)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 인스턴스 통과 | RcaResult 인스턴스 검증 | 동일 객체 반환 | 통과 |
| dict 파싱 | 유효 dict 검증 | RcaResult 인스턴스 | 통과 |
| 5키 누락 거부 | actions 삭제된 결과 검증 | RcaResultInvalid(actions 사유) | 통과 |
| 누락 사유 코드 | actions 누락 결과 검증 | 'detail.actions[missing]' | 통과 |
| 타입 불일치 구분 | actions에 문자열 주입 검증 | 사유에 detail.actions·missing 아님 | 통과 |
| 비객체 입력 거부 | 문자열 검증 | RcaResultInvalid | 통과 |

### tests/test_schema_errors.py (10)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 누락 경로·타입 표기 | 필드 누락 오류 요약 | 'inner[missing]'·'Field required' | 통과 |
| 중첩 경로 표기 | 중첩 필드 누락 요약 | 'inner.name[missing]' | 통과 |
| 다중 오류 결합 | 2개 오류 요약 | ' \| '로 결합 | 통과 |
| 입력값 미로깅 | 오류에 input 값 포함 요약 | 값(비밀값 등) 미노출 | 통과 |
| 커스텀 메시지 보존·상한 | 긴 value_error msg 요약 | 메시지 보존하되 길이 상한 | 통과 |
| 긴 메시지 절단 | MSG_MAX 초과 메시지 요약 | 절단+'…' 접미 | 통과 |
| 오류 건수 상한 | MAX_ERRORS 초과 오류 요약 | 상한+'N건 생략'·'총 M건' | 통과 |
| 상한 인자 조절 | limit=2로 5오류 요약 | '3건 생략' | 통과 |
| 빈 오류 처리 | 빈 리스트 요약 | '(오류 상세 없음)' | 통과 |
| loc 부재 처리 | loc 없는 오류 요약 | '위치 불명' | 통과 |

### tests/test_validation_logging.py (5)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 422 필드 경로 로깅 | 필수 누락 요청 로그 확인 | '요청 스키마 불일치'·camelCase 경로 | 통과 |
| 메서드·경로 로깅 | 422 요청 로그 확인 | 'POST /ingest' 포함 | 통과 |
| 배열 인덱스 경로 로깅 | 잘못된 timestamp 요청 로그 | 'body.logs.0.timestamp' | 통과 |
| 422 응답 형식 유지 | 422 응답 본문 확인 | detail 리스트·type/loc/msg 키 | 통과 |
| 정상 요청 무로그 | 정상 요청 로그 확인 | 불일치 로그 없음·201 | 통과 |

---

## 5. 잡 처리·전송(Spring) (60)

### tests/test_job_queue.py (13)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 잡 정상 처리 | 워커가 PENDING 처리 | runner 호출·status=DONE | 통과 |
| runner 오류 처리 | runner 예외 발생 | status=FAILED | 통과 |
| 유효 결과 저장 | 전송 성공 후 결과 저장 | DONE·error=None·detail 5키 | 통과 |
| 계약 위반 결과 거부 | 5키 중 actions 누락 결과 | FAILED·result=None·사유에 actions | 통과 |
| 없는 잡 스킵 | 미존재 job_id enqueue | runner 미호출·정상 종료 | 통과 |
| 원본 소실 실패 처리 | signals 파일 없는 job 처리 | FAILED('원본' 사유)+Spring 통지 | 통과 |
| 전송 후 파일 회수 | 전송 성공 DONE 확정 후 | 원본 파일 삭제 | 통과 |
| 전송 실패 파일 보존 | 전송 실패 DELIVERING | 파일 보존 | 통과 |
| 영구 실패 처리 | 4xx 전송 실패 | FAILED('422')+파일 회수 | 통과 |
| 409 멱등 전달 처리 | 409 성공 흡수 경로 모킹 배선 | 전송 성공 취급(예외 없음) | 통과 |
| 전체 캡 타임아웃 처리 | runner가 캡 초과·2회 시도 | attempts=2·FAILED | 통과 |
| 캡 이내 정상 | 캡 이내 runner | attempts=1·DONE | 통과 |
| 동시성 상한 | concurrency=2로 6잡 처리 | 피크 ≤2 | 통과 |

### tests/test_job_cleanup.py (4)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 만료 종료 잡 정리 | 보존기간 초과/최근/진행중 혼재 purge | 초과 DONE/FAILED만 삭제(2건) | 통과 |
| 사용 중 파일 목록 | 상태별 job의 files_in_use | 미종료 job 파일만(a/b/c.json) | 통과 |
| 만료 없음 처리 | 최근 job만 purge | 삭제 0 | 통과 |
| 정리 루프 수명주기 | 루프 기동·purge·정지 | 만료 삭제·취소 없이 종료 | 통과 |

### tests/test_stuck_job_reaper.py (6)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 정체 잡 재투입 | 여력 남은 RUNNING reap | PENDING 되돌림+재큐·requeue_count=1 | 통과 |
| 재투입 소진 실패 | 상한 소진 RUNNING reap | FAILED+Spring에 사유 전송 | 통과 |
| 전송 실패 무관 확정 | Spring 전송 예외 시 reap | job은 FAILED 유지 | 통과 |
| 정상 RUNNING 보호 | 임계 안쪽 RUNNING reap | 미회수·RUNNING 유지 | 통과 |
| 타 상태 미대상 | DELIVERING/DONE/FAILED reap | 미회수(0,0) | 통과 |
| 재기동 복구 | recover_on_startup 실행 | PENDING·RUNNING 모두 재큐(임계 무관) | 통과 |

### tests/test_delivery_reconciler.py (7)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 재전송 성공 | DELIVERING 재전송 | 전송·status=DONE | 통과 |
| 재전송 실패 유지 | 재전송 예외 발생 | DELIVERING 유지·n=0 | 통과 |
| 재전송 후 파일 회수 | 재전송 DONE 확정 후 | 원본 파일 삭제 | 통과 |
| 파일 없어도 결과 전달 | 파일 소실 상태 재전송 | 전송·DONE | 통과 |
| 영구 실패 처리 | 4xx 재전송 | FAILED('422')+파일 회수·n=0 | 통과 |
| 409 멱등 처리 | 409 성공 흡수 재전송 | DONE·n=1 | 통과 |
| grace 구간 대기 | grace 내 DELIVERING reap | 미처리·DELIVERING 유지·n=0 | 통과 |

### tests/test_spring_client.py (30)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 페이로드 구조 앵커링 | 성공 페이로드 조립 | type·service는 result 내부·최상위 미노출 | 통과 |
| 성공 raw 정규화 | 성공 페이로드의 3종 raw | 정규화 적용 | 통과 |
| evidence 배열 배제 | evidence 필드 확인 | lines/spans/items 없음 | 통과 |
| 실패 페이로드 형식 | 실패 페이로드 조립 | reason 사용·error/result 없음·raw 정규화 | 통과 |
| 기본 companyCode | companyCode 미지정 페이로드 | SN001 | 통과 |
| 지정 companyCode 전달 | 지정값 성공·실패 페이로드 | 양쪽에 SN042 | 통과 |
| 전송 시그널 상한 | 500건, limit 20 성공 페이로드 | logs 20건 | 통과 |
| 절단 고지 | 잘린 모달리티 source 확인 | '전체 500건 중 주요 20건 수록' | 통과 |
| 전량 수록 고지 | 미절단 모달리티 source 확인 | '전체 1건 전량 수록' | 통과 |
| 기존 source 보존 | LLM source 있는 경우 | 기존+고지 덧붙임 | 통과 |
| source 신규 생성 | LLM source 미기입 | 고지 문구로 생성 | 통과 |
| 0건 고지 생략 | 0건 모달리티 | source 미생성 | 통과 |
| conclusion 불변 | LLM conclusion 확인 | 코드 미변경('lc') | 통과 |
| 실패 경로 상한 | 실패 페이로드 500건, limit 20 | logs 20건·result 없음 | 통과 |
| naive 시각 Z 부여 | tz 없는 값 페이로드 | Z 부여 | 통과 |
| 오프셋 UTC 환산 | 오프셋 값 페이로드 | UTC 환산 | 통과 |
| UTC 값 불변 | 이미 Z인 값 페이로드 | 불변(멱등) | 통과 |
| 미파싱 시각 거부 | 형식 불명 timestamp 정규화 | ValueError | 통과 |
| 구간 시각 정규화 | modalityInfo 구간 시각 페이로드 | Z 정규화 | 통과 |
| 실패 페이로드 시각 정규화 | 실패 페이로드 시각 | Z 정규화 | 통과 |
| 시각 정규화 멱등 | 2회 정규화 | 값 불변 | 통과 |
| camelCase 수용 | companyCode 입력·출력 | 수용·by_alias camelCase | 통과 |
| 2xx 성공 | 200 응답 _post | 예외 없음 | 통과 |
| 409 멱등 성공 | 409 응답 _post | 성공 취급(예외 없음) | 통과 |
| 4xx 영구 오류 | 422 응답 _post | DeliveryPermanentError(422) | 통과 |
| 5xx 일시 오류 | 503 응답 _post | DeliveryTransientError(503) | 통과 |
| 네트워크 오류 전파 | ConnectTimeout 발생 | 그대로 전파 | 통과 |
| 내부 시크릿 헤더 전송 | 시크릿 설정 후 _post | X-Internal-Secret 헤더 실림 | 통과 |
| 시크릿 미설정 생략 | 시크릿 빈 값 _post | 헤더 자체 생략 | 통과 |
| 401 영구 오류 | 401 응답 _post | DeliveryPermanentError(401) | 통과 |

---

## 6. 인프라 (5)

### tests/test_db_session.py (3)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| pre_ping 적용 | kwargs·풀 속성 확인 | pool_pre_ping=True 선언·반영 | 통과 |
| recycle 적용 | kwargs·풀 속성 확인 | pool_recycle=설정값 선언·반영 | 통과 |
| connect_timeout 선언 | connect_args 값 확인 | connect_timeout=설정값·양수 | 통과 |

### tests/test_logging_config.py (2)

| 기능명 | 검증 내용 | 기대 결과 | 실제 결과 |
| --- | --- | --- | --- |
| 로깅 설정 적용 | setup_logging("DEBUG") | 레벨·포매터 적용·httpx WARNING 억제 | 통과 |
| 기본 레벨 적용 | 인자 없이 setup_logging | settings 기본 INFO | 통과 |

---

## 파일별 테스트 수 요약

| 파일 | 테스트 수 | 그룹 |
| --- | ---: | --- |
| tests/test_ingest.py | 11 | 수집 API·인증 |
| tests/test_ingest_auth.py | 7 | 수집 API·인증 |
| tests/test_health.py | 3 | 수집 API·인증 |
| tests/test_bundle_compression.py | 25 | 번들 처리·압축 |
| tests/test_bundle_parser.py | 14 | 번들 처리·압축 |
| tests/test_bundle_store.py | 10 | 번들 처리·압축 |
| tests/test_raw_normalizer.py | 12 | 번들 처리·압축 |
| tests/test_signal_selector.py | 13 | 번들 처리·압축 |
| tests/test_agents.py | 12 | RCA 에이전트·그래프 |
| tests/test_graph.py | 5 | RCA 에이전트·그래프 |
| tests/test_pipeline.py | 7 | RCA 에이전트·그래프 |
| tests/test_report_llm.py | 6 | RCA 에이전트·그래프 |
| tests/test_prompts.py | 8 | RCA 에이전트·그래프 |
| tests/test_llm_layer.py | 7 | RCA 에이전트·그래프 |
| tests/test_analyze_baseline.py | 1 | RCA 에이전트·그래프 |
| tests/test_contracts_taxonomy.py | 4 | 계약·스키마 검증 |
| tests/test_rca_validation.py | 6 | 계약·스키마 검증 |
| tests/test_schema_errors.py | 10 | 계약·스키마 검증 |
| tests/test_validation_logging.py | 5 | 계약·스키마 검증 |
| tests/test_job_queue.py | 13 | 잡 처리·전송(Spring) |
| tests/test_job_cleanup.py | 4 | 잡 처리·전송(Spring) |
| tests/test_stuck_job_reaper.py | 6 | 잡 처리·전송(Spring) |
| tests/test_delivery_reconciler.py | 7 | 잡 처리·전송(Spring) |
| tests/test_spring_client.py | 30 | 잡 처리·전송(Spring) |
| tests/test_db_session.py | 3 | 인프라 |
| tests/test_logging_config.py | 2 | 인프라 |
| **합계** | **231** | — |
