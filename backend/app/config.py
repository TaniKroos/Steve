"""
Backend-only settings, layered on top of cloudagent_core's CoreSettings
(database_url, github_app_id, github_app_private_key_path -- see that
file for why those live in the shared package instead of here).

Import `get_settings()` everywhere you need config -- not `Settings()`
directly. It's wrapped in `functools.lru_cache` so the environment is
only parsed/validated once per process, and a plain zero-argument
callable is exactly what FastAPI's `Depends()` wants (see
dependencies.py).
"""

from functools import lru_cache

from cloudagent_core.settings import CoreSettings


class Settings(CoreSettings):
    # --- Redis: the SSE pub/sub transport (flow 05 in the comms diagram) ---
    redis_url: str

    # --- Our own login session cookie ---
    # Starlette's SessionMiddleware (wired up in main.py) signs the cookie
    # with this key, using itsdangerous, so a browser can't forge or
    # tamper with its contents. Rotating this key invalidates every
    # existing session -- treat it like any other secret, never commit it.
    session_secret_key: str

    # --- "Sign in with GitHub" OAuth app ---
    # A *separate* GitHub registration from the GitHub App (github_app_id
    # above) -- this one only answers "who is this person", it grants no
    # repo access. See Requirements/requirements.md FR-1.
    github_oauth_client_id: str
    github_oauth_client_secret: str

    # --- GitHub App webhook signature verification ---
    github_app_webhook_secret: str

    # --- GitHub App's public slug, e.g. "cloudagent-dev" ---
    # Used only to build the "install this App" URL
    # (github.com/apps/<slug>/installations/new) -- distinct from
    # `github_app_id` (the numeric ID used for JWT signing, in CoreSettings).
    github_app_slug: str

    # --- Sandbox provisioning (SandboxOrchestrator, management-plane only) ---
    e2b_api_key: str
    e2b_sandbox_template: str

    # --- Agent Loop: the other backend service (currently not built --
    # AgentLoopClient will just fail to connect until it exists) ---
    agent_loop_base_url: str
    internal_shared_secret: str

    # --- Frontend/backend origins, for CORS and building OAuth redirect URLs ---
    frontend_base_url: str
    backend_base_url: str


@lru_cache
def get_settings() -> Settings:
    # Values come entirely from the environment / .env file (see
    # CoreSettings' model_config) -- Settings() with no kwargs is correct
    # here, not a bug.
    return Settings()  # type: ignore[call-arg]
