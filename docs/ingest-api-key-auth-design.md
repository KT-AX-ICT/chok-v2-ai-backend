# /ingest API 키 인증 — 설계

작성일 2026-07-27.
SDK → FastAPI 인바운드 인증(공유 API 키) 도입의 설계 기록.

## 배경

1. [app/main.py](../app/main.py)에 인증 미들웨어·의존성이 없다. [app/api/ingest.py](../app/api/ingest.py)의 두 엔드포인트는 **무인증 공개**다.
2. 배포는 `0.0.0.0:8000` 바인딩([entrypoint.sh](../entrypoint.sh), [docker-compose.deploy.yml](../docker-compose.deploy.yml)) — 보안그룹이 8000을 열면 인터넷 공개.
3. `POST /ingest`는 수신 즉시 job 적재 → 큐 워커 → **LLM 호출**로 이어진다([docs/flow.md](flow.md)). 무인증 요청 1건 = OpenAI 비용 1건.
4. `GET /ingest/{job_id}`는 `result`(RCA 전문 — 서비스명·원인·근거)를 그대로 반환하고, `job_id`는 순차 정수라 **열거가 쉽다**.

## 목표

- `/ingest` 두 엔드포인트에 공유 API 키 인증 부착
- 무중단 키 교체 가능(복수 키 동시 허용)
- 인프라 0 — 키는 배포 시크릿(`.env`)에만 존재, 시크릿 매니저·DB 불필요
- 기존 테스트 12건 무영향

## 비목표 (YAGNI)

- **테넌트별 키** — [contracts.py](../app/schemas/contracts.py)에 `company_code`가 있으나 실운용은 기본값 `SN001` 단일. 키 분리의 유일한 실익은 개별 폐기인데, 클라이언트가 1개면 개별 폐기 = 전체 교체와 동일. 다수 테넌트 시점에 `company_code ↔ 키 해시` 테이블로 이전(클라이언트 변경 0).
- **HMAC 서명** — 키 평문 전송을 피할 수 있으나 타임스탬프·nonce 저장소 + SDK 서명 구현이 딸린다. TLS 도입 시 그 복잡도 대부분이 무의미해져 비용 대비 손해.
- **레이트 리밋** — 클라이언트 1개, 인증으로 발신자가 한정되므로 현 단계 실익 없음.
- **본문 크기 제한 · TLS** — 별건(아래 "남은 위험" 참조).

## 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 방식 | 공유 API 키(정적) | 클라이언트 1개, 인프라 0 |
| 헤더 | `X-API-Key` | Bearer는 관례상 발급·만료 토큰 — 정적 키에 신호 불일치 |
| 키 개수 | 단일(복수 허용) | 콤마 구분으로 교체 중 겹침 구간 확보 |
| 부착 위치 | 라우터 레벨 `dependencies` | 신규 엔드포인트 자동 보호(기본값=잠김) |
| 보호 범위 | `POST /ingest` · `GET /ingest/{job_id}` | GET도 RCA 전문 노출 + 순차 열거 가능 |
| 제외 | `GET /health` | LB·readiness probe가 무인증 접근, 노출 정보 없음 |
| 전환 | 단계적(키 미설정 시 통과) | 서버 배포와 인증 활성화 분리 → `.env` 한 줄로 롤백 |
| TLS | 미적용 | 도메인 부재. 한계·완화책은 아래 명시 |

## 설계

### 구성 요소

```
app/core/auth.py       (신규) require_api_key 의존성 + 키 마스킹
app/core/config.py     ingest_api_keys 설정 + ingest_api_key_set 프로퍼티
app/api/ingest.py      라우터 선언에 dependencies 추가
app/main.py            lifespan에 미설정 경고
.env.example           INGEST_API_KEYS= (빈 값)
docker-compose.deploy.yml   environment에 INGEST_API_KEYS 전달
tests/conftest.py      autouse 픽스처 — 테스트 중 인증 비활성 고정
tests/test_ingest_auth.py   (신규) 인증 동작 7건
```

인증 관심사를 `core/auth.py` 한 파일에 격리 — 키 저장소를 env→DB로 옮길 때 변경 지점이 이 파일로 한정된다.

### 설정

```python
# config.py
ingest_api_keys: str = ""

@property
def ingest_api_key_set(self) -> frozenset[str]:
    return frozenset(k.strip() for k in self.ingest_api_keys.split(",") if k.strip())
```

콤마 구분 복수 허용의 목적은 **무중단 교체**다. `old,new` 배포 → SDK를 `new`로 갱신 → `new`만 남기고 재배포.

프로퍼티는 캐시하지 않는다 — 캐시하면 테스트의 monkeypatch가 안 먹는다. 짧은 문자열 split이라 요청당 비용은 무시 가능.

### 검증 로직

```python
# auto_error=False — 헤더 부재 시 FastAPI가 403을 자동 발생시키지 않고 None을 넘긴다.
# 단계적 전환(미설정 시 통과) 판단을 이 함수가 직접 해야 하므로 필수.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

async def require_api_key(api_key: str | None = Security(api_key_header)) -> None:
    keys = settings.ingest_api_key_set
    if not keys:
        return                                    # 단계적 전환 — 미설정 시 통과
    provided = (api_key or "").encode()
    if not any(secrets.compare_digest(provided, k.encode()) for k in keys):
        logger.warning("ingest 인증 실패 — 키 %s", mask(api_key))
        raise HTTPException(status_code=401, detail="invalid or missing API key")
```

- **`APIKeyHeader`(`fastapi.security`)** — 평범한 `Header()` 대신 쓰는 이유는 OpenAPI 스키마에 보안 요건으로 등재되기 때문. `/docs`에 자물쇠 UI가 생겨 키를 넣고 시험할 수 있고, 스펙만 봐도 인증 필요가 드러난다. 동작은 `Header()`와 동일.
- **`secrets.compare_digest`** — `==`는 불일치 위치에 따라 소요 시간이 달라져 타이밍 공격에 키가 새어 나간다. 상수 시간 비교로 차단.
- **`bytes` 변환** — `compare_digest`는 비-ASCII `str`에 `TypeError`를 던진다. 그대로 두면 이상한 키를 보낸 요청이 401이 아니라 500이 된다.
- **키 없음/틀림 미구분** — 응답은 둘 다 401 동일 문구. 구분 응답은 "헤더 이름은 맞았다"는 힌트가 된다. 서버 로그에는 구분해 남기므로 디버깅 지장 없음.

### 적용

```python
router = APIRouter(prefix="/ingest", tags=["ingest"],
                   dependencies=[Depends(require_api_key)])
```

라우터 레벨 부착 — 이후 `/ingest` 아래 엔드포인트를 추가해도 자동 보호. health 라우터는 무변경이라 제외 요건이 구조로 보장된다.

**ASGI 미들웨어를 쓰지 않는 이유**: `request.url.path` 문자열 매칭에 의존해, `prefix` 변경 시 에러 없이 인증이 풀린다. 보안 코드의 조용한 실패는 최악.

### 로그 마스킹

```python
def mask(key: str | None) -> str:
    if not key:
        return "(없음)"
    return f"{key[:4]}…({len(key)}자)"
```

앞 4자 + 길이만. "SDK가 구 키 사용 중" 판단에 충분하고, 로그 유출 시 키는 보전된다.

### 기동 경고

```python
# main.py lifespan
if not settings.ingest_api_key_set:
    logger.warning("INGEST_API_KEYS 미설정 — /ingest 무인증 공개 상태. "
                   "단계적 전환 중이면 정상, 아니면 즉시 설정할 것.")
```

단계적 전환의 대가가 "설정 실수 = 무인증 공개"이므로, 이 경고가 유일한 감지 수단이다. 매 기동 기록.

### 키 생성

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # 43자, 256bit
```

**쿼리스트링 전송 금지** — URL은 액세스 로그·프록시 로그·리퍼러에 남는다. 헤더만 사용.

### 키 보관

| 위치 | 내용 |
|---|---|
| 서버 EC2 `.env` | 실질 원본. `.env`는 [.gitignore](../.gitignore)로 추적 제외 |
| `docker-compose.deploy.yml` | `INGEST_API_KEYS: ${INGEST_API_KEYS:-}` — **누락 시 컨테이너에 값이 안 간다** |
| SDK 설정 | env/설정 파일로 주입. 소스 하드코딩 금지(커밋 시 키 교체가 곧 배포가 됨) |
| `.env.example` | 빈 값만. 예시 값을 적으면 그대로 복사해 쓰는 사고 발생 |

분실해도 복구 불필요 — 재생성 후 양쪽 교체로 종료.

## 테스트

기존 12건([tests/test_ingest.py](../tests/test_ingest.py))은 무변경. 단, `Settings`가 `.env`를 읽으므로 개발자 로컬에 키가 있으면 전부 401이 된다 → conftest에 autouse 픽스처로 테스트 중 강제 비활성([conftest.py](../tests/conftest.py)의 `_dummy_openai_key`와 대칭).

```python
@pytest.fixture(autouse=True)
def _no_ingest_auth(monkeypatch):
    monkeypatch.setattr(settings, "ingest_api_keys", "")
```

인증 테스트는 각 케이스에서 `monkeypatch.setattr(settings, "ingest_api_keys", "<키>")`로 다시 덮어써 키를 주입한다(autouse보다 나중에 적용되므로 그대로 우선한다).

신규 `tests/test_ingest_auth.py`:

| # | 케이스 | 기대 |
|---|---|---|
| 1 | 키 설정 + 헤더 없음 | 401 |
| 2 | 키 설정 + 틀린 키 | 401 |
| 3 | 키 설정 + 맞는 키 | 201 |
| 4 | 복수 키 중 **두 번째** 키 | 201 — 무중단 교체 보장 |
| 5 | 키 미설정 + 헤더 없음 | 201 — 단계적 전환 동작 |
| 6 | 키 설정 + `GET /ingest/{job_id}` 헤더 없음 | 401 — 라우터 레벨 적용 확인 |
| 7 | 키 설정 + `GET /health` | 200 — 제외 보장 |

4·7이 핵심 — 무중단 교체와 health 제외는 깨져도 평시에 드러나지 않는다.

## 전환 절차

1. 키 생성 (`secrets.token_urlsafe(32)`)
2. `docker-compose.deploy.yml`에 `INGEST_API_KEYS` 전달 줄 추가
3. **서버 배포** — 이 시점 `.env`에 키 없음 → 인증 비활성, 기존 SDK 정상 동작
4. **SDK에 `X-API-Key` 헤더 추가** 후 배포
5. **EC2 `.env`에 키 설정 → 재기동** — 여기서 인증 활성화
6. 검증 — 기동 로그의 경고 소멸 확인, 헤더 없는 요청 401 확인

3·5 분리가 단계적 전환의 요점 — 문제 발생 시 `.env` 한 줄을 비우고 재기동하면 즉시 롤백.

## 남은 위험

**TLS 미적용 — 키 평문 전송.** 경로상 관찰자(공용 Wi-Fi·중간 프록시·ISP)가 헤더의 키를 그대로 취득·재사용 가능. 도메인 부재로 이번 범위에서 제외.

완화(이번 설계에 반영):

- 헤더 전송(쿼리스트링 금지) — 로그·리퍼러 노출 차단
- 로그 마스킹 — 앱 로그 경유 유출 차단
- 복수 키 — 유출 의심 시 무중단 즉시 교체
- 보안그룹 IP 제한 병행 권장 — 키 유출 시에도 허용 IP 외 사용 불가

무인증 대비로는 확실한 개선(EC2 IP만 알면 되던 것 → 경로상 관찰자 지위 필요)이나, HTTPS 대체는 아니다.

## 향후

| 항목 | 시점 |
|---|---|
| TLS — Caddy + Let's Encrypt(도메인 없으면 `<ip>.sslip.io` 우회) | 도메인 확보 또는 외부 노출 확대 시 |
| 테넌트별 키 — `company_code ↔ 키 해시` 테이블 | 2번째 기업 온보딩 시 |
| `POST /ingest` 본문 크기 제한 | 별건 |
| 단계적 → 강제(fail-fast) 전환 | 전환 절차 6 완료 후 |
