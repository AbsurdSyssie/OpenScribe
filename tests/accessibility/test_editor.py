from __future__ import annotations

from datetime import timedelta
from html.parser import HTMLParser
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import (
    GeneratedDocument,
    GeneratedDocumentGeneratorType,
    GeneratedDocumentStatus,
    RedactionEntity,
    RedactionRun,
    RedactionRunStatus,
    TemplateMode,
    Transcript,
    TranscriptIngestionMode,
    TranscriptStatus,
    TranscriptVersion,
    User,
    utcnow,
)
from app.services.content_crypto import encrypt_text_for_owner
from tests.accessibility.audit import (
    STATE_SCRIBE_EDITOR_AFTER_EDIT,
    STATE_SCRIBE_EDITOR_COPY_REVIEW,
    STATE_SCRIBE_EDITOR_UNCHECKED_ROW,
    STATE_SCRIBE_TRANSCRIPT_PII_HIDDEN,
    STATE_SCRIBE_TRANSCRIPT_PII_REVEALED,
)
from tests.accessibility.conftest import login, navigate, scan


pytestmark = [pytest.mark.accessibility_audit, pytest.mark.real_db_connections]


def _encrypted(db_session, *, owner: User, table: str, field: str, record_id, plaintext: str) -> str:
    return encrypt_text_for_owner(
        db_session,
        owner_user_id=owner.id,
        table=table,
        field=field,
        record_id=record_id,
        plaintext=plaintext,
    )


def _create_synthetic_editor_content(db_session, *, email: str, empty_note: bool = False) -> str:
    owner = db_session.scalar(select(User).where(User.email == email))
    assert owner is not None
    assert owner.team_id is not None

    now = utcnow()
    transcript_id = uuid4()
    transcript_text = "Synthetic Person attended a routine consultation."
    redacted_text = "[PHI-1] attended a routine consultation."
    transcript = Transcript(
        id=transcript_id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        title="Synthetic accessibility consultation",
        ingestion_mode=TranscriptIngestionMode.whole_file,
        status=TranscriptStatus.ready,
        retention_days_applied=30,
        retention_expires_at=now + timedelta(days=30),
    )
    db_session.add(transcript)
    db_session.flush()
    transcript.current_draft_text_encrypted = _encrypted(
        db_session,
        owner=owner,
        table="transcripts",
        field="current_draft_text_encrypted",
        record_id=transcript.id,
        plaintext=transcript_text,
    )

    version_id = uuid4()
    version = TranscriptVersion(
        id=version_id,
        transcript_id=transcript.id,
        version_no=1,
        text_encrypted=_encrypted(
            db_session,
            owner=owner,
            table="transcript_versions",
            field="text_encrypted",
            record_id=version_id,
            plaintext=transcript_text,
        ),
    )
    db_session.add(version)
    db_session.flush()

    redaction_run_id = uuid4()
    redaction_run = RedactionRun(
        id=redaction_run_id,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        status=RedactionRunStatus.succeeded,
        redacted_text_encrypted=_encrypted(
            db_session,
            owner=owner,
            table="redaction_runs",
            field="redacted_text_encrypted",
            record_id=redaction_run_id,
            plaintext=redacted_text,
        ),
        mapping_hash="synthetic-accessibility-mapping",
        entity_count=1,
        api_provider="synthetic",
        api_model_or_version="test-fixture",
    )
    db_session.add(redaction_run)
    entity_id = uuid4()
    db_session.add(
        RedactionEntity(
            id=entity_id,
            redaction_run_id=redaction_run.id,
            entity_order=1,
            entity_type="PERSON",
            placeholder="[PHI-1]",
            original_value_encrypted=_encrypted(
                db_session,
                owner=owner,
                table="redaction_entities",
                field="original_value_encrypted",
                record_id=entity_id,
                plaintext="Synthetic Person",
            ),
            normalized_value_hash="synthetic-accessibility-person",
            occurrence_count=1,
        )
    )

    document_id = uuid4()
    note_lines = [] if empty_note else [
        f"Synthetic note line {index:02d}: this ordinary test sentence is long enough to make the editor scroll."
        for index in range(36)
    ]
    note_text = "\n".join(note_lines)
    document = GeneratedDocument(
        id=document_id,
        owner_user_id=owner.id,
        team_id=owner.team_id,
        transcript_id=transcript.id,
        transcript_version_id=version.id,
        generator_type=GeneratedDocumentGeneratorType.template,
        status=GeneratedDocumentStatus.ready,
        title="Synthetic accessibility draft",
        source_template_name="Synthetic template",
        document_mode=TemplateMode.freeform,
        original_output_text_encrypted=_encrypted(
            db_session,
            owner=owner,
            table="generated_documents",
            field="original_output_text_encrypted",
            record_id=document_id,
            plaintext=note_text,
        ),
        edited_output_text_encrypted=_encrypted(
            db_session,
            owner=owner,
            table="generated_documents",
            field="edited_output_text_encrypted",
            record_id=document_id,
            plaintext=note_text,
        ),
        is_edited=False,
        retention_expires_at=transcript.retention_expires_at,
        model_used="synthetic-test-fixture",
    )
    db_session.add(document)
    db_session.commit()
    return str(transcript.id)


def _server_rendered_freeform_state(markup: str) -> dict[str, object]:
    """Read only panel visibility, row presence, and no-content copy from HTML."""

    class FreeformStateParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_panel = False
            self.in_output = False
            self.panel_depth = 0
            self.output_depth = 0
            self.current_textarea = False
            self.panel_hidden = None
            self.output_hidden = None
            self.editor_has_content = []
            self.has_no_content_message = False

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "div" and "data-generated-freeform-panel" in values:
                self.in_panel = True
                self.panel_depth = 1
                self.panel_hidden = "hidden" in values
            elif tag == "div" and self.panel_depth:
                self.panel_depth += 1
            if tag == "div" and "data-latest-generated-output" in values:
                self.in_output = True
                self.output_depth = 1
                self.output_hidden = "hidden" in values
            elif tag == "div" and self.output_depth:
                self.output_depth += 1
            if tag == "textarea" and self.in_panel and "data-freeform-note-input" in values:
                self.current_textarea = True
                self.editor_has_content.append(False)

        def handle_endtag(self, tag):
            if tag == "textarea":
                self.current_textarea = False
            elif tag == "div":
                if self.panel_depth:
                    self.panel_depth -= 1
                    self.in_panel = self.panel_depth > 0
                if self.output_depth:
                    self.output_depth -= 1
                    self.in_output = self.output_depth > 0

        def handle_data(self, data):
            if self.current_textarea and data.strip():
                self.editor_has_content[-1] = True
            if self.in_output and "No note content yet." in data:
                self.has_no_content_message = True

    parser = FreeformStateParser()
    parser.feed(markup)
    return {
        "panel_hidden": parser.panel_hidden,
        "output_hidden": parser.output_hidden,
        "editor_has_content": parser.editor_has_content,
        "has_no_content_message": parser.has_no_content_message,
    }


def _wait_for_toast_animations(page) -> None:
    page.wait_for_function(
        """() => [...document.querySelectorAll('.toast:not(.hiding)')]
          .filter(toast => toast.getClientRects().length)
          .every(toast => Number(getComputedStyle(toast).opacity) >= 0.999
            && toast.getAnimations().every(animation => animation.playState !== 'running'))""",
        timeout=5_000,
    )


def _assert_enabled_drag_handle_contrast(page, row, *, checked: bool) -> dict[str, float]:
    checkbox = row.locator("input[data-freeform-note-checkbox]")
    if checked:
        checkbox.check()
    else:
        checkbox.uncheck()
    handle = row.locator(".statement-drag-handle")
    assert handle.is_enabled(), "enabled note-line drag handle was unexpectedly disabled"

    row_box = row.bounding_box()
    assert row_box is not None
    page.mouse.move(row_box["x"] + row_box["width"] * 0.75, row_box["y"] + row_box["height"] / 2)
    page.wait_for_function(
        """row => {
          const handle = row.querySelector('.statement-drag-handle');
          return row.matches(':hover') && handle && getComputedStyle(handle).opacity === '1'
            && handle.getAnimations().every(animation => animation.playState !== 'running');
        }""",
        arg=row.element_handle(),
        timeout=2_000,
    )

    def contrast() -> dict[str, object]:
        return row.evaluate(
            """row => {
              const parse = value => {
                const match = value.match(/[0-9.]+/g);
                if (!match) return null;
                return {rgb: match.slice(0, 3).map(Number), alpha: match.length > 3 ? Number(match[3]) : 1};
              };
              const over = (front, back) => {
                const alpha = front.alpha;
                return front.rgb.map((channel, index) => channel * alpha + back[index] * (1 - alpha));
              };
              const blendGroup = (front, back, opacity) => front.map((channel, index) => channel * opacity + back[index] * (1 - opacity));
              const luminance = rgb => {
                const linear = value => {
                  const c = value / 255;
                  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
                };
                return 0.2126 * linear(rgb[0]) + 0.7152 * linear(rgb[1]) + 0.0722 * linear(rgb[2]);
              };
              const handle = row.querySelector('.statement-drag-handle');
              const panel = row.closest('.freeform-note-panel');
              const workspace = row.closest('.structured-workspace');
              const handleStyle = getComputedStyle(handle);
              const rowStyle = getComputedStyle(row);
              const panelBackground = parse(getComputedStyle(panel).backgroundColor);
              const workspaceBackground = parse(getComputedStyle(workspace).backgroundColor);
              if (!handle || !panelBackground || !workspaceBackground) return {ratio: 0};
              const opaqueCanvas = over(panelBackground, workspaceBackground.rgb);
              const rowSurface = over(parse(rowStyle.backgroundColor) || {rgb: [0,0,0], alpha: 0}, opaqueCanvas);
              const handleSurface = over(parse(handleStyle.backgroundColor) || {rgb: [0,0,0], alpha: 0}, rowSurface);
              const rowOpacity = Number.parseFloat(rowStyle.opacity || '1');
              const handleOpacity = Number.parseFloat(handleStyle.opacity || '1');
              const visibleBackground = blendGroup(handleSurface, opaqueCanvas, rowOpacity * handleOpacity);
              const visibleForeground = blendGroup(over(parse(handleStyle.color), handleSurface), opaqueCanvas, rowOpacity * handleOpacity);
              const [lighter, darker] = [luminance(visibleForeground), luminance(visibleBackground)].sort((a,b) => b-a);
              return {
                ratio: (lighter + 0.05) / (darker + 0.05),
                effectiveForeground: visibleForeground.map(value => Math.round(value)),
                effectiveBackground: visibleBackground.map(value => Math.round(value)),
                handleForeground: handleStyle.color,
                handleBackground: handleStyle.backgroundColor,
                rowOpacity,
              };
            }"""
        )

    hover_sample = contrast()
    assert hover_sample["ratio"] >= 3, f"enabled note-line drag handle has insufficient contrast in {('checked' if checked else 'unchecked')} row hover state: {hover_sample}"

    handle_box = handle.bounding_box()
    assert handle_box is not None
    page.mouse.move(handle_box["x"] + handle_box["width"] / 2, handle_box["y"] + handle_box["height"] / 2)
    page.wait_for_function(
        """row => {
          const handle = row.querySelector('.statement-drag-handle');
          return handle && handle.matches(':hover') && getComputedStyle(handle).opacity === '1'
            && handle.getAnimations().every(animation => animation.playState !== 'running');
        }""",
        arg=row.element_handle(),
        timeout=2_000,
    )
    own_hover_sample = contrast()
    assert own_hover_sample["ratio"] >= 3, f"enabled note-line drag handle has insufficient contrast in {('checked' if checked else 'unchecked')} own-hover state: {own_hover_sample}"

    textarea = row.locator("[data-freeform-note-input]")
    textarea.focus()
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Shift+Tab")
    assert handle.evaluate("element => document.activeElement === element"), "keyboard traversal did not reach the enabled note-line drag handle"
    page.mouse.move(0, 0)
    page.wait_for_function(
        """row => {
          const handle = row.querySelector('.statement-drag-handle');
          return handle && handle.matches(':focus-visible') && getComputedStyle(handle).opacity === '1'
            && handle.getAnimations().every(animation => animation.playState !== 'running');
        }""",
        arg=row.element_handle(),
        timeout=2_000,
    )
    focus_sample = contrast()
    assert focus_sample["ratio"] >= 3, f"enabled note-line drag handle has insufficient contrast in {('checked' if checked else 'unchecked')} keyboard focus state: {focus_sample}"
    return {
        "row_hover": float(hover_sample["ratio"]),
        "own_hover": float(own_hover_sample["ratio"]),
        "keyboard_focus": float(focus_sample["ratio"]),
    }


@pytest.mark.accessibility_states(
    {
        STATE_SCRIBE_EDITOR_COPY_REVIEW: "executed",
        STATE_SCRIBE_EDITOR_AFTER_EDIT: "executed",
        STATE_SCRIBE_EDITOR_UNCHECKED_ROW: "executed",
        STATE_SCRIBE_TRANSCRIPT_PII_REVEALED: "executed",
        STATE_SCRIBE_TRANSCRIPT_PII_HIDDEN: "executed",
    }
)
def test_synthetic_editor_copy_review_state_is_accessible_and_keyboard_gated(
    db_session,
    audit_report,
    audit_browser,
    audit_settings,
    synthetic_audit_user,
    axe_source,
):
    page, blocked = audit_browser
    try:
        empty_transcript_id = _create_synthetic_editor_content(
            db_session,
            email=synthetic_audit_user["email"],
            empty_note=True,
        )
        transcript_id = _create_synthetic_editor_content(
            db_session,
            email=synthetic_audit_user["email"],
        )
        page.set_viewport_size({"width": 1024, "height": 600})
        login(page, synthetic_audit_user, audit_report)
        empty_response = navigate(page, f"/workspace?transcript_id={empty_transcript_id}&tab=output", audit_report, STATE_SCRIBE_EDITOR_COPY_REVIEW)
        if page.locator('form[action="/login"]').count():
            raise AssertionError("authenticated empty-note state rendered the login page")
        empty_state = _server_rendered_freeform_state(empty_response.text())
        assert not any(empty_state["editor_has_content"]), "empty encrypted note rendered unexpected editor content"
        assert empty_state["output_hidden"] is False and empty_state["has_no_content_message"], "empty encrypted note did not render its no-content output state"

        nonempty_response = navigate(page, f"/workspace?transcript_id={transcript_id}&tab=output", audit_report, STATE_SCRIBE_EDITOR_COPY_REVIEW)
        nonempty_state = _server_rendered_freeform_state(nonempty_response.text())
        assert nonempty_state["panel_hidden"] is False, "nonempty encrypted note did not open the freeform editor in server-rendered HTML"
        assert nonempty_state["output_hidden"] is True, "nonempty generated note was not moved into the editor view in server-rendered HTML"
        assert len(nonempty_state["editor_has_content"]) == 37 and any(nonempty_state["editor_has_content"]), "nonempty encrypted note did not render its lines plus blank editor row"
        if page.locator('form[action="/login"]').count():
            raise AssertionError("authenticated editor state rendered the login page")

        recording_mode = page.locator("[data-recording-mode-select]")
        page.locator("body").focus()
        for _ in range(40):
            page.keyboard.press("Tab")
            if recording_mode.evaluate("element => document.activeElement === element"):
                break
        assert recording_mode.evaluate("element => document.activeElement === element"), "keyboard Tab did not reach the enabled Recording mode select"
        mode_trigger = page.locator("[data-record-mode-dropdown]")
        mode_focus = mode_trigger.evaluate(
            """element => {
              const style = getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              const backgroundColor = (raw) => {
                const value = raw.trim();
                const hex = value.match(/^#([0-9a-f]{6})$/i);
                if (hex) return [...hex[1].match(/../g)].map(part => parseInt(part, 16));
                const rgb = value.match(/[0-9.]+/g);
                return rgb?.length >= 3 ? rgb.slice(0, 3).map(Number) : null;
              };
              const luminance = ([red, green, blue]) => {
                const linear = value => {
                  const channel = value / 255;
                  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
                };
                return 0.2126 * linear(red) + 0.7152 * linear(green) + 0.0722 * linear(blue);
              };
              const base = backgroundColor(getComputedStyle(document.documentElement).getPropertyValue('--bg')) || [250, 248, 245];
              const outline = backgroundColor(style.outlineColor);
              let opacity = 1;
              for (let node = element; node; node = node.parentElement) opacity *= Number.parseFloat(getComputedStyle(node).opacity || '1');
              const effective = outline?.map((channel, index) => channel * opacity + base[index] * (1 - opacity));
              const [lighter, darker] = [luminance(effective || [0, 0, 0]), luminance(base)].sort((a, b) => b - a);
              const blockedBy = [];
              const outlineBox = { left: rect.left - 4, right: rect.right + 4, top: rect.top - 4, bottom: rect.bottom + 4 };
              for (let node = element.parentElement; node; node = node.parentElement) {
                const nodeStyle = getComputedStyle(node);
                if (!['hidden', 'clip', 'scroll', 'auto'].includes(nodeStyle.overflowX) && !['hidden', 'clip', 'scroll', 'auto'].includes(nodeStyle.overflowY)) continue;
                const parent = node.getBoundingClientRect();
                if (outlineBox.left < parent.left || outlineBox.right > parent.right || outlineBox.top < parent.top || outlineBox.bottom > parent.bottom) blockedBy.push(node.className?.toString() || node.tagName);
              }
              return {
                outlineStyle: style.outlineStyle,
                outlineWidth: style.outlineWidth,
                outlineOffset: style.outlineOffset,
                outlineColor: style.outlineColor,
                buttonRect: [rect.width, rect.height],
                opacity,
                contrastRatio: ((lighter + 0.05) / (darker + 0.05)),
                clippedBy: blockedBy,
              };
            }"""
        )
        assert mode_focus["outlineStyle"] == "solid" and mode_focus["outlineWidth"] == "2px" and mode_focus["outlineOffset"] == "2px", (
            "the visible Change recording mode button did not indicate focus on its clipped native select"
        )
        assert min(mode_focus["buttonRect"]) >= 24, "visible recording-mode focus target is too small to show the indicator"
        assert mode_focus["contrastRatio"] >= 3, f"effective recording-mode focus indicator contrast is insufficient: {mode_focus['contrastRatio']:.2f}:1"
        assert not mode_focus["clippedBy"], f"recording-mode focus indicator is clipped by a wrapper: {mode_focus['clippedBy']}"
        assert mode_trigger.evaluate("element => !element.matches(':focus')"), "native-select focus incorrectly moved to the visible mode button"

        note_versions = page.get_by_role("group", name="Note versions", exact=True)
        assert note_versions.count() == 1, "note-version scroller was not exposed as a named group"
        note_versions.focus()
        page.keyboard.press("ArrowRight")
        assert note_versions.evaluate("element => document.activeElement === element"), "ArrowRight moved focus out of the note-version group"
        page.keyboard.press("Tab")

        editor_panel = page.locator("[data-generated-freeform-panel]")
        try:
            editor_panel.wait_for(state="visible", timeout=10_000)
        except Exception as exc:
            diagnostics = page.evaluate(
                """() => ({
                  mode: document.querySelector('[data-latest-generated-output]')?.dataset.latestGeneratedMode || '',
                  status: document.querySelector('[data-latest-generated-output]')?.dataset.latestGeneratedStatus || '',
                  panelHidden: document.querySelector('[data-generated-freeform-panel]')?.hidden ?? null,
                  serverRowCount: document.querySelectorAll('[data-generated-freeform-panel] [data-freeform-note-row]').length,
                  nonblankFieldCount: [...document.querySelectorAll('[data-generated-freeform-panel] [data-freeform-note-input]')]
                    .filter(field => field.value.trim()).length,
                })"""
            )
            raise AssertionError(f"freeform editor did not become visible; rendered metadata: {diagnostics}") from exc
        assert page.locator("[data-latest-generated-output]").evaluate("element => element.hidden"), "nonempty generated note was not moved into the editor view"
        assert page.locator("[data-freeform-note-row]").count() == 37, "nonempty generated note did not render its encrypted lines plus the blank editor row"
        page.locator("[data-freeform-note-input]").nth(0).wait_for(state="visible", timeout=5_000)
        result = scan(page, audit_report, STATE_SCRIBE_EDITOR_COPY_REVIEW, axe_source, blocked)
        assert not result.get("violations"), f"axe found {len(result['violations'])} violation group(s) in the synthetic editor state"
        checked_handle_contrast = _assert_enabled_drag_handle_contrast(
            page, page.locator("[data-freeform-note-row]").nth(0), checked=True
        )
        unchecked_handle_contrast = _assert_enabled_drag_handle_contrast(
            page, page.locator("[data-freeform-note-row]").nth(1), checked=False
        )
        audit_report.record_keyboard(
            "enabled_drag_handle_nontext_contrast",
            "passed",
            "Computed contrast ratios, checked row hover/own hover/focus: "
            + "/".join(f"{checked_handle_contrast[key]:.2f}:1" for key in ("row_hover", "own_hover", "keyboard_focus"))
            + "; unchecked row hover/own hover/focus: "
            + "/".join(f"{unchecked_handle_contrast[key]:.2f}:1" for key in ("row_hover", "own_hover", "keyboard_focus")),
        )
        unchecked_result = scan(
            page, audit_report, STATE_SCRIBE_EDITOR_UNCHECKED_ROW, axe_source, blocked
        )
        assert not unchecked_result.get("violations"), "axe found violations in the unchecked synthetic editor row state"
        page.locator("[data-freeform-note-checkbox]").nth(1).check()

        row_count = page.locator("[data-freeform-note-row]").count()
        first_editor = page.locator("[data-freeform-note-input]").nth(0)
        first_editor.focus()
        first_editor.press("End")
        first_editor.press("Enter")
        assert page.locator("[data-freeform-note-row]").count() == row_count + 1, "Enter did not add an editor line"
        page.wait_for_function(
            """() => {
              const active = document.activeElement;
              return active instanceof HTMLTextAreaElement
                && active.matches('[data-freeform-note-input]')
                && active !== document.querySelector('[data-freeform-note-input]');
            }""",
            timeout=5_000,
        )
        inserted_editor = page.locator(":focus[data-freeform-note-input]")
        assert inserted_editor.count() == 1, "Enter did not focus the inserted editor line"
        inserted_editor.press("Control+A")
        inserted_editor.press("Backspace")
        assert inserted_editor.input_value() == "", "keyboard editing did not clear the inserted line"
        assert page.locator("[data-freeform-note-row]").count() == row_count + 1, "clearing text unexpectedly removed the editor line"
        inserted_editor.press("Backspace")
        assert page.locator("[data-freeform-note-row]").count() == row_count, "Backspace did not remove the empty editor line"

        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=audit_settings.base_url)
        sentinel = "synthetic clipboard sentinel"
        page.evaluate("value => navigator.clipboard.writeText(value)", sentinel)
        copy_button = page.locator("[data-copy-structured-lines]")
        page.wait_for_function(
            """() => document.querySelector('[data-copy-structured-lines]')?.dataset.copyReviewBlocked === 'true'""",
            timeout=5_000,
        )
        copy_button.focus()
        page.keyboard.press("Enter")
        assert page.evaluate("() => navigator.clipboard.readText()") == sentinel, "keyboard activation bypassed the generated-note review gate"

        # A real edit invalidates prior review; keyboard traversal through the
        # visible generated draft is required before copy becomes available.
        first_editor.fill("Synthetic edited note line for this accessibility test.")
        page.wait_for_function(
            """() => document.querySelector('[data-copy-structured-lines]')?.dataset.copyReviewBlocked === 'true'""",
            timeout=5_000,
        )
        assert page.evaluate("() => navigator.clipboard.readText()") == sentinel, "editing unexpectedly changed the clipboard"
        edited_result = scan(page, audit_report, STATE_SCRIBE_EDITOR_AFTER_EDIT, axe_source, blocked)
        assert not edited_result.get("violations"), "axe found violations in the edited synthetic editor state"

        # ArrowDown traverses note lines through the editor's real keyboard
        # model and causes the scroll handler to observe the viewed draft.
        last_editor = page.locator("[data-freeform-note-input]").last
        first_editor.focus()
        first_editor.press("End")
        for _ in range(page.locator("[data-freeform-note-input]").count() + 20):
            if last_editor.evaluate("element => element === document.activeElement"):
                break
            page.keyboard.press("ArrowDown")
        assert last_editor.evaluate("element => element === document.activeElement"), "ArrowDown did not reach the last note line"
        page.wait_for_function(
            """() => document.querySelector('[data-generated-freeform-panel]')?.dataset.copyReviewViewed === 'true'""",
            timeout=5_000,
        )
        copy_button.focus()
        page.keyboard.press("Enter")
        page.wait_for_function(
            """async value => (await navigator.clipboard.readText()) !== value""",
            arg=sentinel,
            timeout=5_000,
        )
        assert page.evaluate("async () => (await navigator.clipboard.readText()).length") > len(sentinel), "reviewed keyboard copy did not write selected synthetic lines"

        transcript_tab = page.get_by_role("tab", name="Transcript")
        transcript_tab.focus()
        page.keyboard.press("Enter")
        visibility = page.locator("[data-toggle-pii-visibility]")
        visibility.wait_for(state="visible", timeout=5_000)
        pii_value = page.locator(".pii-value").first
        assert pii_value.count() == 1, "synthetic redaction fixture did not render its PII review row"
        assert not pii_value.evaluate("element => element.textContent !== element.dataset.realValue"), "PII started in revealed state"
        _wait_for_toast_animations(page)
        revealed_result = scan(page, audit_report, STATE_SCRIBE_TRANSCRIPT_PII_REVEALED, axe_source, blocked)
        assert not revealed_result.get("violations"), "axe found violations in the revealed synthetic PII state"
        assert visibility.get_attribute("aria-pressed") == "false"
        visibility.focus()
        page.keyboard.press("Enter")
        assert visibility.get_attribute("aria-pressed") == "true"
        assert page.get_by_role("button", name="Show PII", exact=True).count() == 1
        assert pii_value.evaluate("element => element.textContent !== element.dataset.realValue"), "Hide PII did not mask the synthetic value"
        _wait_for_toast_animations(page)
        hidden_result = scan(page, audit_report, STATE_SCRIBE_TRANSCRIPT_PII_HIDDEN, axe_source, blocked)
        assert not hidden_result.get("violations"), "axe found violations in the masked synthetic PII state"
        visibility.focus()
        page.keyboard.press("Enter")
        assert visibility.get_attribute("aria-pressed") == "false"
        assert page.get_by_role("button", name="Hide PII", exact=True).count() == 1
        assert not pii_value.evaluate("element => element.textContent !== element.dataset.realValue"), "Show PII did not reveal the synthetic value"
        _wait_for_toast_animations(page)
    except Exception:
        audit_report.record_coverage(STATE_SCRIBE_EDITOR_COPY_REVIEW, "failed", "synthetic editor or keyboard-gate assertion failed")
        audit_report.record_keyboard(STATE_SCRIBE_EDITOR_COPY_REVIEW, "failed", "synthetic editor keyboard-gate assertion failed")
        raise

    audit_report.record_keyboard(
        STATE_SCRIBE_EDITOR_COPY_REVIEW,
        "passed",
        "Enter and Backspace edited synthetic lines; keyboard copy stayed blocked until ArrowDown traversal reached the end of the edited draft.",
    )
