"""add elevenlabs stt adapter

Revision ID: e4f5a6b7c9d1
Revises: d3e4f5a6b7c9
Create Date: 2026-05-11 00:00:00.000000
"""

from alembic import op
from sqlalchemy.dialects import postgresql


revision = "e4f5a6b7c9d1"
down_revision = "d3e4f5a6b7c9"
branch_labels = None
depends_on = None


old_stt_adapter_kind = postgresql.ENUM(
    "generic_rest",
    "openai_cloud",
    "openai_compatible_rest",
    name="sttadapterkind",
    create_type=False,
)
new_stt_adapter_kind = postgresql.ENUM(
    "generic_rest",
    "openai_cloud",
    "openai_compatible_rest",
    "elevenlabs_speech_to_text",
    name="sttadapterkind_new",
)
new_stt_adapter_kind_existing = postgresql.ENUM(
    "generic_rest",
    "openai_cloud",
    "openai_compatible_rest",
    "elevenlabs_speech_to_text",
    name="sttadapterkind_new",
    create_type=False,
)


def upgrade() -> None:
    new_stt_adapter_kind.create(op.get_bind(), checkfirst=False)
    op.execute(
        """
        ALTER TABLE team_stt_configs
        ALTER COLUMN adapter_kind
        TYPE sttadapterkind_new
        USING (
            CASE
                WHEN provider_preset = 'elevenlabs' THEN 'elevenlabs_speech_to_text'
                ELSE adapter_kind::text
            END
        )::sttadapterkind_new
        """
    )
    old_stt_adapter_kind.drop(op.get_bind(), checkfirst=False)
    op.execute("ALTER TYPE sttadapterkind_new RENAME TO sttadapterkind")
    op.execute(
        """
        UPDATE team_stt_configs
        SET
            transcribe_path = '/v1/speech-to-text',
            model_name = CASE
                WHEN model_name IN ('scribe_v1', 'scribe_v2') THEN model_name
                ELSE 'scribe_v2'
            END,
            available_models_json = '["scribe_v2", "scribe_v1"]'::json,
            model_field_name = 'model_id',
            file_field_name = 'file',
            language_field_name = 'language_code',
            response_text_path = 'text',
            segments_path = 'words',
            segment_text_field = 'text',
            segment_start_field = 'start',
            segment_end_field = 'end',
            segment_speaker_field = 'speaker_id'
        WHERE provider_preset = 'elevenlabs'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TYPE sttadapterkind RENAME TO sttadapterkind_new")
    old_stt_adapter_kind.create(op.get_bind(), checkfirst=False)
    op.execute(
        """
        ALTER TABLE team_stt_configs
        ALTER COLUMN adapter_kind
        TYPE sttadapterkind
        USING (
            CASE
                WHEN adapter_kind::text = 'elevenlabs_speech_to_text' THEN 'generic_rest'
                ELSE adapter_kind::text
            END
        )::sttadapterkind
        """
    )
    new_stt_adapter_kind_existing.drop(op.get_bind(), checkfirst=False)
