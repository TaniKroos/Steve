"""DTOs for GitHub App installation / repo listing (see routers/github.py,
services/github_service.py)."""

import uuid

from pydantic import BaseModel, ConfigDict


class RepoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner: str
    name: str
    default_branch: str
    private: bool
