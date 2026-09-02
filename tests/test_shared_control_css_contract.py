from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_shared_controls_provide_icon_button_status_badge_and_switch_primitives():
    css = read("app/static/css/components.css")

    for selector in (
        ".btn-icon {",
        ".status-badge {",
        ".status-badge--ready",
        ".status-badge--failed",
        ".status-badge--neutral",
        ".switch_large_dynamic {",
        "body:not(.h-screen) label.switch_large_dynamic { display: inline-flex;",
        '.switch_large_dynamic__track',
        ".switch-compact {",
        "body:not(.h-screen) label.switch-compact { display: inline-flex;",
        '.switch-compact__track',
        '.field-label { display: block;',
        '.select-wrap { position: relative; }',
        '.select-wrap::after {',
        'body:not(.h-screen) .select-wrap select',
    ):
        assert selector in css


def test_shared_control_stylesheet_revision_reaches_settings_and_workspace():
    for relative in ("app/templates/settings.html", "app/templates/workspace.html", "app/templates/transcribe/_head_assets.html"):
        assert 'components.css?v=20260902-shared-fields' in read(relative)


def test_preferences_use_the_shared_compact_switch():
    markup = read("app/templates/settings/_preferences.html")

    assert 'class="switch-compact"' in markup
    assert 'class="switch-compact__track" aria-hidden="true"' in markup


def test_scribe_header_keeps_its_existing_visual_status_pill():
    markup = read("app/templates/transcribe/_workspace.html")
    transcribe_css = read("app/static/css/transcribe.css")

    assert 'class="flex items-center gap-2 px-3 py-1.5 bg-teal-pale rounded-full" data-active-status-pill' in markup
    assert 'class="flex items-center gap-2 px-3 py-1.5 bg-coral/15 rounded-full" data-active-status-pill' in markup
    assert 'class="flex items-center gap-2 px-3 py-1.5 bg-white border border-stone rounded-full" data-active-status-pill' in markup
    assert "\n.btn-icon {\ndisplay: inline-flex;" not in transcribe_css


def test_shared_select_blurs_after_a_new_value_is_chosen():
    script = read("app/static/js/settings/controls.js")

    assert "control instanceof HTMLSelectElement" in script
    assert "control.closest('.select-wrap')" in script
    assert "control.blur();" in script

    for relative in ("app/templates/settings.html", "app/templates/workspace.html"):
        assert 'settings/controls.js?v=20260902-account-dialogs-4' in read(relative)


def test_writing_style_save_control_becomes_active_only_for_unsaved_changes():
    script = read("app/static/js/settings/controls.js")
    markup = read("app/templates/settings/_preferences.html")

    assert "[data-writing-style-form]" in script
    assert "button.disabled = !hasChanges;" in script
    assert "hasChanges ? 'Unsaved changes' : ''" in script
    assert "No unsaved changes" not in script
    assert 'data-writing-style-form' in markup
    assert 'data-save-writing-style disabled' in markup


def test_control_sized_button_uses_the_shared_select_height():
    tokens = read("app/static/css/tokens.css")
    css = read("app/static/css/components.css")
    markup = read("app/templates/settings/_preferences.html")

    assert "--control-height: 44px;" in tokens
    assert "height: var(--control-height);" in css
    assert ".btn-control { min-height: var(--control-height); height: var(--control-height); }" in css
    assert 'class="btn-primary-sm btn-control"' in markup


def test_account_and_writing_assistant_use_shared_field_controls():
    css = read("app/static/css/components.css")
    preferences = read("app/templates/settings/_preferences.html")
    account = read("app/templates/settings/_account.html")

    assert ".field-input," in css
    assert "body:not(.h-screen) .field-input" in css
    assert 'data-model-preference-form' in preferences
    assert 'data-model-save-state aria-live="polite"' in preferences
    assert 'data-save-model disabled>Save model</button>' in preferences
    assert 'class="field-input"' in account
    assert 'class="btn-primary-sm btn-control" type="submit" data-account-name-save disabled>Save name</button>' in account


def test_model_preference_save_uses_the_shared_dirty_state_controller():
    script = read("app/static/js/settings/controls.js")

    assert "[data-model-preference-form]" in script
    assert "[data-save-model]" in script
    assert "[data-model-save-state]" in script

    styles = read("app/static/css/settings.css")
    assert '.settings-card > details form[data-model-preference-form] { display: flex; align-items: flex-end;' in styles
    assert '.setting-row--form form[data-writing-style-form] { flex: 0 1 auto; grid-template-columns: repeat(2,minmax(0,220px)) auto;' in styles
    assert '.setting-row--form form[data-writing-style-form] .setting-control { width: 220px; }' in styles
    assert '.account-setting-row { display: grid; grid-template-columns: minmax(0,360px) minmax(300px,420px); justify-content: start;' in styles


def test_account_controls_use_native_dialogs_and_never_snapshot_csrf_tokens():
    markup = read("app/templates/settings/_account.html")
    script = read("app/static/js/settings/controls.js")
    styles = read("app/static/css/settings.css")

    assert '<dialog class="account-dialog" id="account-email-dialog"' in markup
    assert 'data-account-modal-open="email"' in markup
    assert 'data-account-sensitive-form' in markup
    assert 'data-account-oidc-form' in markup
    assert 'data-dirty-guard' in markup
    assert 'aria-haspopup="dialog"' in markup
    assert "data.delete('_csrf_token');" in script
    assert 'dialog.showModal();' in script
    assert 'dialog.querySelectorAll(\'form\').forEach((form) => form.reset());' in script
    assert "button.disabled = !hasChanges;" in script
    assert 'form.checkValidity()' in script
    assert 'newPassword.value === confirmation.value' in script
    assert 'if (submit.disabled) return;' in script
    assert 'submit.disabled = true;' in script
    assert 'Opening ${form.dataset.providerName}…' in script
    assert 'Disconnecting ${form.dataset.providerName}…' in script
    assert 'data-account-modal-auto-open' in script
    assert 'trigger?.isConnected) trigger.focus();' in script
    assert "[data-account-password-status]" in script
    for guidance in (
        'Enter your current password.',
        'Enter a new password.',
        'Choose a different new password.',
        'Use at least 12 characters.',
        'Add an uppercase letter.',
        'Add a lowercase letter.',
        'Add a number.',
        'Confirm your new password.',
        'New passwords do not match.',
        'Enter your authenticator code.',
        'Ready to change password.',
    ):
        assert guidance in script
    assert 'newPassword.value === currentPassword.value' in script
    assert 'mfaCode.required && !mfaCode.value.trim()' in script
    assert "guidance === 'Ready to change password.'" in script
    assert 'data-account-password-status aria-live="polite">Enter your current password.</p>' in markup
    assert '.account-dialog::backdrop' in styles
    assert '@media (max-width: 800px) { .account-panel__header' in styles
