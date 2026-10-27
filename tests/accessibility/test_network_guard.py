from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

import pytest

from tests.accessibility.audit import blocked_request_reason, same_origin
from tests.accessibility.redirects import fetch_validated_redirect_chain


class _RedirectHandler(BaseHTTPRequestHandler):
    receiver_port = 0

    def do_GET(self):
        if self.path == "/ok":
            self.send_response(200)
            self.end_headers()
            return
        self.send_response(307)
        self.send_header("Location", f"http://127.0.0.1:{self.receiver_port}/receiver")
        self.end_headers()

    def do_POST(self):
        self.send_response(308)
        self.send_header("Location", f"http://127.0.0.1:{self.receiver_port}/receiver")
        self.end_headers()

    def log_message(self, *_args):
        pass


class _ReceiverHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        type(self).hits += 1
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


def _server(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.mark.accessibility_audit
def test_network_guard_blocks_external_307_and_308_before_receiver():
    playwright = pytest.importorskip("playwright.sync_api")
    receiver, receiver_thread = _server(_ReceiverHandler)
    _ReceiverHandler.hits = 0
    _RedirectHandler.receiver_port = receiver.server_port
    source, source_thread = _server(_RedirectHandler)
    origin = f"http://127.0.0.1:{source.server_port}"
    blocked: list[str] = []
    try:
        with playwright.sync_playwright() as runner:
            browser = runner.chromium.launch()
            context = browser.new_context(service_workers="block")

            def guard(route):
                if not same_origin(route.request.url, origin):
                    blocked.append(blocked_request_reason(route.request.url))
                    route.abort("blockedbyclient")
                    return
                request_url = route.request.url
                response = route.fetch(max_redirects=0)
                for _ in range(10):
                    if response.status not in {301, 302, 303, 307, 308}:
                        break
                    destination = urljoin(request_url, response.headers.get("location", ""))
                    if not same_origin(destination, origin):
                        blocked.append(blocked_request_reason(destination))
                        route.abort("blockedbyclient")
                        return
                    request_url = destination
                    response = route.fetch(url=request_url, max_redirects=0)
                route.fulfill(response=response)

            context.route("**/*", guard)
            page = context.new_page()
            with pytest.raises(Exception):
                page.goto(f"{origin}/redirect-307")
            page = context.new_page()
            page.goto(f"{origin}/ok")
            page.evaluate(
                """async (url) => {
                    try { await fetch(url, {method: 'POST', body: 'synthetic-only'}); } catch (_) {}
                }""",
                f"{origin}/redirect-308",
            )
            context.close()
            browser.close()
    finally:
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=2)
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=2)
    assert _ReceiverHandler.hits == 0
    assert blocked == [
        "blocked http request to non-audit origin",
        "blocked http request to non-audit origin",
    ]


@pytest.mark.accessibility_audit
def test_validated_redirect_helper_preserves_cookie_and_csrf_then_blocks_external_redirects():
    playwright = pytest.importorskip("playwright.sync_api")

    class RedirectContractHandler(BaseHTTPRequestHandler):
        receiver_port = 0
        observations: list[dict[str, str | None]] = []

        def _record(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode() if length else ""
            type(self).observations.append({
                "path": self.path,
                "method": self.command,
                "csrf": self.headers.get("X-CSRF-Token"),
                "cookie": self.headers.get("Cookie"),
                "body": body,
            })
            return body

        def do_POST(self):
            self._record()
            if self.path == "/start":
                self.send_response(303)
                self.send_header("Location", "/middle")
                self.send_header("Set-Cookie", "audit_session=synthetic; Path=/; HttpOnly")
                self.end_headers()
            else:
                self.send_response(400)
                self.end_headers()

        def do_GET(self):
            self._record()
            if self.path == "/middle":
                self.send_response(307)
                self.send_header("Location", "/final")
                self.end_headers()
            elif self.path == "/final":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            elif self.path.startswith("/external-"):
                self.send_response(308 if self.path.endswith("308") else 307)
                self.send_header("Location", f"http://127.0.0.1:{self.receiver_port}/receiver")
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_args):
            pass

    class Receiver(BaseHTTPRequestHandler):
        hits = 0

        def do_GET(self):
            type(self).hits += 1
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            pass

    receiver, receiver_thread = _server(Receiver)
    Receiver.hits = 0
    RedirectContractHandler.receiver_port = receiver.server_port
    RedirectContractHandler.observations = []
    source, source_thread = _server(RedirectContractHandler)
    origin = f"http://127.0.0.1:{source.server_port}"
    try:
        with playwright.sync_playwright() as runner:
            request_context = runner.request.new_context()
            response, blocked = fetch_validated_redirect_chain(
                request_context,
                f"{origin}/start",
                origin,
                "POST",
                {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-CSRF-Token": "synthetic-csrf",
                    "Cookie": "untrusted-stale-cookie=discard",
                },
                b"synthetic=body",
            )
            assert blocked is None and response is not None and response.status == 200
            observations = RedirectContractHandler.observations
            assert [entry["path"] for entry in observations] == ["/start", "/middle", "/final"]
            assert [entry["method"] for entry in observations] == ["POST", "GET", "GET"]
            assert observations[0]["body"] == "synthetic=body"
            for entry in observations:
                assert entry["csrf"] == "synthetic-csrf"
                assert "untrusted-stale-cookie" not in (entry["cookie"] or "")
            for entry in observations[1:]:
                assert "audit_session=synthetic" in (entry["cookie"] or "")
            assert observations[1]["body"] == observations[2]["body"] == ""

            for path in ("/external-307", "/external-308"):
                response, blocked = fetch_validated_redirect_chain(
                    request_context, f"{origin}{path}", origin, "GET", {}, None
                )
                assert response is None
                assert blocked == "blocked http request to non-audit origin"
            request_context.dispose()
    finally:
        source.shutdown()
        source.server_close()
        source_thread.join(timeout=2)
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join(timeout=2)
    assert Receiver.hits == 0
