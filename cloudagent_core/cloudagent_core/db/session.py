"""
Async SQLAlchemy engine and session-factory helpers.

Naming heads-up (same note as in models.py): "session" here means
SQLAlchemy's unit-of-work object -- a live DB connection plus a batch of
pending changes -- not our own `Session` ORM model. The two concepts
share a name by SQLAlchemy convention, not by our choice.

Both `backend` and `agent_loop` build their *own* engine by calling
`create_engine(their_own_settings.database_url)`, but both engines point
at the same schema defined in `models.py`. This module only provides the
plumbing; it holds no state of its own.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        database_url,
        echo=False,  # flip to True locally if you want every generated SQL statement printed
        pool_pre_ping=True,  # checks a pooled connection is still alive before reusing it
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        # By default SQLAlchemy "expires" (forgets) every loaded attribute
        # on an object right after a commit, forcing a re-fetch on next
        # access. That's surprising in a request/response cycle where you
        # commonly want to read an object's fields *after* committing it
        # (e.g. to build the JSON response). Turning this off trades a
        # small staleness risk for a lot less confusion -- fine at this
        # project's scale (single-writer-per-request), per
        # Requirements/requirements.md NFR-10.
        expire_on_commit=False,
    )


@asynccontextmanager
async def db_session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Open a session, commit if the `with` block finishes cleanly, roll
    back if it raises. This is the thing FastAPI's `Depends(get_db)`
    wraps in backend/app/dependencies.py -- every request gets exactly one
    of these, and it's guaranteed to either commit or roll back, never
    left half-applied."""
    async with session_factory() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
