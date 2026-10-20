from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """All database models inherit from this."""
    pass


async def assert_runtime_role_is_restricted() -> None:
    """Refuse to start production with a role that bypasses RLS.

    Local development uses the ``finex`` owner role for convenience, but a
    deployed app must connect as ``finex_app`` (or an equivalent login role
    without SUPERUSER/BYPASSRLS).  Failing fast here prevents a configuration
    mistake from silently exposing every tenant's data.
    """
    if settings.APP_ENV.strip().lower() not in {"production", "prod"}:
        return
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user, rolsuper, rolbypassrls "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one_or_none()
    if row is None:
        raise RuntimeError("Could not determine the database runtime role")
    role, is_superuser, bypasses_rls = row
    if is_superuser or bypasses_rls:
        raise RuntimeError(
            f"Refusing to start in production as database role {role!r}: it "
            "bypasses Row Level Security. Set DATABASE_URL to the restricted "
            "finex_app role (and MIGRATION_DATABASE_URL to the owner role)."
        )


async def get_db():
    """Dependency: gives a request one DB session."""
    async with SessionLocal() as session:
        yield session