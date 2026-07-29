# 테스트 통계

`docs/test-cases.md`(테스트 함수 222행) 기준 집계. pytest 케이스 총계는 **231**(파라미터라이즈 확장 9 포함). 전부 통과.

## 요약

| 지표 | 값 |
| --- | --- |
| 테스트 함수 | 222 |
| pytest 케이스(파라미터 포함) | 231 |
| 통과 | 222 (**100%**) |
| 실패 | 0 |
| 테스트 파일 | 26 |
| 기능 영역 | 6 |

## 기능 영역별 분포

| 기능 영역 | 테스트 | 비율 |
| --- | ---: | ---: |
| 번들 처리·압축 | 74 | 33.3% |
| 잡 처리·전송(Spring) | 60 | 27.0% |
| RCA 에이전트·그래프 | 37 | 16.7% |
| 계약·스키마 검증 | 25 | 11.3% |
| 수집 API·인증 | 21 | 9.5% |
| 인프라 | 5 | 2.3% |

## 검증 경로 유형 (기대 결과 기준)

| 유형 | 테스트 | 비율 |
| --- | ---: | ---: |
| 정상·기능 검증 | 190 | 85.6% |
| 에러·거부 경로 | 32 | 14.4% |

> '에러·거부 경로' = 4xx/5xx·FAILED·예외·차단/거부 등 실패 처리 검증. 나머지는 정상·기능 검증. (라벨 규칙 기반 근사치)

## 파일별 테스트 수

| 테스트 파일 | 테스트 | 기능 영역 |
| --- | ---: | --- |
| test_spring_client.py | 30 | 잡 처리·전송(Spring) |
| test_bundle_compression.py | 25 | 번들 처리·압축 |
| test_bundle_parser.py | 14 | 번들 처리·압축 |
| test_signal_selector.py | 13 | 번들 처리·압축 |
| test_job_queue.py | 13 | 잡 처리·전송(Spring) |
| test_raw_normalizer.py | 12 | 번들 처리·압축 |
| test_ingest.py | 11 | 수집 API·인증 |
| test_bundle_store.py | 10 | 번들 처리·압축 |
| test_schema_errors.py | 10 | 계약·스키마 검증 |
| test_agents.py | 8 | RCA 에이전트·그래프 |
| test_ingest_auth.py | 7 | 수집 API·인증 |
| test_pipeline.py | 7 | RCA 에이전트·그래프 |
| test_llm_layer.py | 7 | RCA 에이전트·그래프 |
| test_delivery_reconciler.py | 7 | 잡 처리·전송(Spring) |
| test_report_llm.py | 6 | RCA 에이전트·그래프 |
| test_rca_validation.py | 6 | 계약·스키마 검증 |
| test_stuck_job_reaper.py | 6 | 잡 처리·전송(Spring) |
| test_graph.py | 5 | RCA 에이전트·그래프 |
| test_validation_logging.py | 5 | 계약·스키마 검증 |
| test_contracts_taxonomy.py | 4 | 계약·스키마 검증 |
| test_job_cleanup.py | 4 | 잡 처리·전송(Spring) |
| test_health.py | 3 | 수집 API·인증 |
| test_prompts.py | 3 | RCA 에이전트·그래프 |
| test_db_session.py | 3 | 인프라 |
| test_logging_config.py | 2 | 인프라 |
| test_analyze_baseline.py | 1 | RCA 에이전트·그래프 |
