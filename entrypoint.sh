#!/bin/sh
# 배포 부팅: DB 마이그레이션 → 앱 서빙.
# set -e: 마이그레이션 실패 시 컨테이너 종료(반쪽 기동 방지).
# exec: uvicorn이 PID 1이 되어 종료 시그널을 정상 수신(graceful shutdown).
#
# 주의: 다중 인스턴스로 확장하면 각 컨테이너가 동시에 upgrade를 돌려 경합할 수 있다.
#       그 단계에선 마이그레이션을 별도 1회 job(init container 등)으로 분리 권장.
set -e

echo "[entrypoint] alembic upgrade head"
/app/.venv/bin/alembic upgrade head

echo "[entrypoint] starting uvicorn on :8000"
exec /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
