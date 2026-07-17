# chok-v2-ai-backend
chok v2 — FastAPI 멀티에이전트 RCA (데이터 계층 본체)

📄 [처리 흐름 문서](docs/flow.md)

## 시작하기

```bash
# 1. 의존성 설치 (uv 기반)
uv sync --extra dev
ㅣㅣ
# 2. 환경변수 설정
cp .env.example .env   # 값 수정

# 3. 서버 실행
uv run uvicorn app.main:app --reload
```

테스트:

```bash
uv run pytest
```
