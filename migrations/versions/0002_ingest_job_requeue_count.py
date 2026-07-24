"""ingest_job에 requeue_count 추가

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23

중단(RUNNING 잔류) job을 큐에 다시 넣은 횟수. 재기동 후에도 판단해야 하므로 행에 남긴다.
기존 행은 server_default="0"으로 채워진다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingest_job",
        sa.Column(
            "requeue_count", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_job", "requeue_count")
