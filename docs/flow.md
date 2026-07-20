# 처리 흐름 (Flow)

CHOK v2 AI Backend의 RCA 처리 파이프라인 계획 문서.

## 전체 파이프라인

```mermaid
sequenceDiagram
    participant SDK as 수집기(SDK)
    participant API as POST /ingest
    participant DB as MySQL (IngestJob)
    participant Q as job_queue 워커
    participant A as 에이전트 오케스트레이터
    participant V as rca_validation
    participant S as Spring 게이트웨이

    SDK->>API: IngestBundle (window, trigger_info, logs/metrics/traces)
    API->>DB: IngestJob 기록 (PENDING)
    API-->>SDK: job_id 즉시 반환 (201)
    API->>Q: enqueue(job_id)
    Q->>DB: job_id로 번들 조회
    Q->>A: RCA 위임 (RUNNING)
    A->>A: 모달리티 에이전트(log/metric/trace) + 종합
    A-->>Q: RcaResult
    Q->>V: 전송 전 검증 (detail 5키 계약)
    Q->>S: POST (스냅샷 번들 + 리포트 일괄 전송)
    Q->>DB: 상태 갱신 (DONE / FAILED + 실패 사유)
```

1. **수신** — 수집기가 `POST /ingest`로 `IngestBundle`을 전송하면, `IngestJob(PENDING)`만 DB에 기록하고 `job_id`를 즉시 반환한다. 큐에는 `job_id`만 넘기고 번들은 넘기지 않는다.
2. **비동기 RCA** — `job_queue`의 asyncio 워커 풀이 `job_id`를 꺼내 DB에서 번들을 다시 조회한 뒤, 에이전트 오케스트레이터에 RCA를 위임한다. 오케스트레이터는 모달리티 에이전트(log / metric / trace) + 종합 에이전트를 거쳐 `RcaResult`를 워커에 돌려준다. 현재 오케스트레이터는 stub이다(향후 계획 참조).
3. **전송 전 검증** — Spring으로 보내기 전에 `rca_validation`이 detail 5키(`rca`, `summary`, `evidence`, `impact`, `actions`) 계약을 검증한다. 5키는 프론트 상세 탭과 1:1 고정. 위반 시 잡은 FAILED 처리되고 전송하지 않는다.
4. **결과 전달** — 모든 처리가 끝난 뒤, 스냅샷 번들과 리포트(`RcaResult`: type / severity / service + detail)를 `spring_client`가 Spring 게이트웨이로 단일 POST로 일괄 전송한다.

## 잡 수명주기

- 상태 머신: `PENDING → RUNNING → DONE / FAILED` (실패 시 사유를 `error` 컬럼에 기록)
- 상태 조회: `GET /ingest/{job_id}`
- 정리: `job_cleanup`이 종료(DONE/FAILED)된 잡을 보존기간(기본 24h) 경과 후 주기(기본 1h)마다 삭제. 진행 중인 잡은 보호.

## 기동·종료

`app/main.py`의 lifespan에서 `job_queue`와 `job_cleaner`를 asyncio 태스크로 기동하고, 종료 시 graceful stop 한다.

## 향후 계획

`app/agents/`에 모달리티 에이전트 3종(log / metric / trace) + 종합 에이전트가 들어갈 예정 (**아직 미구현**). trace 에이전트가 채우는 `origin_service`를 종합 에이전트가 대표 `service`로 승격하는 흐름(Q-007)이 계약 초안에 포함되어 있다.
