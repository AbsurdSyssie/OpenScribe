from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def compact(value: str) -> str:
    return " ".join(value.split())


def test_sidebar_labels_do_not_depend_on_transcribe_tailwind_sr_only():
    """Non-Scribe pages do not load Tailwind; accessible labels must not duplicate visibly."""
    sidebar = read("app/templates/workspace/_sidebar.html")

    assert '<span class="sr-only">' not in sidebar
    for label in (
        "Account",
        "Preferences",
        "My Templates",
        "My quick actions",
        "Smart phrases",
        "AI services",
        "Team members",
        "Account requests",
        "Sign out",
    ):
        assert f'aria-label="{label}"' in sidebar
        assert sidebar.count(f">{label}</span>") == 1


def test_sidebar_section_lucide_icons_have_shared_explicit_size():
    """Section icons should match 24px Create/Recent controls without utility CSS."""
    sidebar = read("app/templates/workspace/_sidebar.html")
    css = compact(read("app/static/css/workspace.css"))

    for icon in (
        "user-round",
        "sliders-horizontal",
        "files",
        "zap",
        "text-cursor-input",
        "bot",
        "users",
        "user-round-plus",
        "log-out",
    ):
        assert f'class="workspace-nav__icon" data-lucide="{icon}"' in sidebar
    assert (
        ".workspace-nav__icon { width: 1.5rem; height: 1.5rem; flex: 0 0 1.5rem; }"
        in css
    )


def test_collapse_control_lives_in_account_row_with_both_state_icons():
    sidebar = read("app/templates/workspace/_sidebar.html")
    css = compact(read("app/static/css/workspace.css"))
    brand_row = sidebar.split('<div class="workspace-sidebar__brand-row">', 1)[1].split("</div>", 1)[0]
    account_row = sidebar.split('<div class="workspace-sidebar__account"', 1)[1].split(
        '<div class="workspace-sidebar__top">', 1
    )[0]

    assert "data-sidebar-collapse-toggle" not in brand_row
    assert "data-sidebar-collapse-toggle" in account_row
    assert 'data-sidebar-collapse-icon-expanded data-lucide="panel-left-close"' in account_row
    assert 'data-sidebar-collapse-icon-collapsed data-lucide="panel-left-open"' in account_row
    assert ".workspace-sidebar__collapse { display: inline-flex;" in css
    assert ".workspace-sidebar--collapsed .workspace-sidebar__collapse { display: inline-flex;" in css


def test_non_scribe_section_uses_available_main_width():
    """Settings/library content should not inherit centered 76rem marketing-page gutter."""
    css = compact(read("app/static/css/workspace.css"))
    assert (
        ".workspace-section--settings { width: 100%; max-width: none; margin: 0; }"
        in css
    )
    assert ".workspace-main .workspace-section--settings { max-width: none; }" in css


def test_library_selection_rails_abut_workspace_sidebar_without_main_gutter():
    """All My Library split views must opt out of generic settings-page padding."""
    workspace = compact(read("app/templates/workspace.html"))
    css = compact(read("app/static/css/workspace.css"))

    assert (
        "{% if active_workspace_section in ['templates', 'quick-actions', 'smart-phrases'] %} "
        "workspace-page--library{% endif %}"
    ) in workspace
    assert ".workspace-page--library .workspace-main { padding: 0; }" in css
