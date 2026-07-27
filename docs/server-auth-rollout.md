# 서버 간 인증 적용 절차 — 인계 문서

작성일 2026-07-27. 설계·근거: [ingest-api-key-auth-design.md](ingest-api-key-auth-design.md)

FastAPI 양방향 인증 코드는 완료됐으나 **인증은 꺼져 있다**(키/시크릿 미설정 시 통과하는 단계적 전환). 이 문서는 켜기까지 남은 작업 4건의 실행 절차다.

| # | 대상 | 주체 | 형태 | 담당자 |
|---|---|---|---|---|
| 0 | PR 2건 머지 → 배포 | chok-v2-ai-backend | PR | 이예지·박가희 |
| 1 | SDK `X-API-Key` 헤더 추가 | chok-v2-py-sdk | PR | 이예지 |
| 2 | 배포 서버 `.env`에 키 설정 → 재기동 | 운영 | 서버 작업 | 박가희 |
| 3 | Spring 필터 활성화 | chok-v2-spring-backend | PR | 이석진 |

## 작업 순서

```
0 배포 ──┬─→ 1 SDK 헤더 ──→ 2 서버 키 설정 ──→ 인바운드 ON
         └─→ 3 Spring 주석 해제 ──────────────→ 아웃바운드 ON
```

---

## 0. PR 머지 → 배포

| PR | 내용 |
|---|---|
| [chok-v2-ai-backend#20](https://github.com/KT-AX-ICT/chok-v2-ai-backend/pull/20) | FastAPI 양방향 인증 코드 |
| [chok-v2-deploy#13](https://github.com/KT-AX-ICT/chok-v2-deploy/pull/13) | `fastapi` 서비스에 env 2종 전달 |

배포 후 확인 — 기동 로그에 아래가 있으면 정상(아직 2를 안 했으므로):

```
INGEST_API_KEYS 미설정 — /ingest 무인증 공개 상태. 단계적 전환 중이면 정상, 아니면 즉시 설정할 것.
```

---

## 1. SDK — `X-API-Key` 헤더 추가

**저장소:** `chok-v2-py-sdk`

현재 [`src/rca_sdk/transport/client.py`](https://github.com/KT-AX-ICT/chok-v2-py-sdk)에 `# TODO: 재시도/백오프, 인증 — 서버팀과 미확정` 주석이 있다.   
인증 부분은 지금 작업으로 확정한다.

### 1-1. 설정 추가 — `src/rca_sdk/config.py`

`collect_endpoint` 아래에 필드 추가. `env_prefix="RCA_"`이므로 환경변수 이름은 **`RCA_API_KEY`**가 된다.

```python
    # 전송 대상
    collect_endpoint: str = "http://localhost:8000/ingest"
    # FastAPI /ingest 인증 키(X-API-Key). 비어 있으면 헤더를 붙이지 않는다 —
    # 서버가 키를 요구하지 않는 동안에도 그대로 동작하게 하기 위함.
    api_key: str = ""
```

### 1-2. 헤더 부착 — `src/rca_sdk/transport/client.py`

```python
class TransportClient(Transport):
    def __init__(self, endpoint: str, timeout: float = 10.0, api_key: str = "") -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.api_key = api_key

    def send(self, bundle: SnapshotBundle) -> SubmissionResult:
        # TODO: 재시도/백오프 — 서버팀과 미확정 (docs/api-contract.md).
        headers = {"Content-Type": "application/json"}
        # 키 미설정이면 헤더를 붙이지 않는다(빈 값 전송 아님).
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            resp = httpx.post(
                self.endpoint,
                content=bundle.model_dump_json(by_alias=True),
                headers=headers,
                timeout=self.timeout,
            )
            ...
```

기존 `except` 절과 반환 로직은 그대로 둔다.

### 1-3. 호출부 — `src/rca_sdk/runtime/runner.py:165`

```python
        transport=TransportClient(settings.collect_endpoint, api_key=settings.api_key),
```

### 1-4. 테스트 — `tests/test_transport_client.py`

기존 테스트는 `TransportClient("http://x/ingest")`로 생성하므로 `api_key` 기본값 `""`에 의해 **무변경으로 통과**한다. 2건 추가:

- 키 설정 시 요청 헤더에 `X-API-Key`가 실린다
- 키 미설정 시 헤더가 **부재**한다(빈 값 아님)

### 1-5. 운영 반영

SDK가 도는 호스트의 `.env`에 `RCA_API_KEY=<2에서 생성한 값>`. **2보다 먼저 배포**한다.

---

## 2. 배포 서버 — 키 설정

### 2-1. 키 생성 (생성완료, 별도 전달 예정)

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

43자 URL-safe(256bit). 접두사·따옴표 없이 출력 그대로 쓴다. `random`·`uuid4`·수기 문자열은 예측 가능성이 달라 쓰지 않는다.

### 2-2. 배포 서버 `.env` (chok-v2-deploy 배포 위치)

```
INGEST_API_KEYS=<생성한 값>
```

`INTERNAL_SHARED_SECRET`은 이미 설정돼 있으므로 건드리지 않는다.

### 2-3. 재기동 → 확인 (재기동 프로세스 따로 있으면 진행 X)

```bash
docker compose up -d fastapi
docker compose logs fastapi | grep INGEST_API_KEYS   # 경고가 사라져야 정상
```

동작 확인:

```bash
curl -i -X POST http://<host>:8000/ingest -H 'Content-Type: application/json' -d '{}'
# → 401  (헤더 없음)

curl -i http://<host>:8000/health
# → 200  (health는 인증 제외)
```

422가 아니라 **401**이 나와야 한다 — 인증이 본문 검증보다 먼저 돈다는 뜻이다.

### 2-4. 롤백 (필요 시)

`.env`에서 `INGEST_API_KEYS` 값을 비우고 재기동하면 즉시 인증이 꺼진다. 코드 변경·재배포 불필요.

### 2-5. 키 교체 (필요 시)

`INGEST_API_KEYS=old,new`로 배포 → SDK를 `new`로 갱신 → `new`만 남기고 재배포. 겹침 구간이 있어 무중단이다.

---

## 3. Spring — 필터 활성화

**저장소:** `chok-v2-spring-backend`

**파일:** `src/main/java/com/choks/chokchok/config/SecurityConfig.java`

`filterChain` 메서드에서 주석 3줄을 해제한다:

```java
                        .requestMatchers("/api/internal/**").permitAll()
                        .anyRequest().authenticated())
                // TODO(임시): FastAPI 송신측 X-Internal-Secret 헤더 미구현 → 검증 비활성화. 연동되면 주석 해제.
                // .addFilterBefore(new InternalSecretFilter(internalSecret),
                //         UsernamePasswordAuthenticationFilter.class)
```

→

```java
                        .requestMatchers("/api/internal/**").permitAll()
                        .anyRequest().authenticated())
                .addFilterBefore(new InternalSecretFilter(internalSecret),
                        UsernamePasswordAuthenticationFilter.class)
```

TODO 주석은 사유가 해소됐으므로 함께 삭제한다. `UsernamePasswordAuthenticationFilter`·`InternalSecretFilter` import는 이미 존재하므로 추가 불필요.

`/api/internal/**`의 `permitAll()`은 **그대로 둔다** — JWT를 요구하지 않는다는 뜻이고, 시크릿 검증은 필터가 담당한다.

확인 — `InternalSecretFilterTest`가 이미 동작을 고정하고 있다(올바른 시크릿 통과 / 틀림 401 / 헤더 없음 401 / internal 밖 미검사). 배포 후에는 FastAPI 전송이 계속 성공하는지 본다. 실패 시 FastAPI 로그에 아래가 남는다:

```
Spring 401 — X-Internal-Secret 불일치/미설정 의심. 전송 전량 실패 상태
```

이 메시지가 보이면 양쪽 `INTERNAL_SHARED_SECRET` 값이 다른 것이다. **401은 영구 실패로 분류되어 재시도하지 않으므로**, 그 사이 job은 FAILED로 확정된다 — 발견 즉시 값을 맞춰야 한다.

---

## 완료 후

전환이 끝나면 인바운드를 **강제(fail-fast)**로 올리는 것을 검토한다 — `INGEST_API_KEYS` 미설정 시 기동 거부(현재 `OPENAI_API_KEY` 처리와 동일 패턴).   
단계적 전환을 진행하고자 미설정시에도 허용하도록 해두었다.
