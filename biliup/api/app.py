from __future__ import annotations

import logging
import secrets
import sys
import traceback
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from biliup.config import config
from biliup.core import AppSettings
from biliup.core.redaction import redact_sensitive_text
from biliup.database import Database
from biliup.services import BackgroundJobManager, RecordingScheduler
from biliup.services.config_import import import_legacy_streamers

from .context import AppContext, load_effective_config
from .routers import auth, bilibili, configuration, files, logs, streamers, uploads, users

PUBLIC_PATHS = {
    "/v1/users/login",
    "/v1/users/register",
    "/v1/users/biliup",
    "/healthz",
    "/login",
    "/login.html",
}

redact_log_message = redact_sensitive_text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_log_message(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact_log_message("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        context: AppContext | None = getattr(request.app.state, "context", None)
        if not context or not context.settings.auth_enabled:
            return await call_next(request)
        path = request.url.path
        is_public = path in PUBLIC_PATHS or path.startswith("/_next/") or path.startswith("/favicon")
        if not is_public and path.startswith(("/v1/", "/bili/")) and not request.session.get("user_id"):
            from fastapi.responses import JSONResponse

            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


def _session_secret(paths, configured: str | None) -> str:
    if configured:
        return configured
    secret_file = paths.data / "session.secret"
    if secret_file.is_file():
        return secret_file.read_text(encoding="ascii").strip()
    value = secrets.token_urlsafe(48)
    secret_file.write_text(value, encoding="ascii")
    return value


def _configure_logging(paths, level: str, max_size_mb: int) -> tuple[list[logging.Handler], RotatingFileHandler]:
    log_file = paths.logs / "biliup.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handlers: list[logging.Handler] = [file_handler]
    if sys.stderr.isatty():
        handlers.insert(0, logging.StreamHandler())
    for handler in handlers:
        handler.addFilter(RedactingFilter())
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return handlers, file_handler


def _close_logging_handlers(handlers: list[logging.Handler]) -> None:
    root = logging.getLogger()
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()


def _frontend_file(frontend: Path, relative: str) -> Path | None:
    """Resolve regular files and Next.js static-export RSC paths."""
    relative_path = Path(relative)
    candidates = [
        frontend / relative_path,
        frontend / f"{relative}.html",
        frontend / relative_path / "index.html",
    ]

    # Next static export turns nested RSC files into dotted request URLs, e.g.
    # __next.!token.dashboard.__PAGE__.txt -> __next.!token/dashboard/__PAGE__.txt.
    name = relative_path.name
    if name.startswith("__next.") and name.endswith(".txt"):
        parts = name.removesuffix(".txt").split(".")
        if len(parts) > 2:
            rsc_path = relative_path.parent / ".".join(parts[:2])
            rsc_path = rsc_path.joinpath(*parts[2:-1], f"{parts[-1]}.txt")
            candidates.append(frontend / rsc_path)

    root = frontend.resolve()
    for candidate in candidates:
        target = candidate.resolve()
        if target.is_relative_to(root) and target.is_file():
            return target
    return None


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or AppSettings()
    paths = app_settings.paths()
    database = Database(paths.database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.migrate()
        effective_config = load_effective_config(database, app_settings, paths)
        config.replace(effective_config)
        import_legacy_streamers(database, effective_config)
        scheduler = RecordingScheduler(
            database,
            paths,
            config,
            enabled=app_settings.scheduler_enabled,
        )
        jobs = BackgroundJobManager(database)
        logging_handlers, log_handler = _configure_logging(
            paths,
            app_settings.log_level,
            effective_config.log_file_max_size_mb,
        )
        app.state.context = AppContext(
            app_settings,
            paths,
            database,
            config,
            scheduler,
            jobs,
            log_handler,
        )
        await scheduler.start()
        try:
            yield
        finally:
            await jobs.shutdown()
            await scheduler.stop()
            database.dispose()
            _close_logging_handlers(logging_handlers)

    app = FastAPI(title="biliup", version="1.1.7", lifespan=lifespan)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(paths, app_settings.session_secret),
        same_site="lax",
        https_only=False,
        max_age=86400,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in app_settings.cors_origin.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count"],
    )
    for router in (
        auth.router,
        configuration.router,
        streamers.router,
        uploads.router,
        users.router,
        bilibili.router,
        files.router,
        logs.router,
    ):
        app.include_router(router)

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/static/{file_path:path}")
    def static_file(file_path: str, request: Request):
        context: AppContext = request.app.state.context
        name = Path(file_path).name
        candidates = [context.paths.downloads / name, context.paths.logs / name]
        target = next((candidate for candidate in candidates if candidate.is_file()), None)
        if target is None:
            raise HTTPException(404, "file not found")
        return FileResponse(target)

    @app.api_route("/{page_path:path}", methods=["GET", "HEAD"])
    def frontend(page_path: str, request: Request):
        context: AppContext = request.app.state.context
        if not context.paths.frontend.is_dir():
            raise HTTPException(404, "frontend has not been built")
        relative = page_path.strip("/")
        target = _frontend_file(context.paths.frontend, relative or "index.html")
        if target:
            headers = None
            if relative.startswith("_next/static/"):
                headers = {"Cache-Control": "public, max-age=31536000, immutable"}
            return FileResponse(target, headers=headers)
        if relative.startswith("_next/") or "/__next." in f"/{relative}":
            raise HTTPException(404, "frontend asset not found")
        if relative != "login":
            return RedirectResponse("/login")
        raise HTTPException(404, "page not found")

    return app
