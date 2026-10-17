"""Test del parser di import .lrc (resync): estrazione del solo testo."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lrc_engine.lrc_import import parse_lrc_lyrics


def test_parses_plain_lines():
    content = "[00:14.32] Nel mezzo del cammin\n[00:18.00] di nostra vita\n"
    assert parse_lrc_lyrics(content) == ["Nel mezzo del cammin", "di nostra vita"]


def test_skips_metadata_tags():
    content = (
        "[ti:When Love Takes Over]\n"
        "[ar:Kelly Rowland]\n"
        "[00:08.03] It's complicated\n"
    )
    assert parse_lrc_lyrics(content) == ["It's complicated"]


def test_skips_empty_and_timestamp_only_lines():
    content = "[00:00.00] remove me\n[00:02.00]\n[00:10.00] Primo verso\n\n[00:12.00]   \n"
    assert parse_lrc_lyrics(content) == ["Primo verso"]


def test_handles_multiple_leading_timestamps_on_same_line():
    content = "[00:10.00][00:20.00] Ritornello ripetuto\n"
    assert parse_lrc_lyrics(content) == ["Ritornello ripetuto"]


def test_preserves_line_order():
    content = "[00:30.00] Seconda\n[00:10.00] Prima\n"
    assert parse_lrc_lyrics(content) == ["Seconda", "Prima"]


def test_empty_content_returns_empty_list():
    assert parse_lrc_lyrics("") == []
    assert parse_lrc_lyrics("[ti:Solo metadati]\n[ar:Nessuno]\n") == []


def test_strips_enhanced_lrc_word_tags():
    content = (
        "[00:12.00] <00:12.00> Nel <00:12.50> mezzo <00:13.00> del <00:13.50> cammin <00:13.90>\n"
        "[00:15.00]<00:15.00>di <00:15.40>nostra <00:15.80>vita\n"
    )
    assert parse_lrc_lyrics(content) == ["Nel mezzo del cammin", "di nostra vita"]


def test_enhanced_lrc_roundtrip():
    from lrc_engine.lrc import segments_to_lrc

    words = [
        {"word": w, "start": 1.0 + i, "end": 1.5 + i}
        for i, w in enumerate("It's complicated, really".split())
    ]
    segments = [{"start": 1.0, "end": 3.5, "text": "It's complicated, really", "words": words}]
    content = segments_to_lrc(segments, enhanced=True, sync_anchor=True)
    assert parse_lrc_lyrics(content) == ["It's complicated, really"]
