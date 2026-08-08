"""Data access for `secrets`, backing the `list_secrets` tool. `name`
only, ever -- `encrypted_value` never leaves this repository layer (NFR-1,
plan §6.3). Nothing currently writes this table (backend has no
secret-creation flow yet); this repository is forward-compatible with
that, not blocked on it -- `list_secrets` just returns an empty list
until it exists."""

import uuid

from cloudagent_core.db.models import Secret
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class SecretRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_names_for_repo(self, repo_id: uuid.UUID) -> list[str]:
        result = await self._db.execute(select(Secret.name).where(Secret.repo_id == repo_id))
        return list(result.scalars())
