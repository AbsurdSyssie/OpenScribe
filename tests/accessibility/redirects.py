from __future__ import annotations

from urllib.parse import urljoin

from tests.accessibility.audit import blocked_request_reason, same_origin


_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_HOP_BY_HOP_HEADERS = {
    "cookie", "host", "content-length", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade",
}


def fetch_validated_redirect_chain(request_context, url: str, origin: str, method: str, headers: dict[str, str], data: bytes | None):
    """Fetch a request, following only prevalidated same-origin redirects.

    The browser-context API client commits Set-Cookie for each response before
    the next hop and shares the browser's cookie jar. Redirects are never
    delegated to Chromium, so a cross-origin Location is blocked before fetch.
    """
    request_headers = {key: value for key, value in headers.items() if key.lower() not in _HOP_BY_HOP_HEADERS}
    request_method, request_data = method, data
    for _ in range(11):
        if not same_origin(url, origin):
            return None, blocked_request_reason(url)
        response = request_context.fetch(
            url, method=request_method, headers=request_headers, data=request_data, max_redirects=0
        )
        if response.status not in _REDIRECT_STATUSES:
            return response, None
        location = response.headers.get("location")
        destination = urljoin(url, location or "")
        if not location or not same_origin(destination, origin):
            return None, blocked_request_reason(destination)
        if response.status in {301, 302} and request_method == "POST":
            request_method, request_data = "GET", None
        elif response.status == 303 and request_method not in {"GET", "HEAD"}:
            request_method, request_data = "GET", None
        if request_data is None:
            request_headers = {key: value for key, value in request_headers.items() if key.lower() != "content-type"}
        url = destination
    return None, "blocked redirect chain exceeding audit limit"
