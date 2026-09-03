"""Any OpenAI-compatible chat endpoint — DeepSeek by default.

DeepSeek supports `json_object` but not strict `json_schema`, so the schema is
carried in the prompt and the answer is validated on our side. That is the
right posture regardless of provider: the model is never trusted to honour the
contract, and here it is also checked a second time by reconciliation.
"""

from __future__ import annotations

import json
import os

from ..reconcile.agent import arguments_of
from .client import Completion, Price, ToolCall, ToolTurn

#: Verified on DeepSeek's pricing page, 2026-09-02 (peak, cache miss).
PRICES: dict[str, Price] = {
    "deepseek-v4-flash": Price(0.44, 1.32),
    "deepseek-v4-flash-vision-exp": Price(0.44, 1.32),
    "deepseek-v4-pro": Price(1.32, 3.96),
}

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"

MAX_TOKENS = 2048
"""Ample once reasoning is off: a layout profile is under a hundred tokens."""

DEFAULT_THINKING = "disabled"
"""Measured against DeepSeek: with reasoning on, this task never converged —
8192 output tokens of reasoning and an empty answer, 115 s and $0.0119 per
call. With it off: 84 tokens, 2 s. Set LLM_THINKING=enabled to restore it."""


class OpenAICompatibleClient:
    def __init__(self, client=None):
        from openai import OpenAI

        self._model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self._thinking = os.environ.get("LLM_THINKING", DEFAULT_THINKING)
        self._client = client or OpenAI(
            api_key=os.environ.get("LLM_API_KEY"),
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            timeout=60.0,
        )

    @property
    def price(self) -> Price | None:
        return PRICES.get(self._model)

    def complete_json(self, system: str, user: str, schema: dict) -> Completion:
        # No strict schema mode here, so the schema travels in the prompt and
        # the answer is validated on our side. A schema the model is never
        # shown is a schema it invents.
        instructions = (
            f"{system}\n\nReturn JSON matching exactly this schema — the same "
            f"property names, no others:\n{json.dumps(schema)}"
        )
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=MAX_TOKENS,
            extra_body={"thinking": {"type": self._thinking}},
        )
        usage = response.usage
        return Completion(
            content=response.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    def complete_with_tools(
        self, system: str, transcript: list[dict], tools: list[dict]
    ) -> ToolTurn:
        """One turn of the repair loop, in the chat-completions tool format."""
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, *_as_messages(transcript)],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["parameters"],
                    },
                }
                for tool in tools
            ],
            temperature=0,
            max_tokens=MAX_TOKENS,
            extra_body={"thinking": {"type": self._thinking}},
        )
        message = response.choices[0].message
        usage = response.usage
        return ToolTurn(
            text=message.content or "",
            tool_calls=[
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=arguments_of(call.function.arguments),
                )
                for call in (message.tool_calls or [])
            ],
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )


def _as_messages(transcript: list[dict]) -> list[dict]:
    """Neutral entries -> chat-completions messages."""
    messages = []
    for entry in transcript:
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry["text"]})
        elif entry["role"] == "assistant":
            message: dict = {"role": "assistant", "content": entry.get("text") or None}
            calls = entry.get("tool_calls") or []
            if calls:
                message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in calls
                ]
            messages.append(message)
        else:
            messages.append(
                {"role": "tool", "tool_call_id": entry["call_id"], "content": entry["text"]}
            )
    return messages
