from pathlib import Path
from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def compact_css(css: str) -> str:
    return " ".join(css.split())


def test_scribe_workspace_owns_a_bounded_viewport_and_not_document_scroll():
    """Permanent shell must not let tall Scribe content grow document viewport."""
    css = compact_css(read("app/static/css/workspace.css"))

    assert ".workspace-page--scribe { height: 100vh; height: 100dvh; overflow: hidden; }" in css
    assert (
        ".workspace-page--scribe .workspace-shell { display: flex; height: 100%; min-height: 0; overflow: hidden; }"
        in css
    )
    assert (
        ".workspace-page--scribe [data-workspace-scribe-main] { min-height: 0; overflow: hidden; }"
        in css
    )


def test_scribe_main_has_an_explicit_shell_hook_for_scroll_ownership():
    """Shell CSS needs stable hook; utility-class ordering is not layout contract."""
    workspace = read("app/templates/transcribe/_workspace.html")
    assert '<main data-workspace-scribe-main ' in workspace


def test_scribe_mobile_capture_is_recording_first_and_names_mode_switcher():
    """Phone capture must give recording visual priority over secondary actions."""
    workspace = read("app/templates/transcribe/_workspace.html")
    mobile_css = compact_css(read("app/static/css/transcribe-mobile.css"))

    assert 'class="transcribe-capture-header ' in workspace
    assert 'class="transcribe-capture-controls ' in workspace
    assert 'aria-label="Change recording mode"' in workspace
    assert 'title="Change recording mode"' in workspace
    assert 'class="transcribe-capture-duration text-sm text-slate"' in workspace
    assert 'data-mobile-capture-timer' in workspace
    assert 'data-mobile-record-mode-label' in workspace
    assert ".record-split-button { order: 0; display: grid; grid-template-areas: \"timer\" \"record\" \"wave\" \"mode\";" in mobile_css
    assert "min-height: clamp(20rem, 52dvh, 28rem);" in mobile_css
    assert "width: clamp(11rem, 52vw, 13.5rem); aspect-ratio: 1;" in mobile_css
    assert "border-radius: 50%;" in mobile_css
    assert ".btn-upload, .dictation-global-cta { order: 1; display: flex; flex-direction: column; justify-content: center; min-height: 8rem;" in mobile_css
    assert "@media (max-width: 480px) { [data-tour-target=\"record-controls\"] { grid-template-columns: 1fr; }" not in mobile_css
    assert ".transcribe-capture-header__title-row { align-items: center; gap: 0.75rem; min-width: 0; }" in mobile_css
    assert "text-overflow: ellipsis; white-space: nowrap;" in mobile_css
    assert ".transcribe-capture-status { flex: 0 0 auto; min-width: max-content; }" in mobile_css


def test_scribe_mobile_tabs_are_equal_width_without_overflow_dependency():
    workspace = read("app/templates/transcribe/_workspace.html")
    mobile_css = compact_css(read("app/static/css/transcribe-mobile.css"))

    assert 'class="transcribe-tab-bar ' in workspace
    assert 'class="transcribe-tab-list ' in workspace
    assert ".transcribe-tab-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); width: 100%; gap: 0; }" in mobile_css
    assert ".main-tab { width: 100%; min-width: 0;" in mobile_css
    assert "[data-split-workspace] > div:first-child { overflow-x: auto;" not in mobile_css


def test_scribe_mobile_recent_panel_has_one_tap_toggle_and_coordinated_layers():
    mobile_header = read("app/templates/workspace/_mobile_header.html")
    panel = read("app/templates/transcribe/_session_panel.html")
    extras = read("app/templates/transcribe/_shell_extras.html")
    workspace_js = read("app/static/js/workspace/app.js")
    transcribe_css = compact_css(read("app/static/css/transcribe.css"))
    workspace_css = compact_css(read("app/static/css/workspace.css"))

    assert "{% if active_workspace_section == 'scribe' %}" in mobile_header
    assert 'class="workspace-mobile-header__recent"' in mobile_header
    assert 'data-session-panel-toggle' in mobile_header
    assert 'aria-label="Open recent consultations"' in mobile_header
    assert 'data-session-panel-scrim' in panel
    assert "var sessionPanelToggles = document.querySelectorAll('[data-session-panel-toggle]');" in extras
    assert "sessionPanelToggles.forEach(function(toggle)" in extras
    assert "function isFocusableSessionPanelToggle(toggle)" in extras
    assert "function fallbackSessionPanelTrigger()" in extras
    assert "new CustomEvent('workspace:drawer-close')" in extras
    assert "new CustomEvent('workspace:drawer-opening')" in workspace_js
    assert "document.addEventListener('workspace:drawer-close', () => setOpen(false));" in workspace_js
    assert "sessionPanelScrim?.addEventListener('click'" in extras
    assert "function focusSessionPanelTrigger()" in extras
    assert ".session-panel-scrim:not([hidden]) { position: fixed; inset: var(--workspace-mobile-header-height, 3.5rem) 0 0;" in transcribe_css
    assert ".session-panel { position: fixed; inset: var(--workspace-mobile-header-height, 3.5rem) auto 0 0;" in transcribe_css
    assert ".workspace-shell { --workspace-mobile-header-height: 3.5rem; display: block; }" in workspace_css
    assert "function preferredSessionPanelToggle()" in workspace_js
    assert "toggle.closest('.workspace-mobile-header') && isVisibleSessionToggle(toggle)" in workspace_js


def test_scribe_mobile_recent_panel_remains_renderable_from_capture_destination():
    """Capture hides the workspace, so an open Recent view must explicitly restore its ancestor."""
    mobile_css = compact_css(read("app/static/css/transcribe-mobile.css"))

    assert '[data-workspace-scribe-main][data-mobile-destination-state="capture"] [data-split-workspace] { display: none; }' in mobile_css
    assert (
        'body.session-panel-open [data-workspace-scribe-main][data-mobile-destination-state="capture"] [data-split-workspace] '
        '{ position: fixed; z-index: 180; inset: var(--workspace-mobile-header-height, 3.5rem) 0 0; display: block; overflow: visible; }'
        in mobile_css
    )
    assert (
        'body.session-panel-open [data-workspace-scribe-main][data-mobile-destination-state="capture"] .transcribe-lower-row '
        '{ display: block; height: 100%; overflow: visible; }'
        in mobile_css
    )
    assert '.session-panel { width: 100vw; max-width: 100vw; border: 0; box-shadow: none; }' in mobile_css
    assert '.session-panel__header { position: sticky; top: 0; z-index: 2;' in mobile_css


def test_scribe_mobile_dictation_modal_owns_the_top_layer_and_safe_viewport():
    app_js = read("app/static/js/transcribe/app.js")
    transcribe_css = compact_css(read("app/static/css/transcribe.css"))

    assert "document.body.classList.toggle('modal-open', isOpen);" in app_js
    assert "new CustomEvent('openscribe:dictation-modal-opening')" in app_js
    assert "let dictationModalOpener = null;" in app_js
    assert "const isRestorableDictationFocusTarget = (element) => Boolean(" in app_js
    assert "dictationModalOpener = isRestorableDictationFocusTarget(activeElement) ? activeElement : null;" in app_js
    assert "setDictationModalOpen(false, { restoreFocus: !force });" in app_js
    assert "body.modal-open [data-workspace-scribe-main] { z-index: 200; }" in transcribe_css
    assert "padding: var(--mobile-safe-top, env(safe-area-inset-top, 0px)) 0 var(--mobile-safe-bottom, env(safe-area-inset-bottom, 0px));" in transcribe_css
    assert "height: 100%; max-height: 100%; min-height: 0;" in transcribe_css
    assert 'transcribe.css?v=20260823-modal-close' in read("app/templates/transcribe/_head_assets.html")


def test_shared_modal_close_control_centres_its_icon():
    """Both modal headers reuse this control, so it must centre block-level SVGs."""
    workspace = read("app/templates/transcribe/_workspace.html")
    transcribe_css = compact_css(read("app/static/css/transcribe.css"))

    assert workspace.count('class="template-picker-modal__close"') == 2
    assert (
        ".template-picker-modal__close { display: inline-flex; align-items: center; "
        "justify-content: center;"
    ) in transcribe_css


def test_scribe_mobile_flow_assets_have_current_cache_keys():
    workspace = read("app/templates/workspace.html")
    shell_extras = read("app/templates/transcribe/_shell_extras.html")
    legacy_transcribe = read("app/templates/transcribe.html")

    assert 'workspace/app.js?v=20260821-mobile-scribe-flow' in workspace
    assert 'transcribe/app.js?v=20260830-template-suggestion-observability' in shell_extras
    assert 'transcribe/mobile.js?v=20260823-mobile-toast' in shell_extras
    assert 'documents.js?v=20260821-mobile-production-2' in read("app/static/js/transcribe/app.js")
    assert 'transcribe-mobile.css?v=20260823-mobile-recent' in legacy_transcribe
    assert 'src="/static/js/transcribe/mobile.js' not in legacy_transcribe


def test_scribe_mobile_toasts_are_single_accessible_snackbars_above_navigation():
    workspace = read("app/templates/transcribe/_workspace.html")
    extras = read("app/templates/transcribe/_shell_extras.html")
    app_js = read("app/static/js/transcribe/app.js")
    transcribe_css = compact_css(read("app/static/css/transcribe.css"))
    mobile_css = compact_css(read("app/static/css/transcribe-mobile.css"))

    assert 'data-toast-container aria-label="Notifications"' in workspace
    assert 'id="copy-toast"' not in extras
    assert "var durations = { success: 2500, info: 4000, warning: 6000, error: 0 };" in extras
    assert "window.matchMedia('(max-width: 767px)').matches" in extras
    assert "if (isMobile && activeError && kind !== 'error') return activeError;" in extras
    assert "while (activeToasts.length >= 3)" in extras
    assert "kind === 'error' ? 'alert' : 'status'" in extras
    assert "kind === 'error' ? 'assertive' : 'polite'" in extras
    assert "close.setAttribute('aria-label', 'Dismiss notification');" in extras
    assert "window.showToast?.('Copied', 'success', 2000);" in app_js
    assert ".copy-toast" not in transcribe_css
    assert "bottom: calc(5.5rem + var(--mobile-safe-bottom));" in mobile_css
    assert "body.mobile-recording-strip-visible .toast-container { bottom: calc(10rem + var(--mobile-safe-bottom)); }" in mobile_css
    assert "top: calc(var(--workspace-mobile-header-height, 3.5rem) + 0.75rem + var(--mobile-safe-top));" in mobile_css
    assert "body.modal-open .toast-container { top: calc(4.75rem + var(--mobile-safe-top)); bottom: auto; z-index: 240; }" in mobile_css
    assert "document.body.classList.toggle('mobile-recording-strip-visible', showRecordingStrip);" in read("app/static/js/transcribe/mobile.js")
    assert "-webkit-line-clamp: 2;" in mobile_css
    assert "@media (prefers-reduced-motion: reduce)" in transcribe_css


def test_scribe_mobile_uses_the_canonical_workspace_for_four_destinations_and_sheets():
    workspace = read("app/templates/transcribe/_workspace.html")
    mobile_css = compact_css(read("app/static/css/transcribe-mobile.css"))
    mobile_js = read("app/static/js/transcribe/mobile.js")

    assert 'data-mobile-capture-screen' in workspace
    assert 'data-mobile-destination="capture"' in workspace
    assert 'data-mobile-destination="history"' in workspace
    assert 'data-mobile-destination="output"' in workspace
    assert 'data-mobile-destination="followups"' in workspace
    assert 'data-mobile-note-versions' in workspace
    assert 'data-mobile-note-selector-sheet' in workspace
    assert 'data-mobile-pii-open' in workspace
    assert 'data-mobile-dictation-open' in workspace
    assert 'data-mobile-recording-strip' in workspace
    assert "data-mobile-destination-state" in mobile_js
    assert "openscribe:recording-started" in mobile_js
    assert "transcribe:document-selected" in mobile_js
    assert "100dvh" in mobile_css
    assert ".mobile-scribe-destinations { position: fixed;" in mobile_css
    assert "[data-mobile-note-selector-sheet] { display: none; }" in mobile_css
    assert ".workspace-page--scribe .operator-legal-footer { display: none; }" in mobile_css


def test_mobile_destination_nav_is_a_direct_scribe_main_child_not_hidden_with_capture_workspace():
    """Capture hides the split workspace, so its persistent nav must sit outside it."""
    workspace = read("app/templates/transcribe/_workspace.html")

    class ParentCapture(HTMLParser):
        void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

        def __init__(self):
            super().__init__()
            self.stack = []
            self.destination_parent = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "nav" and "data-mobile-destinations" in attributes:
                self.destination_parent = self.stack[-1] if self.stack else None
            if tag not in self.void_tags:
                self.stack.append((tag, attributes))

        def handle_endtag(self, tag):
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

    parser = ParentCapture()
    parser.feed(workspace)

    assert parser.destination_parent is not None
    tag, attributes = parser.destination_parent
    assert tag == "main"
    assert "data-workspace-scribe-main" in attributes


def test_mobile_sheet_controller_traps_focus_inerts_background_and_keeps_navigation_available_during_recording():
    mobile_js = read("app/static/js/transcribe/mobile.js")

    assert "element.inert = true" in mobile_js
    assert "element.inert = inert" in mobile_js
    assert "sheet.setAttribute('aria-modal', 'true')" in mobile_js
    assert "if (event.key !== 'Tab') return;" in mobile_js
    assert "if (recording && destination === 'capture'" not in mobile_js


def test_opening_full_dictation_editor_releases_the_mobile_sheet_trap():
    mobile_js = read("app/static/js/transcribe/mobile.js")

    assert "document.addEventListener('openscribe:dictation-modal-opening'" in mobile_js
    assert "closeSheets({ restoreFocus: false });" in mobile_js


def test_ready_document_mode_hides_the_other_note_editor_before_hydration():
    workspace = read("app/templates/transcribe/_workspace.html")
    structured = read("app/static/js/transcribe/structured.js")

    assert 'latest_generated_document.status.value == "ready" and latest_generated_document.document_mode.value == "freeform"' in workspace
    assert 'latest_generated_document.status.value == "ready" and latest_generated_document.document_mode.value == "structured"' in workspace
    assert "generatedStatus === 'ready' && generatedMode === 'freeform'" in structured
    assert "generatedStatus === 'ready' && generatedMode === 'structured'" in structured


def test_320px_note_header_uses_a_short_copy_label_without_reducing_target_size():
    workspace = read("app/templates/transcribe/_workspace.html")
    mobile_css = compact_css(read("app/static/css/transcribe-mobile.css"))

    assert 'class="note-copy-label-full">Copy selected</span>' in workspace
    assert 'class="mobile-note-copy-label">Copy</span>' in workspace
    assert 'aria-label="Copy selected note lines"' in workspace
    assert "@media (max-width: 360px) { #workspace-panel-output .note-header-row, #workspace-panel-output .note-header-left { gap: .5rem; }" in mobile_css
    assert "#workspace-panel-output [data-copy-structured-lines] { min-height: 44px;" in mobile_css


def test_note_selection_announces_after_existing_selection_flow_completes():
    documents = read("app/static/js/transcribe/documents.js")
    mobile_js = read("app/static/js/transcribe/mobile.js")

    assert "persistNoteEditsSilently" in documents
    assert "setState({ selectedNoteDocumentId: documentId });" in documents
    assert "new window.CustomEvent('transcribe:document-selected'" in documents
    assert "if (event.detail?.kind === 'note') closeSheets();" in mobile_js


def test_scribe_title_reset_is_scoped_and_settings_assets_stay_section_only():
    """Title reset must not flatten labels, inputs, or buttons in settings forms."""
    css = compact_css(read("app/static/css/workspace.css"))
    shell = read("app/templates/workspace.html")

    assert (
        ".workspace-page--scribe [data-transcript-title-input] { appearance: none; "
        "background: transparent; }"
        in css
    )
    assert ".workspace-section--settings input" not in css
    assert ".workspace-section--settings button" not in css
    assert "{% if active_workspace_section == 'scribe' %}" in shell
    assert "{% else %}<link rel=\"stylesheet\" href=\"/static/css/settings.css" in shell


def test_account_form_keeps_settings_specific_structure_and_classes():
    """Regression guard: layout fix must not replace established settings form styling."""
    account = read("app/templates/settings/_account.html")
    settings_css = compact_css(read("app/static/css/settings.css"))

    assert 'class="account-settings-form"' in account
    assert ".account-settings-form { display: grid;" in settings_css
    assert ".account-settings-form label { display: grid;" in settings_css
    assert ".account-settings-form button { justify-self: start; align-self: end; }" in settings_css
