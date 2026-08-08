"""
AzureOpenAIClient(LLMPort): talks to an Azure OpenAI resource. Same
underlying wire protocol as any OpenAI-compatible host (all the actual
streaming/translation logic lives in `OpenAICompatibleClient`), but
construction genuinely differs from a plain OpenAI-compatible endpoint in
three ways the official `openai` SDK's `AsyncAzureOpenAI` client exists
specifically to handle, so this uses that rather than trying to coerce
`AsyncOpenAI(base_url=...)` into working:

  - Auth is an `api-key` header, not `Authorization: Bearer <key>`.
  - Routing is by *deployment name* (a name you chose when deploying a
    model in the Azure portal), not by model name in the request body --
    `deployment` here is passed as `model=` on every call the same way
    `LlamaClient` passes a model name, but Azure interprets it as a
    deployment id.
  - Every request needs an `api_version` query param pinning which
    Azure OpenAI API revision to speak.
"""

from openai import AsyncAzureOpenAI

from app.llm.openai_compatible import OpenAICompatibleClient


class AzureOpenAIClient(OpenAICompatibleClient):
    def __init__(
        self, api_key: str, azure_endpoint: str, deployment: str, api_version: str, max_tokens: int = 8192
    ) -> None:
        client = AsyncAzureOpenAI(api_key=api_key, azure_endpoint=azure_endpoint, api_version=api_version)
        super().__init__(client, deployment, max_tokens)
