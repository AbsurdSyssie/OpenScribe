import pytest
from uuid import uuid4

from tests.accessibility import audit
from tests.accessibility import conftest as accessibility_conftest
from tests.accessibility.conftest import navigate
from tests.accessibility.audit import (
    AUDIT_STATES,
    STATE_ANONYMOUS_LOGIN,
    AuditSetupError,
    AuditSettings,
    AuditReport,
    blocked_request_reason,
    same_origin,
    sanitise_axe_result,
    settings_from_environment,
)


def test_configuration_rejects_non_local_or_credential_bearing_target():
    with pytest.raises(AuditSetupError):
        settings_from_environment(base_url="https://audit:secret@example.test")


def test_network_guard_requires_exact_origin_and_never_reports_target_details():
    origin = "http://127.0.0.1:32000"
    assert same_origin("http://127.0.0.1:32000/login", origin)
    assert not same_origin("http://localhost:32000/login", origin)
    assert not same_origin("http://127.0.0.1:32001/login", origin)
    reason = blocked_request_reason("https://user:password@example.test/private/token?secret=value#fragment")
    assert reason == "blocked https request to non-audit origin"


def test_axe_result_keeps_only_bounded_redacted_diagnostics():
    safe = sanitise_axe_result(
        {"violations": [{"id": "label", "nodes": [{"target": ['input[data-name=patient@example.test]'], "failureSummary": "token=secret"}]}]}
    )
    node = safe["violations"][0]["nodes"][0]
    assert "patient@example.test" not in str(node)
    assert "secret" not in str(node)
    assert node["target"]


def test_axe_target_redacts_dynamic_identifiers():
    identifier = str(uuid4())
    safe = sanitise_axe_result(
        {"violations": [{"id": "label", "nodes": [{"target": [f"#generated-{identifier}"], "failureSummary": "missing name"}]}]}
    )
    assert identifier not in str(safe)
    assert "redacted-id" in str(safe)


def test_required_state_guard_rejects_missing_or_incomplete_coverage():
    from tests.accessibility.audit import missing_required_states

    expected = {"visible_state": "executed", "manual_state": "needs_manual_review"}
    assert missing_required_states([], expected) == ["visible_state", "manual_state"]
    assert missing_required_states(
        [{"state": "visible_state", "status": "passed"}], expected
    ) == ["manual_state"]
    assert missing_required_states(
        [
            {"state": "visible_state", "status": "incomplete"},
            {"state": "manual_state", "status": "needs_manual_review"},
        ],
        expected,
    ) == ["visible_state"]


def test_required_state_guard_accepts_completed_scans_and_explicit_negative_state():
    from tests.accessibility.audit import missing_required_states

    assert missing_required_states(
        [
            {"state": "axe_state", "status": "passed"},
            {"state": "manual_state", "status": "needs_manual_review"},
            {"state": "rejected_login", "status": "failed"},
            {"state": "auth_workspace", "status": "unexecuted"},
        ],
        {
            "axe_state": "executed",
            "manual_state": "executed",
            "rejected_login": "failed",
            "auth_workspace": "unexecuted",
        },
    ) == []


def test_isolated_stream_stub_does_not_emit_empty_workspace_update():
    assert accessibility_conftest.SYNTHETIC_AUDIT_STREAM_BODY.startswith(":")
    assert "event: workspace" not in accessibility_conftest.SYNTHETIC_AUDIT_STREAM_BODY
    assert "data: {}" not in accessibility_conftest.SYNTHETIC_AUDIT_STREAM_BODY


def test_missing_local_axe_bundle_has_a_clear_setup_error(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "axe_bundle_path", lambda: tmp_path / "axe.min.js")
    with pytest.raises(AuditSetupError, match="Local axe-core is unavailable"):
        audit.require_axe_bundle()


def test_report_marks_incomplete_rules_for_manual_review_and_writes_no_credentials(tmp_path):
    settings = AuditSettings("http://localhost:8080", tmp_path)
    report = AuditReport(settings)
    report.record_scan(
        "login",
        {"violations": [], "incomplete": [{"id": "color-contrast", "nodes": []}], "passes": [], "inapplicable": []},
        external_requests=[],
        supported_rule_counts={"wcag2aa": 1},
    )
    path = report.write()
    contents = path.read_text(encoding="utf-8")
    assert '"status": "needs_manual_review"' in contents
    assert "password" not in contents


def test_report_retains_failed_and_unexecuted_coverage_without_exception_text(tmp_path):
    report = AuditReport(AuditSettings("http://localhost:8080", tmp_path))
    report.record_coverage("normal_login_keyboard", "failed", "password=do-not-write")
    contents = report.write().read_text(encoding="utf-8")
    assert '"status": "failed"' in contents
    assert '"status": "unexecuted"' in contents
    assert "do-not-write" not in contents


def test_report_uses_one_canonical_state_name_for_coverage_and_scan(tmp_path):
    report = AuditReport(AuditSettings("http://localhost:8080", tmp_path))
    report.record_scan(STATE_ANONYMOUS_LOGIN, {"violations": [], "incomplete": [], "passes": [], "inapplicable": []}, external_requests=[], supported_rule_counts={"wcag2a": 1})
    assert [item for item in report.coverage if item["state"] == STATE_ANONYMOUS_LOGIN] == [
        {"state": STATE_ANONYMOUS_LOGIN, "status": "passed", "reason": "axe completed"}
    ]
    assert set(item["state"] for item in report.coverage) == set(AUDIT_STATES)


def test_blocked_scan_is_incomplete_never_passed(tmp_path):
    report = AuditReport(AuditSettings("http://localhost:8080", tmp_path))
    report.record_scan(STATE_ANONYMOUS_LOGIN, {"violations": [], "incomplete": [], "passes": [], "inapplicable": []}, external_requests=["blocked http request to non-audit origin"], supported_rule_counts={"wcag2a": 1})
    assert report.scans[0]["status"] == "incomplete"
    assert next(item for item in report.coverage if item["state"] == STATE_ANONYMOUS_LOGIN)["status"] == "incomplete"


def test_navigation_exception_marks_its_canonical_state_failed(tmp_path):
    class BrokenPage:
        def goto(self, *_args, **_kwargs):
            raise RuntimeError("do not report this")

    report = AuditReport(AuditSettings("http://localhost:8080", tmp_path))
    with pytest.raises(RuntimeError):
        navigate(BrokenPage(), "/login", report, STATE_ANONYMOUS_LOGIN)
    assert next(item for item in report.coverage if item["state"] == STATE_ANONYMOUS_LOGIN)["status"] == "failed"
