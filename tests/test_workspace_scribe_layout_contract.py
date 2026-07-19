from pathlib import Path


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
