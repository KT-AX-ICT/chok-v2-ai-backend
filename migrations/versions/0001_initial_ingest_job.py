"""initial: ingest_job 테이블

Revision ID: 0001
Revises:
Create Date: 2026-07-21

app/db/models.py의 IngestJob과 1:1. 라이브 DB 없이 손으로 작성(autogenerate 아님).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_job",
        sa.Column("job_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("bundle", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("job_id"),
    )


def downgrade() -> None:
    op.drop_table("ingest_job")
