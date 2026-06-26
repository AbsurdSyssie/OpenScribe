import logging
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi.errors import RateLimitExceeded

from app.db import SessionLocal
from app.services.security_audit import record_security_event

security_logger = logging.getLogger("openscribe.security")


@dataclass(slots=True)
class AppError(Exception):
    status_code: int
    code: str
    message: str
    details: dict[str, Any] | None = None


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if details:
        payload["error"]["details"] = details
    return payload


def error_response(status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_payload(code, message, details))


def validation_details(exc: RequestValidationError) -> dict[str, Any]:
    return {"issue_count": len(exc.errors())}


def _retry_after_seconds(limit_detail: object | None) -> str:
    if limit_detail is None:
        return "60"
    match = re.search(r"per\s+(\d+)?\s*(second|minute|hour|day)", str(limit_detail).lower())
    if not match:
        return "60"
    amount = int(match.group(1) or "1")
    unit_seconds = {
        "second": 1,
        "minute": 60,
        "hour": 60 * 60,
        "day": 60 * 60 * 24,
    }[match.group(2)]
    return str(amount * unit_seconds)


def _security_relevant_validation_issues(exc: RequestValidationError) -> list[dict[str, Any]]:
    security_markers = (
        "remote stt endpoints must use https",
        "remote llm endpoints must use https",
        "remote de-identification endpoints must use https",
        "secret-bearing de-identification headers must use bearer_token/vault storage",
        "secret-bearing de-identification body fields must use bearer_token/vault storage",
    )
    issues: list[dict[str, Any]] = []
    for issue in exc.errors():
        message = str(issue.get("msg", ""))
        normalized_message = message.lower()
        if not any(marker in normalized_message for marker in security_markers):
            continue
        issues.append(
            {
                "field": ".".join(str(part) for part in issue.get("loc", ())),
                "type": issue.get("type"),
                "message": message,
            }
        )
    return issues


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    security_issues = _security_relevant_validation_issues(exc)
    if security_issues:
        session_factory = getattr(request.app.state, "db_session_factory", SessionLocal)
        try:
            with session_factory() as db:
                record_security_event(
                    db,
                    action="security_validation_rejected",
                    request=request,
                    details={
                        "category": "validation",
                        "outcome": "blocked",
                        "reason_code": "security_relevant_validation",
                        "status_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
                        "issues": security_issues,
                    },
                )
        except Exception:
            security_logger.exception(
                "security_validation_audit_failed",
                extra={"path": request.url.path, "method": request.method},
            )
    return error_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        validation_details(exc),
    )


async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return error_response(exc.status_code, "business_rule_violation", str(exc.detail))


async def rate_limit_error_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    details = {"limit": str(exc.detail)} if exc.detail else None
    headers = {"Retry-After": _retry_after_seconds(exc.detail)}
    return_path = "/login"
    return_label = "Return to login"
    if request.url.path.startswith("/transcribe"):
        return_path = "/transcribe"
        return_label = "Return to transcription workspace"
    elif request.url.path.startswith("/request-access"):
        return_path = "/request-access"
        return_label = "Return to request access"
    security_logger.warning(
        "rate_limit_exceeded",
        extra={
            "event": "rate_limit_exceeded",
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "limit": str(exc.detail) if exc.detail else None,
            "rate_limit_subject": getattr(request.state, "rate_limit_subject", None),
        },
    )
    session_factory = getattr(request.app.state, "db_session_factory", SessionLocal)
    try:
        with session_factory() as db:
            record_security_event(
                db,
                action="rate_limit_exceeded",
                request=request,
                details={
                    "category": "rate_limit",
                    "outcome": "blocked",
                    "reason_code": "rate_limited",
                    "status_code": status.HTTP_429_TOO_MANY_REQUESTS,
                    "limit": str(exc.detail) if exc.detail else None,
                    "rate_limit_subject": getattr(request.state, "rate_limit_subject", None),
                },
            )
    except Exception:
        security_logger.exception(
            "rate_limit_audit_failed",
            extra={"path": request.url.path, "method": request.method},
        )
    is_api_path = request.url.path.startswith("/api/")
    if not is_api_path:
        return HTMLResponse(
            f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="UTF-8">
              <meta name="viewport" content="width=device-width, initial-scale=1.0">
              <title>Too Many Requests</title>
            </head>
            <body>
              <main>
                <h1>Too many requests.</h1>
                <p>Please wait a moment and try again.</p>
                <p>This protection is temporary and is intended to slow repeated or automated requests.</p>
                <a href="{return_path}">{return_label}</a>
              </main>
            </body>
            </html>
            """,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=headers,
        )
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=error_payload("rate_limited", "Too many requests", details),
        headers=headers,
    )
