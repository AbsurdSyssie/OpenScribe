import re
from inspect import signature
from pathlib import Path
from types import SimpleNamespace

from app import main
from app.web.transcribe_workspace import _order_assets_by_preferences, render_transcribe


def test_main_compatibility_aliases_point_to_extracted_helpers():
    assert main._open_realtime_workspace_db_session is main.open_realtime_workspace_db_session
    assert main._serialize_sse_event is main.serialize_sse_event
    assert main._home_redirect_url is main.home_redirect_url
    assert main._admin_redirect_url is main.admin_redirect_url
    assert main._transcribe_redirect is main.transcribe_redirect


def test_render_transcribe_keeps_legacy_route_call_shape():
    params = signature(render_transcribe).parameters
    assert params["local_dev_emails"].default is None
    assert params["request_is_localhost_only"].default is None


def test_followup_redesign_orders_default_then_favorites_then_name():
    assets = [
        SimpleNamespace(id="c", name="Zebra"),
        SimpleNamespace(id="a", name="Alpha"),
        SimpleNamespace(id="b", name="Beta"),
        SimpleNamespace(id="d", name="Delta"),
    ]

    ordered = _order_assets_by_preferences(assets, favorite_ids=["d", "b"], default_id="c")

    assert [asset.id for asset in ordered] == ["c", "d", "b", "a"]


def test_followup_redesign_preserves_required_hooks():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()
    actions_js = Path("app/static/js/transcribe/actions.js").read_text()
    documents_js = Path("app/static/js/transcribe/documents.js").read_text()

    for hook in [
        "data-quick-action-select",
        "data-quick-action-context-input",
        "data-run-quick-action-trigger",
        "data-followup-prompt-input",
        "data-generate-followup-trigger",
        "data-latest-followup-output",
        "data-followup-history",
        "data-quick-action-card",
    ]:
        assert hook in workspace_template

    assert workspace_template.index("followup-output-v2") < workspace_template.index("followup-request-v2")
    assert workspace_template.index("followup-output-footer-v2") < workspace_template.index("data-followup-history")
    assert workspace_template.index("followup-output-v2") < workspace_template.index("data-followup-history")
    assert workspace_template.index("followup-step-v2--actions") < workspace_template.index("data-quick-action-card-list")
    assert workspace_template.index("data-quick-action-card-list") < workspace_template.index("data-followup-llm-request-slot")
    assert "data-followup-selected-action-panel" not in workspace_template
    assert "data-followup-output-title" in workspace_template
    assert "data-followup-title-input" in workspace_template
    assert "data-followup-body-input" in workspace_template
    assert "data-latest-followup-updated-at" in workspace_template
    assert "data-followup-llm-request-toggle-label" in workspace_template
    assert workspace_template.index("data-followup-llm-request-toggle") < workspace_template.index("data-followup-llm-request-slot")
    assert "data-followup-output-subtitle" in workspace_template
    assert "data-followup-prompt-preview" not in workspace_template
    assert "Prompt preview" not in workspace_template
    assert workspace_template.index("data-followup-llm-request-slot") < workspace_template.index("followup-primary-actions-v2")
    assert "maxlength=\"2000\"" in workspace_template
    assert re.search(
        r"<textarea\b(?=[^>]*data-quick-action-context-input)(?=[^>]*data-followup-prompt-input)[^>]*>",
        workspace_template,
    )
    assert "data-followup-selected-action-name" not in workspace_template
    assert "data-selected-quick-action-run" not in workspace_template
    assert "data-quick-action-card-run" in workspace_template
    assert "aria-label=\"Generate {{ quick_action.name }} without context\"" in workspace_template
    assert "data-lucide=\"arrow-left\"" not in workspace_template
    assert "followup-action-button-v2--primary" in workspace_template
    assert "dom.followupSelectedActionPanel" not in actions_js
    assert "dom.selectedQuickActionRunButton" not in actions_js
    assert "dom.quickActionCardRunButtons?.forEach" in actions_js
    assert "dirtyFollowupDocumentId" in Path("app/static/js/transcribe/app.js").read_text()
    assert "hasPendingGeneratedFollowupEdits" in documents_js
    assert "const savedDocument = await persistFollowupEditsSilently?.();" in documents_js
    assert "renderSelectedFollowup({ preserveEditor: preserveDirtyFollowupEditor });" in Path("app/static/js/transcribe/app.js").read_text()
    assert "quickActionContextOverride = '';" in actions_js
    assert "runQuickActionForm.submit();" not in Path("app/templates/transcribe/_shell_extras.html").read_text()
    assert ".followup-selected-action-v2" not in head_assets
    assert ".followup-action-card-shell-v2" in head_assets
    assert ".followup-action-card-run-v2" in head_assets
    assert ".followup-output-header-v2" in head_assets
    assert ".followup-output-title-v2" in head_assets
    assert ".followup-output-title-input-v2" in head_assets
    assert ".followup-output-body-input-v2" in head_assets
    assert "font-size: 1.05rem;" in head_assets
    assert ".followup-selected-action-v2__generate" not in head_assets
    assert ".followup-action-button-v2--primary" in head_assets
    assert "followup-llm-request-pre-v2" in documents_js
    assert "followupOutputTitle.textContent" in documents_js
    assert "followupOutputTitle.value = title" in documents_js
    assert "wrapper.hidden = true" in documents_js
    assert "shouldRestoreOpen" not in documents_js
    assert "[data-followup-llm-request-slot]" in head_assets
    assert "followup-output-card-v2 followup-llm-request-card-v2" in documents_js
    assert "panel.hidden = !panel.hidden" in actions_js
    assert "Hide request" in actions_js
    assert "Show request" in actions_js
    assert "flex: 0 0 auto;" in head_assets
    assert ".followup-llm-request-card-v2[hidden]" in head_assets
    assert "display: none;" in head_assets
    assert ".followup-llm-request-card-v2" in head_assets


def test_followup_llm_request_wraps_without_horizontal_scroll():
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()

    assert ".followup-llm-request-pre-v2" in head_assets
    assert "overflow-x: hidden;" in head_assets
    assert "overflow-y: auto;" in head_assets
    assert "white-space: pre-wrap;" in head_assets
    assert "overflow-wrap: anywhere;" in head_assets


def test_clinical_note_empty_state_uses_compact_spacing():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()

    assert "empty-state empty-state--clinical-note" in workspace_template
    assert "assistant-flat-output--empty" in workspace_template
    assert ".empty-state--clinical-note" in head_assets
    assert ".assistant-flat-output--empty" in head_assets


def test_note_editor_empty_state_tracks_existing_note_content():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()
    transcribe_workspace = Path("app/web/transcribe_workspace.py").read_text()
    structured_js = Path("app/static/js/transcribe/structured.js").read_text()

    assert "structured_editor_has_text" in transcribe_workspace
    assert "freeform_editor_has_text" in transcribe_workspace
    assert 'data-structured-note-empty-state {% if structured_editor_has_text %}hidden{% endif %}' in workspace_template
    assert 'data-freeform-note-empty-state {% if freeform_editor_has_text %}hidden{% endif %}' in workspace_template
    assert ".note-editor-empty-state[hidden]" in head_assets
    assert "display: none;" in head_assets
    assert "dom.generatedStructuredPanel.hidden = false;\n    syncNoteEmptyState();" in structured_js
    assert "dom.generatedFreeformPanel.hidden = false;\n    syncNoteEmptyState();" in structured_js


def test_transcribe_transcript_render_guard_owns_transcript_dom_updates():
    app_js = Path("app/static/js/transcribe/app.js").read_text()

    assert "let lastDraftRenderSignature = null;" in app_js
    assert "let deferredDraftRenderText = null;" in app_js
    assert "const draftRenderSignature = (text, entities = [], options = {}) =>" in app_js
    assert "const selectionTouchesActiveDraft = () =>" in app_js
    assert "document.addEventListener('selectionchange', flushDeferredDraftRender);" in app_js
    assert "document.addEventListener('keyup', flushDeferredDraftRender);" in app_js
    assert "lastDraftRenderSignature = null;" in app_js
    assert "deferredDraftRenderText = null;" in app_js
    assert "renderDraft(draftText, { force: activeTranscriptChanged });" in app_js
    assert "renderPiiEntities(workspaceTranscriptPiiEntities, { updateTranscriptHighlights: false });" in app_js
    assert "renderDraft(currentDraftText, { force: true });" in app_js
    assert "renderHighlightedTranscript(currentDraftText" not in app_js
    assert app_js.count("renderHighlightedTranscript(nextText, workspaceTranscriptPiiEntities, { maskPii: piiMasked });") == 1
