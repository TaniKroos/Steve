"""
SQLAlchemy ORM models -- the single source of truth for the database
schema. Alembic (see cloudagent_core/alembic/) reads `Base.metadata` from
this file to autogenerate migrations, so this file and the actual
database schema should never be able to drift apart for long.

Both `backend` and (later) `agent_loop` import these same classes, so a
`Session` row means exactly the same thing in both services -- there's no
translation layer or duplicated schema to keep in sync by hand.

--- A short SQLAlchemy 2.0 primer, for anyone new to it ---

- `Mapped[X]` is a type hint that also tells SQLAlchemy "this Python
  attribute maps to a database column, and its type is derived from X".
  `Mapped[str | None]` means a nullable text column, for example.
- `mapped_column(...)` is where you configure the actual column further:
  type overrides, defaults, foreign keys, uniqueness, indexing.
- `relationship(...)` is *not* a column. It's a Python-side convenience
  for navigating between related objects (e.g. `session.user` instead of
  a manual join/query) -- SQLAlchemy resolves it lazily via a separate
  query unless you explicitly ask it to eager-load.
- Every table below uses a random UUID primary key rather than an
  auto-incrementing integer, via the `_uuid_pk()` helper. Two reasons:
  it doesn't leak "how many rows exist" the way a sequential ID does, and
  it can be generated client-side (e.g. by agent_loop before a DB round
  trip) if that's ever useful, which a DB-assigned integer can't be.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Every mapped model below inherits from this. SQLAlchemy uses
    `Base.metadata` (which collects every subclass's table definition) as
    the thing Alembic compares the live database against when
    autogenerating a migration.

    `type_annotation_map` overrides what a bare `Mapped[datetime]`
    compiles to, project-wide: `DateTime(timezone=True)` instead of
    SQLAlchemy's plain (timezone-naive) default. This exists because of a
    real bug, not preemptively -- `SandboxOrchestrator.provision()`
    assigns `datetime.now(timezone.utc)` (timezone-*aware*) to
    `Sandbox.expires_at`, and without this override that column would be
    a naive `TIMESTAMP`, which asyncpg flatly refuses to accept an aware
    datetime for. Every `*_at` column in this file follows the same
    convention (assigned via `datetime.now(timezone.utc)` wherever the
    app sets one at all), so fixing it once here, for every column,
    beats patching individual columns as each one happens to get
    exercised for the first time.
    """

    type_annotation_map = {datetime: DateTime(timezone=True)}


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    # GitHub's own numeric user ID -- stable even if the user renames
    # their GitHub account, which `github_login` is not.
    github_user_id: Mapped[int] = mapped_column(unique=True, index=True)
    github_login: Mapped[str]
    email: Mapped[str | None]
    avatar_url: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    installations: Mapped[list["GithubInstallation"]] = relationship(back_populates="user")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user")


class GithubInstallation(Base):
    """One row per GitHub App installation -- roughly, one row per GitHub
    org/user account that has installed the App and granted it access to
    some repos."""

    __tablename__ = "github_installations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    installation_id: Mapped[int] = mapped_column(unique=True, index=True)  # GitHub's own ID for this installation
    account_login: Mapped[str]
    account_type: Mapped[str]  # "User" or "Organization", verbatim from GitHub's API
    suspended_at: Mapped[datetime | None]
    # When we last re-fetched this installation's repo list from GitHub.
    # NULL until the first sync ever runs. GithubService checks this
    # against a staleness threshold before deciding whether a repo-list
    # read needs to trigger a fresh GitHub call -- see
    # GithubService.list_repos_for_user(). Timezone-aware via
    # `Base.type_annotation_map` (see the comment on `Base` above).
    last_synced_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="installations")
    repos: Mapped[list["Repo"]] = relationship(back_populates="installation")


class Repo(Base):
    __tablename__ = "repos"

    id: Mapped[uuid.UUID] = _uuid_pk()
    installation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("github_installations.id"), index=True)
    github_repo_id: Mapped[int] = mapped_column(unique=True, index=True)
    owner: Mapped[str]
    name: Mapped[str]
    default_branch: Mapped[str]
    private: Mapped[bool]
    last_synced_at: Mapped[datetime] = mapped_column(server_default=func.now())

    installation: Mapped["GithubInstallation"] = relationship(back_populates="repos")
    sessions: Mapped[list["Session"]] = relationship(back_populates="repo")


class Session(Base):
    """One chat session against one repo.

    Naming note: this clashes in spirit with SQLAlchemy's own `Session`
    class (the DB connection / unit-of-work object used throughout this
    codebase as `AsyncSession`). The two are never imported unqualified
    into the same file, so it hasn't caused an actual bug, but it's worth
    knowing about the moment you see the word "Session" and pausing on
    which one is meant.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"), index=True)
    # One of: starting | running | blocked | idle | closed.
    # Deliberately a plain string column for now rather than a DB enum --
    # easy to widen the state machine later without a migration that
    # touches the column type. See .Arch/backend-lld-plan.md §3 (State
    # pattern) for where this might graduate to a real class.
    status: Mapped[str] = mapped_column(default="starting")
    title: Mapped[str | None]
    branch_name: Mapped[str | None]
    pr_number: Mapped[int | None]
    pr_url: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    last_active_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="sessions")
    repo: Mapped["Repo"] = relationship(back_populates="sessions")
    sandboxes: Mapped[list["Sandbox"]] = relationship(back_populates="session")
    messages: Mapped[list["Message"]] = relationship(back_populates="session")


class Sandbox(Base):
    """Many-to-one against Session, on purpose: resuming a session after
    its sandbox was torn down spins up a *new* sandbox row rather than
    reusing the old one, so a session's full sandbox history stays
    visible instead of being overwritten."""

    __tablename__ = "sandboxes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    e2b_sandbox_id: Mapped[str]
    status: Mapped[str] = mapped_column(default="creating")  # creating | running | paused | terminated
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime | None]
    token_expires_at: Mapped[datetime | None]
    terminated_at: Mapped[datetime | None]

    session: Mapped["Session"] = relationship(back_populates="sandboxes")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("session_id", "sequence_no"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    role: Mapped[str]  # "user" | "assistant" | "tool"
    # A list of provider-neutral content blocks -- NOT any single vendor's
    # raw wire format. Each element is one of:
    #   {"type": "text", "text": "..."}
    #   {"type": "tool_use", "id": "...", "name": "...", "input": {...}}
    #   {"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": bool}
    # Both agent_loop/app/llm/anthropic_client.py and llama_client.py
    # translate *to* this shape when persisting and *from* it when
    # reconstructing history for the next LLM call -- see
    # claude/agent-loop-plan.md §4.3 for why this exists: storing a
    # single provider's native format would permanently couple this
    # column to whichever LLM was chosen first, which stopped being an
    # acceptable trade the moment supporting more than one provider
    # became a real goal instead of a hypothetical.
    content: Mapped[dict] = mapped_column(JSON)
    sequence_no: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    session: Mapped["Session"] = relationship(back_populates="messages")
    tool_calls: Mapped[list["ToolCall"]] = relationship(back_populates="message")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = _uuid_pk()
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), index=True)
    tool_name: Mapped[str]
    tool_use_id: Mapped[str]  # Anthropic's own id for this specific tool_use content block
    input: Mapped[dict] = mapped_column(JSON)
    output: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="pending")  # pending | success | error
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    message: Mapped["Message"] = relationship(back_populates="tool_calls")


class Secret(Base):
    """A repo-scoped secret (e.g. an API key a session's sandbox needs).

    Only `name` is ever exposed to the LLM/tool layer (the `list_secrets`
    tool, per .Arch/architecture-plan.md §4) -- `encrypted_value` goes
    straight from this table into the sandbox's environment variables at
    creation time, bypassing LLM context entirely. See
    Requirements/requirements.md NFR-1.
    """

    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id"), index=True)
    name: Mapped[str]
    encrypted_value: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
