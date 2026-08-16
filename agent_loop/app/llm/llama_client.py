"""
LlamaClient(LLMPort): talks to whatever generic OpenAI-compatible endpoint
is configured (Together AI, Groq, Fireworks, a local Ollama server -- see
claude/agent-loop-plan.md §4.4). All the actual protocol logic (streaming,
message/tool translation, retry-on-generation-failure) lives in
`OpenAICompatibleClient` -- this class only knows how to construct the
right SDK client for a bare `base_url` + API key.
"""

from openai import AsyncOpenAI

from app.llm.openai_compatible import OpenAICompatibleClient


class LlamaClient(OpenAICompatibleClient):
    def __init__(self, api_key: str, base_url: str, model: str, max_tokens: int = 8192) -> None:
        super().__init__(AsyncOpenAI(api_key=api_key, base_url=base_url), model, max_tokens)
