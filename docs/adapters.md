# Adapters

GTM Superintelligence is recorder-agnostic. An **adapter** converts any vendor's raw
transcript output into the normalized `NormalizedTranscript` format the pipeline
consumes. The pipeline never reads a vendor payload directly.

---

## The normalized transcript format

The `NormalizedTranscript` schema lives in `schemas/transcript.schema.json`.
Only two fields are required:

```json
{
  "schema_version": "1.0",
  "turns": [
    {"speaker": "Alex", "side": "rep",      "text": "Walk me through how you do this today."},
    {"speaker": "Sam",  "side": "prospect", "text": "Sure, we currently use a spreadsheet…"}
  ]
}
```

Optional fields that improve coaching quality:

| Field | Type | Notes |
|---|---|---|
| `call_id` | string | Stable identifier; falls back to a content hash if absent. |
| `title` | string | Human-readable title (e.g., "Acme Corp — Discovery"). |
| `started_at` | ISO-8601 datetime | Start time of the call. |
| `duration_seconds` | number | Total call duration. |
| `source.recorder` | string | Which recorder produced the raw file (e.g., `"gong"`). |
| `source.adapter` | string | Which adapter processed it. |
| `source.language` | BCP-47 string | e.g., `"en-US"`. |
| `participants` | array | Participant list with `id`, `name`, `side`, `title`, `organization`, `talk_seconds`. |
| `turns[].start_seconds` | number | Turn start time; enables timestamped evidence quotes. |
| `turns[].end_seconds` | number | Turn end time. |
| `metadata` | object | Free-form context: CRM stage, deal amount, prior call summaries, etc. |

The `side` field on participants and turns is critical for coaching: only
`rep`-side turns are evaluated. Valid values are `rep`, `prospect`, `customer`,
`partner`, `internal`, `unknown`.

---

## Built-in adapters

There are **10** built-in adapters:

| Adapter ID | Input format | Notes |
|---|---|---|
| `vtt` | WebVTT (`.vtt`) | Parses both `<v Speaker>` voice spans and an inline `Name: text` prefix — the latter is how **Zoom** cloud-recording transcripts and **Avoma** VTT exports carry the speaker. Timestamps in seconds. |
| `srt` | SubRip (`.srt`) | Speaker extracted from cue text when present. Reliable export path for Otter. |
| `gong` | Gong transcript export (JSON) | Combined transcript + parties doc: maps `callTranscripts[].transcript[].sentences[]`, resolves opaque `speakerId` to name and `affiliation` (Internal/External), divides ms→seconds. |
| `fireflies` | Fireflies.ai transcript export (JSON) | Flat `transcript.sentences[]` with `speaker_name`/`text`/`start_time` (seconds). |
| `otter` | Otter.ai export (JSON or `.txt`) | Best-effort/unverified (API is Enterprise-gated). Handles `utterances`/`transcripts`/`speeches`, `speaker_id`→top-level `speakers[]`, `start_offset` in milliseconds. Prefer the SRT/TXT export when possible. |
| `recall` | Recall.ai export (JSON) | JSON array of turns `{participant, words[]}`; joins words into a turn, `start_timestamp.relative` in seconds, maps `is_host`→rep (others→prospect; override with `--participants`). |
| `grain` | Grain export (JSON) | JSON array `[{start,end,text,speaker,participant_id}]`; `start`/`end` in milliseconds (ms→seconds). |
| `granola` | Granola public API (`GET /v1/notes/{id}?include=transcript`) | JSON array of segments `{text, start_time, end_time, speaker:{source}}`. Splits by **audio source** nested under `speaker` (`microphone`→rep, `speaker`→prospect) rather than diarized names, so `side` comes from the channel. Absolute ISO `start_time`/`end_time` rebased to call offsets. Accepts a bare array or the wrapped `{"transcript": [...]}` note object. |
| `json-generic` | Any JSON with a `turns` or `utterances` array | Configurable field mapping. |
| `plaintext` | Plain `.txt` file with speaker-prefixed lines | Final fallback; sniffs for `Speaker: text` lines. |

### Auto-detection

When you run `gtmsi coach <file>`, `load_transcript()` resolves the adapter
like this:

1. If you pass an explicit `--adapter <id>` flag, that adapter is used directly
   (auto-detection is skipped).
2. Otherwise, GTM Superintelligence iterates the registered adapters in a **fixed priority
   order** and calls each adapter's `sniff(path, text)`. The first adapter whose
   `sniff` returns `True` **and** that successfully parses at least one turn
   wins. The priority order is:

   ```
   VTT → SRT → Gong → Fireflies → Otter → Recall → Grain → Granola → JSONGeneric → Plaintext
   ```

   Most-specific recorders are tried first; the generic JSON adapter and the
   plaintext fallback come last. There is no separate file-extension dispatch
   step — an adapter's `sniff` may look at the file extension (the plaintext
   adapter, for example, accepts `.txt`/`.md`/`.log`), but extension matching is
   internal to each adapter, not a stage of its own.

If no adapter sniffs and parses successfully, the load raises an error; pass
`--adapter` explicitly to force one.

---

## The `plaintext` adapter in practice

The plaintext adapter expects lines in the format `Speaker Name: text`, with
blank lines between turns optional. Example:

```
Alex: Thanks for making time today. Here's what I was hoping to cover — does
that agenda work for you?

Sam: Yes, though I'd also love to understand pricing before we wrap up.

Alex: Absolutely, we'll get to that. First, walk me through how you handle
reporting today.
```

The adapter infers `side` from heuristics (turn order, keyword detection) unless
you provide a `--participants` mapping flag:

```bash
gtmsi coach call.txt --participants '{"Alex": "rep", "Sam": "prospect"}'
```

---

## Writing a new adapter

An adapter is a small **class** (see `src/gtmsi/adapters/base.py` and any
existing adapter such as `plaintext.py`). The interface is:

- a `name` string attribute (the adapter id, e.g. used by `--adapter`),
- `sniff(self, path, text) -> bool` — return `True` if this adapter can parse
  the file (auto-detection calls this),
- `parse(self, path, text) -> Transcript` — return a normalized
  `Transcript` (from `gtmsi.models`).

The skeleton below shows the minimum interface:

```python
# src/gtmsi/adapters/my_recorder.py

import json

from ..models import Transcript, Turn
from .base import build_participants


class MyRecorderAdapter:
    name = "my-recorder"

    def sniff(self, path: str, text: str) -> bool:
        """Return True if this adapter recognizes the file."""
        if not path.lower().endswith(".json"):
            return False
        try:
            raw = json.loads(text)
        except ValueError:
            return False
        return isinstance(raw, dict) and "my_recorder_version" in raw

    def parse(self, path: str, text: str) -> Transcript:
        raw = json.loads(text)
        turns = [
            Turn(
                speaker=u["speaker_name"],
                side=_map_side(u.get("role")),
                text=u["transcript"],
                start_seconds=u.get("start_time"),
                end_seconds=u.get("end_time"),
            )
            for u in raw.get("utterances", [])
        ]
        return Transcript(
            call_id=raw.get("call_id"),
            title=raw.get("title"),
            source={"recorder": "my-recorder", "adapter": self.name},
            participants=build_participants(turns),
            turns=turns,
        )


def _map_side(role: str | None) -> str:
    return {"host": "rep", "guest": "prospect", "internal": "internal"}.get(role or "", "unknown")
```

**Registration:** add an **instance** of your adapter to the `ADAPTERS` list in
`src/gtmsi/adapters/__init__.py`. List position is priority order — put
specific recorders before the generic JSON and plaintext fallbacks:

```python
from .my_recorder import MyRecorderAdapter

ADAPTERS = [
    VTTAdapter(),
    SRTAdapter(),
    GongAdapter(),
    FirefliesAdapter(),
    OtterAdapter(),
    RecallAdapter(),
    GrainAdapter(),
    MyRecorderAdapter(),   # before the generic fallbacks
    JSONGenericAdapter(),
    PlaintextAdapter(),
]
```

**Detection:** auto-detection iterates `ADAPTERS` in order and calls
`sniff(path, text)` on each, using the first adapter that both sniffs `True` and
parses at least one turn. Make `sniff` specific enough that it doesn't claim
files it can't actually parse.

---

## Metadata enrichment

The `metadata` field in the normalized transcript is a free-form object. You can
pass deal context that improves coaching quality:

```json
{
  "schema_version": "1.0",
  "turns": ["…"],
  "metadata": {
    "crm_stage": "Technical Validation",
    "deal_amount_usd": 48000,
    "prior_call_summary": "First discovery call; rep surfaced manual reporting pain.",
    "competitors_mentioned": ["vendor-a", "vendor-b"],
    "rep_name": "Alex Chen",
    "account_name": "Acme Corp"
  }
}
```

The pipeline passes `metadata` to the coaching prompt so the model can
contextualize scores and improvements without fabricating business facts.

---

## Cross-references

- Normalized transcript schema: `schemas/transcript.schema.json`
- Core concepts: [concepts.md](./concepts.md)
- Privacy and PII handling: [privacy-and-pii.md](./privacy-and-pii.md)
- Pipeline architecture: [architecture.md](./architecture.md)
