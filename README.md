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

## 배포 노트 (DB)

Spring과 FastAPI는 **하나의 MySQL 인스턴스를 공유**하고, 그 안에서 각자 스키마만 나눠 쓴다 (Spring=`chokchok`, FastAPI=`chok_ai`). 로컬은 Spring compose를 흉내 낸 대역 컨테이너([docker-compose.yml](docker-compose.yml), 포트 3307)로 개발한다.

서버 배포 시 주의:

- **`chok_ai` 스키마·계정은 서버에서 한 번 만들어줘야 한다.** Spring compose의 MySQL은 최초 기동 시 `chokchok`만 생성하므로 `chok_ai`는 별도로 만들어야 한다. Spring 저장소의 initdb 스크립트(`docs/schema.sql` 등)에 아래를 추가해두면 새 환경마다 자동 적용된다:
  ```sql
  CREATE DATABASE IF NOT EXISTS chok_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER IF NOT EXISTS 'chok_ai'@'%' IDENTIFIED BY '<비밀번호>';
  GRANT ALL PRIVILEGES ON chok_ai.* TO 'chok_ai'@'%';
  ```
  테이블(`ingest_job`)은 그다음 FastAPI 배포 단계에서 `alembic upgrade head`가 생성한다 (스키마는 Alembic이 소유).
- FastAPI 접속 정보(`.env`)는 실행 위치에 따라 달라진다:
  - (1) FastAPI를 Spring compose 네트워크에 컨테이너로 넣는 경우 → `MYSQL_HOST=db`, `MYSQL_PORT=3306`
  - (2) FastAPI를 호스트에서 직접(uvicorn) 돌리는 경우 → `MYSQL_HOST=127.0.0.1`, `MYSQL_PORT=3307` (현재 로컬과 동일)

## DB 마이그레이션 (Alembic)

스키마는 Alembic이 소유한다(`ingest_job`). Spring은 Flyway로 자기 테이블을 관리하므로, 서비스별로 자기 DB의 마이그레이션만 소유한다(공유 인스턴스, 분리 스키마).

- 적용: `uv run alembic upgrade head` (배포 부팅 전, 로컬은 위 4단계)
- 스키마 변경: `uv run alembic revision --autogenerate -m "설명"` → 생성 파일 검토 후 커밋 (라이브 DB 필요)
- 롤백: `uv run alembic downgrade -1`
- 로컬 빠른 개발: `DB_AUTO_CREATE=true`면 기동 시 `create_all`로 대체 가능(마이그레이션 우회)
- 주의: `alembic.ini`는 Windows(cp949) 호환 위해 ASCII만 — 한글 주석은 `migrations/env.py`에.
