# DB 배포 가이드 — chok_ai 프로비저닝

Spring·FastAPI가 **MySQL 인스턴스를 공유**하고 스키마만 분리한다 (Spring=`chokchok`, FastAPI=`chok_ai`). 이 문서는 공유 MySQL에 **FastAPI용 `chok_ai` 데이터베이스·유저·권한을 1회 생성**하는 절차다.

- 테이블은 이 문서 범위 밖 — FastAPI 배포가 `alembic upgrade head`로 생성한다.
- 실행 주체: 공유 MySQL 소유처(인프라/DBA, 또는 Spring compose 관리자). SQL은 FastAPI 팀이 제공.

## 생성 대상

- 데이터베이스 `chok_ai` (utf8mb4)
- 유저 `'chok_ai'@'%'` + 비밀번호 (보안 강화 시 `%`를 앱 네트워크/호스트로 제한 가능)
- 권한 `chok_ai.*` 한정 — 다른 스키마(chokchok) 접근 없음

SQL 파일: [`deploy/init-chok-ai.sql`](../deploy/init-chok-ai.sql)

```sql
CREATE DATABASE IF NOT EXISTS chok_ai
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'chok_ai'@'%' IDENTIFIED BY '<비밀번호>';
GRANT ALL PRIVILEGES ON chok_ai.* TO 'chok_ai'@'%';
FLUSH PRIVILEGES;
```

## 적용 방법 (환경별)

- **Docker MySQL (공유 compose)**: SQL을 `/docker-entrypoint-initdb.d/`에 배치 → 최초 기동 시 자동 실행.
  - 단, 데이터 볼륨이 이미 있으면 initdb는 안 돈다 → 아래 수동 실행으로.
- **매니지드/기존 서버**: 관리자(root)로 직접 실행.
  ```bash
  mysql -h <host> -P <port> -u root -p < deploy/init-chok-ai.sql
  ```

## 비밀번호

- `<비밀번호>`를 **실제 강한 값으로 교체**. 레포·문서에 실값을 커밋하지 않는다.
- 같은 값을 FastAPI에 `MYSQL_PASSWORD`로 주입 (배포 시크릿).

## 검증

```bash
mysql -u chok_ai -p chok_ai -e "SELECT 1;"   # 접속·권한 확인
```

이후 FastAPI 배포에서 `alembic upgrade head` → `GET /health`가 200이면 정상.

## 역할 경계

- DB·유저·권한 = **이 문서** (공유 MySQL 소유처가 실행)
- `chok_ai` 테이블 = **FastAPI Alembic** (`ingest_job`)
- `chokchok` 테이블 = **Spring Flyway**
- 서로의 스키마는 건드리지 않는다.

## 핸드오프

넘길 것: 이 문서 + [`deploy/init-chok-ai.sql`](../deploy/init-chok-ai.sql) + 비밀번호(안전 채널). **"두기"만으론 적용 안 됨** — 인프라 티켓 또는 Spring initdb 반영 PR로 실행을 명시적으로 요청해야 한다.
