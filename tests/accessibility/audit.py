"""Privacy-safe support code for the opt-in local accessibility audit."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
AXE_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa")
MAX_NODES_PER_RULE = 10
MAX_TEXT_LENGTH = 300
STATE_ANONYMOUS_LOGIN = "anonymous_login"
STATE_AUTH_WORKSPACE = "authenticated_workspace_synthetic"
STATE_AUTH_SCRIBE_EMPTY = "authenticated_scribe_empty_synthetic"
STATE_AUTH_SCRIBE_NARROW = "authenticated_scribe_narrow_synthetic"
STATE_LOGIN_KEYBOARD = "normal_login_keyboard"
STATE_SCRIBE_TABS_KEYBOARD = "scribe_tabs_keyboard"
STATE_CLINICAL_MANUAL = "other_clinical_workflows_manual_review"
STATE_SCRIBE_EDITOR_COPY_REVIEW = "scribe_editor_copy_review_synthetic"
STATE_SCRIBE_EDITOR_AFTER_EDIT = "scribe_editor_after_edit_synthetic"
STATE_SCRIBE_EDITOR_UNCHECKED_ROW = "scribe_editor_unchecked_row_synthetic"
STATE_SCRIBE_TRANSCRIPT_PII_HIDDEN = "scribe_transcript_pii_hidden_synthetic"
STATE_SCRIBE_TRANSCRIPT_PII_REVEALED = "scribe_transcript_pii_revealed_synthetic"
STATE_DIALOG_NOTIFICATIONS = "dialog_notifications_synthetic"
STATE_LOGIN_VALIDATION = "login_validation_error_synthetic"
STATE_RESPONSIVE_KEYBOARD = "responsive_320_keyboard_synthetic"
STATE_ACCOUNT_PERSONAL = "account_personal_owner_synthetic"
STATE_PREFERENCES_PERSONAL = "preferences_personal_owner_synthetic"
STATE_TEMPLATES_PERSONAL = "templates_personal_library_synthetic"
STATE_QUICK_ACTIONS_PERSONAL = "quick_actions_personal_library_synthetic"
STATE_SMART_PHRASES_PERSONAL = "smart_phrases_personal_library_synthetic"
STATE_SMART_PHRASE_VALIDATION = "smart_phrase_invalid_form_synthetic"
STATE_TEMPLATE_IMPORT_DIALOG = "templates_import_dialog_synthetic"
STATE_QUICK_ACTION_IMPORT_DIALOG = "quick_actions_import_dialog_synthetic"
STATE_ACCOUNT_EMAIL_DIALOG = "account_email_dialog_synthetic"
STATE_GUIDE_EMPTY_DESKTOP = "transcription_guide_empty_desktop_synthetic"
STATE_GUIDE_EMPTY_NARROW = "transcription_guide_empty_narrow_synthetic"
STATE_GUIDE_POPULATED_DESKTOP = "transcription_guide_populated_desktop_synthetic"
STATE_GUIDE_POPULATED_NARROW = "transcription_guide_populated_narrow_synthetic"
STATE_HELP_DESKTOP = "workspace_help_desktop_synthetic"
STATE_HELP_NARROW = "workspace_help_narrow_synthetic"
STATE_HELP_MOBILE_DRAWER = "workspace_help_mobile_drawer_synthetic"
AUDIT_STATES = (
    STATE_ANONYMOUS_LOGIN,
    STATE_AUTH_WORKSPACE,
    STATE_AUTH_SCRIBE_EMPTY,
    STATE_AUTH_SCRIBE_NARROW,
    STATE_LOGIN_KEYBOARD,
    STATE_SCRIBE_TABS_KEYBOARD,
    STATE_SCRIBE_EDITOR_COPY_REVIEW,
    STATE_SCRIBE_EDITOR_AFTER_EDIT,
    STATE_SCRIBE_EDITOR_UNCHECKED_ROW,
    STATE_SCRIBE_TRANSCRIPT_PII_HIDDEN,
    STATE_SCRIBE_TRANSCRIPT_PII_REVEALED,
    STATE_DIALOG_NOTIFICATIONS,
    STATE_LOGIN_VALIDATION,
    STATE_RESPONSIVE_KEYBOARD,
    STATE_ACCOUNT_PERSONAL,
    STATE_PREFERENCES_PERSONAL,
    STATE_TEMPLATES_PERSONAL,
    STATE_QUICK_ACTIONS_PERSONAL,
    STATE_SMART_PHRASES_PERSONAL,
    STATE_SMART_PHRASE_VALIDATION,
    STATE_TEMPLATE_IMPORT_DIALOG,
    STATE_QUICK_ACTION_IMPORT_DIALOG,
    STATE_ACCOUNT_EMAIL_DIALOG,
    STATE_GUIDE_EMPTY_DESKTOP,
    STATE_GUIDE_EMPTY_NARROW,
    STATE_GUIDE_POPULATED_DESKTOP,
    STATE_GUIDE_POPULATED_NARROW,
    STATE_HELP_DESKTOP,
    STATE_HELP_NARROW,
    STATE_HELP_MOBILE_DRAWER,
    STATE_CLINICAL_MANUAL,
)


class AuditSetupError(RuntimeError):
    """The selected audit cannot safely run in its requested environment."""


@dataclass(frozen=True)
class AuditSettings:
    base_url: str
    report_dir: Path


def settings_from_environment(*, base_url: str) -> AuditSettings:
    parsed = urlparse(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.hostname.lower() not in LOCAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AuditSetupError(
            "OPENSCRIBE_ACCESSIBILITY_BASE_URL must be an http(s) localhost, 127.0.0.1, or [::1] URL without credentials, query, or fragment."
        )
    report_dir = Path(os.getenv("OPENSCRIBE_ACCESSIBILITY_REPORT_DIR", "test-results/accessibility"))
    return AuditSettings(base_url=base_url.rstrip("/"), report_dir=report_dir)


def same_origin(url: str, origin: str) -> bool:
    """Allow only one exact scheme, host, and port; aliases are not interchangeable."""
    candidate = urlparse(url)
    expected = urlparse(origin)
    return (
        candidate.scheme == expected.scheme
        and candidate.hostname == expected.hostname
        and candidate.port == expected.port
        and candidate.username is None
        and candidate.password is None
    )


def blocked_request_reason(url: str) -> str:
    """Do not retain a target URL because paths and userinfo can contain secrets."""
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.hostname:
            return f"blocked {parsed.scheme} request to non-audit origin"
    except ValueError:
        pass
    return "blocked malformed request"


def axe_bundle_path() -> Path:
    return Path(__file__).with_name("node_modules") / "axe-core" / "axe.min.js"


def require_axe_bundle() -> Path:
    bundle = axe_bundle_path()
    if not bundle.is_file():
        raise AuditSetupError(
            "Local axe-core is unavailable. Run `npm ci` in tests/accessibility; the audit never downloads axe from a CDN."
        )
    return bundle


_SENSITIVE_VALUE = re.compile(
    r"(?i)(password|token|secret|cookie|authorization|bearer|session|email|patient|transcript|note|dictation)\s*([=:])\s*[^\s,;]+"
)
_UUID = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b")
_LONG_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])[0-9]{16,}(?![A-Za-z0-9])")
_QUOTED_VALUE = re.compile(r"(['\"]).*?\1")
_SELECTOR_ATTRIBUTE = re.compile(r"\[[^\]]+\]")


def redact_text(value: object) -> str:
    """Keep bounded diagnostic structure without retaining DOM values or secrets."""
    text = str(value or "")
    text = _SENSITIVE_VALUE.sub(r"\1\2[redacted]", text)
    text = _UUID.sub("redacted-id", text)
    text = _LONG_IDENTIFIER.sub("redacted-id", text)
    text = _QUOTED_VALUE.sub("[redacted]", text)
    text = _SELECTOR_ATTRIBUTE.sub("[attribute]", text)
    return text[:MAX_TEXT_LENGTH]


def sanitise_nodes(nodes: list[dict]) -> list[dict[str, object]]:
    safe_nodes: list[dict[str, object]] = []
    for node in nodes[:MAX_NODES_PER_RULE]:
        target = node.get("target", [])
        selectors = target if isinstance(target, list) else [target]
        safe_nodes.append(
            {
                "target": [redact_text(selector) for selector in selectors][:3],
                "failure_summary": redact_text(node.get("failureSummary", "")),
            }
        )
    return safe_nodes


def sanitise_axe_result(result: dict) -> dict[str, object]:
    def rules(category: str) -> list[dict[str, object]]:
        safe_rules: list[dict[str, object]] = []
        for rule in result.get(category, []) or []:
            safe_rules.append(
                {
                    "id": redact_text(rule.get("id", "")),
                    "impact": redact_text(rule.get("impact", "")),
                    "help": redact_text(rule.get("help", "")),
                    "help_url": redact_text(rule.get("helpUrl", "")),
                    "tags": [redact_text(tag) for tag in rule.get("tags", [])],
                    "nodes": sanitise_nodes(rule.get("nodes", [])),
                }
            )
        return safe_rules

    return {category: rules(category) for category in ("violations", "incomplete", "inapplicable", "passes")}


def missing_required_states(
    coverage: list[dict[str, str]], expected: dict[str, str]
) -> list[str]:
    """Return required states that were skipped or did not reach their expected status."""
    actual = {item["state"]: item["status"] for item in coverage}
    mismatches: list[str] = []
    for state, expected_status in expected.items():
        status = actual.get(state, "unexecuted")
        if expected_status == "executed":
            if status in {"unexecuted", "failed", "incomplete"}:
                mismatches.append(state)
        elif status != expected_status:
            mismatches.append(state)
    return mismatches


class AuditReport:
    """In-memory report writer. It never stores page HTML, screenshots, URLs with queries, or auth state."""

    def __init__(self, settings: AuditSettings, *, report_name: str = "accessibility-audit"):
        self.settings = settings
        self.report_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", report_name).strip("-") or "accessibility-audit"
        self.scans: list[dict[str, object]] = []
        self.keyboard: list[dict[str, str]] = []
        self.external_requests: list[str] = []
        self.coverage: list[dict[str, str]] = [
            {"state": state, "status": "unexecuted", "reason": "not reached"}
            for state in AUDIT_STATES
        ]

    def record_coverage(self, state: str, status: str, reason: str) -> None:
        safe_reason = redact_text(reason)
        for item in self.coverage:
            if item["state"] == state:
                item.update(status=status, reason=safe_reason)
                return
        self.coverage.append({"state": state, "status": status, "reason": safe_reason})

    def record_scan(
        self, state: str, result: dict, *, external_requests: list[str], supported_rule_counts: dict[str, int]
    ) -> None:
        safe = sanitise_axe_result(result)
        status = "failed" if safe["violations"] else "passed"
        if safe["incomplete"] and status == "passed":
            status = "needs_manual_review"
        if external_requests:
            status = "incomplete"
        self.scans.append(
            {
                "state": state,
                "status": status,
                "supported_rule_counts": supported_rule_counts,
                "axe": safe,
            }
        )
        self.record_coverage(state, status, "axe completed")
        self.external_requests.extend(external_requests)

    def record_keyboard(self, state: str, status: str, detail: str) -> None:
        self.keyboard.append({"state": state, "status": status, "detail": redact_text(detail)})

    def write(self) -> Path:
        self.settings.report_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": {
                "base_url": self.settings.base_url,
                "axe_tags": list(AXE_TAGS),
                "claim": "Automated checks only. This report is not a WCAG conformance certification.",
                "limitations": ["Live workspace stream updates are not exercised by the isolated audit."],
            },
            "scans": self.scans,
            "keyboard": self.keyboard,
            "coverage": self.coverage,
            "external_requests_blocked": sorted(set(self.external_requests)),
        }
        report = self.settings.report_dir / f"{self.report_name}.json"
        report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return report
