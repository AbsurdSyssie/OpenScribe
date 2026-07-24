from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.web.templates import templates


PARTIAL = Path("app/templates/settings/_smart_phrase_library.html").read_text()
SCRIPT = Path("app/static/js/settings/smart-phrases.js").read_text()
STYLES = Path("app/static/css/settings-smart-phrases.css").read_text()


def _phrase(**overrides):
    values = {
        "id": uuid4(),
        "trigger": "CESRF",
        "expansion_text": "Check examination and safety net.",
        "description": "Consultation reminder",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _render_partial(phrases, selected_id=""):
    template = templates.env.get_template("settings/_smart_phrase_library.html")
    return template.render(
        request=SimpleNamespace(query_params={"smart_phrase_id": selected_id}),
        settings_tab="smart-phrases",
        personal_smart_phrases=phrases,
    )


def test_smart_phrase_settings_uses_master_detail_page_not_drawer():
    assert 'aria-label="Smart phrase library"' in PARTIAL
    assert 'id="personal-smart-phrase-heading">Personal</h3>' in PARTIAL
    assert 'class="smart-phrase-library-detail"' in PARTIAL
    assert 'class="smart-phrase-library-back" href="/workspace/library/smart-phrases"' in PARTIAL
    assert "smart-phrase-drawer" not in PARTIAL
    assert ".smart-phrase-library-shell.has-selection .smart-phrase-library-sidebar" in STYLES
    assert ".smart-phrase-library-shell.has-selection .smart-phrase-library-detail" in STYLES


def test_smart_phrase_selection_is_canonical_and_owner_list_resolved():
    assert "request.query_params.get('smart_phrase_id', '')" in PARTIAL
    assert "phrase.id|string == requested_smart_phrase_id" in PARTIAL
    assert "/workspace/library/smart-phrases?smart_phrase_id=new" in PARTIAL
    assert "personal_smart_phrases|sort(attribute='trigger')" in PARTIAL
    assert "team_smart_phrases" not in PARTIAL
    assert "smart_phrase_id" in SCRIPT
    assert "new URLSearchParams({ tab: 'smart-phrases' })" in SCRIPT


def test_smart_phrase_editor_reuses_csrf_protected_owner_api_and_preserves_errors():
    assert "import { csrfFetch } from '../csrf.js';" in SCRIPT
    assert "'/api/v1/smart-phrases/personal'" in SCRIPT
    assert "`/api/v1/smart-phrases/personal/${id}`" in SCRIPT
    assert "method: id ? 'PATCH' : 'POST'" in SCRIPT
    assert "method: 'DELETE'" in SCRIPT
    assert "formPayload(form)" in SCRIPT
    assert "showError(error instanceof Error ? error.message" in SCRIPT
    assert "showLibraryError(error instanceof Error ? error.message" in SCRIPT
    assert "window.showToast" not in SCRIPT
    assert "beforeunload" in SCRIPT
    assert "confirmDiscardIfDirty" in SCRIPT
    assert "Discard unsaved smart phrase changes?" in SCRIPT
    assert "window.location.assign" not in SCRIPT.split("catch (error)", 1)[1].split("finally", 1)[0]
    assert 'name="trigger"' in PARTIAL
    assert 'name="expansion_text"' in PARTIAL
    assert 'name="description"' in PARTIAL
    assert 'pattern="[A-Z0-9_]+"' in PARTIAL


def test_smart_phrase_rows_offer_edit_duplicate_and_hard_delete_actions():
    assert 'aria-label="Edit {{ phrase.trigger }}"' in PARTIAL
    assert "data-smart-phrase-duplicate-row" in PARTIAL
    assert "data-smart-phrase-delete-row" in PARTIAL
    assert "Delete /${record.trigger} permanently?" in SCRIPT
    assert "Delete this smart phrase permanently?" in SCRIPT
    assert "nextCopyTrigger" in SCRIPT


def test_smart_phrase_partial_opens_only_phrase_from_owner_filtered_list():
    phrase = _phrase()

    selected = _render_partial([phrase], str(phrase.id))
    inaccessible = _render_partial([phrase], str(uuid4()))

    assert "smart-phrase-library-shell has-selection" in selected
    assert 'aria-current="page"' in selected
    assert 'value="CESRF"' in selected
    assert "Check examination and safety net." in selected
    assert "smart-phrase-library-shell has-selection" not in inaccessible
    assert "Select a smart phrase" in inaccessible


def test_smart_phrase_partial_opens_blank_creator_for_canonical_new_selection():
    rendered = _render_partial([], "new")

    assert "smart-phrase-library-shell has-selection" in rendered
    assert "New smart phrase" in rendered
    assert 'data-smart-phrase-id=""' in rendered
    assert "smart-phrase-drawer" not in rendered


def test_smart_phrase_library_renders_personal_portability_controls():
    rendered = _render_partial([_phrase()])

    assert 'class="smart-phrase-library-utilities" aria-label="Smart phrase import and export"' in rendered
    assert 'data-smart-phrase-export-checkbox' in rendered
    assert 'data-smart-phrase-import-dialog' in rendered
    assert 'data-smart-phrase-import-file' in rendered
    assert 'data-smart-phrase-import-json' in rendered
    assert 'data-smart-phrase-import-destination' not in rendered
    assert "Imported smart phrases are personal to you." in rendered


def test_smart_phrase_io_frontend_keeps_personal_import_and_safe_rendering():
    script = Path("app/static/js/settings/smart-phrase-io.js").read_text(encoding="utf-8")

    for endpoint in (
        "/api/v1/smart-phrases/export",
        "/api/v1/smart-phrases/import/preflight",
        "/api/v1/smart-phrases/import",
    ):
        assert endpoint in script
    assert "document.querySelector('[data-smart-phrase-import-dialog]')" in script
    assert "smart_phrase_ids" in script
    assert "data.append('bundle', currentFile, currentFile.name)" in script
    assert "data.append('selected_indexes', JSON.stringify(indexes))" in script
    assert "JSON.parse(json)" in script
    assert "new File([json]" in script
    assert "source_trigger" in script
    assert "proposed_trigger" in script
    assert ".textContent =" in script
    assert "innerHTML" not in script
    assert "const isCleanSingleSmartPhrase = (body)" in script
    assert "search.disabled = true;" in script
    assert "search.disabled = false;" in script
    assert "search.value = priorSearchValue;" in script
    assert "search.dispatchEvent(new Event('input'));" in script


def test_smart_phrase_import_shows_success_state_before_library_refresh():
    script = Path("app/static/js/settings/smart-phrase-io.js").read_text(encoding="utf-8")

    assert "data-smart-phrase-import-success hidden" in PARTIAL
    assert 'data-lucide="party-popper"' in PARTIAL
    assert "data-smart-phrase-import-continue hidden" in PARTIAL
    assert "smart phrase${imported === 1 ? '' : 's'} imported and ready to use." in script
    assert "continueButton.focus()" in script
    assert "let seconds = 5" in script
    assert "continueButton.textContent = `Close (${seconds})`" in script
    assert "continueButton.addEventListener('click', finishImport)" in script


def test_smart_phrase_help_copies_schema_aware_ai_instructions():
    script = Path("app/static/js/settings/smart-phrase-io.js").read_text(encoding="utf-8")

    assert "Create a smart phrase with AI" in PARTIAL
    assert "Copy instructions for AI" in PARTIAL
    for hook in (
        "data-smart-phrase-help-copy",
        "data-smart-phrase-help-status",
        "data-smart-phrase-help-fallback",
        "data-smart-phrase-help-prompt",
    ):
        assert hook in PARTIAL
        assert f"[{hook}]" in script
    assert "Ask only the questions needed to resolve information that is missing or unclear." in script
    assert "Store trigger without the leading slash" in script
    assert "only uppercase A-Z, numbers, and underscores" in script
    assert "Smart phrase bundles reject unknown fields." in script
    assert "openscribe-smart-phrase-bundle-v1.schema.json" in script
    assert "navigator.clipboard.writeText" in script
    assert ".select()" in script
