"""Database models and initialisation for GHCR-Pulse."""
import os
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Allow override for local development / tests
DB_PATH = os.getenv("DB_PATH", "/data/stats.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-naive datetime (for SQLite compatibility)."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


engine = create_async_engine(DATABASE_URL, echo=False, future=True)
AsyncSessionLocal: sessionmaker[AsyncSession] = sessionmaker(  # type: ignore[type-arg]
    engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


class Repo(Base):
    """A tracked GHCR container image repository."""

    __tablename__ = "repos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    # Canonical "owner/name" identifier – must be unique
    full_name = Column(String(511), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class PullStat(Base):
    """A single pull-count data-point for a tracked repository."""

    __tablename__ = "pull_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    repo_id = Column(
        Integer, ForeignKey("repos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pull_count = Column(BigInteger, nullable=False)
    recorded_at = Column(DateTime, default=_utcnow, nullable=False, index=True)


async def init_db() -> None:
    """Create all tables if they do not yet exist."""
    # Ensure the data directory exists (important for first run)
    data_dir = os.path.dirname(DB_PATH)
    if data_dir:
        os.makedirs(data_dir, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
