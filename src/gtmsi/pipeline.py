"""The coaching pipeline: classify -> (infer outcomes + score + coach).

Two LLM calls per transcript:
  1. classify()  -> Classification          (cheap; can use a small model)
  2. coach()     -> the rest of the report   (outcomes + scores + coaching)

The frameworks, scorecard, outcome library, and system prompt are passed as cached
context blocks so bulk runs are fast and cheap. Inject a custom ``llm`` (anything
with ``complete_json``) to run offline or in tests.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from .llm import AnthropicCoach, CachedBlock
from .models import Classification, CoachingReport, Transcript
from .redaction import redact_transcript
from .registry import Registry, load_registry

# How many transcript characters to send. Generous; trims pathological inputs.
MAX_TRANSCRIPT_CHARS = 60_000


class LLMLike(Protocol):
    def complete_json(self, system: str, cached_blocks: list[CachedBlock], user_text: str, max_tokens: int | None = None): ...


# Prompt and knowledge-base text is identical across every transcript in a bulk run,
# so cache the file reads (keyed by absolute path). The on-disk files don't change
# mid-process; this turns ~5 re-reads per transcript into one read per file.
@lru_cache(maxsize=256)
def _read_path(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


def _read(reg: Registry, *parts: str) -> str:
    return _read_path(str(reg.root.joinpath(*parts)))


def _raw_yaml(reg: Registry, subdir: str, _id: str) -> str:
    return _read_path(str(reg.root / subdir / f"{_id}.yaml"))


def _transcript_text(t: Transcript) -> str:
    txt = t.as_text()
    if len(txt) > MAX_TRANSCRIPT_CHARS:
        txt = txt[:MAX_TRANSCRIPT_CHARS] + "\n[...transcript truncated...]"
    return txt


# --------------------------------------------------------------------------- stage 1
def classify(t: Transcript, reg: Registry, llm: LLMLike) -> Classification:
    system = _read(reg, "prompts", "system.md")
    template = _read(reg, "prompts", "classifier.md")
    call_types_yaml = _read(reg, "config", "call_types.yaml")

    user = template.replace("{{CALL_TYPES_YAML}}", "(provided above as cached context)")
    user = user.replace("{{TRANSCRIPT}}", _transcript_text(t))

    data = llm.complete_json(
        system=system,
        cached_blocks=[CachedBlock("Call-type taxonomy", call_types_yaml)],
        user_text=user,
        max_tokens=1024,
    )
    # Guard: ensure phase agrees with the chosen call type when possible.
    ct = reg.call_types.get(data.get("call_type", ""))
    if ct and data.get("phase") != ct.phase:
        data["phase"] = ct.phase
    return Classification(**data)


# --------------------------------------------------------------------------- stage 2
def coach(t: Transcript, classification: Classification, reg: Registry, llm: LLMLike) -> CoachingReport:
    scorecard = reg.scorecard_for(classification.call_type)
    if scorecard is None:
        scorecard = reg.scorecards.get("generic-conversation")
    if scorecard is None:
        raise ValueError(f"No scorecard available for call type '{classification.call_type}'")

    system = _read(reg, "prompts", "system.md")
    template = _read(reg, "prompts", "coaching.md")
    scorecard_yaml = _raw_yaml(reg, "scorecards", scorecard.id)
    frameworks_yaml = "\n---\n".join(
        _raw_yaml(reg, "frameworks", fw.id) for fw in reg.frameworks_for_scorecard(scorecard)
    )
    outcomes_yaml = _read(reg, "config", "outcomes.yaml")

    user = (
        template.replace("{{CLASSIFICATION}}", classification.model_dump_json(indent=2))
        .replace("{{SCORECARD_YAML}}", "(provided above as cached context)")
        .replace("{{FRAMEWORKS_YAML}}", "(provided above as cached context)")
        .replace("{{OUTCOMES_YAML}}", "(provided above as cached context)")
        .replace("{{TRANSCRIPT}}", _transcript_text(t))
    )

    data = llm.complete_json(
        system=system,
        cached_blocks=[
            CachedBlock(f"Scorecard: {scorecard.name}", scorecard_yaml),
            CachedBlock("Frameworks", frameworks_yaml),
            CachedBlock("Outcome library", outcomes_yaml),
        ],
        user_text=user,
        # A full coaching report (outcomes + 7-9 scored criteria with rationale and
        # evidence quotes + strengths + improvements + manager notes) routinely runs
        # past 4096 output tokens on a substantive call, truncating the JSON mid-stream.
        # 16384 fits a full report across Sonnet 4.6 and Opus 4.8; the retry in
        # AnthropicCoach.complete_json bumps further if a call still truncates.
        max_tokens=16384,
    )

    # Merge known classification and back-fill criterion weights/bands defensively.
    data["classification"] = classification.model_dump()
    data.setdefault("call_id", t.call_id)
    for s in data.get("scores", []):
        crit = next((c for c in scorecard.criteria if c.id == s.get("criterion_id")), None)
        if crit and s.get("weight") is None:
            s["weight"] = crit.weight
        # Coerce a missing/null score to 0 so a malformed LLM response degrades
        # gracefully instead of crashing model construction.
        if s.get("score") is None:
            s["score"] = 0
        if not s.get("band"):
            s["band"] = scorecard.band_for(s["score"])
    return CoachingReport(**data)


# --------------------------------------------------------------------------- top-level
def coach_transcript(
    transcript: Transcript,
    reg: Registry | None = None,
    llm: LLMLike | None = None,
    redact: bool = False,
    model: str | None = None,
) -> CoachingReport:
    """End-to-end: classify then coach a single transcript."""
    reg = reg or load_registry()
    llm = llm or (AnthropicCoach(model=model) if model else AnthropicCoach())
    if redact:
        transcript = redact_transcript(transcript)
    classification = classify(transcript, reg, llm)
    return coach(transcript, classification, reg, llm)
