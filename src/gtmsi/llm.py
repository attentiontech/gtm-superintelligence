"""Thin wrapper over the Anthropic Messages API with prompt caching.

Why caching matters here: the system prompt + the frameworks + the scorecard are
identical across every call you coach. We mark them with ``cache_control`` so they
are billed/processed once and reused, which is a large cost/latency win when you
coach calls in bulk. The transcript (the part that changes) is sent uncached.

The wrapper also enforces JSON output and retries once on malformed JSON.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_MODEL = os.environ.get("GTMSI_MODEL", "claude-sonnet-4-6")


@dataclass
class CachedBlock:
    """A large, reusable chunk of context to cache (label is for readability only)."""

    label: str
    text: str


class LLMError(RuntimeError):
    pass


# Ceiling for the truncation retry. Safely under the smallest supported model
# output cap (Sonnet 4.6 tops out at 64K; Opus 4.8 at 128K), so doubling the
# coach budget here never sends an over-limit max_tokens that the API rejects.
_MAX_RETRY_TOKENS = 32768


class AnthropicCoach:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, max_tokens: int = 4096):
        try:
            from anthropic import Anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError(
                "The 'anthropic' package is required for live coaching. "
                "Install with `pip install anthropic`, or run inside Claude Code (no API key needed)."
            ) from e
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set. Export it or pass api_key=...")
        self.client = Anthropic(api_key=key)
        self.model = model
        self.max_tokens = max_tokens

    def complete_json(
        self,
        system: str,
        cached_blocks: list[CachedBlock],
        user_text: str,
        max_tokens: int | None = None,
    ) -> Any:
        """Run one message and parse a JSON object/array from the response.

        ``cached_blocks`` are concatenated ahead of ``user_text`` as user-content
        blocks, each marked ephemeral-cacheable. ``system`` is also cached.
        """
        content_blocks: list[dict[str, Any]] = []
        for blk in cached_blocks:
            content_blocks.append(
                {
                    "type": "text",
                    "text": f"### {blk.label}\n{blk.text}",
                    "cache_control": {"type": "ephemeral"},
                }
            )
        content_blocks.append({"type": "text", "text": user_text})

        system_blocks = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]

        raw, stop_reason = self._call(system_blocks, content_blocks, max_tokens)
        try:
            return _extract_json(raw)
        except ValueError as first_err:
            # The most common cause of unparseable JSON is a response truncated at
            # the token cap. When that's what happened, retrying with the SAME budget
            # just truncates again — so bump the budget for the retry.
            truncated = stop_reason == "max_tokens"
            retry_tokens = max_tokens
            if truncated:
                base = max_tokens or self.max_tokens
                retry_tokens = min(base * 2, _MAX_RETRY_TOKENS)

            fix, fix_stop = self._call(
                system_blocks,
                content_blocks
                + [{"type": "text", "text": "Your previous output was not valid JSON. Reply with ONLY the JSON, no prose."}],
                retry_tokens,
            )
            try:
                return _extract_json(fix)
            except ValueError as retry_err:
                # Guard the final parse: surface a typed, actionable error instead of
                # letting a raw json.JSONDecodeError escape.
                if truncated or fix_stop == "max_tokens":
                    raise LLMError(
                        "model output exceeded max_tokens and was truncated; raise max_tokens "
                        "or shorten the transcript"
                    ) from retry_err
                raise LLMError(f"model did not return valid JSON: {retry_err}") from first_err

    def _call(self, system_blocks, content_blocks, max_tokens) -> tuple[str, str | None]:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": content_blocks}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return text, getattr(resp, "stop_reason", None)


def _extract_json(text: str) -> Any:
    text = text.strip()
    # Strip ```json fences if present.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: grab the outermost {...} or [...].
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
    raise ValueError("No JSON found in model output")
