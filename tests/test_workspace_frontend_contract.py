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
    assert "data-start-guide" in sidebar
    assert "Open page guide" in sidebar
    assert "active_workspace_section == 'scribe' %}<button type=\"button\" data-start-guide" not in sidebar
    assert "data-tutorial-consultation-form" in sidebar
    assert 'action="/transcribe/tutorial"' in sidebar
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


def test_scribe_guide_reuses_one_role_neutral_workflow():
    tour = read("app/static/js/transcribe/tour.js")
    assert "export function createScribeWorkflowSteps()" in tour
    assert "isScribeTour ? createScribeWorkflowSteps() : steps" in tour
    assert "openscribe:tour:transcribe:" in tour
    assert "workflow-v2" in tour
    assert "viewerRole" not in tour
    assert "activateTab('output')" in tour
    assert "activateTab('history')" in tour
    assert "activateTab('followups')" in tour
    assert "data-tutorial-consultation-form" in tour
    assert "Tutorial sectioned note" in tour

    expected_steps = [
        "Start each consult here",
        "Choose the note template",
        "Record the consult",
        "Write the working note",
        "Stop the recording",
        "Review the transcript",
        "Add your dictation",
        "Check the template",
        "Create the note",
        "Edit the note",
        "Choose, move, and copy lines",
        "Open Follow Ups",
        "Add context",
        "Use a quick action if useful",
        "Generate the follow-up",
        "Make another version",
    ]
    positions = [tour.index(title) for title in expected_steps]
    assert positions == sorted(positions)


def test_workspace_section_guides_cover_personal_library_and_leader_pages():
    page = read("app/templates/workspace.html")
    overlay = read("app/templates/workspace/_guide_overlay.html")
    guide = read("app/static/js/workspace/section-guide.js")
    styles = read("app/static/css/workspace-guide.css")

    assert 'workspace/_guide_overlay.html' in page
    assert '/static/css/workspace-guide.css?v=20260728-library-guide-resume' in page
    assert '/static/js/workspace/section-guide.js?v=20260728-library-guide-resume' in page
    assert 'data-tour-overlay' in overlay
    assert overlay.count('data-tour-scrim=') == 4
    for control in ('data-tour-back', 'data-tour-close-button', 'data-tour-next'):
        assert control in overlay
    assert 'backdrop-filter: blur(4px)' in styles

    for section in (
        'account',
        'preferences',
        'templates',
        'quick-actions',
        'smart-phrases',
        'ai-services',
        'team-members',
        'account-requests',
    ):
        assert f"{section}: [" in guide or f"'{section}': [" in guide

    for title in (
        'Manage your account',
        'Set your defaults',
        'Create a template',
        'Create a quick action',
        'Create a smart phrase',
        'Choose team services',
        'Manage team access',
        'Review access requests',
    ):
        assert title in guide

    assert 'openscribe:tour:workspace:' in guide
    assert "GUIDE_VERSION = 'section-v2'" in guide
    assert '.submit(' not in guide
    assert 'requestSubmit' not in guide


def test_library_guides_open_first_item_and_resume_after_navigation():
    guide = read("app/static/js/workspace/section-guide.js")

    assert "const LIBRARY_GUIDE_CONFIG" in guide
    assert "[data-template-library] .template-library-row__select" in guide
    assert "[data-quick-action-library] .template-library-row__select" in guide
    assert "[data-smart-phrase-library] .smart-phrase-library-row__select" in guide
    assert "shell.classList.contains('has-selection')" in guide
    assert "url.searchParams.set('guide', '1')" in guide
    assert "window.location.assign(url.toString())" in guide
    assert "resetGuideCompletion()" in guide
    assert "clearGuideQuery()" in guide
    assert "guideStartButtons: []" in guide
    assert "guide.startTour({ force: true })" in guide


def test_library_action_controls_remain_anchored_during_scroll():
    styles = read("app/static/css/workspace-guide.css")

    assert ".template-library-utilities," in styles
    assert ".smart-phrase-library-utilities" in styles
    assert ".template-library-detail .action-bar," in styles
    assert ".smart-phrase-editor-actions" in styles
    assert "position: sticky" in styles
    assert "bottom: 0" in styles
    assert ".template-library-detail," in styles
    assert ".smart-phrase-library-detail" in styles
    assert "overflow-y: auto" in styles


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


def test_scribe_template_navigation_does_not_reintroduce_home_landing():
    workspace = read("app/templates/transcribe/_workspace.html")
    layout = read("app/static/js/transcribe/layout.js")
    assert 'href="/workspace/library/templates"' in workspace
    assert 'data-settings-url="/workspace/library/templates?scope=' in workspace
    assert "/home?tab=templates" not in layout
    assert "/settings?tab=quick-actions" not in layout
    assert "/workspace/library/templates" in layout
    assert "/workspace/library/quick-actions" in layout


def test_settings_partial_post_return_metadata_is_canonical():
    settings_dir = ROOT / "app/templates/settings"
    partials = "\n".join(path.read_text(encoding="utf-8") for path in settings_dir.glob("_*.html"))
    assert 'name="return_view" value="settings"' not in partials
