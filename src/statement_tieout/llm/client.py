"""The model client, behind one Protocol (ADR-004).

The provider is an installation parameter, not a build-time choice: this
project's deliverable is a function that reviewers run on their own machines
and that a customer may deploy into their own VPC, where the model is hosted
differently everywhere. Selecting it is an env var; nothing else in the
codebase knows which one is in use.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

__all__ = ["Completion", "LLMClient", "Usage", "build_client"]


@dataclass(frozen=True)
class Completion:
    """Raw content plus the token counts the cost model is built on (SPEC §8)."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class Usage:
    """What a run of the ladder spent. Accumulated, never estimated."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, completion: Completion, price: Price | None = None) -> None:
        self.calls += 1
        self.prompt_tokens += completion.prompt_tokens
        self.completion_tokens += completion.completion_tokens
        if price is not None:
            self.cost_usd += price.of(completion)


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens, as published by the vendor."""

    input_per_mtok: float
    output_per_mtok: float

    def of(self, completion: Completion) -> float:
        return (
            completion.prompt_tokens * self.input_per_mtok
            + completion.completion_tokens * self.output_per_mtok
        ) / 1_000_000


class LLMClient(Protocol):
    """Ask for one JSON object matching a schema. Sync: `extract()` is sync."""

    def complete_json(self, system: str, user: str, schema: dict) -> Completion: ...


def build_client() -> LLMClient | None:
    """The client `LLM_PROVIDER` selects, or None when nothing is configured.

    None is a first-class answer: every rung that uses a model is optional,
    and the deterministic path must run with no credential at all.
    """
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if not provider:
        provider = "openai_compatible" if os.environ.get("LLM_API_KEY") else ""
    if not provider:
        return None

    if provider == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient()
    if provider in ("openai_compatible", "deepseek", "openai"):
        from .openai_compatible import OpenAICompatibleClient

        return OpenAICompatibleClient()
    raise ValueError(f"unknown LLM_PROVIDER: {provider!r}")
