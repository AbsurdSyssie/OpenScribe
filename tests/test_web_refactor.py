from inspect import signature

from app import main
from app.web.transcribe_workspace import render_transcribe


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
