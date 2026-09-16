"""Stable error codes and the standard error envelope (00_API_AND_DATA_CONTRACTS §1)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from sta_common.context import get_context
from sta_common.logging import get_logger
from sta_common.redaction import redact_text

log = get_logger(__name__)


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED_COVERAGE = "UNSUPPORTED_COVERAGE"
    POLICY_VALIDATION_FAILED = "POLICY_VALIDATION_FAILED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AUTHENTICATION_REQUIRED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.DEPENDENCY_TIMEOUT: 504,
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INSUFFICIENT_EVIDENCE: 422,
    ErrorCode.UNSUPPORTED_COVERAGE: 422,
    ErrorCode.POLICY_VALIDATION_FAILED: 422,
    ErrorCode.PRECONDITION_FAILED: 412,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.INTERNAL_ERROR: 500,
}

_RETRYABLE = {ErrorCode.RATE_LIMITED, ErrorCode.DEPENDENCY_TIMEOUT, ErrorCode.DEPENDENCY_UNAVAILABLE}


class FieldError(BaseModel):
    path: str
    code: str
    message: str | None = None


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    field_errors: list[FieldError] = Field(default_factory=list)
    retryable: bool = False
    retry_after_seconds: int | None = None


class AppError(Exception):
    """Raise anywhere; the handler maps it to the envelope without leaking internals."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        field_errors: list[FieldError] | None = None,
        retry_after_seconds: int | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field_errors = field_errors or []
        self.retry_after_seconds = retry_after_seconds
        self.status_code = status_code or _STATUS_BY_CODE[code]
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE


def error_envelope(err: ErrorBody, contract_version: str) -> dict[str, Any]:
    ctx = get_context()
    return {
        "error": err.model_dump(mode="json"),
        "meta": {
            "request_id": ctx.request_id,
            "correlation_id": ctx.correlation_id,
            "contract_version": contract_version,
        },
    }


def install_error_handlers(app: FastAPI, contract_version: str) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> ORJSONResponse:
        body = ErrorBody(
            code=exc.code,
            message=redact_text(exc.message),
            field_errors=exc.field_errors,
            retryable=exc.retryable,
            retry_after_seconds=exc.retry_after_seconds,
        )
        headers = {}
        if exc.retry_after_seconds is not None:
            headers["Retry-After"] = str(exc.retry_after_seconds)
        if exc.status_code >= 500:
            log.warning("app_error", error_code=exc.code, status=exc.status_code)
        return ORJSONResponse(error_envelope(body, contract_version), status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> ORJSONResponse:
        fields = [
            FieldError(
                path=".".join(str(p) for p in e.get("loc", ()) if p != "body"),
                code=str(e.get("type", "invalid")).upper(),
                message=redact_text(str(e.get("msg", ""))),
            )
            for e in exc.errors()
        ]
        body = ErrorBody(code=ErrorCode.VALIDATION_ERROR, message="Request validation failed", field_errors=fields)
        return ORJSONResponse(error_envelope(body, contract_version), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> ORJSONResponse:
        code = {
            401: ErrorCode.AUTHENTICATION_REQUIRED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            405: ErrorCode.VALIDATION_ERROR,
            409: ErrorCode.CONFLICT,
            413: ErrorCode.PAYLOAD_TOO_LARGE,
            429: ErrorCode.RATE_LIMITED,
        }.get(exc.status_code, ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.VALIDATION_ERROR)
        body = ErrorBody(code=code, message=redact_text(str(exc.detail)), retryable=code in _RETRYABLE)
        return ORJSONResponse(error_envelope(body, contract_version), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> ORJSONResponse:
        log.exception("unhandled_error", error_code=ErrorCode.INTERNAL_ERROR, status=500)
        body = ErrorBody(code=ErrorCode.INTERNAL_ERROR, message="An internal error occurred")
        return ORJSONResponse(error_envelope(body, contract_version), status_code=500)
