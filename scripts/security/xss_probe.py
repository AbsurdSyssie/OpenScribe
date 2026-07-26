#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from html import escape
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


PAYLOADS: tuple[str, ...] = (
    '"><u data-xss-probe="probe">PROBE</u>',
    "'\"><img src=x data-xss-probe=\"img\">",
)
HTML_TAG_RE = re.compile(r"<(script|img|svg|iframe|u)\b", re.IGNORECASE)
CSRF_COOKIE_NAME = "openscribe_csrf"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    detail: str
    evidence: str | None = None


def body_contains_unescaped_payload(body: str, payload: str) -> bool:
    if payload in body:
        return True
    for match in HTML_TAG_RE.finditer(payload):
        tag = match.group(1).lower()
        if f"<{tag}" in body.lower():
            return True
    return False


def body_contains_only_escaped_payload(body: str, payload: str) -> bool:
    return escape(payload, quote=True) in body and payload not in body


def extract_csrf_token(body: str) -> str | None:
    match = re.search(r'name="_csrf_token"\s+value="([^"]+)"', body)
    if match:
        return match.group(1)
    return None


def expect_no_html_injection(body: str, payload: str) -> tuple[bool, str]:
    if body_contains_unescaped_payload(body, payload):
        return False, "payload reflected as live HTML or raw markup"
    if body_contains_only_escaped_payload(body, payload):
        return True, "payload only present in escaped form"
    return True, "payload not reflected"


def request_page(client: httpx.Client, base_url: str, path: str) -> httpx.Response:
    return client.get(urljoin(base_url, path), follow_redirects=True)


def post_form(client: httpx.Client, base_url: str, path: str, data: dict[str, str]) -> httpx.Response:
    return client.post(urljoin(base_url, path), data=data, follow_redirects=True)


def csrf_headers(client: httpx.Client, base_url: str) -> dict[str, str]:
    token = client.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        raise RuntimeError("authenticated CSRF cookie is unavailable after login")
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError("base URL must include scheme and host")
    return {
        "Origin": f"{parsed.scheme}://{parsed.netloc}",
        "X-CSRF-Token": token,
    }


def public_request_access_probe(client: httpx.Client, base_url: str, payload: str) -> ProbeResult:
    page = request_page(client, base_url, "/request-access")
    csrf_token = extract_csrf_token(page.text)
    if not csrf_token:
        return ProbeResult("public_request_access", False, "csrf token not found on request-access page")
    email = f"xss-{uuid.uuid4().hex[:8]}@example.com"
    response = post_form(
        client,
        base_url,
        "/request-access",
        {
            "requested_name": f"Probe {payload}",
            "requested_email": email,
            "requested_team_name": f"Team {payload}",
            "request_details": f"Details {payload}",
            "_csrf_token": csrf_token,
        },
    )
    ok, detail = expect_no_html_injection(response.text, payload)
    return ProbeResult("public_request_access", ok, detail, evidence=email if ok else payload)


def public_login_probe(client: httpx.Client, base_url: str, payload: str) -> ProbeResult:
    page = request_page(client, base_url, "/login")
    csrf_token = extract_csrf_token(page.text)
    if not csrf_token:
        return ProbeResult("public_login", False, "csrf token not found on login page")
    response = post_form(
        client,
        base_url,
        "/login",
        {
            "email": payload,
            "password": "wrongpass1",
            "_csrf_token": csrf_token,
        },
    )
    ok, detail = expect_no_html_injection(response.text, payload)
    return ProbeResult("public_login", ok, detail)


def api_login(client: httpx.Client, base_url: str, *, email: str, password: str) -> dict[str, Any]:
    response = client.post(
        urljoin(base_url, "/api/v1/auth/login"),
        json={"email": email, "password": password},
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    auth_level = payload.get("auth_level")
    if auth_level != "full":
        raise RuntimeError(
            "authenticated XSS suite requires a full session; use a localhost seeded development account without MFA "
            f"(received auth_level={auth_level!r})"
        )
    # Login rotates the session-bound HttpOnly CSRF cookie. httpx can send its
    # value as the required header even though browser JavaScript cannot read it.
    csrf_headers(client, base_url)
    return payload


def authenticated_personal_template_probe(
    client: httpx.Client,
    base_url: str,
    payload: str,
) -> ProbeResult:
    name = f"Template {payload}"
    create = client.post(
        urljoin(base_url, "/api/v1/templates/personal"),
        json={
            "scope": "user",
            "name": name,
            "description": f"Desc {payload}",
            "prompt_text": "Write a note.",
            "mode": "freeform",
            "is_active": True,
        },
        headers=csrf_headers(client, base_url),
        follow_redirects=True,
    )
    if create.status_code >= 400:
        return ProbeResult("personal_template_create", False, f"template create failed: {create.status_code}", create.text[:200])
    template_id = create.json().get("id")
    try:
        response = request_page(client, base_url, "/workspace/library/templates")
        ok, detail = expect_no_html_injection(response.text, payload)
        return ProbeResult("personal_template_render", ok, detail, evidence=str(template_id) if template_id else None)
    finally:
        if template_id:
            client.delete(
                urljoin(base_url, f"/api/v1/templates/personal/{template_id}"),
                headers=csrf_headers(client, base_url),
                follow_redirects=True,
            )


def authenticated_personal_quick_action_probe(
    client: httpx.Client,
    base_url: str,
    payload: str,
) -> ProbeResult:
    name = f"Quick Action {payload}"
    create = client.post(
        urljoin(base_url, "/api/v1/quick-actions/personal"),
        json={
            "scope": "user",
            "name": name,
            "description": f"Desc {payload}",
            "prompt_text": "Create a follow-up.",
            "is_active": True,
        },
        headers=csrf_headers(client, base_url),
        follow_redirects=True,
    )
    if create.status_code >= 400:
        return ProbeResult("personal_quick_action_create", False, f"quick action create failed: {create.status_code}", create.text[:200])
    quick_action_id = create.json().get("id")
    try:
        response = request_page(client, base_url, "/workspace/library/quick-actions")
        ok, detail = expect_no_html_injection(response.text, payload)
        return ProbeResult("personal_quick_action_render", ok, detail, evidence=str(quick_action_id) if quick_action_id else None)
    finally:
        if quick_action_id:
            client.delete(
                urljoin(base_url, f"/api/v1/quick-actions/personal/{quick_action_id}"),
                headers=csrf_headers(client, base_url),
                follow_redirects=True,
            )


def authenticated_transcript_title_probe(
    client: httpx.Client,
    base_url: str,
    payload: str,
) -> ProbeResult:
    create = client.post(
        urljoin(base_url, "/api/v1/transcripts/start"),
        json={"title": f"Transcript {payload}"},
        headers=csrf_headers(client, base_url),
        follow_redirects=True,
    )
    if create.status_code >= 400:
        return ProbeResult("transcript_create", False, f"transcript start failed: {create.status_code}", create.text[:200])
    transcript_id = create.json().get("id")
    try:
        response = request_page(client, base_url, f"/workspace?transcript_id={transcript_id}")
        ok, detail = expect_no_html_injection(response.text, payload)
        return ProbeResult("transcript_title_render", ok, detail, evidence=str(transcript_id) if transcript_id else None)
    finally:
        if transcript_id:
            client.delete(
                urljoin(base_url, f"/api/v1/transcripts/{transcript_id}"),
                headers=csrf_headers(client, base_url),
                follow_redirects=True,
            )


def run_public_suite(client: httpx.Client, base_url: str) -> list[ProbeResult]:
    results: list[ProbeResult] = []
    for payload in PAYLOADS:
        results.append(public_request_access_probe(client, base_url, payload))
        results.append(public_login_probe(client, base_url, payload))
    return results


def run_authenticated_suite(client: httpx.Client, base_url: str, *, email: str, password: str) -> list[ProbeResult]:
    api_login(client, base_url, email=email, password=password)
    results: list[ProbeResult] = []
    for payload in PAYLOADS:
        results.append(authenticated_personal_template_probe(client, base_url, payload))
        results.append(authenticated_personal_quick_action_probe(client, base_url, payload))
        results.append(authenticated_transcript_title_probe(client, base_url, payload))
    return results


def print_results(results: list[ProbeResult], *, as_json: bool) -> int:
    if as_json:
        import json

        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            status = "ok" if result.ok else "FAIL"
            extra = f" | evidence={result.evidence}" if result.evidence else ""
            print(f"{result.name} | {status} | {result.detail}{extra}")
    return 0 if all(result.ok for result in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe OpenScribe pages for basic reflected/stored XSS behavior.")
    parser.add_argument("--base-url", default=os.getenv("OPENSCRIBE_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--suite", choices=("public", "authenticated", "all"), default="public")
    parser.add_argument("--email", default=os.getenv("OPENSCRIBE_EMAIL"))
    parser.add_argument("--password", default=os.getenv("OPENSCRIBE_PASSWORD"))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    client = httpx.Client(timeout=20.0, verify=True)
    try:
        results: list[ProbeResult] = []
        if args.suite in {"public", "all"}:
            results.extend(run_public_suite(client, args.base_url))
        if args.suite in {"authenticated", "all"}:
            if not args.email or not args.password:
                print("Authenticated suite requires --email and --password or OPENSCRIBE_EMAIL/OPENSCRIBE_PASSWORD.", file=sys.stderr)
                return 2
            results.extend(run_authenticated_suite(client, args.base_url, email=args.email, password=args.password))
        return print_results(results, as_json=args.as_json)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"XSS probe failed: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
