"""FINEX backend application factory."""

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import auth, customers, dashboard, proposals
from app.core.config import get_settings
from app.core.db import assert_runtime_role_is_restricted, engine
from app.core.logging_filter import install_pii_filter
from app.core.middleware import RequestContextMiddleware
from app.services.errors import DomainError

settings = get_settings()


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

    @app.get("/")
    async def root():
        return {"app": settings.APP_NAME, "env": settings.APP_ENV}

    @app.get("/health")
    async def health():
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
