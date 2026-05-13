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


def test_clinical_note_empty_state_uses_compact_spacing():
    workspace_template = Path("app/templates/transcribe/_workspace.html").read_text()
    head_assets = Path("app/templates/transcribe/_head_assets.html").read_text()

    assert "empty-state empty-state--clinical-note" in workspace_template
    assert "assistant-flat-output--empty" in workspace_template
    assert ".empty-state--clinical-note" in head_assets
    assert ".assistant-flat-output--empty" in head_assets
