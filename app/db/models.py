from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class IngestJob(Base):
    __tablename__ = "ingest_job"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    # logs/metrics/traces를 뺀 경량 번들(윈도·트리거·모달리티 메타 등). 무거운 3종은 파일로 뺐다.
    bundle: Mapped[dict] = mapped_column(JSON, nullable=False)
    # 3종 원본이 담긴 파일 이름. 디렉터리는 설정(bundle_storage_dir)에서 해석한다.
    # NULL이면 파일 분리 도입 이전 job이라 bundle 안에 3종이 그대로 들어 있다.
    signals_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 중단 회수로 큐에 다시 넣은 횟수. 재기동 후에도 알아야 하므로 메모리가 아닌 행에 남긴다
    # (허용 횟수를 넘기면 FAILED로 확정 — stuck_job_reaper).
    requeue_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
