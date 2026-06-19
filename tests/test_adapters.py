"""Adapters must normalize every supported format into the same shape."""
import json

import pytest

from gtmsi.adapters import load_transcript
from gtmsi.adapters.plaintext import PlaintextAdapter

PLAINTEXT = """\
Rep (Jordan): Thanks for hopping on. Here's what I hoped to cover — does that work?
Prospect (Sam): Sounds good.
[00:30] Jordan: Walk me through how you handle reporting today.
Sam: Honestly it's a mess of spreadsheets.
"""

VTT = """WEBVTT

1
00:00:00.000 --> 00:00:06.000
<v Riley>Thanks everyone for joining the demo today.

2
00:00:06.500 --> 00:00:12.000
<v Pat>Looking forward to it.
"""


def test_plaintext_roles_and_timestamps(tmp_path):
    p = tmp_path / "call.txt"
    p.write_text(PLAINTEXT)
    t = load_transcript(str(p))
    assert len(t.turns) == 4
    assert t.turns[0].speaker == "Jordan"
    assert t.turns[0].side == "rep"
    assert t.turns[1].side == "prospect"
    assert t.turns[2].start_seconds == 30


def test_vtt_voice_spans(tmp_path):
    p = tmp_path / "demo.vtt"
    p.write_text(VTT)
    t = load_transcript(str(p))
    assert t.source["recorder"] == "vtt"
    assert t.turns[0].speaker == "Riley"
    assert "demo today" in t.turns[0].text
    # end_seconds is now populated (regression: was always None)
    assert t.turns[0].start_seconds == 0
    assert t.turns[0].end_seconds == 6


def test_plaintext_hms_timestamp(tmp_path):
    # Regression: HH:MM:SS must be H*3600 + M*60 + S, not H*60 + M + S.
    p = tmp_path / "long.txt"
    p.write_text("[01:02:03] Rep (Jordan): An hour in.\n[00:45] Sam: Earlier.\n")
    t = load_transcript(str(p))
    assert t.turns[0].start_seconds == 3723  # 1h 2m 3s
    assert t.turns[1].start_seconds == 45    # 0m 45s


def test_generic_json_roundtrip(tmp_path):
    doc = {
        "schema_version": "1.0",
        "source": {"recorder": "generic"},
        "participants": [{"id": "A", "name": "Taylor", "side": "rep"}],
        "turns": [{"speaker": "Taylor", "side": "rep", "text": "Hi there", "start_seconds": 0}],
    }
    p = tmp_path / "norm.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.turns[0].speaker == "Taylor"
    assert t.turns[0].side == "rep"


def test_bare_list_json(tmp_path):
    doc = [{"speaker": "Alex", "text": "Hello"}, {"speaker_name": "Morgan", "sentence": "Hi"}]
    p = tmp_path / "list.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert [x.speaker for x in t.turns] == ["Alex", "Morgan"]


def test_gong_adapter_maps_speaker_and_affiliation(tmp_path):
    doc = {
        "transcript": [
            {"speakerId": "1", "sentences": [{"start": 60, "end": 600, "text": "Hi there."}]},
            {"speakerId": "2", "sentences": [{"start": 700, "end": 900, "text": "Hello."}]},
        ],
        "parties": [
            {"speakerId": "1", "name": "Jordan", "affiliation": "Internal"},
            {"speakerId": "2", "name": "Sam", "affiliation": "External"},
        ],
    }
    p = tmp_path / "call.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "gong"
    sides = {turn.speaker: turn.side for turn in t.turns}
    assert sides["Jordan"] == "rep"       # Internal -> rep
    assert sides["Sam"] == "prospect"     # External -> prospect
    assert t.turns[0].start_seconds == 0.06  # ms -> seconds


def test_fireflies_adapter(tmp_path):
    doc = {"sentences": [
        {"speaker_name": "Jordan", "text": "Hi there.", "start_time": 1.5},
        {"speaker_name": "Jordan", "text": "How are you?", "start_time": 2.5},
    ]}
    p = tmp_path / "ff.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "fireflies"
    assert t.turns[0].speaker == "Jordan"
    assert t.turns[0].start_seconds == 1.5


def test_otter_adapter(tmp_path):
    doc = {"speeches": [{"speaker": "Jordan", "transcript": "Hi there.", "start_time": 2}]}
    p = tmp_path / "ot.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "otter"
    assert t.turns[0].speaker == "Jordan"


ZOOM_VTT = """WEBVTT

1
00:00:16.239 --> 00:00:27.079
John: Hi, this is a test for audio transcripts.

2
00:00:28.079 --> 00:00:30.239
Wewake: I am going to say something here.
"""


def test_zoom_vtt_inline_speaker(tmp_path):
    # Regression: Zoom/Avoma VTT use inline "Name: text", not <v> voice spans.
    p = tmp_path / "zoom.vtt"
    p.write_text(ZOOM_VTT)
    t = load_transcript(str(p))
    assert t.source["recorder"] == "vtt"
    assert t.turns[0].speaker == "John"
    assert t.turns[0].text == "Hi, this is a test for audio transcripts."
    assert t.turns[0].start_seconds == 16
    assert t.turns[1].speaker == "Wewake"


def test_vtt_does_not_treat_note_prefix_as_speaker(tmp_path):
    # "Note:" / "Music:" etc. are cue prefixes, not speakers — must not be split out.
    vtt = "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\nNote: this is important context.\n"
    p = tmp_path / "n.vtt"
    p.write_text(vtt)
    t = load_transcript(str(p))
    assert t.turns[0].speaker == "Speaker"  # not "Note"
    assert "important context" in t.turns[0].text


def test_vtt_skips_note_block_and_no_hour_timestamp(tmp_path):
    vtt = ("WEBVTT\n\nNOTE meeting summary\nfollow-ups below\n\n"
           "00:30.000 --> 00:35.000\n<v Riley>Hello there.\n")
    p = tmp_path / "n.vtt"
    p.write_text(vtt)
    t = load_transcript(str(p))
    assert len(t.turns) == 1                 # NOTE block skipped
    assert t.turns[0].speaker == "Riley"
    assert t.turns[0].start_seconds == 30    # MM:SS (no hour) parsed


def test_vtt_voice_class_annotation(tmp_path):
    p = tmp_path / "c.vtt"
    p.write_text("WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.000\n<v.loud Riley Smith>Hi all.\n")
    t = load_transcript(str(p))
    assert t.turns[0].speaker == "Riley Smith"   # .class stripped, full name kept


def test_plaintext_heading_not_treated_as_speaker(tmp_path):
    p = tmp_path / "h.txt"
    p.write_text("Rep (Jordan): Let's recap.\nAction items: follow up with the vendor.\n")
    t = load_transcript(str(p))
    # "Action items:" is a heading, not a speaker -> folded into Jordan's turn.
    assert len(t.turns) == 1
    assert t.turns[0].speaker == "Jordan"
    assert "Action items" in t.turns[0].text


def test_generic_json_treats_numbers_as_seconds(tmp_path):
    # Regression: generic JSON must NOT guess ms; 8000 stays 8000s (not /1000).
    doc = [{"speaker": "A", "text": "hi", "start": 8000}]
    p = tmp_path / "g.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["adapter"] == "json-generic"
    assert t.turns[0].start_seconds == 8000.0


def test_grain_first_speaker_unidentified_still_detected(tmp_path):
    doc = [
        {"start": 1000, "end": 2000, "text": "hi", "speaker": "?"},  # no participant_id
        {"start": 3000, "end": 4000, "text": "yo", "speaker": "B", "participant_id": "x"},
    ]
    p = tmp_path / "g2.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "grain"
    assert t.turns[0].start_seconds == 1.0   # 1000 ms -> 1 s


def test_recall_adapter(tmp_path):
    doc = [
        {"participant": {"name": "Alice", "is_host": True},
         "words": [{"text": "Hello", "start_timestamp": {"relative": 1.5}},
                   {"text": "there", "start_timestamp": {"relative": 1.8}}]},
        {"participant": {"name": "Bob", "is_host": False},
         "words": [{"text": "Hi", "start_timestamp": {"relative": 3.0}}]},
    ]
    p = tmp_path / "recall.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "recall"
    assert t.turns[0].speaker == "Alice" and t.turns[0].side == "rep"
    assert t.turns[0].text == "Hello there" and t.turns[0].start_seconds == 1.5
    assert t.turns[1].side == "prospect"


def test_grain_adapter_milliseconds(tmp_path):
    doc = [
        {"start": 8000, "end": 9000, "text": "Hello there.", "speaker": "Obi", "participant_id": "x"},
        {"start": 11482, "end": 13000, "text": "Hi.", "speaker": "Grievous", "participant_id": None},
    ]
    p = tmp_path / "grain.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "grain"
    assert t.turns[0].speaker == "Obi"
    assert t.turns[0].start_seconds == 8.0   # 8000 ms -> 8 s
    assert t.turns[1].start_seconds == 11.482


def test_otter_real_shape(tmp_path):
    doc = {"speakers": [{"id": "s1", "name": "Dana"}],
           "utterances": [{"speaker_id": "s1", "transcript": "Hi there", "start_offset": 4500}]}
    p = tmp_path / "otter2.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "otter"
    assert t.turns[0].speaker == "Dana"
    assert t.turns[0].start_seconds == 4.5   # 4500 ms -> 4.5 s


def test_granola_audio_source_sides(tmp_path):
    # Real public-API shape (GET /v1/notes/{id}?include=transcript): the audio source
    # is nested under `speaker` (microphone == rep, speaker == the other side), and
    # timestamps are absolute ISO `start_time`/`end_time` rebased to call offsets.
    doc = [
        {"text": "Walk me through how you do this today.",
         "start_time": "2026-05-28T23:04:04.000Z", "end_time": "2026-05-28T23:04:10.000Z",
         "speaker": {"source": "microphone"}},
        {"text": "It's mostly spreadsheets.",
         "start_time": "2026-05-28T23:04:15.500Z", "end_time": "2026-05-28T23:04:19.000Z",
         "speaker": {"source": "speaker"}},
    ]
    p = tmp_path / "call.granola.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "granola"
    assert t.turns[0].side == "rep"          # microphone -> rep
    assert t.turns[0].speaker == "You"       # unnamed mic channel
    assert t.turns[0].start_seconds == 0.0   # rebased to first segment
    assert t.turns[1].side == "prospect"     # speaker (other side) -> prospect
    assert t.turns[1].speaker == "Participant"
    assert t.turns[1].start_seconds == 11.5  # 23:04:15.5 - 23:04:04
    assert t.started_at == "2026-05-28T23:04:04+00:00"


def test_granola_wrapped_note_and_legacy_source(tmp_path):
    # Accept the full note object {"transcript": [...]} and the legacy top-level
    # `source: system` desktop shape with `start_timestamp`.
    doc = {"transcript": [
        {"source": "system", "text": "Legacy other-side line.",
         "start_timestamp": "2026-05-28T23:04:04.000Z"},
    ]}
    p = tmp_path / "note.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] == "granola"
    assert t.turns[0].side == "prospect"


def test_granola_does_not_steal_generic_json(tmp_path):
    # A generic {"turns": [...]} export has no microphone/system source -> json-generic.
    doc = {"turns": [{"speaker": "Rep", "text": "Hi", "side": "rep"}]}
    p = tmp_path / "generic.json"
    p.write_text(json.dumps(doc))
    t = load_transcript(str(p))
    assert t.source["recorder"] != "granola"


def test_force_adapter(tmp_path):
    p = tmp_path / "ambiguous.txt"
    p.write_text(PLAINTEXT)
    t = load_transcript(str(p), adapter="plaintext")
    assert t.source["adapter"] == "plaintext"


def test_unknown_adapter_raises(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text(PLAINTEXT)
    with pytest.raises(ValueError):
        load_transcript(str(p), adapter="nope")


def test_talk_ratio():
    t = PlaintextAdapter().parse("x.txt", PLAINTEXT)
    r = t.talk_ratio()
    assert abs(r["rep"] + r["other"] - 1.0) < 1e-6
