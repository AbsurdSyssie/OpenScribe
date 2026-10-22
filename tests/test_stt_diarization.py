from app.services.deepgram_streaming import (
    DeepgramResultEvent,
    DeepgramWordEvent,
    deepgram_event_words_or_fallback,
    group_deepgram_words,
)
from app.services import stt
from app.models import SttAdapterKind, SttConfigSetupStatus, SttProviderPreset, TeamRole
from app.services.preferences import diarization_preference_enabled
from app.services.transcript_streaming import _selection_snapshot
from types import SimpleNamespace
from uuid import uuid4


def test_batch_speaker_segments_keep_zero_and_group_adjacent_words():
    result = stt._speaker_turns_from_segments(
        {"words": [
            {"text": "Hello", "speaker": 0, "start": 0.1, "end": 0.3},
            {"text": "there.", "speaker": 0, "start": 0.31, "end": 0.5},
            {"text": "Yes.", "speaker": 1, "start": 0.6, "end": 0.8},
        ]},
        segments_path="words",
        segment_text_field="text",
        segment_start_field="start",
        segment_end_field="end",
        segment_speaker_field="speaker",
    )

    assert result is not None
    assert [(turn.text, turn.speaker_id, turn.start_ms, turn.end_ms) for turn in result] == [
        ("Hello there.", "0", 100, 500),
        ("Yes.", "1", 600, 800),
    ]


def test_invalid_batch_metadata_keeps_text_and_drops_only_bad_attribution_or_timing():
    result = stt._speaker_turns_from_segments(
        {"words": [
            {"text": "Keep this", "speaker": "x" * 65, "start": 2.0, "end": 1.0},
        ]},
        segments_path="words",
        segment_text_field="text",
        segment_start_field="start",
        segment_end_field="end",
        segment_speaker_field="speaker",
    )

    assert result is not None
    assert len(result) == 1
    assert result[0].text == "Keep this"
    assert result[0].speaker_id is None
    assert result[0].start_ms is None and result[0].end_ms is None


def test_oversized_batch_word_map_falls_back_without_truncating_transcript():
    previous_limit = stt.MAX_DIARIZATION_SEGMENTS
    stt.MAX_DIARIZATION_SEGMENTS = 1
    try:
        result = stt._speaker_turns_from_segments(
            {"words": [{"text": "one", "speaker": 0}, {"text": "two", "speaker": 1}]},
            segments_path="words",
            segment_text_field="text",
            segment_start_field="start",
            segment_end_field="end",
            segment_speaker_field="speaker",
        )
    finally:
        stt.MAX_DIARIZATION_SEGMENTS = previous_limit

    assert result is None


def test_deepgram_word_mismatch_preserves_result_as_unattributed_and_zero_is_valid():
    incomplete = DeepgramResultEvent(
        text="complete source text", is_final=True, speech_final=True,
        from_finalize=False, start=0.0, duration=1.0,
        words=(DeepgramWordEvent("complete", "0", 0.0, 0.3),),
    )
    fallback = deepgram_event_words_or_fallback(incomplete)
    assert len(fallback) == 1
    assert fallback[0].text == "complete source text"
    assert fallback[0].speaker_id is None

    grouped = group_deepgram_words((
        DeepgramWordEvent("hello", "0", 0.0, 0.2),
        DeepgramWordEvent("there.", "0", 0.2, 0.4),
    ))
    assert grouped == [{"text": "hello there.", "speaker_id": "0", "start_ms": 0, "end_ms": 400}]


def test_custom_provider_option_is_removed_or_overridden_by_frozen_choice():
    assert stt._deepgram_query_fields({"diarize": "true", "smart_format": "true"}, enabled=False) == {
        "smart_format": "true",
    }
    assert stt._deepgram_query_fields({"DIARIZE": "false"}, enabled=True) == {"diarize": "true"}


def test_persistent_user_opt_in_is_default_off_and_scope_limited():
    from app.models import UserAppPreference

    pref = UserAppPreference(user_id=uuid4(), preferences_json={"diarization_enabled": True})
    db = SimpleNamespace(scalar=lambda _statement: pref)
    user = SimpleNamespace(id=pref.user_id, team_id=uuid4(), team_role=TeamRole.user, is_system_admin=False)
    assert diarization_preference_enabled(db, user) is True

    pref.preferences_json = {"diarization_enabled": False}
    assert diarization_preference_enabled(db, user) is False
    pref.preferences_json = {"diarization_enabled": True}
    assert diarization_preference_enabled(db, SimpleNamespace(**{**vars(user), "is_system_admin": True})) is False


def test_capability_is_provider_and_connection_type_specific():
    def config(preset, adapter, *, live=False, batch=False, mapping=True):
        return SimpleNamespace(
            is_active=True, setup_status=SttConfigSetupStatus.ready,
            provider_preset=preset, adapter_kind=adapter,
            base_url="https://api.deepgram.com" if preset == "deepgram" else "https://stt.example.test",
            diarization_live_enabled=live, diarization_batch_enabled=batch,
            segments_path="segments", segment_speaker_field="speaker",
            diarization_field_name="speaker_mode" if mapping else None,
            diarization_on_value="true", diarization_off_value="false",
        )

    deepgram = config("deepgram", SttAdapterKind.generic_rest, live=True, batch=True)
    custom = config("custom_rest_openapi", SttAdapterKind.generic_rest, live=True, batch=True)
    assert stt.stt_config_supports_diarization(deepgram, connection_type="live")
    assert stt.stt_config_supports_diarization(deepgram, connection_type="batch")
    assert not stt.stt_config_supports_diarization(custom, connection_type="live")
    assert stt.stt_config_supports_diarization(custom, connection_type="batch")
    custom.diarization_field_name = None
    assert not stt.stt_config_supports_diarization(custom, connection_type="batch")


def test_live_selection_strips_saved_diarize_when_frozen_off_and_forces_true_when_on():
    base = {
        "provider_preset": "deepgram", "adapter_kind": "generic_rest",
        "mip_opt_out": True, "config_id": str(uuid4()),
        "base_url": "https://api.deepgram.com", "model_name": "nova-3",
        "extra_query_params": {"diarize": "true", "smart_format": "true"},
        "diarization_live_enabled": True,
    }
    off = _selection_snapshot({**base, "stt_diarisation_enabled": False})
    assert "diarize" not in off["extra_query_params"]
    on = _selection_snapshot({**base, "stt_diarisation_enabled": True})
    assert on["extra_query_params"]["diarize"] == "true"

    custom_live = {**base, "provider_preset": "custom_rest_openapi"}
    try:
        _selection_snapshot(custom_live)
    except Exception as exc:
        assert getattr(exc, "code", None) == "stream_selection_unsupported"
    else:
        raise AssertionError("custom providers must not enter the Deepgram live route")


def test_custom_multipart_option_is_overridden_to_frozen_off(monkeypatch):
    from contextlib import contextmanager
    import httpx

    captured = {}
    response = httpx.Response(
        200, json={"text": "Synthetic sentence."},
        request=httpx.Request("POST", "https://stt.example.test/transcribe"),
    )

    @contextmanager
    def fake_stream(method, url, **kwargs):
        captured.update(kwargs)
        try:
            yield response
        finally:
            response.close()

    monkeypatch.setattr(stt.httpx, "stream", fake_stream)
    result = stt._transcribe_via_http(
        base_url="https://stt.example.test", transcribe_path="/transcribe",
        file_field_name="file", response_text_path="text",
        extra_form_fields_json={"Speaker_Mode": "true", "format": "json"},
        bearer_token="synthetic", model_name=None, model_field_name=None,
        language=None, language_field_name=None, audio_bytes=b"synthetic audio",
        filename="synthetic.wav", content_type="audio/wav",
        provider_preset=SttProviderPreset.custom_rest_openapi.value,
        adapter_kind=SttAdapterKind.generic_rest, diarization_enabled=False,
        diarization_field_name="speaker_mode", diarization_on_value="on",
        diarization_off_value="off",
    )
    assert result == "Synthetic sentence."
    assert captured["data"] == {"format": "json", "speaker_mode": "off"}


def test_deepgram_batch_transport_returns_typed_turns_and_freezes_diarize_true(monkeypatch):
    from contextlib import contextmanager
    import httpx

    captured = {}
    response = httpx.Response(200, json={"results": {"channels": [{"alternatives": [{
        "transcript": "Hello there.",
        "words": [
            {"punctuated_word": "Hello", "speaker": 0, "start": 0.0, "end": 0.2},
            {"punctuated_word": "there.", "speaker": 0, "start": 0.21, "end": 0.4},
        ],
    }]}]}}, request=httpx.Request("POST", "https://api.deepgram.com/v1/listen"))

    @contextmanager
    def fake_stream(method, url, **kwargs):
        captured.update(kwargs)
        try:
            yield response
        finally:
            response.close()

    monkeypatch.setattr(stt.httpx, "stream", fake_stream)
    result = stt._transcribe_via_deepgram(
        url="https://api.deepgram.com/v1/listen", bearer_token="synthetic",
        audio_bytes=b"synthetic audio", content_type="audio/wav", model_name="nova-3",
        language=None, extra_query_params={"diarize": "false"},
        response_text_path="results.channels.0.alternatives.0.transcript",
        segments_path=None, segment_text_field=None, segment_start_field="start",
        segment_end_field="end", segment_speaker_field=None, diarization_enabled=True,
    )
    assert isinstance(result, stt.SttTranscriptResult)
    assert result.text == "Hello there."
    assert [(turn.text, turn.speaker_id) for turn in result.turns] == [("Hello there.", "0")]
    assert captured["params"]["diarize"] == "true"


def test_batch_worker_uses_frozen_flags_after_preferences_or_admin_config_change(monkeypatch):
    calls = []

    def fake_http(**kwargs):
        calls.append(kwargs)
        return "Synthetic transcript."

    monkeypatch.setattr(stt, "_transcribe_via_http", fake_http)

    def dispatch(enabled):
        return stt.transcribe_with_stt_snapshot(
            object(), team_id=uuid4(), stt_config_id=uuid4(),
            provider_preset="custom_rest_openapi", adapter_kind="generic_rest",
            base_url="https://stt.example.test", transcribe_path="/transcribe",
            file_field_name="file", response_text_path="text", extra_form_fields_json={},
            model_name=None, model_field_name=None, language=None, language_field_name=None,
            segments_path="segments", segment_text_field="text", segment_start_field="start",
            segment_end_field="end", segment_speaker_field="speaker",
            stt_diarisation_enabled=enabled, diarization_batch_enabled=True,
            diarization_field_name="speaker_mode", diarization_on_value="yes",
            diarization_off_value="no", audio_bytes=b"synthetic", filename="synthetic.wav",
            content_type="audio/wav", _resolved_bearer_token="synthetic",
        )

    # These are immutable queued-job values. No current user preference or
    # mutable TeamSttConfig is consulted after dispatch.
    assert dispatch(True) == "Synthetic transcript."
    assert calls[-1]["diarization_enabled"] is True
    assert calls[-1]["diarization_on_value"] == "yes"
    # Dictation and opted-out captures queue false; a saved provider default
    # cannot turn it back on inside the worker.
    assert dispatch(False) == "Synthetic transcript."
    assert calls[-1]["diarization_enabled"] is False


def test_dictation_stt_path_does_not_enable_diarization(monkeypatch):
    captured = {}
    config = SimpleNamespace(
        team_id=uuid4(), adapter_kind=SttAdapterKind.generic_rest,
        provider_preset=SttProviderPreset.deepgram.value,
        base_url="https://api.deepgram.com", transcribe_path="/v1/listen",
        file_field_name="file", response_text_path="text", extra_form_fields_json={"diarize": "true"},
        model_field_name="model", language_field_name="language",
    )
    monkeypatch.setattr(stt, "resolve_selected_team_stt", lambda *_args, **_kwargs: (None, config, "nova-3", None))
    monkeypatch.setattr(stt, "_read_saved_stt_bearer_token", lambda **_kwargs: "synthetic")

    def metered(_db, *, provider_call, **_kwargs):
        return provider_call()

    monkeypatch.setattr(stt, "_transcribe_metered_audio", metered)

    def resolved(**kwargs):
        captured.update(kwargs)
        return "Synthetic dictation."

    monkeypatch.setattr(stt, "_transcribe_with_resolved_team_stt", resolved)
    result = stt.transcribe_metered_team_stt(
        object(), team_id=config.team_id, owner_user_id=uuid4(), transcript_id=uuid4(),
        attempt_kind=object(), measured_duration_seconds=1,
        purpose=stt.SttSelectionPurpose.post_consultation_dictation,
        audio_bytes=b"synthetic", filename="synthetic.wav", content_type="audio/wav",
    )
    assert result == "Synthetic dictation."
    assert "diarization_enabled" not in captured
    assert "stt_diarisation_enabled" not in captured


def test_live_scope_survives_checkpoints_and_changes_after_provider_reconnect(monkeypatch):
    import asyncio
    from datetime import timedelta
    from app.routes import deepgram_live as live
    from app.models import utcnow
    from app.services.transcript_streaming import StreamBatchAuthorization, StreamSessionStarted, StreamWindowCommitted

    async def scenario():
        started = StreamSessionStarted(
            session_id=uuid4(), connection_fence=1, fence_token=uuid4(),
            lease_expires_at=utcnow() + timedelta(seconds=30), window_no=0,
            next_source_sample_offset=0,
        )
        class Socket:
            async def send_json(self, _payload):
                return None

        async def owner_call(*_args):
            return None

        monkeypatch.setattr(live, "_owner_call", owner_call)
        relay = live._Relay(Socket(), uuid4(), started)
        providers = []
        committed_scopes = []
        total = 0

        class Provider:
            def __init__(self):
                self.events = asyncio.Queue()
                self.media = []

            async def receive(self, *, timeout_seconds=None):
                return await self.events.get()

            async def send_media(self, data):
                self.media.append(data)

            async def send_close_stream(self):
                from app.services.deepgram_streaming import DeepgramMetadataEvent
                self.events.put_nowait(DeepgramMetadataEvent(1, "synthetic", 1))

        class Context:
            def __init__(self):
                self.provider = Provider()
                providers.append(self.provider)

            async def __aenter__(self):
                return self.provider

            async def __aexit__(self, *_args):
                return None

        monkeypatch.setattr("app.services.deepgram_streaming.DeepgramLiveStream", lambda *_a, **_kw: Context())

        async def service(name, **kwargs):
            nonlocal total
            if name == "authorize_stream_batch":
                first, count = kwargs["first_sample_offset"], kwargs["sample_count"]
                start = total
                total += count
                return StreamBatchAuthorization(
                    uuid4(), uuid4(), True, relay.window_no, start, total,
                    first + count, total, total - relay.committed_samples,
                )
            if name == "record_stream_batch_sent":
                return None
            if name == "commit_stream_window":
                committed_scopes.append(kwargs["scope_id"])
                return StreamWindowCommitted(
                    relay.window_no, kwargs["source_end_sample_offset"], kwargs["forwarded_sample_count"],
                )
            raise AssertionError(name)

        relay.service = service
        preflight = live._Preflight(
            config=object(), snapshot={"stt_diarisation_enabled": True}, api_key="synthetic",
        )
        pcm = b"\x10\x00\x11\x00"

        async def send_window(offset, word):
            await relay.audio_frame(live.FRAME_HEADER.pack(1, offset, 2) + pcm, preflight)
            from app.services.deepgram_streaming import DeepgramResultEvent, DeepgramWordEvent
            text = word + "."
            relay.finals[(offset, offset + 2)] = text
            relay.final_events[(offset, offset + 2)] = DeepgramResultEvent(
                text=text, is_final=True, speech_final=True, from_finalize=False,
                start=offset / live.SAMPLE_RATE, duration=2 / live.SAMPLE_RATE,
                words=(DeepgramWordEvent(text, "0", 0.0, 0.01),),
            )
            return relay.scope_id

        first_scope = await send_window(0, "First")
        await relay._commit_window()  # checkpoint on the same provider socket
        second_scope = await send_window(2, "Second")
        await relay._commit_window()
        assert first_scope == second_scope == committed_scopes[0] == committed_scopes[1]

        await relay._drain_and_close_provider()
        third_scope = await send_window(4, "Third")
        await relay._commit_window()
        assert third_scope != second_scope
        assert committed_scopes[2] == third_scope
        await relay.close()

    asyncio.run(scenario())


def test_capture_settings_freeze_preference_and_provider_mapping(monkeypatch):
    from app.models import TranscriptIngestionMode, TranscriptStatus
    from app.services import transcripts as transcript_service

    owner_id = uuid4()
    config_id = uuid4()
    transcript = SimpleNamespace(
        id=uuid4(), owner_user_id=owner_id, team_id=uuid4(),
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.recording,
    )
    config = SimpleNamespace(
        id=config_id, is_active=True, setup_status=SttConfigSetupStatus.ready,
        provider_preset=SttProviderPreset.custom_rest_openapi.value,
        adapter_kind=SttAdapterKind.generic_rest, base_url="https://stt.example.test",
        diarization_live_enabled=False, diarization_batch_enabled=True,
        segments_path="segments", segment_speaker_field="speaker",
        diarization_field_name="speaker_mode", diarization_on_value="enabled",
        diarization_off_value="disabled",
    )

    class FakeDb:
        added = None

        def add(self, item):
            self.added = item

        def commit(self):
            return None

        def refresh(self, _item):
            return None

    db = FakeDb()
    monkeypatch.setattr(transcript_service, "_lock_split_source_writer_transcript", lambda *_a, **_kw: transcript)
    monkeypatch.setattr(transcript_service, "transcript_is_expired", lambda _transcript: False)
    monkeypatch.setattr(transcript_service, "assert_no_unresolved_stream_session", lambda *_a, **_kw: None)
    monkeypatch.setattr(transcript_service, "resolve_selected_team_stt", lambda *_a, **_kw: (None, config, None, None))
    preference = {"on": True}
    monkeypatch.setattr(transcript_service, "diarization_preference_enabled", lambda *_a: preference["on"])
    user = SimpleNamespace(id=owner_id)

    capture = transcript_service.create_transcript_capture_settings(
        db, user, transcript_id=transcript.id,
    )
    assert capture.stt_diarisation_enabled is True
    assert capture.stt_config_id == config_id
    assert capture.stt_diarization_field_name == "speaker_mode"
    assert capture.stt_diarization_on_value == "enabled"
    assert capture.stt_diarization_off_value == "disabled"

    # Later preference or admin changes affect a later capture, never this saved row.
    preference["on"] = False
    config.diarization_batch_enabled = False
    assert capture.stt_diarisation_enabled is True
    assert capture.stt_diarization_batch_enabled is True


def test_audio_queue_uses_capture_snapshot_only_for_same_selected_config(monkeypatch):
    from app.models import TranscriptIngestionMode, TranscriptStatus
    from app.services import transcripts as transcript_service

    owner_id, team_id, transcript_id, config_id = (uuid4() for _ in range(4))
    owner = SimpleNamespace(id=owner_id, team_id=team_id)
    transcript = SimpleNamespace(
        id=transcript_id, owner_user_id=owner_id, team_id=team_id,
        ingestion_mode=TranscriptIngestionMode.whole_file, status=TranscriptStatus.ready,
    )
    config = SimpleNamespace(
        id=config_id, team_id=team_id,
        provider_preset=SttProviderPreset.custom_rest_openapi.value,
        adapter_kind=SttAdapterKind.generic_rest, base_url="https://stt.example.test",
        transcribe_path="/transcribe", model_name="synthetic", model_field_name="model",
        file_field_name="file", language=None, language_field_name="language",
        response_text_path="text", segments_path="segments", segment_text_field="text",
        segment_start_field="start", segment_end_field="end", segment_speaker_field="speaker",
        extra_form_fields_json={}, diarization_live_enabled=False, diarization_batch_enabled=True,
        diarization_field_name="current_flag", diarization_on_value="yes", diarization_off_value="no",
    )
    captured_settings = SimpleNamespace(
        id=uuid4(), stt_config_id=config_id, stt_diarisation_enabled=True,
        stt_diarization_batch_enabled=True, stt_diarization_field_name="capture_flag",
        stt_diarization_on_value="enabled", stt_diarization_off_value="disabled",
        stt_contract_fingerprint=transcript_service._stt_capture_config_fingerprint(config),
    )

    class FakeDb:
        def __init__(self):
            self.results = iter([None, captured_settings])
            self.added = []

        def scalar(self, _query):
            return next(self.results)

        def add(self, item):
            self.added.append(item)

        def flush(self):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

        def refresh(self, _item):
            return None

    monkeypatch.setattr(transcript_service, "_get_owner_transcript_for_ingestion", lambda *_a, **_kw: transcript)
    monkeypatch.setattr(transcript_service, "resolve_selected_team_stt", lambda *_a, **_kw: (None, config, "synthetic", None))
    monkeypatch.setattr(transcript_service, "ensure_stt_config_credential_ready", lambda **_kw: None)
    monkeypatch.setattr(transcript_service, "inspect_audio_duration_seconds", lambda **_kw: 1.0)
    monkeypatch.setattr(transcript_service, "_enforce_whole_file_hourly_usage_budget", lambda *_a, **_kw: None)
    monkeypatch.setattr(transcript_service, "write_transcript_ingestion_source_audio", lambda **_kw: "synthetic-vault-ref")
    monkeypatch.setattr(transcript_service, "_queue_ingestion_attempt_and_dispatch", lambda *_a, **_kw: None)
    monkeypatch.setattr(transcript_service, "record_security_event", lambda *_a, **_kw: None)

    _result_transcript, matching_job = transcript_service.queue_audio_file_ingestion(
        FakeDb(), owner, transcript_id=transcript_id, filename="synthetic.wav",
        source_audio_blob=b"synthetic", capture_settings_id=uuid4(),
    )
    assert matching_job.stt_diarisation_enabled is True
    assert matching_job.stt_diarization_field_name == "capture_flag"
    assert matching_job.stt_diarization_on_value == "enabled"
    assert matching_job.stt_diarization_off_value == "disabled"

    # An in-place provider contract change between capture and upload preserves
    # normal transcription but disables incompatible captured diarisation.
    config.diarization_field_name = "changed_flag"
    second_db = FakeDb()
    _result_transcript, mismatched_job = transcript_service.queue_audio_file_ingestion(
        second_db, owner, transcript_id=transcript_id, filename="synthetic.wav",
        source_audio_blob=b"synthetic", capture_settings_id=uuid4(),
    )
    assert mismatched_job.stt_diarisation_enabled is False
