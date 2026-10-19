from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin

import pytest

from tests.accessibility.audit import blocked_request_reason, same_origin


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
