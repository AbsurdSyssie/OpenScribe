"""Distinguish transient dictation streams from conversation streams."""
from alembic import op
import sqlalchemy as sa

revision = "g0b1c2d3e4f5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("transcript_stream_sessions", sa.Column("purpose", sa.String(40), nullable=False, server_default="conversation"))
    op.create_check_constraint("ck_transcript_stream_sessions_purpose", "transcript_stream_sessions", "purpose IN ('conversation', 'post_consultation_dictation')")

def downgrade():
    if op.get_bind().execute(sa.text("SELECT 1 FROM transcript_stream_sessions WHERE purpose = :purpose LIMIT 1"), {"purpose": "post_consultation_dictation"}).first():
        raise RuntimeError("Cannot downgrade while dictation stream sessions exist")
    op.drop_constraint("ck_transcript_stream_sessions_purpose", "transcript_stream_sessions", type_="check")
    op.drop_column("transcript_stream_sessions", "purpose")
