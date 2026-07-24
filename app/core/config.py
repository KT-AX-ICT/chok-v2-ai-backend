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
    # Spring 전송 시 모달리티별 항목 상한. Spring은 같은 MySQL을 쓰므로 원본 전량을 실으면
    # max_allowed_packet을 넘긴다(로그 30만 줄 관측). 진단 가치 우선으로 선별해 이 수만 보낸다
    # (signal_selector). 이 배열이 화면 evidence 행의 원천이라, 과하게 줄이면 근거가 빈약해진다.
    spring_signal_limit: int = 200

    # 로그 레벨 (DEBUG/INFO/WARNING/ERROR). 중앙 로깅 설정(core.logging_config)에서 사용.
    log_level: str = "INFO"

    # 기동 시 테이블 자동 생성(create_all). 운영은 Alembic 마이그레이션을 쓰므로 기본 off.
    # 로컬 편의용으로만 켠다(DB_AUTO_CREATE=true).
    db_auto_create: bool = False

    # RCA job 동시 처리 상한(워커 수). 수집기 폭주 시 병렬 처리량 제어.
    rca_worker_concurrency: int = 2

    # --- 번들 원본 파일 저장 ---
    # logs/metrics/traces 원본을 담는 파일의 디렉터리. 한 건이 수십 MB까지 커져 DB(JSON 컬럼)에
    # 넣으면 max_allowed_packet을 넘기므로 파일로 뺀다(bundle_store). 컨테이너에서는 이 경로에
    # 볼륨을 마운트해야 재시작 후에도 처리 중이던 job의 원본이 남는다.
    bundle_storage_dir: str = "data/bundles"
    # 고아 파일 회수 기준 나이(시간). 정상 경로는 job 종료 시 삭제이므로, 이보다 오래 남은 파일은
    # 파일만 쓰이고 job 기록이 실패한 경우 등으로 놓친 것이다.
    bundle_orphan_max_age_hours: float = 24.0

    # --- DB 커넥션 복원력 ---
    # 커넥션 재생성 주기(초). MySQL wait_timeout보다 짧게 잡아야 서버가 끊기 전에 선제 교체된다.
    # 공유 인스턴스의 wait_timeout이 이보다 짧으면 그 값 아래로 낮춰야 한다.
    db_pool_recycle_seconds: int = 3600
    # 커넥션 수립 타임아웃(초). 타임아웃이 없으면 커넥션이 죽어도 예외 없이 무한 대기하고,
    # 그러면 ingest가 503조차 못 내려 SDK가 계속 기다린다.
    db_connect_timeout_seconds: int = 10

    # --- 중단 job 회수 ---
    # RUNNING이 이 시간을 넘기면 중단으로 간주(초).
    # 값 선정 — 임계가 짧으면 정상 처리 중인 job을 중단으로 오인해 회수하고(RUNNING 전이 후에는
    # 행을 갱신하지 않아 updated_at만으로 진행 여부를 구분할 수 없다), 길면 진짜 멈춘 job의 발견이
    # 늦어진다. 늦은 발견은 지연에 그치지만 오인 회수는 끝나가던 분석을 버리므로 긴 쪽을 택했다.
    # 30분은 계산된 상한이 아니라 보수적 운영값이다 — LLM 호출에 요청 타임아웃이 없어
    # (agents/llm.py의 make_llm) 최악 소요를 코드로 한정할 수 없기 때문. LLM 요청 타임아웃을
    # 두거나 실제 RCA 소요 분포를 측정하면 그 값에서 역산해 줄일 수 있다.
    stuck_job_after_seconds: int = 1800
    stuck_job_interval_seconds: int = 300
    # 중단 job의 큐 재투입 허용 횟수. 넘기면 FAILED로 확정한다(무한 재투입·크래시 루프 방지).
    max_job_requeue: int = 1

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
