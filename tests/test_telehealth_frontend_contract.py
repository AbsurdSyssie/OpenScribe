"""Feature-gated Telehealth UI contract; ordinary microphone capture stays default."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static/js/transcribe"


def test_phone_toggle_and_first_use_notice_are_server_feature_gated():
    html = (ROOT / "app/templates/transcribe/_workspace.html").read_text(encoding="utf-8")
    assert "{% if telehealth_feature_enabled %}" in html
    assert "{% if not user_app_preferences_json.get('show_telehealth_control', true) %}hidden{% endif %}" in html
    assert 'data-telehealth-toggle' in html
    assert 'data-lucide="phone"' in html
    assert 'aria-pressed="false"' in html
    assert "{% if telehealth_feature_enabled %}" in html
    for marker in (
        "data-telehealth-guidance",
        "data-telehealth-guidance-continue",
        "data-telehealth-guidance-cancel",
        "data-telehealth-speech-status=\"clinician\"",
        "data-telehealth-speech-status=\"patient\"",
        "data-telehealth-combined",
        "data-telehealth-retry",
        "data-telehealth-transcribe",
        "data-telehealth-discard",
        "data-telehealth-debug-panel",
        "data-telehealth-debug-reference",
        "data-telehealth-debug-events",
    ):
        assert marker in html
    assert "Headphones are recommended" in html
    assert "Inform all participants before recording" in html
    assert "temporarily encrypted in this browser profile" in html
    assert "A crash can still lose the final unflushed audio" in html
    page = (ROOT / "app/templates/workspace.html").read_text(encoding="utf-8")
    assert "{% if telehealth_feature_enabled %}" in page
    assert "/static/css/telehealth.css" in page


def test_dual_audio_never_uses_existing_microphone_handler_for_telehealth():
    ui = (STATIC / "telehealthUI.js").read_text(encoding="utf-8")
    main = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "attachTelehealthControls({" in main
    assert "mainRecord.addEventListener('click', (event) => {" in ui
    assert "event.stopImmediatePropagation();" in ui
    assert "}, true);" in ui
    assert "const attempt = client.start(); // Direct user activation" in ui
    assert "getIngestionMode?.() !== 'whole_file'" in ui
    assert "telehealth_guidance_acknowledged: true" in ui
    assert "SPEECH_CONFIRM_MS = 5000" in ui
    assert "confirmed[channel]" in ui
    assert "onLevel" in ui
    assert "onSpeechFrame" in ui
    assert "createTelehealthDiagnostics" in ui
    assert "Download diagnostics" not in ui
    assert "/telehealth/diagnostics" in ui
    assert "AUDIO_ACTIVITY_THRESHOLD" not in ui
    assert "createTelehealthRecordingClient" in ui
    assert "await client.stop()" in ui
    assert "await client.transcribe()" in ui
    assert "onStateChange: render" in ui
    assert "Show the current crash-buffer/privacy disclosure on every selection" in ui
    assert "toggle.hidden = getPreferences?.().show_telehealth_control === false" in ui
    assert "!restoredCapture && !enabled" in ui
    assert "client.discard()" in ui
    assert "data-telehealth-discard" in ui
    assert "/telehealth/unresolved" in ui
    assert "postRecovery(saved, 'discard')" in ui
    assert "replayTelehealthRecoveryWindows({" in ui
    assert "await recoveryJournal.clear(saved.capture_session_id, saved.transcript_id)" in ui
    assert "onStateChange: render" in ui
    assert "isUnresolved:" in ui


def test_source_clock_gaps_are_never_ignored_for_medical_audio():
    recording = (STATIC / "telehealthRecordingClient.js").read_text(encoding="utf-8")
    assert "if (gap.endFrame > gap.startFrame)" in recording
    assert "warn(gap.channel, 'audio_clock_interrupted')" in recording
    assert "void stop().catch(() => {});" in recording


def test_owner_only_telehealth_settings_are_independently_switchable():
    html = (ROOT / "app/templates/settings/_preferences.html").read_text(encoding="utf-8")
    controls = (ROOT / "app/static/js/settings/controls.js").read_text(encoding="utf-8")
    assert "data-telehealth-visibility-preference" in html
    assert "user_app_preferences_json.get('show_telehealth_control', true)" in html
    assert "telehealthVisibility ? input.checked : current.show_telehealth_control !== false" in controls
    assert "telehealth_guidance_acknowledged: current.telehealth_guidance_acknowledged === true" in controls


def test_ui_cannot_render_while_feature_flag_is_disabled():
    workspace = (ROOT / "app/web/workspace.py").read_text(encoding="utf-8")
    transcribe = (ROOT / "app/web/transcribe_workspace.py").read_text(encoding="utf-8")
    api = (ROOT / "app/routes/api_routes.py").read_text(encoding="utf-8")
    assert 'os.getenv("TELEHEALTH_API_ENABLED", "false")' in workspace
    assert 'os.getenv("TELEHEALTH_API_ENABLED", "false")' in transcribe
    assert 'os.getenv("TELEHEALTH_API_ENABLED", "false")' in api
    assert '"telehealth_guidance_required"' in api
    assert 'UserAppPreference.user_id == context.user.id' in api
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'TELEHEALTH_API_ENABLED: "${TELEHEALTH_API_ENABLED:-false}"' in compose
