"""Rung 2: the model derives a layout profile (SPEC §4 stage 7, §7.14).

No network here. The client is a stub, because what is worth testing is the
contract around the model — what it is shown, what shape its answer must
take, and what happens when it is wrong — not the model itself.

The model returns a *profile*, never rows. It sees a sample of the page, and
whatever it says is checked by the same free verifier that checks everything
else.
"""

import json
from dataclasses import dataclass, field

from statement_tieout.layout import SideStrategy
from statement_tieout.layout.llm import MAX_SAMPLE_PAGES, profile_from_pages
from statement_tieout.llm.client import Completion

from .helpers import DATE_X, DESC_X, LEFT_X, line, page, rows_page


@dataclass
class StubClient:
    """Returns canned JSON and records what it was asked."""

    replies: list[str] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    def complete_json(self, system: str, user: str, schema: dict) -> Completion:
        self.calls.append({"system": system, "user": user, "schema": schema})
        content = self.replies.pop(0) if self.replies else "{}"
        return Completion(content=content, prompt_tokens=100, completion_tokens=20)


VALID_PROFILE = json.dumps(
    {
        "date_column": {"x0": 20.0, "x1": 90.0},
        "amount_columns": [{"x0": 320.0, "x1": 400.0}],
        "balance_column": {"x0": 480.0, "x1": 560.0},
        "side_strategy": "signed",
        "date_formats": ["%m/%d/%Y"],
        "deposit_sections": [],
        "withdrawal_sections": [],
    }
)

SAMPLE = rows_page(
    ("01/01/2025", "CIGNA CLAIMS PAYMENT", "8,164.30", "2,023,046.77"),
    ("01/02/2025", "MEDPRO INSURANCE", "-17,459.90", "2,005,586.87"),
)


class TestHappyPath:
    def test_returns_the_parsed_profile(self):
        client = StubClient([VALID_PROFILE])
        profile, usage = profile_from_pages([SAMPLE], client)
        assert profile.side_strategy is SideStrategy.SIGNED
        assert profile.date_formats == ["%m/%d/%Y"]

    def test_usage_is_accumulated_for_the_cost_story(self):
        _, usage = profile_from_pages([SAMPLE], StubClient([VALID_PROFILE]))
        assert usage.calls == 1
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 20


class TestWhatTheModelIsShown:
    def test_the_prompt_carries_words_with_coordinates(self):
        client = StubClient([VALID_PROFILE])
        profile_from_pages([SAMPLE], client)
        prompt = client.calls[0]["user"]
        assert "CIGNA" in prompt
        assert "8,164.30" in prompt
        assert "x0=" in prompt

    def test_at_most_two_pages_are_sampled(self):
        pages = [
            rows_page((f"01/0{i + 1}/2025", "A", "1.00", "2.00"), top=100.0)
            for i in range(6)
        ]
        client = StubClient([VALID_PROFILE])
        profile_from_pages(pages, client)
        prompt = client.calls[0]["user"]
        assert prompt.count("--- page") <= MAX_SAMPLE_PAGES

    def test_only_a_sample_of_rows_is_sent_never_the_whole_table(self):
        """SPEC §7.14 — the model must not be asked to read every row."""
        big = page(*[
            line(100.0 + i * 12.0, (DATE_X, "01/01/2025"), (DESC_X, f"ROW{i}"),
                 (LEFT_X, "1.00"))
            for i in range(200)
        ])
        client = StubClient([VALID_PROFILE])
        profile_from_pages([big], client)
        assert "ROW199" not in client.calls[0]["user"]

    def test_the_schema_is_handed_over_explicitly(self):
        client = StubClient([VALID_PROFILE])
        profile_from_pages([SAMPLE], client)
        assert "side_strategy" in json.dumps(client.calls[0]["schema"])


class TestInvalidAnswers:
    def test_malformed_json_is_retried_once_then_gives_up(self):
        client = StubClient(["not json at all", "still not json", "nor this"])
        profile, usage = profile_from_pages([SAMPLE], client)
        assert profile is None
        assert usage.calls == len(client.calls)

    def test_a_schema_violation_is_fed_back_to_the_model(self):
        bad = json.dumps({"date_column": {"x0": 1.0, "x1": 2.0}, "side_strategy": "signed"})
        client = StubClient([bad, VALID_PROFILE])
        profile, _ = profile_from_pages([SAMPLE], client)
        assert profile is not None
        assert "amount_columns" in client.calls[1]["user"]

    def test_an_unknown_side_strategy_is_rejected(self):
        bad = json.loads(VALID_PROFILE) | {"side_strategy": "vibes"}
        client = StubClient([json.dumps(bad), json.dumps(bad), json.dumps(bad)])
        profile, _ = profile_from_pages([SAMPLE], client)
        assert profile is None

    def test_json_wrapped_in_prose_is_still_read(self):
        """Models without strict schema support wrap their answer in chatter."""
        client = StubClient([f"Here is the profile:\n```json\n{VALID_PROFILE}\n```\nHope it helps"])
        profile, _ = profile_from_pages([SAMPLE], client)
        assert profile is not None


class TestBounds:
    def test_the_attempt_ceiling_is_respected(self):
        client = StubClient(["{}"] * 10)
        profile_from_pages([SAMPLE], client, max_attempts=2)
        assert len(client.calls) == 2

    def test_no_pages_means_no_call(self):
        client = StubClient([VALID_PROFILE])
        profile, usage = profile_from_pages([], client)
        assert profile is None
        assert client.calls == []
        assert usage.calls == 0


class TestFeedback:
    def test_a_previous_residual_is_included_when_given(self):
        client = StubClient([VALID_PROFILE])
        profile_from_pages([SAMPLE], client, feedback="deposits_total off by -1,240.50")
        assert "1,240.50" in client.calls[0]["user"]
