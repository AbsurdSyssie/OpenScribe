from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_workspace_shell_dispatches_one_section_and_loads_conditional_assets():
    page = read("app/templates/workspace.html")
    assert 'data-workspace-section="{{ active_workspace_section }}"' in page
    assert '{% if active_workspace_section == \'account\' %}{% include "settings/_account.html" %}' in page
    assert '{% elif active_workspace_section == \'preferences\' %}{% include "settings/_preferences.html" %}' in page
    assert '{% include "settings/_assets.html" %}' not in page
    assert 'active_workspace_section == \'scribe\'' in page
    assert "/static/js/workspace/app.js" in page


def test_workspace_sidebar_has_required_order_roles_and_recording_markers():
    sidebar = read("app/templates/workspace/_sidebar.html")
    expected = [
        ">Create new consultation</span>",
        ">Back to Scribe</span>",
        ">Account</span>",
        ">Preferences</span>",
        ">My Templates</span>",
        ">My quick actions</span>",
        ">Smart phrases</span>",
        ">AI services</span>",
        ">Team members</span>",
        ">Account requests</span>",
        ">Sign out</span>",
    ]
    positions = [sidebar.index(label) for label in expected]
    assert positions == sorted(positions)
    assert "Recent consultations" in sidebar
    assert "{% if is_manager %}" in sidebar
    assert "not current_user.is_system_admin and current_user.team_id" in sidebar
    assert 'href="/home"' not in sidebar
    assert "data-recording-navigation" in sidebar
    assert 'action="/logout"' in sidebar


def test_workspace_js_uses_session_memory_recording_events_and_native_history():
    script = read("app/static/js/workspace/app.js")
    assert "openscribe.workspace.lastTranscriptId" in script
    assert "openscribe:recording-started" in script
    assert "openscribe:recording-stopped" in script
    assert "openscribe:recording-cancelled" in script
    assert "openscribe:recording-failed" in script
    assert "Finish or cancel the recording before leaving Scribe." in script
    assert "beforeunload" in script
    assert "open_recent" in script
    assert "history.replaceState" in script
    assert "pushState" not in script


def test_media_controller_emits_authoritative_workspace_recording_events():
    media = read("app/static/js/transcribe/media.js")
    assert "dispatchWorkspaceRecordingEvent('started')" in media
    assert "dispatchWorkspaceRecordingEvent('stopped')" in media
    assert "dispatchWorkspaceRecordingEvent('failed')" in media
    assert "openscribe:recording-${state}" in media


def test_settings_module_initializers_are_target_scoped():
    script = read("app/static/js/settings/app.js")
    for marker in ("[data-confirm-submit]", "[data-service-toggle]", "[data-stt-selection-form]", "[data-llm-selection-form]", "[data-dirty-guard]"):
        assert marker in script
    assert "data-settings-menu" not in script


def test_workspace_library_partials_use_canonical_urls_and_return_view():
    templates = read("app/templates/settings/_template_library.html")
    quick_actions = read("app/templates/settings/_quick_action_library.html") + read("app/templates/settings/_quick_action_editor.html")
    smart_phrases = read("app/templates/settings/_smart_phrase_library.html")
    assert "/workspace/library/templates?scope=" in templates
    assert "/workspace/library/quick-actions?scope=" in quick_actions
    assert "/workspace/library/smart-phrases?smart_phrase_id=" in smart_phrases
    for markup in (templates, quick_actions, smart_phrases):
        assert "/settings?tab=" not in markup
        assert 'name="return_view" value="settings"' not in markup
    assert "'return_view': 'workspace'" in templates
    assert 'name="return_view" value="workspace"' in quick_actions


def test_settings_partial_post_return_metadata_is_canonical():
    settings_dir = ROOT / "app/templates/settings"
    partials = "\n".join(path.read_text(encoding="utf-8") for path in settings_dir.glob("_*.html"))
    assert 'name="return_view" value="settings"' not in partials
