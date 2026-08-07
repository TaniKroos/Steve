"""
Repository layer: one class per DB aggregate (User, GithubInstallation,
Repo, Session, Sandbox), each wrapping the SQLAlchemy queries for that
table and nothing else.

The rule that keeps this layer honest: a repository method may contain
a query, but never a business decision. "Does this user own this repo?"
is a business decision -- it belongs in a service (see services/), which
calls a repository's plain `get(...)` and then checks the result itself.
If you find an `if` statement here that isn't about *how to fetch/store
data* (pagination, filtering, upsert-vs-insert), it's probably grown
into the wrong layer.

Every repository takes an `AsyncSession` via its constructor -- see
dependencies.py for where that comes from per-request. None of them
import FastAPI, HTTP status codes, or Pydantic schemas; they only know
about SQLAlchemy and the ORM models in cloudagent_core.db.models.
"""
