# CHOK v2 AI Backend — FastAPI
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 의존성 레이어 캐시: 소스보다 먼저 lock만 복사해서 sync
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 앱 코드 → 프로젝트 설치
COPY app ./app
RUN uv sync --frozen --no-dev

# 런타임 정적 파일(코드 아님, sync 불필요): 마이그레이션 + 부팅 스크립트
COPY alembic.ini ./
COPY migrations ./migrations
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# 번들 원본(logs/metrics/traces) 파일 저장 디렉터리 = 볼륨 마운트 지점.
# 이미지 안에 미리 만들어 두어야 named volume이 이 디렉터리의 소유권을 물려받아
# non-root(appuser)가 쓸 수 있다. (bind mount로 바꾸면 호스트 쪽 소유권이 우선이므로 별도 조정 필요.)
RUN mkdir -p /app/data/bundles

# non-root 실행 (보안 기본). .venv 포함 /app 소유권 이전.
RUN useradd -r -u 1001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
# 부팅: alembic upgrade head → uvicorn (entrypoint.sh)
ENTRYPOINT ["./entrypoint.sh"]
