"""Tests for the LLM->JSON boundary in AnthropicCoach.complete_json.

These exercise the truncation-aware retry without hitting the network or needing
the `anthropic` package: we build the coach with ``__new__`` and inject a fake
client that returns canned (text, stop_reason) responses and records the
``max_tokens`` it was called with.
"""
import json

import pytest

from gtmsi.llm import AnthropicCoach, LLMError


class _FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text, stop_reason):
        self.content = [_FakeBlock(text)]
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # max_tokens per create() call, in order

    def create(self, *, model, max_tokens, system, messages):
        self.calls.append(max_tokens)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _coach(responses, max_tokens=4096):
    coach = AnthropicCoach.__new__(AnthropicCoach)  # bypass __init__ (no key/SDK needed)
    coach.client = _FakeClient(responses)
    coach.model = "claude-sonnet-4-6"
    coach.max_tokens = max_tokens
    return coach


def _complete(coach, max_tokens=4096):
    return coach.complete_json(system="sys", cached_blocks=[], user_text="u", max_tokens=max_tokens)


def test_truncation_retry_raises_the_budget():
    # First call truncates at the cap; the retry must go out with a larger budget.
    coach = _coach([
        _FakeResp('{"a": 1, "b": "cut off here', "max_tokens"),
        _FakeResp('{"a": 1, "b": "ok"}', "end_turn"),
    ])
    assert _complete(coach, max_tokens=4096) == {"a": 1, "b": "ok"}
    assert coach.client.messages.calls == [4096, 8192]  # doubled on truncation


def test_retry_budget_capped_at_ceiling():
    # Doubling must not exceed the 32768 ceiling (smallest model output cap headroom).
    coach = _coach([
        _FakeResp("{ truncated", "max_tokens"),
        _FakeResp('{"ok": true}', "end_turn"),
    ])
    assert _complete(coach, max_tokens=20000) == {"ok": True}
    assert coach.client.messages.calls == [20000, 32768]  # min(40000, 32768)


def test_both_truncated_raises_typed_error():
    coach = _coach([
        _FakeResp("{ truncated", "max_tokens"),
        _FakeResp("{ still truncated", "max_tokens"),
    ])
    with pytest.raises(LLMError, match="exceeded max_tokens"):
        _complete(coach)


def test_non_truncation_malformed_retries_same_budget():
    # A genuinely malformed (not truncated) first response retries with the SAME budget.
    coach = _coach([
        _FakeResp("sorry, here is prose not json", "end_turn"),
        _FakeResp('{"a": 2}', "end_turn"),
    ])
    assert _complete(coach, max_tokens=4096) == {"a": 2}
    assert coach.client.messages.calls == [4096, 4096]  # not bumped


def test_persistent_garbage_raises_typed_error_not_decode_error():
    coach = _coach([
        _FakeResp("no json", "end_turn"),
        _FakeResp("still no json", "end_turn"),
    ])
    with pytest.raises(LLMError, match="did not return valid JSON"):
        _complete(coach)


def test_valid_first_response_no_retry():
    coach = _coach([_FakeResp(json.dumps({"a": 1}), "end_turn")])
    assert _complete(coach) == {"a": 1}
    assert coach.client.messages.calls == [4096]  # single call, no retry
