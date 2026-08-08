"""
Agent-loop-only settings, layered on `cloudagent_core.settings.CoreSettings`
(database_url, github_app_id, github_app_private_key_path) the same way
backend/app/config.py does. See claude/agent-loop-plan.md §11 for the
full env var list this mirrors.
"""

from functools import lru_cache
from typing import Literal

from cloudagent_core.settings import CoreSettings


class Settings(CoreSettings):
    # --- Redis: session ownership/heartbeat/instance-registry (§5) and
    # the SSE event publish side (§9), same Redis instance backend uses ---
    redis_url: str

    # --- Backend <-> Agent Loop internal RPC -- same secret backend's
    # AgentLoopClient sends ---
    internal_shared_secret: str

    # --- LLM provider selection (§4.4) -- exactly one `if`, in main.py's
    # lifespan, picks the concrete adapter; nothing else branches on this ---
    llm_provider: Literal["anthropic", "llama", "azure_openai"] = "anthropic"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5"
    anthropic_max_tokens: int = 8192

    llama_api_key: str | None = None
    llama_base_url: str = "https://api.together.xyz/v1"  # or Groq/Fireworks/a local Ollama server
    llama_model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    llama_max_tokens: int = 8192

    # --- Azure OpenAI -- a real, distinct wire protocol from the generic
    # OpenAI-compatible `llama` path above (api-key header instead of
    # bearer auth, deployment-based routing, a required api_version) --
    # see llm/azure_openai_client.py for why it's a separate adapter ---
    azure_openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None  # e.g. https://<resource-name>.openai.azure.com
    azure_openai_deployment: str | None = None  # the deployment name you chose in the Azure portal, not a model name
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_max_tokens: int = 8192

    # --- Sandbox connectivity -- same E2B project backend's
    # SandboxOrchestrator provisions into, but used execution-plane-only
    # here (see e2b_sandbox.py) ---
    e2b_api_key: str
    e2b_sandbox_template: str

    # --- Instance identity/routing (§5.1) -- the URL this instance
    # advertises to backend for direct routing when Fly's internal-DNS
    # pattern doesn't apply (i.e. local dev; see loop/ownership.py's
    # resolve_instance_identity) ---
    agent_loop_public_url: str = "http://localhost:8001"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
