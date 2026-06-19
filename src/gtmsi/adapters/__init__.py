"""Transcript adapters + auto-detection.

``load_transcript(path)`` sniffs the file against each adapter in priority order and
returns a normalized :class:`~gtmsi.models.Transcript`. Vendor JSON adapters run
before the generic JSON catch-all; plain text is the final fallback.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import Transcript
from .fireflies import FirefliesAdapter
from .gong import GongAdapter
from .grain import GrainAdapter
from .granola import GranolaAdapter
from .json_generic import JSONGenericAdapter
from .otter import OtterAdapter
from .plaintext import PlaintextAdapter
from .recall import RecallAdapter
from .srt import SRTAdapter
from .vtt import VTTAdapter

_log = logging.getLogger(__name__)

# Priority order matters: most specific first, generic fallbacks last.
ADAPTERS = [
    VTTAdapter(),
    SRTAdapter(),
    GongAdapter(),
    FirefliesAdapter(),
    OtterAdapter(),
    RecallAdapter(),
    GrainAdapter(),
    GranolaAdapter(),
    JSONGenericAdapter(),
    PlaintextAdapter(),
]

ADAPTERS_BY_NAME = {a.name: a for a in ADAPTERS}


def load_transcript(path: str, adapter: str | None = None) -> Transcript:
    """Load and normalize a transcript file.

    Args:
        path: file to read.
        adapter: force a specific adapter by name (skip auto-detection).
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    if adapter:
        if adapter not in ADAPTERS_BY_NAME:
            raise ValueError(f"Unknown adapter '{adapter}'. Choices: {sorted(ADAPTERS_BY_NAME)}")
        return ADAPTERS_BY_NAME[adapter].parse(path, text)

    for ad in ADAPTERS:
        try:
            if ad.sniff(path, text):
                t = ad.parse(path, text)
                if t.turns:
                    return t
        except Exception as e:  # noqa: BLE001 - a flaky adapter shouldn't block others
            _log.debug("adapter %s failed on %s: %s", ad.name, path, e, exc_info=True)
            continue
    raise ValueError(f"No adapter could parse '{path}'. Try --adapter to force one.")


__all__ = ["load_transcript", "ADAPTERS", "ADAPTERS_BY_NAME"]
