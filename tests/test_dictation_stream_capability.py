"""Dictation streaming uses its selected provider independently of consultation mode."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import SttAdapterKind, SttSelectionPurpose, TranscriptIngestionMode
from app.web import transcribe_workspace as workspace


def _capability(monkeypatch, *, purpose, mode=TranscriptIngestionMode.whole_file,
                provider="deepgram", owner_matches=True, administrator=False,
                available=True):
    owner = SimpleNamespace(id=uuid4(), team_id=uuid4(), is_system_admin=administrator)
    root = SimpleNamespace(owner_user_id=owner.id if owner_matches else uuid4(), ingestion_mode=mode)
    config = SimpleNamespace(provider_preset=provider, adapter_kind=SttAdapterKind.generic_rest,
                             base_url="https://api.deepgram.com", extra_form_fields_json={})
    selected = SimpleNamespace(config=config)
    resolved_purposes = []

    def resolve(_db, *, team_id, purpose):
        assert team_id == owner.team_id
        resolved_purposes.append(purpose)
        return selected, config, "nova-3", "en-GB"

    monkeypatch.setattr(workspace, "resolve_selected_team_stt_service", resolve)
    result = workspace._deepgram_stream_capable(
        None, transcript=root, current_user=owner, stt_available=available,
        stt_selection=selected, purpose=purpose,
    )
    return result, resolved_purposes


def test_dictation_streaming_uses_dictation_selection_on_whole_file_root(monkeypatch):
    result, purposes = _capability(monkeypatch, purpose=SttSelectionPurpose.post_consultation_dictation)
    assert result
    assert purposes == [SttSelectionPurpose.post_consultation_dictation]


def test_conversation_streaming_still_requires_live_root(monkeypatch):
    result, purposes = _capability(monkeypatch, purpose=SttSelectionPurpose.conversation)
    assert not result
    assert purposes == []
    result, purposes = _capability(monkeypatch, purpose=SttSelectionPurpose.conversation,
                                  mode=TranscriptIngestionMode.live_chunked)
    assert result
    assert purposes == [SttSelectionPurpose.conversation]


@pytest.mark.parametrize("options", [
    {"provider": "other"}, {"owner_matches": False},
    {"administrator": True}, {"available": False},
])
def test_dictation_streaming_capability_fails_closed(monkeypatch, options):
    result, purposes = _capability(monkeypatch, purpose=SttSelectionPurpose.post_consultation_dictation, **options)
    assert not result
    assert purposes == []
