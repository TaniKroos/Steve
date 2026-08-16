"""
AzureOpenAIClient(LLMPort): talks to an Azure OpenAI resource. Same
underlying wire protocol as any OpenAI-compatible host once connected
(all the actual streaming/translation logic lives in
`OpenAICompatibleClient`), but Azure genuinely has *two* different
generations of how you connect at all, and this class picks the right
one from the shape of `azure_endpoint` rather than making the operator
know the difference:

  - **Classic** (`https://<resource>.openai.azure.com`, no `/openai/v1`
    suffix): needs the `openai` SDK's dedicated `AsyncAzureOpenAI`
    client -- auth via an `api-key` header (not bearer), routing by
    *deployment name* baked into the URL path, and a required
    `api_version` query param pinning which Azure API revision to speak.
  - **v1** (`https://<resource>.openai.azure.com/openai/v1/` or the
    `.services.ai.azure.com` equivalent -- Azure's newer API generation,
    GA since August 2025, confirmed against Microsoft's own docs): this
    is deliberately wire-compatible with the plain `AsyncOpenAI` client
    -- `base_url` + bearer-style `api_key`, `model=` is the deployment
    name, and no `api_version` at all. Constructing `AsyncAzureOpenAI`
    against a v1-shaped endpoint would double up the URL path (the SDK
    appends its own `/openai/deployments/{id}/...` on top of an endpoint
    that already ends in `/openai/v1/`), so this branch uses the plain
    client instead, the same one `LlamaClient` already uses for every
    other OpenAI-compatible host.
"""

from openai import AsyncAzureOpenAI, AsyncOpenAI

from app.llm.openai_compatible import OpenAICompatibleClient


class AzureOpenAIClient(OpenAICompatibleClient):
    def __init__(
        self, api_key: str, azure_endpoint: str, deployment: str, api_version: str, max_tokens: int = 8192
    ) -> None:
        if _is_v1_endpoint(azure_endpoint):
            client = AsyncOpenAI(api_key=api_key, base_url=azure_endpoint)
        else:
            client = AsyncAzureOpenAI(api_key=api_key, azure_endpoint=azure_endpoint, api_version=api_version)
        # Newer OpenAI/Azure model generations reject the classic
        # `max_tokens` field on Chat Completions outright (400:
        # "'max_tokens' is not supported with this model. Use
        # 'max_completion_tokens' instead" -- confirmed against a real
        # gpt-5.1-chat deployment) -- unlike LlamaClient's hosts, which
        # still expect the classic name.
        super().__init__(client, deployment, max_tokens, max_tokens_param="max_completion_tokens")


def _is_v1_endpoint(azure_endpoint: str) -> bool:
    return azure_endpoint.rstrip("/").endswith("/openai/v1")
