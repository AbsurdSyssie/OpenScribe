from scripts.security.xss_probe import (
    body_contains_only_escaped_payload,
    body_contains_unescaped_payload,
    expect_no_html_injection,
    extract_csrf_token,
    public_login_probe,
)


def test_extract_csrf_token_reads_hidden_input_value():
    body = '<form><input type="hidden" name="_csrf_token" value="abc123"></form>'
    assert extract_csrf_token(body) == "abc123"


def test_body_contains_unescaped_payload_detects_raw_markup():
    payload = '"><u data-xss-probe="probe">PROBE</u>'
    body = f"<html><body>{payload}</body></html>"
    assert body_contains_unescaped_payload(body, payload) is True


def test_body_contains_only_escaped_payload_detects_escaped_markup():
    payload = '"><u data-xss-probe="probe">PROBE</u>'
    body = "&quot;&gt;&lt;u data-xss-probe=&quot;probe&quot;&gt;PROBE&lt;/u&gt;"
    assert body_contains_only_escaped_payload(body, payload) is True


def test_expect_no_html_injection_accepts_escaped_reflection():
    payload = '"><u data-xss-probe="probe">PROBE</u>'
    body = "<html><body>&quot;&gt;&lt;u data-xss-probe=&quot;probe&quot;&gt;PROBE&lt;/u&gt;</body></html>"
    ok, detail = expect_no_html_injection(body, payload)
    assert ok is True
    assert detail == "payload only present in escaped form"


def test_expect_no_html_injection_rejects_live_markup():
    payload = '"><u data-xss-probe="probe">PROBE</u>'
    body = f"<html><body>{payload}</body></html>"
    ok, detail = expect_no_html_injection(body, payload)
    assert ok is False
    assert detail == "payload reflected as live HTML or raw markup"


def test_public_login_probe_submits_payload_in_login_field(monkeypatch):
    class DummyResponse:
        def __init__(self, text: str):
            self.text = text

    captured: dict[str, str] = {}
    payload = '"><u data-xss-probe="probe">PROBE</u>'

    monkeypatch.setattr(
        "scripts.security.xss_probe.request_page",
        lambda client, base_url, path: DummyResponse('<form><input type="hidden" name="_csrf_token" value="abc123"></form>'),
    )

    def fake_post_form(client, base_url, path, data):
        captured.update(data)
        return DummyResponse("<html><body>invalid login</body></html>")

    monkeypatch.setattr("scripts.security.xss_probe.post_form", fake_post_form)

    result = public_login_probe(object(), "http://example.test", payload)

    assert result.ok is True
    assert captured["email"] == payload
    assert captured["password"] == "wrongpass1"
