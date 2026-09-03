"""Any OpenAI-compatible chat endpoint — DeepSeek by default.

DeepSeek supports `json_object` but not strict `json_schema`, so the schema is
carried in the prompt and the answer is validated on our side. That is the
right posture regardless of provider: the model is never trusted to honour the
contract, and here it is also checked a second time by reconciliation.
"""

from __future__ import annotations

import os

from .client import Completion, Price

#: Verified on DeepSeek's pricing page, 2026-09-02 (peak, cache miss).
PRICES: dict[str, Price] = {
    "deepseek-v4-flash": Price(0.44, 1.32),
    "deepseek-v4-flash-vision-exp": Price(0.44, 1.32),
    "deepseek-v4-pro": Price(1.32, 3.96),
}

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

MAX_TOKENS = 8192
"""Generous: DeepSeek's default model reasons before answering, and the cap
covers reasoning *and* answer. Measured — at 2048 it produced 2048 tokens of
reasoning and an empty answer, and at 4096 it did the same."""


class OpenAICompatibleClient:
    def __init__(self, client=None):
        from openai import OpenAI

        self._model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self._client = client or OpenAI(
            api_key=os.environ.get("LLM_API_KEY"),
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            timeout=60.0,
        )

    @property
    def price(self) -> Price | None:
        return PRICES.get(self._model)

    def complete_json(self, system: str, user: str, schema: dict) -> Completion:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=MAX_TOKENS,
        )
        usage = response.usage
        return Completion(
            content=response.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
