"""Security headers, request ids, body size limit, same-origin and CSRF checks, refused-request logging (§9.3)."""
from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .security import SESSION_COOKIE, csrf_ok

MAX_BODY = 32 * 1024
UNSAFE = ("POST", "PUT", "PATCH", "DELETE")
# Writes allowed without a session (and so without a CSRF token): signing in.
CSRF_EXEMPT = ("/api/session/login",)

def csp(style_nonce: str | None = None) -> str:
    """The Content Security Policy. The app page gets a per-response nonce so the component library's scroll lock
    can add its one <style> element; nothing else inline is ever allowed (no 'unsafe-inline')."""
    style = f"'self' 'nonce-{style_nonce}'" if style_nonce else "'self'"
    return (f"default-src 'self'; script-src 'self'; style-src {style}; img-src 'self' data:; font-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


CSP = csp()


def _refuse(request: Request, status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, "request_id": request.state.request_id}, status_code=status)


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings):
        super().__init__(app)
        self.settings = settings
        # The Vite dev server proxies /api from another port; its origin is accepted only with UI_DEV=1.
        self.dev_origins = ("http://localhost:5173", "http://127.0.0.1:5173") if settings.dev else ()

    async def dispatch(self, request: Request, call_next):
        request.state.request_id = secrets.token_hex(8)
        request.state.user_id = None
        response = await self._checked(request, call_next)
        self._headers(request, response)
        if response.status_code in (401, 403, 429) and request.url.path.startswith("/api/"):
            request.app.state.access_log.record(action=f"{request.method} {request.url.path}",
                                                outcome=str(response.status_code), user_id=request.state.user_id,
                                                request_id=request.state.request_id)
        return response

    async def _checked(self, request: Request, call_next):
        if request.method in UNSAFE and request.url.path.startswith("/api/"):
            length = request.headers.get("content-length")
            if length is None or not length.isdigit() or int(length) > MAX_BODY:
                return _refuse(request, 413 if length and length.isdigit() else 411, "body_size",
                               "Request body missing a length or too large.")
            origin = request.headers.get("origin")
            same_origin = origin == f"{request.url.scheme}://{request.url.netloc}"
            if origin is not None and not same_origin and origin not in self.dev_origins:
                return _refuse(request, 403, "bad_origin", "Request refused: it did not come from this application.")
            if request.url.path not in CSRF_EXEMPT:
                session = request.app.state.sessions.get(request.cookies.get(SESSION_COOKIE), touch=False)
                if session is None:
                    return _refuse(request, 401, "not_signed_in", "Please sign in.")
                request.state.user_id = session.user_id
                if not csrf_ok(session, request.headers.get("x-csrf-token")):
                    return _refuse(request, 403, "bad_csrf", "Your session token is missing or out of date. Reload the page.")
        return await call_next(request)

    def _headers(self, request: Request, response) -> None:
        h = response.headers
        if "content-security-policy" not in h:  # the app page sets its own, with a nonce
            h["Content-Security-Policy"] = CSP
        h["X-Frame-Options"] = "DENY"
        h["X-Content-Type-Options"] = "nosniff"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), clipboard-read=(), payment=(), usb=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        h["X-Request-Id"] = request.state.request_id
        if request.url.path.startswith("/api/"):
            h["Cache-Control"] = "no-store"
            h["Pragma"] = "no-cache"
        if self.settings.https:
            h["Strict-Transport-Security"] = "max-age=31536000"
