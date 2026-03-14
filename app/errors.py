from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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
