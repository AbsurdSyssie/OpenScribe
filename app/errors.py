import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi.errors import RateLimitExceeded

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
    issues = []
    for issue in exc.errors():
        location = ".".join(str(part) for part in issue["loc"])
        issues.append(
            {
                "field": location,
                "message": issue["msg"],
                "type": issue["type"],
            }
        )
    return {"issues": issues}


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
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
    security_logger.warning(
        "rate_limit_exceeded",
        extra={
            "event": "rate_limit_exceeded",
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "limit": str(exc.detail) if exc.detail else None,
        },
    )
    is_api_path = request.url.path.startswith("/api/")
    if not is_api_path:
        return HTMLResponse(
            """
            <!DOCTYPE html>
            <html lang="en">
            <head>
              <meta charset="UTF-8">
              <meta name="viewport" content="width=device-width, initial-scale=1.0">
              <title>Too Many Requests</title>
              <style>
                body {
                  margin: 0;
                  min-height: 100vh;
                  display: grid;
                  place-items: center;
                  padding: 24px;
                  font-family: "Iowan Old Style", "Palatino Linotype", serif;
                  background: linear-gradient(180deg, #f8f3ea 0%, #f3efe6 100%);
                  color: #1b1d1f;
                }
                main {
                  width: min(620px, 100%);
                  background: #fffaf0;
                  border: 1px solid #d4c7ab;
                  border-radius: 24px;
                  padding: 28px;
                  box-shadow: 0 18px 60px rgba(76, 54, 30, 0.08);
                }
                a {
                  display: inline-block;
                  margin-top: 12px;
                  color: #7f251b;
                }
              </style>
            </head>
            <body>
              <main>
                <h1>Too many requests.</h1>
                <p>Please wait a moment and try again.</p>
                <p>This protection is temporary and is intended to slow repeated guessing attempts.</p>
                <a href="/login">Return to login</a>
              </main>
            </body>
            </html>
            """,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    return error_response(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", "Too many requests", details)
