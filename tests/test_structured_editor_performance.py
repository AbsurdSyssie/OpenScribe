from pathlib import Path


def _structured_editor_source() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "app" / "static" / "js" / "transcribe" / "structured.js").read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_bootstrap_hydrates_server_rendered_note_rows_instead_of_rebuilding_them():
    source = _structured_editor_source()
    bootstrap = _between(source, "  const bootstrapFromDom = () => {", "\n\n  const clearStructuredSelection")

    assert "hydrateStructuredRowsFromDom();" in bootstrap
    assert "hydrateFreeformRowsFromDom();" in bootstrap
    assert "renderStructuredSections(generatedStructuredDraft);" not in bootstrap
    assert "renderFreeformLines(generatedFreeformDraft);" not in bootstrap


def test_initial_autosize_is_bounded_and_deferred_for_long_notes():
    source = _structured_editor_source()
    autosize = _between(source, "  const autosizeStatementEditorsIn = (container) => {", "\n\n  const focusStatementEditor")
    create_row = _between(source, "  const createStatementRow = ({", "\n\n  const collectFreeformLinesFromText")

    assert "const EAGER_AUTOSIZE_EDITOR_LIMIT = 80;" in source
    assert "textareas.slice(0, EAGER_AUTOSIZE_EDITOR_LIMIT)" in autosize
    assert "textareas.slice(EAGER_AUTOSIZE_EDITOR_LIMIT)" in autosize
    assert "new window.IntersectionObserver" in autosize
    assert "requestAnimationFrame(() => autosizeStatementEditor(textarea))" not in create_row


def test_typing_updates_linked_draft_lines_without_full_note_rescan():
    source = _structured_editor_source()
    structured_callbacks = _between(source, "  const structuredRowCallbacks = () => ({", "\n\n  const freeformRowCallbacks")
    freeform_callbacks = _between(source, "  const freeformRowCallbacks = () => ({", "\n\n  const addGeneratedStructuredLine")

    assert "syncGeneratedStructuredDraftLineFromDom" in structured_callbacks
    assert "syncGeneratedStructuredDraftFromDom();" not in structured_callbacks
    assert "syncGeneratedFreeformDraftLineFromDom" in freeform_callbacks
    assert "syncGeneratedFreeformDraftFromDom();" not in freeform_callbacks
    assert "invalidateCopyReviewForEdit" in structured_callbacks
    assert "invalidateCopyReviewForEdit" in freeform_callbacks
