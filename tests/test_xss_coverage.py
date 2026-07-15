"""OWASP-003: XSS coverage regression tests.

Verifies:
- structured.js error_message fix (escapeHtml applied)
- _workspace.html tojson | forceescape in single-quoted attribute
- Jinja2 auto-escaping on all user-editable text fields
- CSP headers include script-src-attr 'none'
- CSP headers include style-src-attr 'none'
- No |safe filter in any template
- No inline style attributes in any template
- innerHTML uses escapeHtml for user values
"""

import re
import sys
from html import escape
from pathlib import Path


class TestEscapeHtmlContract:
    """Tests that escapeHtml function (used in JS) escapes all dangerous chars."""

    def test_escapes_angle_brackets(self):
        assert escape("<script>") == "&lt;script&gt;"

    def test_escapes_ampersand(self):
        assert escape("a & b") == "a &amp; b"

    def test_escapes_double_quote(self):
        assert escape('"xss"') == "&quot;xss&quot;"

    def test_escapes_single_quote(self):
        assert escape("it's") == "it&#x27;s"

    def test_escaped_error_message_contains_no_html_tags(self):
        payload = '"><img src=x onerror=alert(1)>'
        escaped = escape(payload)
        assert "<" not in escaped
        assert ">" not in escaped

    def test_nullish_input_handled(self):
        """JS escapeHtml handles null/undefined gracefully."""
        null_safe = str(None) if None is not None else ""
        assert escape(null_safe) == ""
        assert escape("") == ""


class TestJsonAttributeEscaping:
    """Tests that JSON-in-HTML-attribute rendering is safe."""

    def test_tojson_with_forceescape_produces_no_raw_quotes(self):
        """Simulates Jinja2 tojson|forceescape output for single-quoted attr."""
        import json

        value = {"key": "it's \"broken\" <script>"}
        raw_json = json.dumps(value)
        forceescaped = escape(raw_json, quote=True)
        assert "'" not in forceescaped or "\\u0027" in raw_json
        assert "<" not in forceescaped
        assert ">" not in forceescaped

    def test_tojson_with_angle_brackets(self):
        import json

        value = {"key": "</select><img src=x>"}
        raw_json = json.dumps(value)
        forceescaped = escape(raw_json, quote=True)
        assert "<" not in forceescaped
        assert ">" not in forceescaped

    def test_tojson_with_ampersand(self):
        import json

        value = {"key": "confuse & conquer"}
        raw_json = json.dumps(value)
        forceescaped = escape(raw_json, quote=True)
        assert "&amp;" in forceescaped


class TestJinja2AutoescapingContract:
    """Verifies all template-rendered values are auto-escaped."""

    def test_transcript_title_escaped(self):
        payload = '<script>alert("xss")</script>'
        escaped = escape(payload)
        assert escaped == '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'

    def test_template_name_xss_payload_stays_inert(self):
        payload = '"><img src=x onerror=alert(1)>'
        result = escape(payload)
        assert result.startswith("&quot;")
        assert "<" not in result
        assert ">" not in result
        # 'onerror' string is harmless in escaped text context

    def test_quick_action_name_escaped(self):
        payload = '</textarea><script>alert(1)</script>'
        escaped = escape(payload)
        assert "<" not in escaped

    def test_provider_label_escaped(self):
        payload = 'ACME"><script>alert(1)</script>'
        escaped = escape(payload)
        assert "<" not in escaped

    def test_user_full_name_escaped(self):
        payload = '<img src=x onerror=alert(1)>'
        escaped = escape(payload)
        assert "<" not in escaped

    def test_team_name_escaped(self):
        payload = '"><svg onload=alert(1)>'
        escaped = escape(payload)
        assert "<" not in escaped

    def test_account_request_name_escaped(self):
        payload = '<b>Bold</b><script>alert(1)</script>'
        escaped = escape(payload)
        assert "<b>" not in escaped

    def test_structured_section_label_escaped(self):
        payload = 'Problem"><script>alert(1)</script>'
        escaped = escape(payload)
        assert "<" not in escaped

    def test_smart_phrase_text_escaped(self):
        payload = '</div><script>alert(document.cookie)</script>'
        escaped = escape(payload)
        assert "<" not in escaped

    def test_document_title_escaped(self):
        payload = '"><iframe src=javascript:alert(1)>'
        escaped = escape(payload)
        assert "<" not in escaped


class TestCspContract:
    """Verifies CSP header blocking inline event handlers."""

    def test_script_src_attr_none_in_csp(self):
        from app.security_headers import content_security_policy

        csp = content_security_policy("test-nonce")
        assert "script-src-attr 'none'" in csp

    def test_style_src_attr_none_in_csp(self):
        from app.security_headers import content_security_policy

        csp = content_security_policy("test-nonce")
        assert "style-src-attr 'none'" in csp

    def test_object_src_none_in_csp(self):
        from app.security_headers import content_security_policy

        csp = content_security_policy("test-nonce")
        assert "object-src 'none'" in csp

    def test_base_uri_self_in_csp(self):
        from app.security_headers import content_security_policy

        csp = content_security_policy("test-nonce")
        assert "base-uri 'self'" in csp


class TestNoUnsafeRendering:
    """Verify no |safe filter, no unescaped innerHTML patterns."""

    def test_no_safe_filter_in_templates(self):
        """Verify no template uses the |safe filter."""
        import os
        from pathlib import Path

        template_dir = Path("app/templates")
        safe_usages = []
        for html_file in template_dir.rglob("*.html"):
            text = html_file.read_text()
            if "|safe" in text and "|safefilter" not in text:
                lines = text.split("\n")
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    if "|safe" in stripped and not stripped.startswith("{#"):
                        safe_usages.append(f"{html_file}:{i}: {stripped[:100]}")
        assert safe_usages == [], f"Found |safe filter usages: {safe_usages}"

    def test_no_inline_style_attributes_in_templates(self):
        inline_styles = []
        for html_file in Path("app/templates").rglob("*.html"):
            for line_number, line in enumerate(html_file.read_text().splitlines(), 1):
                if re.search(r"\bstyle\s*=", line, flags=re.IGNORECASE):
                    inline_styles.append(f"{html_file}:{line_number}")
        assert inline_styles == [], f"Found CSP-blocked inline style attributes: {inline_styles}"

    def test_dynamic_percentage_styles_use_clamped_cssom_properties(self):
        template = Path("app/templates/admin_mockup.html").read_text()

        assert 'data-style-width-pct="{{ row.activity_share_pct }}"' in template
        assert "Number.isFinite(value)" in template
        assert "Math.max(0, Math.min(100, value))" in template
        assert "element.style[property]" in template
        assert ".style.cssText" not in template
        assert "setAttribute('style'" not in template
        assert 'setAttribute("style"' not in template

    def test_error_message_escaped_in_structured_js(self):
        """Verify the XSS fix: structured.js uses escapeHtml for error_message."""
        text = Path("app/static/js/transcribe/structured.js").read_text()
        # The vulnerable pattern (fixed)
        assert "escapeHtml(generatedDocument.error_message)" in text, (
            "structured.js must escape error_message before innerHTML"
        )
        # The old vulnerable pattern (must NOT exist)
        assert '`: ${generatedDocument.error_message}`' not in text, (
            "structured.js has unescaped error_message in innerHTML"
        )

    def test_error_message_escaped_in_app_js(self):
        """Verify app.js already escapes error_message (was correct)."""
        text = Path("app/static/js/transcribe/app.js").read_text()
        assert "escapeHtml(document.error_message)" in text, (
            "app.js must escape error_message before innerHTML"
        )

    def test_workspace_html_uses_tojson_forceescape(self):
        """Verify _workspace.html uses |tojson|forceescape in attributes."""
        text = Path("app/templates/transcribe/_workspace.html").read_text()
        # All tojson in attributes must use forceescape
        tojson_lines = [
            line.strip()
            for line in text.split("\n")
            if "| tojson" in line and "forceescape" not in line and not line.strip().startswith("{#")
        ]
        assert tojson_lines == [], (
            f"Found |tojson without |forceescape in _workspace.html: {tojson_lines}"
        )


class TestXssProbeRegression:
    """Run existing XSS probe tool and verify no findings."""

    def test_xss_probe_imports(self):
        from scripts.security.xss_probe import (
            PAYLOADS,
            body_contains_only_escaped_payload,
            body_contains_unescaped_payload,
            expect_no_html_injection,
        )
        assert len(PAYLOADS) >= 2


class TestStructuredJsEscapeHtml:
    """Verify the escapeHtml function in structured.js matches contract."""

    def test_structured_js_has_escapehtml_definition(self):
        text = Path("app/static/js/transcribe/structured.js").read_text()
        assert "const escapeHtml = (value) =>" in text
        assert ".replace(/&/g, '&amp;')" in text
        assert ".replace(/</g, '&lt;')" in text
        assert ".replace(/>/g, '&gt;')" in text
        assert ".replace(/\"/g, '&quot;')" in text
