"""Reconcile the standalone Terms migration with first-content history."""

from alembic import op


revision = "05e6f7a8b9c0"
down_revision = "04d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The standalone Terms branch used revision 04 directly after 02, while the
    # canonical chain inserts 03 first. In those two known histories, presence
    # of track_first_content_at distinguishes the canonical schema from the
    # standalone Terms schema. Static SQL supports Alembic offline rendering.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_attribute
                WHERE attrelid = 'transcripts'::regclass
                  AND attname = 'track_first_content_at'
                  AND NOT attisdropped
            ) THEN
                ALTER TABLE transcripts
                    ADD COLUMN first_content_at TIMESTAMPTZ NULL;
                ALTER TABLE transcripts
                    ADD COLUMN track_first_content_at BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE transcripts
                    ALTER COLUMN track_first_content_at SET DEFAULT TRUE;
                ALTER TABLE transcripts
                    ADD CONSTRAINT ck_transcripts_first_content_tracking
                    CHECK (track_first_content_at OR first_content_at IS NULL);
                CREATE INDEX ix_transcripts_owner_sidebar_timestamp
                ON transcripts (
                    owner_user_id,
                    COALESCE(first_content_at, created_at) DESC,
                    id DESC
                );
            END IF;
        END;
        $$
    """)


def downgrade() -> None:
    # This is a forward-only history repair. Downgrading to canonical 04 must
    # keep the first-content schema that revision 03 owns.
    pass
