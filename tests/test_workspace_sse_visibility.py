"""Background-tab update suspension contract for the transcribe workspace."""

from pathlib import Path


def test_hidden_idle_tabs_close_sse_but_active_capture_keeps_it():
    app_js = Path("app/static/js/transcribe/app.js").read_text()
    helper = app_js.split("const shouldSuspendHiddenWorkspaceRealtime = () => (", 1)[1].split(");", 1)[0]

    assert "document.visibilityState === 'hidden'" in helper
    assert "!captureController?.isCaptureUiActive?.()" in helper
    assert "!dictationLiveCapture" in helper
    assert "!dictationLiveStartup" in helper
    assert "['starting', 'recording', 'paused', 'transcribing'].includes(dictationRecordingState)" in helper
    assert "return !shouldSuspendHiddenWorkspaceRealtime() && !isWorkspaceRealtimeConnected();" in app_js


def test_workspace_reconnects_and_refreshes_when_visible():
    app_js = Path("app/static/js/transcribe/app.js").read_text()
    handler = app_js.split("document.addEventListener('visibilitychange', () => {", 1)[1].split("\n      });", 1)[0]

    assert "if (shouldSuspendHiddenWorkspaceRealtime()) {" in handler
    assert "clearWorkspaceRefreshBurst();" in handler
    assert "closeWorkspaceEventSource();" in handler
    assert "document.visibilityState === 'visible'" in handler
    assert "syncWorkspaceRealtimeConnection();" in handler
    assert "void fetchWorkspace();" in handler
    assert "if (shouldSuspendHiddenWorkspaceRealtime()) {" in app_js.split("const syncWorkspaceRealtimeConnection = () => {", 1)[1].split("if (!window.EventSource)", 1)[0]
