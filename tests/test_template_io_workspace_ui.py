from pathlib import Path

from app.models import TeamRole, TemplateScope


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_template_library_renders_export_mode_and_scope_authorized_import_destination(
    client,
    make_team,
    make_user,
    make_template,
):
    team = make_team(name="Template portability")
    member = make_user(email="portable-member@example.com", password="password-1", team=team, team_role=TeamRole.user)
    leader = make_user(email="portable-leader@example.com", password="password-2", team=team, team_role=TeamRole.leader)
    personal = make_template(owner=member, actor=member, name="Portable personal")
    shared = make_template(scope=TemplateScope.team, team=team, actor=leader, name="Portable team")

    client.post("/login", data={"email": member.email, "password": "password-1"}, follow_redirects=False)
    member_page = client.get("/workspace/library/templates")
    assert member_page.status_code == 200
    assert member_page.text.count("data-template-export-checkbox") == 2
    assert f'value="{personal.id}" data-template-export-checkbox' in member_page.text
    assert f'value="{shared.id}" data-template-export-checkbox' in member_page.text
    assert (
        'name="template-import-destination" value="personal" checked '
        "data-template-import-destination"
    ) in member_page.text
    assert (
        'name="template-import-destination" value="team" '
        "data-template-import-destination"
    ) not in member_page.text
    assert member_page.text.count("data-template-import-dropzone") == 1
    assert "data-template-import-file" in member_page.text
    assert "data-template-import-json" in member_page.text
    assert "data-template-import-paste-submit" in member_page.text
    assert 'data-template-import-confirm disabled' in member_page.text

    client.post("/logout", follow_redirects=False)
    client.post("/login", data={"email": leader.email, "password": "password-2"}, follow_redirects=False)
    leader_page = client.get("/workspace/library/templates")
    assert leader_page.status_code == 200
    assert (
        'name="template-import-destination" value="personal" checked '
        "data-template-import-destination"
    ) in leader_page.text
    assert (
        'name="template-import-destination" value="team" '
        "data-template-import-destination"
    ) in leader_page.text
    assert leader_page.text.count("data-template-import-dropzone") == 1

    schema = client.get("/static/schemas/openscribe-template-bundle-v1.schema.json")
    assert schema.status_code == 200
    assert schema.json()["properties"]["format"]["const"] == "openscribe-template-bundle"


def test_template_io_frontend_uses_csrf_fetch_raw_file_reupload_and_safe_rendering():
    script = read("app/static/js/settings/template-io.js")
    settings_app = read("app/static/js/settings/app.js")

    assert "document.querySelector('[data-template-import-dialog]')" in script
    assert "document.querySelector('[data-template-help-dialog]')" in script
    for endpoint in (
        "/api/v1/templates/export",
        "/api/v1/templates/import/preflight",
        "/api/v1/templates/import",
    ):
        assert endpoint in script
    assert "csrfFetch" in script
    assert "data.append('bundle', currentFile, currentFile.name)" in script
    assert "data.append('selected_indexes', JSON.stringify(indexes))" in script
    assert ".textContent =" in script
    assert "innerHTML" not in script
    assert "initTemplateIO();" in settings_app


def test_template_io_frontend_supports_shared_upload_paste_and_clean_single_template_fast_path():
    script = read("app/static/js/settings/template-io.js")

    assert "[data-template-import-destination]:checked" in script
    assert "[data-template-import-file]" in script
    assert "[data-template-import-json]" in script
    assert "[data-template-import-paste-submit]" in script
    assert "application/json" in script
    assert "```" in script
    assert "new File([json]" in script
    assert "JSON.parse(json)" in script
    assert "quotation marks inside the template text that have not been escaped" in script
    assert "const isCleanSingleTemplate = (body)" in script
    for clean_condition in (
        "entries.length === 1",
        "entry?.status === 'ready'",
        "entry.selectable",
        "entry.selected_by_default",
        "!(body.warnings || []).length",
        "!(entry.warnings || []).length",
    ):
        assert clean_condition in script
    assert "if (isCleanSingleTemplate(body))" in script
    assert "await importCurrent([body.entries[0].index]);" in script
    assert "importCurrent(selectedIndexes())" in script


def test_template_io_help_exposes_ai_prompt_copy_and_manual_fallback_hooks():
    markup = read("app/templates/settings/_template_library.html")
    script = read("app/static/js/settings/template-io.js")

    assert "Create a template with AI" in markup
    assert "Templates tell OpenScribe how to organise and write your finished note." in markup
    assert "You do not need to write any code." in markup
    assert "<strong>Import</strong> adds a template" in markup
    assert "<strong>Export</strong> saves selected templates" in markup
    assert "Copy instructions for AI" in markup
    assert "you do not need to understand or edit it" in markup
    assert "Return to OpenScribe" in markup
    assert "Do not include patient information" in markup
    for hook in (
        "data-template-help-open",
        "data-template-help-dialog",
        "data-template-help-copy",
        "data-template-help-status",
        "data-template-help-fallback",
        "data-template-help-prompt",
    ):
        assert hook in markup
        assert f"[{hook}]" in script
    assert "navigator.clipboard.writeText" in script
    assert "Instructions copied" in script
    assert "problem → Problem" in script
    assert "social_history → Social history" in script
    assert "section_order to consecutive integers starting at 1" in script
    assert "check the entire output with JSON.parse" in script
    assert "Never place an unescaped double quote inside a string value" in script
    assert ".select()" in script


def test_template_io_controls_are_bottom_rail_accessible_and_not_inline_scripted():
    markup = read("app/templates/settings/_template_library.html")
    css = read("app/static/css/settings.css")

    assert 'class="template-library-utilities" aria-label="Template import and export"' in markup
    assert 'aria-label="Close import dialog"' in markup
    assert 'aria-label="Help with importing, exporting, and creating templates"' in markup
    assert 'href="/static/schemas/openscribe-template-bundle-v1.schema.json"' in markup
    assert 'role="status" aria-live="polite"' in markup
    assert "onclick=" not in markup
    assert ".template-library-groups { min-height: 0; flex: 1; overflow-y: auto;" in css
    assert ".template-library-utilities { flex: 0 0 auto;" in css


def test_template_import_shows_success_state_before_library_refresh():
    markup = read("app/templates/settings/_template_library.html")
    script = read("app/static/js/settings/template-io.js")

    assert 'data-template-import-success hidden' in markup
    assert 'data-lucide="party-popper"' in markup
    assert "Import complete" in markup
    assert "data-template-import-continue hidden" in markup
    assert "imported and ready to use." in script
    assert "continueButton.focus()" in script
    assert "let seconds = 5" in script
    assert "continueButton.textContent = `Close (${seconds})`" in script
    assert "continueButton.addEventListener('click', finishImport)" in script
