"""add stt credential inspection status

Revision ID: 20260509_002
Revises: 20260509_001
Create Date: 2026-05-09 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260509_002"
down_revision: Union[str, None] = "20260509_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    status_enum = sa.Enum("unknown", "pending_inspection", "verified", "partial", "degraded", "invalid", name="providercredentialstatus")
    status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "team_stt_configs",
        sa.Column("credential_status", status_enum, nullable=False, server_default="unknown"),
    )
    op.add_column("team_stt_configs", sa.Column("credential_fingerprint", sa.String(length=128), nullable=True))
    op.add_column("team_stt_configs", sa.Column("inspection_metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("team_stt_configs", "inspection_metadata_json")
    op.drop_column("team_stt_configs", "credential_fingerprint")
    op.drop_column("team_stt_configs", "credential_status")
    status_enum = sa.Enum("unknown", "pending_inspection", "verified", "partial", "degraded", "invalid", name="providercredentialstatus")
    status_enum.drop(op.get_bind(), checkfirst=True)
