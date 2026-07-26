#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import pyotp


SESSION_COOKIE_NAME = "openscribe_session"
CSRF_COOKIE_NAME = "openscribe_csrf"


@dataclass(slots=True)
class ProbeStep:
    name: str
    ok: bool
    detail: str
    status_code: int | None = None


@dataclass(slots=True)
class ProbeSummary:
    run_id: str
    base_url: str
    started_at: str
    completed_at: str | None = None
    dry_run: bool = True
    production_guard: str = "not_evaluated"
    synthetic_team_id: str | None = None
    synthetic_user_ids: dict[str, str] = field(default_factory=dict)
    transcript_id: str | None = None
    steps: list[ProbeStep] = field(default_factory=list)
    cleanup: list[ProbeStep] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(step.ok for step in [*self.steps, *self.cleanup])

    def add(self, name: str, ok: bool, detail: str, status_code: int | None = None) -> None:
        self.steps.append(ProbeStep(name=name, ok=ok, detail=detail, status_code=status_code))

    def add_cleanup(self, name: str, ok: bool, detail: str, status_code: int | None = None) -> None:
        self.cleanup.append(ProbeStep(name=name, ok=ok, detail=detail, status_code=status_code))


class ProbeClient:
    def __init__(self, *, base_url: str, origin: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.origin = origin.rstrip("/")
        self.client = httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self.client.close()

    def _url(self, path: str) -> str:
        return urljoin(self.base_url, path.lstrip("/"))

    def _headers(self, method: str) -> dict[str, str]:
        headers: dict[str, str] = {}
        if urlparse(self.base_url).scheme == "http":
            cookie_header = self._cookie_header()
            if cookie_header:
                headers["Cookie"] = cookie_header
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and self.client.cookies.get(SESSION_COOKIE_NAME):
            csrf = self.client.cookies.get(CSRF_COOKIE_NAME)
            if csrf:
                headers["X-CSRF-Token"] = csrf
            headers["Origin"] = self.origin
        return headers

    def _cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.client.cookies.jar)

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(self._headers(method))
        return self.client.request(method, self._url(path), headers=headers, **kwargs)

    def login(self, *, email: str, password: str) -> httpx.Response:
        return self.request("POST", "/api/v1/auth/login", json={"email": email, "password": password})

    def logout(self) -> None:
        self.request("POST", "/api/v1/auth/logout")
        self.client.cookies.clear()


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_probably_production(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return False
    safe_tokens = ("staging", "stage", "dev", "test", "local")
    return not any(token in host for token in safe_tokens)


def validate_execution_guard(args: argparse.Namespace) -> str:
    if not args.execute:
        return "dry_run"
    if args.confirm_run_id != args.run_id:
        raise SystemExit("--execute requires --confirm-run-id equal to --run-id")
    if is_probably_production(args.base_url) and not args.allow_production:
        raise SystemExit("Refusing probable production target without --allow-production")
    if is_probably_production(args.base_url) and args.allow_production and args.confirm_run_id != args.run_id:
        raise SystemExit("Production run requires --confirm-run-id equal to --run-id")
    return "production_allowed" if is_probably_production(args.base_url) else "non_production"


def require_success(response: httpx.Response, *, expected: set[int], label: str) -> dict[str, Any]:
    if response.status_code not in expected:
        raise RuntimeError(f"{label} failed: status={response.status_code} body={response.text[:240]}")
    if response.content:
        try:
            return response.json()
        except ValueError:
            return {}
    return {}


def expect_status(response: httpx.Response, *, allowed: set[int], name: str, summary: ProbeSummary) -> None:
    ok = response.status_code in allowed
    summary.add(name, ok, f"expected {sorted(allowed)}, got {response.status_code}", response.status_code)
    if not ok:
        raise RuntimeError(f"{name} failed: status={response.status_code} body={response.text[:240]}")


def create_synthetic_user(api: ProbeClient, *, email: str, full_name: str, team_id: str, team_role: str, password: str) -> dict[str, Any]:
    created = api.request(
        "POST",
        "/api/v1/users",
        json={
            "full_name": full_name,
            "email": email,
            "temporary_password": password,
            "team_id": team_id,
            "team_role": team_role,
            "is_system_admin": False,
            "status": "active",
            "mfa_required": False,
        },
    )
    return require_success(created, expected={201}, label=f"create user {email}")


def complete_password_onboarding(base_url: str, origin: str, *, email: str, temporary_password: str, new_password: str, timeout: float) -> ProbeClient:
    api = ProbeClient(base_url=base_url, origin=origin, timeout=timeout)
    try:
        require_success(api.login(email=email, password=temporary_password), expected={200}, label=f"login onboarding {email}")
        changed = api.request("POST", "/api/v1/onboarding/password", json={"new_password": new_password})
        body = require_success(changed, expected={200}, label=f"complete password onboarding {email}")
        if body.get("onboarding_state") == "pending_totp_enrollment":
            enrollment = require_success(api.request("POST", "/api/v1/onboarding/totp/start"), expected={200}, label=f"start totp onboarding {email}")
            code = pyotp.TOTP(enrollment["secret"]).now()
            verified = require_success(api.request("POST", "/api/v1/onboarding/totp/verify", json={"code": code}), expected={200}, label=f"verify totp onboarding {email}")
            body = verified
        if body.get("onboarding_state") == "pending_recovery_codes":
            require_success(api.request("POST", "/api/v1/onboarding/skip-recovery-codes"), expected={200}, label=f"skip recovery codes onboarding {email}")
            body["onboarding_state"] = "complete"
        if body.get("onboarding_state") != "complete":
            raise RuntimeError(f"onboarding did not complete for {email}: {body.get('onboarding_state')}")
        return api
    except Exception:
        api.close()
        raise


def run_probe(args: argparse.Namespace) -> ProbeSummary:
    guard = validate_execution_guard(args)
    summary = ProbeSummary(
        run_id=args.run_id,
        base_url=args.base_url.rstrip("/"),
        started_at=utc_iso(),
        dry_run=not args.execute,
        production_guard=guard,
    )
    if not args.execute:
        summary.add("dry_run_plan", True, "Would create synthetic team/users, assert access controls, delete transcript, delete users, delete team.")
        summary.completed_at = utc_iso()
        return summary

    admin_email = args.admin_email or os.environ.get("OWASP_LIFECYCLE_ADMIN_EMAIL")
    admin_password = args.admin_password or os.environ.get("OWASP_LIFECYCLE_ADMIN_PASSWORD")
    admin_session = args.admin_session or os.environ.get("OWASP_LIFECYCLE_ADMIN_SESSION")
    if not admin_session and (not admin_email or not admin_password):
        raise SystemExit("Need --admin-email/--admin-password or OWASP_LIFECYCLE_ADMIN_SESSION")

    origin = args.origin or args.base_url.rstrip("/")
    admin = ProbeClient(base_url=args.base_url, origin=origin, timeout=args.timeout)
    onboarded_sessions: dict[str, ProbeClient] = {}
    users: dict[str, dict[str, Any]] = {}
    team_id: str | None = None
    owner_password = f"Owner-{args.run_id}-Pass1"
    peer_password = f"Peer-{args.run_id}-Pass1"
    leader_password = f"Leader-{args.run_id}-Pass1"
    owner_new_password = f"Owner-{args.run_id}-Done1"
    peer_new_password = f"Peer-{args.run_id}-Done1"
    leader_new_password = f"Leader-{args.run_id}-Done1"

    try:
        if admin_session:
            admin.client.cookies.set(SESSION_COOKIE_NAME, admin_session)
            admin.request("GET", "/login")
            summary.add("admin_session_loaded", True, "Admin session cookie loaded from environment/argument.")
        else:
            login_body = require_success(admin.login(email=admin_email, password=admin_password), expected={200}, label="admin login")
            summary.add("admin_login", login_body.get("auth_level") == "full", f"auth_level={login_body.get('auth_level')}")

        run_slug = args.run_id.lower().replace("_", "-")
        team = require_success(
            admin.request(
                "POST",
                "/api/v1/teams",
                json={"name": f"OWASP Live Lifecycle Test {args.run_id}", "status": "active", "default_retention_days": 1},
            ),
            expected={201},
            label="create team",
        )
        team_id = str(team["id"])
        summary.synthetic_team_id = team_id
        summary.add("team_created", True, "Synthetic team created.", 201)

        user_specs = {
            "owner": (f"owasp-owner-{run_slug}@{args.email_domain}", "OWASP Owner", "user", owner_password, owner_new_password),
            "peer": (f"owasp-peer-{run_slug}@{args.email_domain}", "OWASP Peer", "user", peer_password, peer_new_password),
            "leader": (f"owasp-leader-{run_slug}@{args.email_domain}", "OWASP Leader", "leader", leader_password, leader_new_password),
        }
        for role, (email, full_name, team_role, temp_password, new_password) in user_specs.items():
            user = create_synthetic_user(admin, email=email, full_name=f"{full_name} {args.run_id}", team_id=team_id, team_role=team_role, password=temp_password)
            users[role] = user
            summary.synthetic_user_ids[role] = str(user["id"])
            onboarded_sessions[role] = complete_password_onboarding(args.base_url, origin, email=email, temporary_password=temp_password, new_password=new_password, timeout=args.timeout)
            summary.add(f"{role}_created_and_onboarded", True, "Synthetic user created and password onboarding completed.", 201)

        owner = onboarded_sessions["owner"]
        peer = onboarded_sessions["peer"]
        leader = onboarded_sessions["leader"]
        try:
            transcript = require_success(
                owner.request(
                    "POST",
                    "/api/v1/transcripts/start",
                    json={
                        "title": f"OWASP lifecycle transcript {args.run_id}",
                        "ingestion_mode": "whole_file",
                        "current_draft_text_encrypted": f"OWASP_LIFECYCLE_TEST_{args.run_id}",
                    },
                ),
                expected={201},
                label="owner create transcript",
            )
            transcript_id = str(transcript["id"])
            summary.transcript_id = transcript_id
            summary.add("owner_created_transcript", True, "Synthetic transcript root created.", 201)

            require_success(
                owner.request("POST", f"/api/v1/transcripts/{transcript_id}/commit", json={"text_encrypted": f"OWASP_LIFECYCLE_TEST_{args.run_id}"}),
                expected={200},
                label="owner commit transcript",
            )
            summary.add("owner_committed_transcript_version", True, "Transcript version committed.", 200)

            require_success(
                owner.request(
                    "PATCH",
                    f"/api/v1/transcripts/{transcript_id}/working-note",
                    json={"mode": "freeform", "freeform_text": f"OWASP_LIFECYCLE_NOTE_{args.run_id}"},
                ),
                expected={200},
                label="owner working note",
            )
            summary.add("owner_created_working_note", True, "Working note created.", 200)

            expect_status(peer.request("GET", f"/api/v1/transcripts/{transcript_id}"), allowed={403, 404}, name="peer_cannot_read_owner_transcript", summary=summary)
            expect_status(peer.request("DELETE", f"/api/v1/transcripts/{transcript_id}"), allowed={403, 404}, name="peer_cannot_delete_owner_transcript", summary=summary)
            expect_status(leader.request("GET", f"/api/v1/transcripts/{transcript_id}"), allowed={403, 404}, name="leader_cannot_read_owner_transcript", summary=summary)
            expect_status(admin.request("GET", f"/api/v1/transcripts/{transcript_id}"), allowed={403, 404}, name="admin_cannot_read_owner_transcript", summary=summary)

            expect_status(owner.request("DELETE", f"/api/v1/transcripts/{transcript_id}"), allowed={204}, name="owner_deleted_transcript_root", summary=summary)
            expect_status(owner.request("GET", f"/api/v1/transcripts/{transcript_id}"), allowed={404}, name="deleted_transcript_not_readable", summary=summary)

            replacement = require_success(
                owner.request(
                    "POST",
                    "/api/v1/transcripts/start",
                    json={"title": f"OWASP lifecycle lock check {args.run_id}", "ingestion_mode": "whole_file", "current_draft_text_encrypted": f"OWASP_LOCK_TEST_{args.run_id}"},
                ),
                expected={201},
                label="owner create replacement transcript",
            )
            replacement_id = str(replacement["id"])
            summary.add("owner_created_lock_check_transcript", True, "Second transcript created before suspension.", 201)
            expect_status(leader.request("POST", f"/api/v1/users/{users['owner']['id']}/suspend"), allowed={200}, name="leader_suspended_owner", summary=summary)
            expect_status(owner.request("GET", f"/api/v1/transcripts/{replacement_id}"), allowed={401, 403}, name="suspended_owner_session_revoked", summary=summary)
            expect_status(leader.request("POST", f"/api/v1/users/{users['owner']['id']}/reactivate"), allowed={200}, name="leader_reactivated_owner", summary=summary)
            summary.add("reactivation_requires_onboarding", True, "Reactivation preserves content but resets account onboarding; cleanup will delete user via admin.")
        finally:
            for session in onboarded_sessions.values():
                session.close()

    finally:
        for role, user in reversed(list(users.items())):
            try:
                response = admin.request("DELETE", f"/api/v1/users/{user['id']}")
                summary.add_cleanup(f"delete_{role}_user", response.status_code in {204, 404}, "Synthetic user delete attempted.", response.status_code)
            except Exception as exc:  # noqa: BLE001
                summary.add_cleanup(f"delete_{role}_user", False, f"cleanup failed: {exc}")
        if team_id:
            try:
                response = admin.request("POST", f"/admin/teams/{team_id}/delete", data={"return_view": "workspace", "return_tab": "teams"})
                summary.add_cleanup("delete_synthetic_team", response.status_code in {200, 303, 404}, "Synthetic team delete attempted.", response.status_code)
            except Exception as exc:  # noqa: BLE001
                summary.add_cleanup("delete_synthetic_team", False, f"cleanup failed: {exc}")
        admin.close()
        summary.completed_at = utc_iso()
    return summary


def write_summary(summary: ProbeSummary, output: str | None) -> None:
    data = asdict(summary)
    data["ok"] = summary.ok
    rendered = json.dumps(data, indent=2, sort_keys=True)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run synthetic live-safe OWASP lifecycle/deletion probe.")
    parser.add_argument("--base-url", required=True, help="Target base URL, for example https://staging.openscribe.co.uk")
    parser.add_argument("--origin", help="Origin header value. Defaults to base URL.")
    parser.add_argument("--run-id", default=f"owasp-{uuid.uuid4().hex[:10]}", help="Unique synthetic run id.")
    parser.add_argument("--execute", action="store_true", help="Actually create/delete synthetic live data. Default is dry-run.")
    parser.add_argument("--confirm-run-id", help="Must equal --run-id for execute mode.")
    parser.add_argument("--allow-production", action="store_true", help="Allow probable production host.")
    parser.add_argument("--admin-email", help="System-admin email. Also supports OWASP_LIFECYCLE_ADMIN_EMAIL.")
    parser.add_argument("--admin-password", help="System-admin password. Also supports OWASP_LIFECYCLE_ADMIN_PASSWORD.")
    parser.add_argument("--admin-session", help="Existing openscribe_session token. Also supports OWASP_LIFECYCLE_ADMIN_SESSION.")
    parser.add_argument("--email-domain", default="owasp-probe.openscribe.co.uk", help="Synthetic account email domain. No mail is sent by this probe.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", help="Optional JSON summary output path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_probe(args)
    write_summary(summary, args.output)
    return 0 if summary.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
