from pathlib import Path

from starlette.requests import Request

from app.web.templates import DEFAULT_SOURCE_CODE_URL, source_code_url, templates


def _request() -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.legal_footer_links = ()
    request.state.operator_legal_profile = None
    request.state.legal_cookie_notice_version = "unpublished"
    request.state.csp_nonce = "test-nonce"
    return request


def test_source_offer_uses_default_and_rejects_unsafe_url(monkeypatch):
    monkeypatch.delenv("APP_SOURCE_CODE_URL", raising=False)
    assert source_code_url() == DEFAULT_SOURCE_CODE_URL

    monkeypatch.setenv("APP_SOURCE_CODE_URL", "javascript:alert(1)")
    assert source_code_url() == DEFAULT_SOURCE_CODE_URL


def test_shared_footer_renders_configured_source_offer_and_release(monkeypatch):
    monkeypatch.setenv("APP_SOURCE_CODE_URL", "https://source.example/releases/2026.08.11")
    monkeypatch.setenv("APP_RELEASE", "2026.08.11+test")

    rendered = templates.env.get_template("_legal_footer_banner.html").render(request=_request())

    assert 'href="https://source.example/releases/2026.08.11"' in rendered
    assert "Source code" in rendered
    assert "Release 2026.08.11+test" in rendered


def test_vendor_notice_records_pinned_sortable_and_model_provenance():
    notices = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    sortable = Path("app/static/vendor/sortable/Sortable.min.js").read_text(encoding="utf-8")
    vad_bundle = Path("app/static/vendor/vad-web/0.0.29/bundle.min.js").read_text(encoding="utf-8")
    vad_notice = Path("app/static/vendor/vad-web/0.0.29/bundle.min.js.LICENSE.txt").read_text(encoding="utf-8")

    assert "sortablejs@1.15.2" in notices
    assert "silero_vad_v5.onnx" in notices
    assert "/*! Sortable 1.15.2 - MIT" in sortable
    assert "bundle.min.js.LICENSE.txt" in vad_bundle
    assert "ONNX Runtime Web v1.22.0" in vad_notice
