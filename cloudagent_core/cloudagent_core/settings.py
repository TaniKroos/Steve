"""
Settings shared by every service in this project.

Why this lives in cloudagent_core (and not duplicated in backend/ and,
later, agent_loop/): both services need to know the database URL and the
GitHub App's identity (App ID + private key) to do their jobs. If each
service defined these independently, it would be easy for them to drift
apart -- e.g. backend pointing at one Postgres instance, agent_loop at
another -- a bug that's invisible until it very much isn't. Defining these
fields once here, and having each service's own settings *inherit* from
this class, means a service can only ever add fields, never redefine
these particular ones.

Concretely: backend/app/config.py does

    class Settings(CoreSettings):
        redis_url: str
        session_secret_key: str
        ...

and pydantic-settings merges "read this field from the environment"
behavior across the whole inheritance chain automatically -- you don't
have to wire that up by hand.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    # pydantic-settings reads each field's value from a process
    # environment variable of the same name (case-insensitive), falling
    # back to a local `.env` file if the variable isn't set in the
    # environment. This is what makes `DATABASE_URL=...` in a shell, or
    # in `.env`, "just become" `self.database_url` -- with type validation
    # for free. Get a required field wrong (e.g. leave DATABASE_URL unset
    # with no default) and the app refuses to start with a clear error
    # message, instead of failing confusingly the first time it's used.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # A subclass (like backend's Settings) will see env vars this base
        # class doesn't declare fields for -- "ignore" instead of "forbid"
        # so that's not an error at this layer.
        extra="ignore",
    )

    # SQLAlchemy async connection string, e.g.
    # postgresql+asyncpg://user:pass@host:5432/dbname
    database_url: str

    # --- GitHub App identity, used by cloudagent_core.github_app.GithubApp ---
    # The numeric ID GitHub assigned when the App was registered.
    github_app_id: str
    # Path to the App's private key (.pem), not the key content itself --
    # this lets the key be mounted as a secret *file* in production rather
    # than stuffed whole into an env var.
    github_app_private_key_path: str
