"""LLM provider abstraction — Claude and OpenAI, switchable via config."""

from abc import ABC, abstractmethod

import yaml

from engine.context import get_config_path


class LLMProvider(ABC):
    provider: str  # "claude" or "openai"
    model: str
    max_tokens: int

    @abstractmethod
    def generate(self, prompt: str, system: str | None = None) -> str:
        """Send a prompt to the LLM and return the text response."""


class ClaudeProvider(LLMProvider):
    provider = "claude"

    def __init__(self, model: str = "claude-sonnet-4-20250514", max_tokens: int = 16384):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def generate(self, prompt: str, system: str | None = None) -> str:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        response = self.client.messages.create(**kwargs)
        return response.content[0].text


class OpenAIProvider(LLMProvider):
    provider = "openai"

    def __init__(self, model: str = "gpt-4o", max_tokens: int = 16384):
        import openai

        self.client = openai.OpenAI()
        self.model = model
        self.max_tokens = max_tokens

    def generate(self, prompt: str, system: str | None = None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self.client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=messages,
        )
        return response.choices[0].message.content


def get_provider(config_path: str | None = None) -> LLMProvider:
    """Factory: return the LLM provider specified in config."""
    if config_path is None:
        config_path = str(get_config_path())
    with open(config_path) as f:
        config = yaml.safe_load(f)

    llm = config["llm"]
    provider_name = llm["provider"]

    max_tokens = llm.get("max_tokens", 16384)

    if provider_name == "claude":
        return ClaudeProvider(model=llm["claude"]["model"], max_tokens=max_tokens)
    elif provider_name == "openai":
        return OpenAIProvider(model=llm["openai"]["model"], max_tokens=max_tokens)
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")
