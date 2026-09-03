"""Claude, on whichever platform the installation is pointed at (ADR-004).

`LLM_PLATFORM` picks the client class; all four expose the same
`messages.create`, so the rest of this module is identical for every one.
"""

from __future__ import annotations

import os

from .client import Completion, Price, ToolCall, ToolTurn

#: Anthropic list prices per million tokens, verified 2026-09-02.
PRICES: dict[str, Price] = {
    "claude-opus-5": Price(5.0, 25.0),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-haiku-4-5": Price(1.0, 5.0),
}

DEFAULT_MODEL = "claude-opus-5"


class AnthropicClient:
    def __init__(self, client=None):
        self._model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self._client = client or _platform_client()

    @property
    def price(self) -> Price | None:
        return PRICES.get(self._model)

    def complete_json(self, system: str, user: str, schema: dict) -> Completion:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            content=text,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )

    def complete_with_tools(
        self, system: str, transcript: list[dict], tools: list[dict]
    ) -> ToolTurn:
        """One turn of the repair loop, in the Messages tool format."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=system,
            messages=_as_messages(transcript),
            tools=[
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "input_schema": tool["parameters"],
                }
                for tool in tools
            ],
        )
        text = "".join(b.text for b in response.content if b.type == "text")
        return ToolTurn(
            text=text,
            tool_calls=[
                ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
                for b in response.content
                if b.type == "tool_use"
            ],
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )



def _platform_client():
    platform = os.environ.get("LLM_PLATFORM", "anthropic").strip().lower()
    if platform == "bedrock":
        from anthropic import AnthropicBedrockMantle

        return AnthropicBedrockMantle(aws_region=os.environ["AWS_REGION"])
    if platform == "vertex":
        from anthropic import AnthropicVertex

        return AnthropicVertex(
            project_id=os.environ["GOOGLE_CLOUD_PROJECT"],
            region=os.environ.get("GOOGLE_CLOUD_REGION", "global"),
        )
    if platform == "foundry":
        from anthropic import AnthropicFoundry

        return AnthropicFoundry(
            api_key=os.environ["ANTHROPIC_API_KEY"], resource=os.environ["AZURE_RESOURCE"]
        )
    from anthropic import Anthropic

    return Anthropic()


def _as_messages(transcript: list[dict]) -> list[dict]:
    """Neutral entries -> Messages blocks, with consecutive tool results grouped.

    Grouping matters: the Messages API expects every tool_result for one
    assistant turn in a single user message.
    """
    messages: list[dict] = []
    pending: list[dict] = []

    def flush() -> None:
        if pending:
            messages.append({"role": "user", "content": list(pending)})
            pending.clear()

    for entry in transcript:
        if entry["role"] == "tool":
            pending.append(
                {
                    "type": "tool_result",
                    "tool_use_id": entry["call_id"],
                    "content": entry["text"],
                }
            )
            continue
        flush()
        if entry["role"] == "user":
            messages.append({"role": "user", "content": entry["text"]})
        else:
            blocks: list[dict] = []
            if entry.get("text"):
                blocks.append({"type": "text", "text": entry["text"]})
            blocks.extend(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in entry.get("tool_calls") or []
            )
            messages.append(
                {"role": "assistant", "content": blocks or [{"type": "text", "text": " "}]}
            )
    flush()
    return messages
