"""Async SQLAlchemy engine/session helpers with per-service schema search_path."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def build_dsn(*, host: str, port: int, db: str, user: str, password: str, driver: str = "postgresql+asyncpg") -> str:
    return f"{driver}://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}"


def create_engine(dsn: str, *, schema: str, pool_size: int = 5, echo: bool = False) -> AsyncEngine:
    return create_async_engine(
        dsn,
        pool_size=pool_size,
        max_overflow=5,
        pool_pre_ping=True,
        pool_timeout=5,
        echo=echo,
        connect_args={"server_settings": {"search_path": f"{schema},public", "application_name": f"sta-{schema}"}},
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def unit_of_work(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ping(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
