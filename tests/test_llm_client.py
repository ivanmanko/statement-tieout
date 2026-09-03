"""The OpenAI-compatible client (ADR-004), tested against a stub SDK.

Two things here were found by running against DeepSeek and cost real money to
learn, so they are pinned: the schema must travel in the prompt because the
provider has no strict schema mode, and reasoning must be switched off or the
model spends its whole budget thinking and returns nothing.
"""

import json
from dataclasses import dataclass, field

import pytest

from statement_tieout.llm.openai_compatible import OpenAICompatibleClient

SCHEMA = {"type": "object", "properties": {"side_strategy": {"type": "string"}}}


@dataclass
class StubMessage:
    content: str


@dataclass
class StubChoice:
    message: StubMessage


@dataclass
class StubUsage:
    prompt_tokens: int = 11
    completion_tokens: int = 22


@dataclass
class StubResponse:
    choices: list
    usage: StubUsage


@dataclass
class StubCompletions:
    calls: list = field(default_factory=list)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return StubResponse([StubChoice(StubMessage('{"side_strategy": "signed"}'))], StubUsage())


@dataclass
class StubChat:
    completions: StubCompletions


@dataclass
class StubSDK:
    chat: StubChat


@pytest.fixture
def sdk_and_client(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    sdk = StubSDK(StubChat(StubCompletions()))
    return sdk, OpenAICompatibleClient(client=sdk)


class TestSchemaTravelsInThePrompt:
    """The provider has no strict schema mode, so the model must be shown it."""

    def test_the_schema_appears_in_the_messages(self, sdk_and_client):
        sdk, client = sdk_and_client
        client.complete_json("be brief", "here are the words", SCHEMA)
        sent = json.dumps(sdk.chat.completions.calls[0]["messages"])
        assert "side_strategy" in sent

    def test_json_object_mode_is_still_requested(self, sdk_and_client):
        sdk, client = sdk_and_client
        client.complete_json("be brief", "words", SCHEMA)
        assert sdk.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


class TestReasoningIsOff:
    """Measured: reasoning on, this task never converged — 8192 tokens, no answer."""

    def test_thinking_is_disabled_by_default(self, sdk_and_client):
        sdk, client = sdk_and_client
        client.complete_json("be brief", "words", SCHEMA)
        assert sdk.chat.completions.calls[0]["extra_body"]["thinking"] == {"type": "disabled"}

    def test_it_can_be_turned_back_on(self, monkeypatch):
        monkeypatch.setenv("LLM_THINKING", "enabled")
        sdk = StubSDK(StubChat(StubCompletions()))
        OpenAICompatibleClient(client=sdk).complete_json("s", "u", SCHEMA)
        assert sdk.chat.completions.calls[0]["extra_body"]["thinking"] == {"type": "enabled"}


class TestUsage:
    def test_token_counts_come_back_for_the_cost_model(self, sdk_and_client):
        sdk, client = sdk_and_client
        completion = client.complete_json("s", "u", SCHEMA)
        assert (completion.prompt_tokens, completion.completion_tokens) == (11, 22)
        assert completion.content == '{"side_strategy": "signed"}'

    def test_a_known_model_carries_a_price(self, sdk_and_client):
        _, client = sdk_and_client
        assert client.price is not None

    def test_an_unknown_model_has_no_price_rather_than_a_guess(self, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "some-model-we-have-not-priced")
        sdk = StubSDK(StubChat(StubCompletions()))
        assert OpenAICompatibleClient(client=sdk).price is None
