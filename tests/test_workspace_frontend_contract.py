import subprocess
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
    assert "data-recording-navigation" in sidebar
    assert 'action="/logout"' in sidebar


def test_workspace_js_uses_session_memory_recording_events_and_native_history():
    script = read("app/static/js/workspace/app.js")
    transcribe_script = read("app/static/js/transcribe/app.js")
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
    assert "recordingNavigationStates.has(element)" in script
    assert "openscribe:recording-navigation-added" in script
    assert "link.dataset.recordingNavigation = '';" in transcribe_script
    assert "new CustomEvent('openscribe:recording-navigation-added', { detail: { element: link } })" in transcribe_script


def test_workspace_recording_lock_applies_to_late_session_links_and_restores_them(tmp_path):
    root = Path(__file__).resolve().parents[1]
    runner = tmp_path / "workspace_recording_lock_runner.cjs"
    runner.write_text(
        """
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        class Element {
          constructor() {
            this.attributes = new Map();
            this.dataset = {};
            this.disabled = false;
            this.classList = {
              values: new Set(),
              add: (value) => this.classList.values.add(value),
              remove: (value) => this.classList.values.delete(value),
              toggle: (value, enabled) => enabled ? this.classList.values.add(value) : this.classList.values.delete(value),
            };
          }
          get title() { return this.getAttribute('title') || ''; }
          set title(value) { this.setAttribute('title', value); }
          getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
          setAttribute(name, value) { this.attributes.set(name, String(value)); }
          removeAttribute(name) { this.attributes.delete(name); }
          matches(selector) { return selector === '[data-recording-navigation]' && this.dataset.recordingNavigation !== undefined; }
          closest() { return null; }
          getClientRects() { return [{}]; }
        }
        class HTMLAnchorElement extends Element {}
        class HTMLButtonElement extends Element {}
        const handlers = new Map();
        const navigation = [];
        const document = {
          body: { dataset: {} },
          querySelector: () => null,
          querySelectorAll: (selector) => selector === '[data-recording-navigation]' ? navigation : [],
          addEventListener: (name, handler) => handlers.set(name, handler),
        };
        const window = {
          addEventListener() {}, removeEventListener() {}, requestAnimationFrame(callback) { callback(); },
          matchMedia() { return { matches: false }; }, sessionStorage: { getItem() { return ''; }, setItem() {} },
          location: { href: 'https://example.test/workspace' }, history: { replaceState() {} }, lucide: null,
        };
        const source = fs.readFileSync(__SOURCE_PATH__, 'utf8')
          .replace(/export \\{[^}]+\\};/, '');
        const sandbox = { document, window, URL, HTMLAnchorElement, HTMLButtonElement, WeakMap };
        vm.createContext(sandbox);
        vm.runInContext(source, sandbox, { filename: __SOURCE_PATH__ });

        const lateLink = new HTMLAnchorElement();
        lateLink.dataset.recordingNavigation = '';
        lateLink.setAttribute('title', 'Open consultation');
        lateLink.setAttribute('tabindex', '3');
        lateLink.setAttribute('aria-disabled', 'false');

        sandbox.setRecordingLock(true);
        handlers.get('openscribe:recording-navigation-added')({ detail: { element: lateLink } });
        navigation.push(lateLink);
        assert.equal(lateLink.title, 'Finish or cancel the recording before leaving Scribe.');
        assert.equal(lateLink.getAttribute('aria-disabled'), 'true');
        assert.equal(lateLink.getAttribute('tabindex'), '-1');
        assert.equal(lateLink.classList.values.has('workspace-navigation-disabled'), true);

        sandbox.setRecordingLock(true);
        sandbox.setRecordingLock(false);
        assert.equal(lateLink.title, 'Open consultation');
        assert.equal(lateLink.getAttribute('aria-disabled'), 'false');
        assert.equal(lateLink.getAttribute('tabindex'), '3');
        assert.equal(lateLink.classList.values.has('workspace-navigation-disabled'), false);
        """.replace("__SOURCE_PATH__", repr(str(root / "app/static/js/workspace/app.js"))),
        encoding="utf-8",
    )

    subprocess.run(["node", str(runner)], check=True, cwd=root)


def test_media_controller_emits_authoritative_workspace_recording_events():
    media = read("app/static/js/transcribe/media.js")
    assert "dispatchWorkspaceRecordingEvent('started')" in media
    assert "dispatchWorkspaceRecordingEvent('stopped')" in media
    assert "dispatchWorkspaceRecordingEvent('failed')" in media
    assert "openscribe:recording-${state}" in media


def test_capture_upload_recovery_keeps_audio_only_in_the_current_tab():
    media = read("app/static/js/transcribe/media.js")
    actions = read("app/static/js/transcribe/actions.js")
    workspace = read("app/templates/transcribe/_workspace.html")

    assert "data-pending-audio-retry" in workspace
    assert "data-pending-audio-discard" in workspace
    assert "Audio is still available in this tab." in media
    assert "Audio is still available in this tab." in actions
    assert "const maxAttempts = canReplay ? 3 : 1;" in media
    assert "attempt <= maxAttempts" in actions
    assert "Idempotency-Key" in media and "Idempotency-Key" in actions
    assert "crypto?.getRandomValues" in media and "crypto?.getRandomValues" in actions
    assert "canReplay: Boolean(idempotencyKey)" in media
    assert "const maxAttempts = idempotencyKey ? 3 : 1;" in actions
    assert "transcribe:active-transcript-changed" in media and "transcribe:active-transcript-changed" in actions
    assert "indexedDB" not in media.lower() and "indexedDB" not in actions.lower()
    assert "verifyAccepted" not in media
    assert "response.status === 409 &&" not in media
    assert "retryConflict && response.status === 409" in media
    assert "conflictRetryDelayMs: Number(config.batchRolloverConflictRetryMs || 5000)" in media
    assert "Previous recording part is still transcribing. Retrying this audio part automatically" in media
    assert "_openscribePendingAudioRetry" in media and "_openscribePendingAudioRetry" in actions
    assert "Retry the pending audio upload before starting another recording." in media
    assert "Discard this pending audio? It cannot be recovered after you discard it." in media
    assert "Discard this pending audio? It cannot be recovered after you discard it." in actions


def test_settings_module_initializers_are_target_scoped():
    script = read("app/static/js/settings/app.js")
    for marker in ("[data-confirm-submit]", "[data-service-toggle]", "[data-stt-selection-form]", "[data-llm-selection-form]", "[data-dirty-guard]"):
        assert marker in script
    assert "data-settings-menu" not in script


def test_workspace_settings_controls_auto_submit_personal_preference_toggles_once():
    controls = read("app/static/js/settings/controls.js")
    legacy_settings = read("app/templates/settings.html")

    assert "[data-template-suggestion-preference], [data-consultation-splitting-preference]" in controls
    assert "event.target.form?.requestSubmit()" in controls
    assert "data-template-suggestion-preference]')?.addEventListener" not in legacy_settings
    assert "data-consultation-splitting-preference" not in legacy_settings


def test_team_member_actions_use_one_top_layer_menu_and_explicit_dialogs():
    markup = read("app/templates/settings/_team_members.html")
    script = read("app/static/js/settings/member-menu.js")
    styles = read("app/static/css/settings.css")

    assert 'data-member-menu-trigger' in markup
    assert 'aria-haspopup="menu"' in markup
    assert 'popover="manual"' in markup
    assert 'role="menu"' in markup
    assert 'data-member-action-dialog' in markup
    assert 'data-member-dialog-action="reset-mfa"' in markup
    assert 'data-member-dialog-action="delete"' in markup
    assert '<details class="member-menu">' not in markup

    assert "showPopover()" in script
    assert "hidePopover()" in script
    assert "fallbackOpen" in script
    assert "activeMenu" in script
    assert "event.key === 'Escape'" in script
    assert "event.key === 'ArrowDown'" in script
    assert "window.addEventListener('resize'" in script
    assert "window.addEventListener('scroll'" in script

    assert ".member-menu__panel:popover-open" in styles
    assert ".member-menu__panel[data-fallback-open]" in styles
    assert "position: fixed" in styles
    assert ".member-action-dialog::backdrop" in styles


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
    sidebar = read("app/templates/workspace/_sidebar.html")
    layout = read("app/static/js/transcribe/layout.js")
    assert 'href="/workspace/library/templates"' in sidebar
    assert "data-workspace-settings-link" not in workspace
    assert 'data-settings-url="/workspace/library/templates?scope=' in workspace
    assert "/home?tab=templates" not in layout
    assert "/settings?tab=quick-actions" not in layout


def test_settings_partial_post_return_metadata_is_canonical():
    settings_dir = ROOT / "app/templates/settings"
    partials = "\n".join(path.read_text(encoding="utf-8") for path in settings_dir.glob("_*.html"))
    assert 'name="return_view" value="settings"' not in partials
