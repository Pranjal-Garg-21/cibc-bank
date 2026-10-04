"""Error shape for every API response: {code, message, request_id}. Never a stack trace, SQL or data."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("tiui")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def not_connected(feature: str) -> ApiError:
    return ApiError(501, "not_connected", f"{feature} is not connected in this build yet.")


def _body(request: Request, code: str, message: str) -> dict:
    return {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, e: ApiError):
        return JSONResponse(_body(request, e.code, e.message), status_code=e.status, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, e: RequestValidationError):
        fields = sorted({".".join(str(p) for p in err.get("loc", [])[1:]) for err in e.errors()})
        return JSONResponse(_body(request, "invalid_input", "Check: " + ", ".join(f for f in fields if f)),
                            status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "error")
        return JSONResponse(_body(request, code, "Not found." if e.status_code == 404 else "Request not allowed."),
                            status_code=e.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, e: Exception):
        log.error("unhandled error type=%s request_id=%s", type(e).__name__, getattr(request.state, "request_id", None))
        return JSONResponse(_body(request, "internal_error", "Something went wrong. Quote the request id to support."),
                            status_code=500)
