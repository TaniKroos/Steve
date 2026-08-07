"""DTOs for the login flow (see routers/auth.py, services/auth_service.py)."""

import uuid

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    # `from_attributes=True` (Pydantic v2's replacement for v1's
    # `orm_mode`) lets us build this straight from a SQLAlchemy `User`
    # object -- `UserResponse.model_validate(user_row)` -- instead of
    # manually copying each field across.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    github_login: str
    email: str | None
    avatar_url: str | None
