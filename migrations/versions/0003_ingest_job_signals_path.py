"""ingest_job에 signals_path 추가

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-23

logs/metrics/traces 원본을 담은 파일의 이름. 기존 행은 NULL로 남고, 그 경우 bundle 안에
3종이 그대로 들어 있는 것으로 해석한다(도입 시점에 진행 중이던 job 보호).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingest_job", sa.Column("signals_path", sa.String(length=255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ingest_job", "signals_path")
