# chok-v2-ai-backend
chok v2 — FastAPI 멀티에이전트 RCA (데이터 계층 본체)

📄 [처리 흐름 문서](docs/flow.md) · [LLM 에이전트 설계](docs/agent-design.md) · [번들 raw 압축 전략](docs/bundle-compression.md) · [구현 계획](docs/implementation-plan.md)

## 시작하기

```bash
# 1. 의존성 설치 (uv 기반)
uv sync --extra dev

# 2. 환경변수 설정
cp .env.example .env   # 값 수정

# 3. MySQL 기동 (Docker)
docker compose up -d

# 4. DB 스키마 생성 (Alembic 마이그레이션)
uv run alembic upgrade head

# 5. 서버 실행
uv run uvicorn app.main:app --reload
```

테스트:

```bash
uv run pytest
```

## 실행 설정 경계

이 저장소의 `.env.example`과 `docker-compose.yml`은 **FastAPI 로컬 개발 전용**이다.
`docker-compose.yml`은 MySQL만 기동하며 FastAPI는 `uv run uvicorn ...`으로 실행한다.

통합 배포의 이미지 digest, 환경변수 주입, 서비스 네트워크, published port와 영속 볼륨은
[`chok-v2-deploy`](https://github.com/KT-AX-ICT/chok-v2-deploy) 저장소가 관리한다.
서비스 CI는 FastAPI 이미지를 만든 뒤 deploy 저장소의 image digest만 변경하며, 이 저장소의
`.env`를 배포 서버로 복사하지 않는다.

## DB 배포 노트

> 상세 절차·SQL: [DB 배포 가이드](docs/db-deploy.md)

Spring과 FastAPI는 **하나의 MySQL 인스턴스를 공유**하고, 그 안에서 각자 스키마만 나눠 쓴다 (Spring=`chokchok`, FastAPI=`chok_ai`). 로컬은 Spring compose를 흉내 낸 대역 컨테이너([docker-compose.yml](docker-compose.yml), 포트 3307)로 개발한다.

서버 배포 시 주의:

- 중앙 deploy의 MySQL 초기화 스크립트가 `chokchok`, `chok_ai` DB와 서비스별 계정을 만든다.
- FastAPI 컨테이너는 Compose 내부 주소 `mysql:3306`을 사용한다.
- 컨테이너 시작 시 `entrypoint.sh`가 `alembic upgrade head`를 실행해 `ingest_job`을 관리한다.
- SDK 원본 bundle은 `/app/data/bundles`에 저장되므로 중앙 deploy에서 영속 volume을 연결한다.
- Compose 밖의 공유 MySQL을 사용하는 별도 환경에서만 [DB 배포 가이드](docs/db-deploy.md)의
  프로비저닝 절차를 사용한다.

## DB 마이그레이션 (Alembic)

스키마는 Alembic이 소유한다(`ingest_job`). Spring은 Flyway로 자기 테이블을 관리하므로, 서비스별로 자기 DB의 마이그레이션만 소유한다(공유 인스턴스, 분리 스키마).

- 적용: `uv run alembic upgrade head` (배포 부팅 전, 로컬은 위 4단계)
- 스키마 변경: `uv run alembic revision --autogenerate -m "설명"` → 생성 파일 검토 후 커밋 (라이브 DB 필요)
- 롤백: `uv run alembic downgrade -1`
- 로컬 빠른 개발: `DB_AUTO_CREATE=true`면 기동 시 `create_all`로 대체 가능(마이그레이션 우회)
- 주의: `alembic.ini`는 Windows(cp949) 호환 위해 ASCII만 — 한글 주석은 `migrations/env.py`에.
