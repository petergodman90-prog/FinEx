"""FINEX backend application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import auth, customers, dashboard, proposals
from app.core.config import get_settings
from app.core.db import assert_runtime_role_is_restricted, engine
from app.core.logging_filter import install_pii_filter
from app.core.middleware import RequestContextMiddleware
from app.services.errors import DomainError

settings = get_settings()

# backend/app/main.py -> repository root (index.html, css/, js/, pages/, assets/)
REPO_ROOT = Path(__file__).resolve().parents[2]


def create_app() -> FastAPI:
    install_pii_filter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail fast if the database is unreachable.
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        # In production, never run as a role that silently bypasses RLS.
        await assert_runtime_role_is_restricted()
        yield
        await engine.dispose()

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Baseline browser hardening and PII-safe caching for the API."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response_path = request.url.path
        if response_path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif response_path == "/" or response_path.endswith(".html"):
            # The UI is not content-hashed. Revalidate the shell on every visit
            # so a deploy cannot leave an old dashboard open in a browser tab.
            response.headers.setdefault("Cache-Control", "no-store")
        elif response_path.endswith((".js", ".css")):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # Cross-origin access is opt-in and never a wildcard: production serves the
    # UI and API from one origin (reverse proxy), so CORS_ORIGINS stays empty.
    cors_origins = [
        origin.strip()
        for origin in settings.CORS_ORIGINS.split(",")
        if origin.strip()
    ]
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(DomainError)
    async def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        content: dict = {"detail": str(exc), "code": exc.code}
        if exc.details:
            content["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content=content)

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth.router)
    api.include_router(customers.router)
    api.include_router(proposals.router)
    api.include_router(dashboard.router)
    app.include_router(api)

    @app.get("/health")
    async def health():
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    # Same-origin UI: only the public frontend directories are mounted. Never
    # mount the repository root, because it contains .env, backend/ and db/.
    index_file = REPO_ROOT / "index.html"
    if settings.SERVE_FRONTEND and index_file.is_file():
        @app.get("/", include_in_schema=False)
        @app.get("/index.html", include_in_schema=False)
        async def index_page():
            return FileResponse(index_file)

        for directory in ("assets", "css", "js", "pages"):
            directory_path = REPO_ROOT / directory
            if directory_path.is_dir():
                app.mount(
                    f"/{directory}",
                    StaticFiles(directory=directory_path),
                    name=f"web-{directory}",
                )
    else:
        @app.get("/")
        async def root():
            return {"app": settings.APP_NAME, "env": settings.APP_ENV}

    return app


app = create_app()
