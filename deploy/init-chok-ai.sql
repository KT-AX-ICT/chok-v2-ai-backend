-- chok_ai 프로비저닝 — 공유 MySQL 인스턴스에 FastAPI용 DB·유저·권한 생성 (1회).
--
-- 적용:
--   (A) 공유 MySQL의 /docker-entrypoint-initdb.d/ 에 배치 → 최초 기동 시 자동 실행
--       (데이터 볼륨이 이미 있으면 initdb는 안 도니 아래 (B)로 수동 실행)
--   (B) 관리자(root)로 직접:  mysql -h <host> -P <port> -u root -p < deploy/init-chok-ai.sql
--
-- 주의: '<비밀번호>'를 실제 강한 비밀번호로 교체하고, 같은 값을 FastAPI의 MYSQL_PASSWORD로 주입.
--       보안 강화 시 'chok_ai'@'%' 의 '%'를 앱 네트워크/호스트로 제한 가능.

CREATE DATABASE IF NOT EXISTS chok_ai
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'chok_ai'@'%' IDENTIFIED BY '<비밀번호>';

GRANT ALL PRIVILEGES ON chok_ai.* TO 'chok_ai'@'%';

FLUSH PRIVILEGES;
