from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from app.errors import AppError
from app.models import TemplateMode
from app.web import transcribe_workspace


@pytest.mark.parametrize(
    ("edited_text", "expected_rows", "expected_has_content"),
    [
        (
            "\n First line \n\nSecond line  \n",
            [
                {"text": "First line", "checked": True},
                {"text": "Second line", "checked": True},
                {"text": "", "checked": True},
            ],
            True,
        ),
        (" \n\t ", [{"text": "", "checked": True}], False),
    ],
)
def test_freeform_workspace_helpers_read_only_edited_output_text(
    monkeypatch,
    edited_text,
    expected_rows,
    expected_has_content,
):
    document = SimpleNamespace(document_mode=TemplateMode.freeform)
    db = object()
    read_edited_text = Mock(return_value=edited_text)
    monkeypatch.setattr(transcribe_workspace, "generated_document_text_service", read_edited_text)
    monkeypatch.setattr(
        transcribe_workspace,
        "generated_document_response",
        Mock(side_effect=AssertionError("full document serializer must not run")),
    )
    monkeypatch.setattr(
        transcribe_workspace,
        "generated_document_section_text_service",
        Mock(side_effect=AssertionError("section decryptor must not run")),
    )

    assert transcribe_workspace._freeform_editor_rows(db, generated_document=document) == expected_rows
    assert transcribe_workspace._generated_note_has_content(db, document) is expected_has_content
    assert read_edited_text.call_args_list == [call(db, document=document, field="edited_output_text_encrypted")] * 2


def test_workspace_text_helpers_keep_none_and_structured_paths(monkeypatch):
    assert transcribe_workspace._freeform_editor_rows(object(), generated_document=None) == [{"text": "", "checked": True}]
    assert transcribe_workspace._generated_note_has_content(object(), None) is False

    structured_document = SimpleNamespace(
        document_mode=TemplateMode.structured,
        sections=[SimpleNamespace(id="problem", section_key="problem")],
    )
    monkeypatch.setattr(
        transcribe_workspace,
        "generated_document_text_service",
        Mock(side_effect=AssertionError("document text must not be read for structured output")),
    )
    monkeypatch.setattr(
        transcribe_workspace,
        "generated_document_section_text_service",
        lambda _db, *, section, field: "Structured content" if (section.id, field) == ("problem", "edited_text_encrypted") else "",
    )

    assert transcribe_workspace._freeform_editor_rows(object(), generated_document=structured_document) == []
    assert transcribe_workspace._generated_note_has_content(object(), structured_document) is True


@pytest.mark.parametrize("helper", [transcribe_workspace._freeform_editor_rows, transcribe_workspace._generated_note_has_content])
def test_workspace_text_helpers_propagate_edited_output_field_errors(monkeypatch, helper):
    document = SimpleNamespace(document_mode=TemplateMode.freeform)
    expected_error = AppError(500, "content_crypto_invalid", "Synthetic encrypted field failure")

    monkeypatch.setattr(transcribe_workspace, "generated_document_text_service", Mock(side_effect=expected_error))

    with pytest.raises(AppError) as error:
        if helper is transcribe_workspace._freeform_editor_rows:
            helper(object(), generated_document=document)
        else:
            helper(object(), document)

    assert error.value is expected_error
