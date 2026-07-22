from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": .env를 docker-compose와 공유하므로 앱이 안 쓰는 키(MYSQL_ROOT_PASSWORD 등) 허용
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "password"
    mysql_db: str = "chok_ai"

    spring_base_url: str = "http://localhost:8080"

    # 로그 레벨 (DEBUG/INFO/WARNING/ERROR). 중앙 로깅 설정(core.logging_config)에서 사용.
    log_level: str = "INFO"

    # 기동 시 테이블 자동 생성(create_all). 운영은 Alembic 마이그레이션을 쓰므로 기본 off.
    # 로컬 편의용으로만 켠다(DB_AUTO_CREATE=true).
    db_auto_create: bool = False

    # RCA job 동시 처리 상한(워커 수). 수집기 폭주 시 병렬 처리량 제어.
    rca_worker_concurrency: int = 2

    # --- LLM (OpenAI) ---
    # 빈 값이면 LLM 미기동(테스트·로컬 무키 환경). 모델 ID는 스냅샷 고정 — 교체는 env 한 줄.
    openai_api_key: str = ""
    openai_model_report: str = "gpt-5.5-2026-04-23"
    openai_model_analysis: str = "gpt-5.4-mini-2026-03-17"
    openai_model_light: str = "gpt-5.4-nano-2026-03-17"  # router + scan 공용
    # 전역 LLM 동시 호출 상한(세마포어). TPM 병목 완화의 2차 밸브(1차는 워커 수).
    openai_max_concurrency: int = 4
    # 429 등 재시도 횟수 — langchain-openai 내장 지수 백오프 사용.
    openai_max_retries: int = 3
    # 모달리티 입력 절단 상한(문자 수). 압축 후에도 초과 시 최후 방어선.
    openai_max_input_chars: int = 120_000

    @property
    def async_db_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}"
        )


settings = Settings()
