import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

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


def test_template_io_frontend_configures_shared_controller_and_keeps_template_help_local():
    script = read("app/static/js/settings/template-io.js")
    shared = read("app/static/js/settings/library-io.js")
    help_controller = read("app/static/js/settings/library-help.js")
    settings_app = read("app/static/js/settings/app.js")

    assert "initLibraryIO" in script
    assert "prefix: 'template'" in script
    for endpoint in (
        "/api/v1/templates/export",
        "/api/v1/templates/import/preflight",
        "/api/v1/templates/import",
    ):
        assert endpoint in script
    assert "initLibraryHelp(library, {" in script
    assert "prefix: 'template'" in script
    assert "csrfFetch" in shared
    assert "data.append('bundle', currentFile, currentFile.name)" in shared
    assert "data.append('selected_indexes', JSON.stringify(indexes))" in shared
    assert ".textContent =" in shared
    assert "innerHTML" not in shared
    assert "initTemplateIO();" in settings_app
    for selector in ("dialog", "open", "copy", "status", "fallback", "prompt"):
        assert f"[data-${{prefix}}-help-{selector}]" in help_controller
    assert "credentials: 'same-origin'" in help_controller
    assert "navigator.clipboard.writeText" in help_controller
    assert "promptOutput.focus()" in help_controller
    assert "promptOutput.select()" in help_controller
    assert "copy.disabled = false;" in help_controller


def test_shared_library_io_keeps_upload_paste_and_safe_rendering_contracts():
    script = read("app/static/js/settings/library-io.js")

    assert "import-destination" in script
    assert "import-file" in script
    assert "import-json" in script
    assert "import-paste-submit" in script
    assert "application/json" in script
    assert "```" in script
    assert "new File([json]" in script
    assert "JSON.parse(json)" in script
    for condition in (
        "entries.length === 1",
        "entry?.status === 'ready'",
        "entry.selectable",
        "entry.selected_by_default",
        "!(body.warnings || []).length",
        "!(entry.warnings || []).length",
    ):
        assert condition in script


def test_template_io_keeps_template_specific_paste_guidance():
    script = read("app/static/js/settings/template-io.js")

    assert "quotation marks inside the template text that have not been escaped" in script


@pytest.mark.parametrize(
    ("prefix", "module", "initializer", "endpoint", "payload_key", "prompt_text"),
    [
        ("template", "template-io.js", "initTemplateIO", "/api/v1/templates", "template_ids", "OpenScribe template bundle"),
        ("quick-action", "quick-action-io.js", "initQuickActionIO", "/api/v1/quick-actions", "quick_action_ids", "OpenScribe quick action bundle"),
    ],
)
def test_library_io_adapters_use_real_dom_controls(
    client, make_user, make_template, make_quick_action,
    prefix, module, initializer, endpoint, payload_key, prompt_text,
):
    playwright_sync = pytest.importorskip("playwright.sync_api")
    user = make_user(email="io-browser@example.com", password="password-1", team_role=TeamRole.leader)
    (make_template if prefix == "template" else make_quick_action)(owner=user, actor=user)
    client.post("/login", data={"email": user.email, "password": "password-1"})
    response = client.get(endpoint.replace("/api/v1", "/workspace/library"))
    assert response.status_code == 200
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", response.text, flags=re.DOTALL)

    def serve(route):
        path = urlsplit(route.request.url).path
        if path == "/":
            route.fulfill(body=html, content_type="text/html")
        elif path.startswith("/static/"):
            asset = ROOT / "app" / path.lstrip("/")
            route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        else:
            route.abort()

    with playwright_sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except playwright_sync.Error as exc:
            pytest.skip(f"Playwright browser unavailable: {exc}")
        try:
            page = browser.new_page()
            page.route("**/*", serve)
            page.goto("http://library.test/")
            page.evaluate(
                """async ({ prefix, module, initializer, endpoint, payloadKey, promptText }) => {
                  const check = (ok, message) => { if (!ok) throw new Error(message); };
                  const node = (name) => document.querySelector(`[data-${prefix}-${name}]`);
                  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
                  const checks = [node('export-checkbox')];
                  while (checks.length < 101) {
                    const copy = checks[0].cloneNode();
                    checks[0].parentElement.append(copy);
                    checks.push(copy);
                  }
                  checks.forEach((input) => { input.checked = true; });
                  const requests = [];
                  window.OpenScribeCSRF = { getToken: () => 'test-csrf' };
                  window.fetch = (url, options) => new Promise((resolve) => requests.push({ url, options, resolve }));
                  const reply = async (index, body) => {
                    requests[index].resolve({
                      ok: true,
                      headers: new Headers(),
                      json: async () => body,
                      blob: async () => new Blob([JSON.stringify(body)]),
                    });
                    await settle();
                  };
                  const adapter = await import(`/static/js/settings/${module}`);
                  adapter[initializer]();
                  node('export-open').click();
                  node('export-submit').click();
                  check(!requests.length, 'Export must reject more than 100 items');
                  checks.slice(1).forEach((input) => { input.checked = false; });
                  node('export-submit').click();
                  check(requests[0].url === `${endpoint}/export`, 'Export endpoint');
                  check(JSON.stringify(JSON.parse(requests[0].options.body)) === JSON.stringify({ [payloadKey]: [checks[0].value] }), 'Export payload');
                  await reply(0, {});

                  node('import-open').click();
                  const destination = (value) => document.querySelector(`[data-${prefix}-import-destination][value="${value}"]`);
                  destination('team').checked = true;
                  const upload = (name) => {
                    const transfer = new DataTransfer();
                    transfer.items.add(new File([name], name, { type: 'application/json' }));
                    node('import-file').files = transfer.files;
                    node('import-file').dispatchEvent(new Event('change'));
                  };
                  upload('old.json');
                  upload('current.json');
                  destination('personal').checked = true;
                  const clean = { entries: [{ index: 0, status: 'ready', selectable: true, selected_by_default: true }] };
                  await reply(1, clean);
                  check(requests.length === 3 && !node('import-results').children.length, 'Ignore stale preflight');
                  await reply(2, { ...clean, warnings: ['Review before importing'] });
                  check(requests.length === 3 && !node('import-preflight').hidden, 'Warnings must prevent automatic import');
                  const selected = node('import-results').querySelector('input');
                  selected.checked = false;
                  selected.dispatchEvent(new Event('change'));
                  check(node('import-confirm').disabled, 'Empty selection must disable import');
                  selected.click();
                  node('import-confirm').click();
                  node('import-confirm').dispatchEvent(new Event('click'));
                  for (const [target, type] of [[node('import-dialog'), 'cancel'], [node('import-dialog').querySelector('form'), 'submit']]) {
                    check(!target.dispatchEvent(new Event(type, { cancelable: true })), `Block ${type} during commit`);
                  }
                  check(requests.length === 4, 'Prevent duplicate commit');
                  const commit = requests[3];
                  check(commit.url === `${endpoint}/import`, 'Commit endpoint');
                  check(commit.options.body.get('selected_indexes') === '[0]', 'Selected indexes');
                  for (const index of [2, 3]) {
                    const data = requests[index].options.body;
                    check(data.get('destination') === 'team', 'Keep preflight destination snapshot');
                    check(data.get('bundle').name === 'current.json' && await data.get('bundle').text() === 'current.json', 'Reupload original file');
                  }
                  await reply(3, { summary: { imported: 1 } });
                  check(!node('import-success').hidden, 'Show import success');
                  upload('clean.json');
                  await reply(4, clean);
                  check(requests.length === 6 && requests[5].url === `${endpoint}/import`, 'Automatically commit clean single entry');
                  check(requests[5].options.body.get('destination') === 'personal', 'New preflight uses new destination');
                  await reply(5, { summary: { imported: 1 } });
                  for (const { options } of requests) {
                    check(options.method === 'POST' && options.credentials === 'include', 'POST with credentials');
                    check(options.headers.get('X-CSRF-Token') === 'test-csrf', 'Use CSRF wrapper');
                  }
                  const helpRequests = [];
                  window.fetch = (url, options) => new Promise((resolve) => helpRequests.push({ url, options, resolve }));
                  const dialog = node('help-dialog');
                  const copy = node('help-copy');
                  node('help-open').click();
                  check(dialog.open, 'Open help dialog');
                  copy.click();
                  check(helpRequests.length === 1 && helpRequests[0].options.credentials === 'same-origin', 'Fetch schema with same-origin credentials');
                  helpRequests[0].resolve({ ok: false });
                  await settle();
                  check(!copy.disabled && node('help-status').classList.contains('is-error'), 'Re-enable copy after schema failure');
                  const copied = [];
                  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text) => copied.push(text) } });
                  copy.click();
                  check(helpRequests.length === 2, 'Retry schema after failure');
                  helpRequests[1].resolve({ ok: true, json: async () => ({ type: 'object' }) });
                  await settle();
                  check(copied[0].includes(promptText), 'Use asset-specific prompt builder');
                  navigator.clipboard.writeText = async () => { throw new Error('blocked'); };
                  node('help-open').click();
                  copy.click();
                  await settle();
                  const output = node('help-prompt');
                  check(helpRequests.length === 2 && !node('help-fallback').hidden, 'Cache schema and show manual fallback');
                  check(document.activeElement === output && output.selectionStart === 0 && output.selectionEnd === output.value.length, 'Select fallback prompt');
                }""",
                {"prefix": prefix, "module": module, "initializer": initializer, "endpoint": endpoint, "payloadKey": payload_key, "promptText": prompt_text},
            )
        finally:
            browser.close()


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
    assert "schemaUrl: '/static/schemas/openscribe-template-bundle-v1.schema.json?v=20260725-section-keys'" in script
    assert "errorMessage: 'The template instructions could not be loaded.'" in script
    assert "do not add a section_label field" in script
    assert "Never invent a profile or section key" in script
    assert "section_order to consecutive integers starting at 1" in script
    assert "check the entire output with JSON.parse" in script
    assert "Never place an unescaped double quote inside a string value" in script


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

    assert 'data-template-import-success hidden' in markup
    assert 'data-lucide="party-popper"' in markup
    assert "Import complete" in markup
    assert "data-template-import-continue hidden" in markup
    shared = read("app/static/js/settings/library-io.js")
    assert "imported and ready to use." in shared
    assert "continueButton.focus()" in shared
    assert "let seconds = 5" in shared
    assert "continueButton.textContent = `Close (${seconds})`" in shared
    assert "continueButton.addEventListener('click', finishImport)" in shared
