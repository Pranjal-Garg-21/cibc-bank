"""App factory. Run from the repo root:

    uvicorn tiui.main:app --app-dir ui --host 127.0.0.1 --port 8000 --no-server-header

Serves /api/* and the built React app (ui/web/dist). See ui/README.md.
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import errors
from .accounts import AccountStore, SharedDemoAccounts
from .audit import AccessLog
from .middleware import SecurityMiddleware, csp
from .routers import ask, identity, nba, next_action, oversight, overview, session, status, tables, team
from .security import RateLimiter, SessionStore
from .settings import Settings, from_env


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.access_log.close()

    app = FastAPI(lifespan=lifespan, title="TrustIssues UI API", docs_url="/api/docs" if settings.dev else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if settings.dev else None)
    app.state.settings = settings
    app.state.sessions = SessionStore(settings.idle_minutes * 60 if settings.session_timeouts else None,
                                     settings.max_hours * 3600 if settings.session_timeouts else None)
    app.state.limiter = RateLimiter()
    app.state.accounts = (SharedDemoAccounts(settings.demo_password) if settings.demo_password is not None
                          else AccountStore(settings.accounts_file, mfa_key=settings.mfa_key))
    app.state.access_log = AccessLog(settings.ui_audit)
    app.state.ask_kwargs = None
    errors.install(app)
    app.add_middleware(SecurityMiddleware, settings=settings)
    for r in (session, ask, nba, next_action, overview, oversight, identity, status, tables, team):
        app.include_router(r.router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    dist = settings.dist_dir
    if (dist / "index.html").is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"code": "not_found", "message": "Not found."}, status_code=404)
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            nonce = secrets.token_urlsafe(16)
            html = (dist / "index.html").read_text(encoding="utf-8").replace("__CSP_NONCE__", nonce)
            return HTMLResponse(html, headers={"Cache-Control": "no-store", "Content-Security-Policy": csp(nonce)})
    return app


app = create_app()
