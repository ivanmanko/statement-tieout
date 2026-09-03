"""Rendering the neutral transcript into each provider's wire format (ADR-004).

One repair loop serves every provider because the transcript it keeps is
provider-neutral. What differs is only this translation, so this is where it
is pinned.
"""

import json
from dataclasses import dataclass, field

import pytest

from statement_tieout.llm.anthropic_client import AnthropicClient
from statement_tieout.llm.client import ToolCall
from statement_tieout.llm.openai_compatible import OpenAICompatibleClient

TOOLS = [{"name": "state", "description": "check", "parameters": {"type": "object"}}]

TRANSCRIPT = [
    {"role": "user", "text": "it does not reconcile"},
    {"role": "assistant", "text": "let me look",
     "tool_calls": [ToolCall(id="c0", name="state", arguments={})]},
    {"role": "tool", "call_id": "c0", "name": "state", "text": "residual -200.00"},
]


# ------------------------------------------------------------------ OpenAI-compatible


@dataclass
class _OaiFn:
    name: str
    arguments: str


@dataclass
class _OaiCall:
    id: str
    function: _OaiFn
    type: str = "function"


@dataclass
class _OaiMessage:
    content: str | None = "thinking"
    tool_calls: list = field(default_factory=list)


@dataclass
class _OaiCompletions:
    message: _OaiMessage = field(default_factory=_OaiMessage)
    calls: list = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)

        @dataclass
        class _Usage:
            prompt_tokens: int = 7
            completion_tokens: int = 3

        @dataclass
        class _Choice:
            message: object

        @dataclass
        class _Response:
            choices: list
            usage: _Usage

        return _Response([_Choice(self.message)], _Usage())


def oai(message=None):
    sdk = type("SDK", (), {})()
    sdk.chat = type("Chat", (), {})()
    sdk.chat.completions = _OaiCompletions(message or _OaiMessage())
    return sdk, OpenAICompatibleClient(client=sdk)


class TestOpenAICompatibleWire:
    def test_roles_are_rendered(self):
        sdk, client = oai()
        client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        roles = [m["role"] for m in sdk.chat.completions.calls[0]["messages"]]
        assert roles == ["system", "user", "assistant", "tool"]

    def test_a_tool_result_carries_its_call_id(self):
        sdk, client = oai()
        client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        tool_message = sdk.chat.completions.calls[0]["messages"][-1]
        assert tool_message["tool_call_id"] == "c0"

    def test_tools_are_wrapped_as_functions(self):
        sdk, client = oai()
        client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        sent = sdk.chat.completions.calls[0]["tools"]
        assert sent[0]["type"] == "function"
        assert sent[0]["function"]["name"] == "state"

    def test_reasoning_stays_off(self):
        sdk, client = oai()
        client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        assert sdk.chat.completions.calls[0]["extra_body"]["thinking"]["type"] == "disabled"

    def test_a_returned_tool_call_is_parsed(self):
        message = _OaiMessage(
            content=None,
            tool_calls=[_OaiCall(id="x1", function=_OaiFn("drop_row", json.dumps({"index": 3})))],
        )
        sdk, client = oai(message)
        turn = client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        assert turn.tool_calls == [ToolCall(id="x1", name="drop_row", arguments={"index": 3})]
        assert (turn.prompt_tokens, turn.completion_tokens) == (7, 3)

    def test_malformed_arguments_become_an_empty_dict(self):
        message = _OaiMessage(tool_calls=[_OaiCall(id="x", function=_OaiFn("state", "{oops"))])
        sdk, client = oai(message)
        assert client.complete_with_tools("sys", TRANSCRIPT, TOOLS).tool_calls[0].arguments == {}


# --------------------------------------------------------------------------- Anthropic


@dataclass
class _Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class _AnthropicMessages:
    blocks: list = field(default_factory=list)
    calls: list = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)

        @dataclass
        class _Usage:
            input_tokens: int = 11
            output_tokens: int = 4

        @dataclass
        class _Response:
            content: list
            usage: _Usage

        return _Response(self.blocks, _Usage())


@pytest.fixture
def anthropic(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-opus-5")
    sdk = type("SDK", (), {})()
    sdk.messages = _AnthropicMessages()
    return sdk, AnthropicClient(client=sdk)


class TestAnthropicWire:
    def test_tool_results_are_grouped_into_one_user_message(self, anthropic):
        sdk, client = anthropic
        transcript = TRANSCRIPT + [
            {"role": "tool", "call_id": "c1", "name": "state", "text": "still -200.00"}
        ]
        client.complete_with_tools("sys", transcript, TOOLS)
        messages = sdk.messages.calls[0]["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "user"]
        assert len(messages[-1]["content"]) == 2

    def test_a_tool_result_block_carries_the_use_id(self, anthropic):
        sdk, client = anthropic
        client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        block = sdk.messages.calls[0]["messages"][-1]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "c0"

    def test_tools_use_input_schema(self, anthropic):
        sdk, client = anthropic
        client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        assert "input_schema" in sdk.messages.calls[0]["tools"][0]

    def test_tool_use_blocks_are_parsed(self, anthropic):
        sdk, client = anthropic
        sdk.messages.blocks = [
            _Block(type="text", text="looking"),
            _Block(type="tool_use", id="t9", name="set_side", input={"index": 1}),
        ]
        turn = client.complete_with_tools("sys", TRANSCRIPT, TOOLS)
        assert turn.text == "looking"
        assert turn.tool_calls == [ToolCall(id="t9", name="set_side", arguments={"index": 1})]
        assert (turn.prompt_tokens, turn.completion_tokens) == (11, 4)
