"""Granola adapter.

Granola records the local meeting audio and splits the transcript by **audio
source** rather than by diarized speaker. In the public API
(`GET /v1/notes/{id}?include=transcript`) each segment carries a `speaker` object
whose `source` is:

- ``"microphone"`` — the Granola user's own mic (the person running the call:
  typically the rep / CSM).
- ``"speaker"`` — the other side, captured from the system/speaker output (the
  prospect or customer). (Older/desktop payloads call this ``"system"``.)

That source split is a gift for coaching: ``side`` comes straight from the audio
channel, with no name heuristic needed to find the rep. We map
``microphone -> rep`` and ``speaker``/``system`` ``-> prospect``. On post-sales
calls the other channel is really the customer/partner — override with
``--participants`` / transcript ``metadata`` when that matters; scoring only
evaluates rep-side turns either way.

Verified against the live public API — a flat array of segments with ISO-8601
**absolute** timestamps (we convert them to offsets from the first segment):

    [
      {"text": "Walk me through how you do this today.",
       "start_time": "2026-05-28T23:04:09.803Z",
       "end_time":   "2026-05-28T23:04:10.123Z",
       "speaker": {"source": "microphone"}},
      {"text": "Sure, it's mostly spreadsheets…",
       "start_time": "2026-05-28T23:04:11.936Z",
       "end_time":   "2026-05-28T23:04:13.856Z",
       "speaker": {"source": "speaker"}},
      ...
    ]

The array may arrive bare, wrapped as ``{"transcript": [...]}`` (the full note
object from the API), or under ``segments``. Granola attributes no per-person
names, so the display name is the channel ("You" / "Participant") unless a
``speaker.name`` (or a string ``speaker``) is present, which we prefer. Granola
also exports plain text, which the plaintext adapter covers.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..models import Transcript, Turn
from .base import build_participants, guess_side, title_from_path

# Audio-source values, mapped to a transcript side. "speaker" is the OTHER side in
# the public API (the computer's speaker output); "system" is the older alias.
_MIC_SOURCES = {"microphone", "mic", "me"}
_OTHER_SOURCES = {"speaker", "system", "them"}
# Where the segment array may live when the export is an object, not a bare list.
_ARRAY_KEYS = ("transcript", "segments", "entries")


class GranolaAdapter:
    name = "granola"

    def sniff(self, path: str, text: str) -> bool:
        if not path.lower().endswith(".json"):
            return False
        try:
            data = json.loads(text)
        except ValueError:
            return False
        rows = self._rows(data)
        if not rows:
            return False
        # Distinctive Granola signature: text + a microphone/speaker audio source,
        # whether the source sits on the segment or nested under `speaker`.
        return any(
            isinstance(r, dict) and "text" in r and self._source(r) in (_MIC_SOURCES | _OTHER_SOURCES)
            for r in rows[:5]
        )

    def parse(self, path: str, text: str) -> Transcript:
        rows = [r for r in self._rows(json.loads(text)) if isinstance(r, dict)]

        # Pass 1: parse absolute timestamps so we can rebase to call-relative offsets.
        starts = [self._ts(r.get("start_time") or r.get("start_timestamp") or r.get("start")) for r in rows]
        base = min((s for s in starts if s is not None), default=None)

        def offset(ts: datetime | None) -> float | None:
            return (ts - base).total_seconds() if ts is not None and base is not None else None

        turns: list[Turn] = []
        for r, start in zip(rows, starts, strict=True):
            txt = (r.get("text") or "").strip()
            if not txt:
                continue
            src = self._source(r)
            side = "rep" if src in _MIC_SOURCES else "prospect" if src in _OTHER_SOURCES else None
            name = self._name(r) or ("You" if side == "rep" else "Participant")
            if side is None:  # unknown source — fall back to the name heuristic
                side = guess_side(str(name))
            end = self._ts(r.get("end_time") or r.get("end_timestamp") or r.get("end"))
            turns.append(
                Turn(
                    speaker=str(name),
                    side=side,
                    text=txt,
                    start_seconds=offset(start),
                    end_seconds=offset(end),
                )
            )

        return Transcript(
            title=title_from_path(path),
            started_at=base.isoformat() if base else None,
            source={"recorder": "granola", "adapter": self.name},
            participants=build_participants(turns),
            turns=turns,
        )

    def _rows(self, data: Any) -> list:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in _ARRAY_KEYS:
                if isinstance(data.get(k), list):
                    return data[k]
        return []

    def _source(self, r: dict) -> str:
        """Audio source, whether on the segment (`source`) or nested (`speaker.source`)."""
        sp = r.get("speaker")
        if isinstance(sp, dict) and sp.get("source"):
            return str(sp["source"]).lower()
        return str(r.get("source", "")).lower()

    def _name(self, r: dict) -> str | None:
        """Explicit speaker name when Granola provides one (rare — usually unnamed)."""
        sp = r.get("speaker")
        if isinstance(sp, str):
            return sp
        if isinstance(sp, dict) and sp.get("name"):
            return str(sp["name"])
        return None

    def _ts(self, v: Any) -> datetime | None:
        """Parse an ISO-8601 string (or epoch number) to an aware UTC datetime."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            secs = v / 1000.0 if v > 1e11 else float(v)  # ms vs s heuristic
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        try:
            dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
