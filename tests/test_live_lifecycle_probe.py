import pytest

from scripts.security import live_lifecycle_deletion_probe as probe


def parse_args(*args):
    return probe.build_parser().parse_args(list(args))


def test_live_lifecycle_probe_dry_run_does_not_require_credentials():
    args = parse_args("--base-url", "https://staging.openscribe.co.uk", "--run-id", "owasp-test")

    summary = probe.run_probe(args)

    assert summary.ok is True
    assert summary.dry_run is True
    assert summary.production_guard == "dry_run"
    assert summary.steps[0].name == "dry_run_plan"


def test_live_lifecycle_probe_execute_requires_matching_confirmation():
    args = parse_args(
        "--base-url",
        "https://staging.openscribe.co.uk",
        "--run-id",
        "owasp-test",
        "--execute",
        "--confirm-run-id",
        "wrong",
    )

    with pytest.raises(SystemExit, match="--execute requires --confirm-run-id"):
        probe.validate_execution_guard(args)


def test_live_lifecycle_probe_blocks_probable_production_without_flag():
    args = parse_args(
        "--base-url",
        "https://openscribe.co.uk",
        "--run-id",
        "owasp-test",
        "--execute",
        "--confirm-run-id",
        "owasp-test",
    )

    with pytest.raises(SystemExit, match="Refusing probable production"):
        probe.validate_execution_guard(args)


def test_live_lifecycle_probe_allows_staging_with_confirmation():
    args = parse_args(
        "--base-url",
        "https://staging.openscribe.co.uk",
        "--run-id",
        "owasp-test",
        "--execute",
        "--confirm-run-id",
        "owasp-test",
    )

    assert probe.validate_execution_guard(args) == "non_production"


def test_probe_client_replays_cookie_header_for_local_http():
    client = probe.ProbeClient(base_url="http://127.0.0.1:8080", origin="http://127.0.0.1:8080", timeout=1)
    try:
        client.client.cookies.set("openscribe_session", "session-token")
        client.client.cookies.set("openscribe_csrf", "csrf-token")

        headers = client._headers("POST")

        assert headers["Cookie"] == "openscribe_session=session-token; openscribe_csrf=csrf-token"
        assert headers["X-CSRF-Token"] == "csrf-token"
        assert headers["Origin"] == "http://127.0.0.1:8080"
    finally:
        client.close()
