"""Test unitari del formattatore LRC (RF-04) e degli helper della pipeline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lrc_engine.lrc import (
    format_srt_timestamp,
    format_timestamp,
    segments_to_lrc,
    segments_to_srt,
)
from lrc_engine.transcription import has_speech


def test_format_timestamp():
    assert format_timestamp(0) == "[00:00.00]"
    assert format_timestamp(14.32) == "[00:14.32]"
    assert format_timestamp(75.5) == "[01:15.50]"
    assert format_timestamp(600) == "[10:00.00]"
    assert format_timestamp(-3) == "[00:00.00]"  # clamp a zero


def test_short_segment_single_line():
    segments = [
        {
            "start": 14.32,
            "end": 18.0,
            "text": "Nel mezzo del cammin di nostra vita",
            "words": [],
        }
    ]
    lrc = segments_to_lrc(segments)
    assert lrc == "[00:14.32] Nel mezzo del cammin di nostra vita\n"


def test_long_segment_split_by_words():
    words = [
        {"word": w, "start": 10.0 + i, "end": 10.5 + i}
        for i, w in enumerate(
            "una frase molto lunga che sicuramente supera il limite "
            "massimo di caratteri consentito per riga".split()
        )
    ]
    segments = [
        {
            "start": 10.0,
            "end": 25.0,
            "text": " ".join(w["word"] for w in words),
            "words": words,
        }
    ]
    lrc = segments_to_lrc(segments, max_line_len=45)
    lines = lrc.strip().split("\n")
    assert len(lines) > 1
    for line in lines:
        ts, _, text = line.partition("] ")
        assert ts.startswith("[")
        assert len(text) <= 45
    # La prima riga parte dalla prima parola
    assert lines[0].startswith("[00:10.00]")


def test_long_segment_without_words_interpolates():
    text = (
        "una frase molto lunga senza timestamp per parola che deve comunque "
        "essere spezzata in righe con tempi interpolati"
    )
    segments = [{"start": 0.0, "end": 20.0, "text": text, "words": []}]
    lrc = segments_to_lrc(segments, max_line_len=40)
    lines = lrc.strip().split("\n")
    assert len(lines) > 1
    assert lines[0].startswith("[00:00.00]")
    # I timestamp devono essere crescenti
    stamps = [line.split("]")[0] for line in lines]
    assert stamps == sorted(stamps)


def test_empty_segments_skipped():
    segments = [
        {"start": 1.0, "end": 2.0, "text": "  ", "words": []},
        {"start": 3.0, "end": 4.0, "text": "Ciao", "words": []},
    ]
    lrc = segments_to_lrc(segments)
    assert lrc == "[00:03.00] Ciao\n"


def test_lines_sorted_by_time():
    segments = [
        {"start": 30.0, "end": 32.0, "text": "Seconda", "words": []},
        {"start": 10.0, "end": 12.0, "text": "Prima", "words": []},
    ]
    lrc = segments_to_lrc(segments)
    assert lrc.index("Prima") < lrc.index("Seconda")


def test_sync_anchor_added_when_first_line_after_two_seconds():
    segments = [{"start": 10.0, "end": 12.0, "text": "Primo verso", "words": []}]
    lrc = segments_to_lrc(segments, sync_anchor=True)
    lines = lrc.strip("\n").split("\n")
    assert lines[0] == "[00:00.00] remove me"
    assert lines[1] == "[00:02.00]"  # riga vuota che chiude il marker dopo 2 s
    assert lines[2] == "[00:10.00] Primo verso"


def test_sync_anchor_no_closing_line_if_lyric_starts_early():
    segments = [{"start": 1.2, "end": 3.0, "text": "Subito", "words": []}]
    lrc = segments_to_lrc(segments, sync_anchor=True)
    lines = lrc.strip("\n").split("\n")
    assert lines[0] == "[00:00.00] remove me"
    # Il primo verso arriva prima dei 2 s: e' lui a sostituire il marker
    assert lines[1] == "[00:01.20] Subito"
    assert len(lines) == 2


def test_sync_anchor_skipped_if_lyric_starts_at_zero():
    segments = [{"start": 0.0, "end": 2.0, "text": "Dallo zero", "words": []}]
    lrc = segments_to_lrc(segments, sync_anchor=True)
    assert "remove me" not in lrc
    assert lrc.startswith("[00:00.00] Dallo zero")


def test_sync_anchor_disabled_by_default():
    segments = [{"start": 10.0, "end": 12.0, "text": "Primo verso", "words": []}]
    assert "remove me" not in segments_to_lrc(segments)


def test_format_srt_timestamp():
    assert format_srt_timestamp(0) == "00:00:00,000"
    assert format_srt_timestamp(14.32) == "00:00:14,320"
    assert format_srt_timestamp(75.5) == "00:01:15,500"
    assert format_srt_timestamp(3661.234) == "01:01:01,234"
    assert format_srt_timestamp(-3) == "00:00:00,000"  # clamp a zero


def test_srt_short_segment_single_block():
    segments = [
        {
            "start": 14.32,
            "end": 18.0,
            "text": "Nel mezzo del cammin di nostra vita",
            "words": [],
        }
    ]
    srt = segments_to_srt(segments)
    assert srt == (
        "1\n00:00:14,320 --> 00:00:18,000\nNel mezzo del cammin di nostra vita\n\n"
    )


def test_srt_multiple_blocks_numbered_and_separated_by_blank_line():
    segments = [
        {"start": 1.0, "end": 2.0, "text": "Prima", "words": []},
        {"start": 3.0, "end": 4.0, "text": "Seconda", "words": []},
    ]
    srt = segments_to_srt(segments)
    assert srt == (
        "1\n00:00:01,000 --> 00:00:02,000\nPrima\n"
        "\n"
        "2\n00:00:03,000 --> 00:00:04,000\nSeconda\n\n"
    )


def test_srt_long_segment_split_by_words_multiple_blocks():
    words = [
        {"word": w, "start": 10.0 + i, "end": 10.5 + i}
        for i, w in enumerate(
            "una frase molto lunga che sicuramente supera il limite "
            "massimo di caratteri consentito per riga".split()
        )
    ]
    segments = [
        {
            "start": 10.0,
            "end": 25.0,
            "text": " ".join(w["word"] for w in words),
            "words": words,
        }
    ]
    srt = segments_to_srt(segments, max_line_len=45)
    assert srt.count("-->") > 1
    assert srt.startswith("1\n00:00:10,000 -->")


def test_srt_sync_anchor_block_added():
    segments = [{"start": 10.0, "end": 12.0, "text": "Primo verso", "words": []}]
    srt = segments_to_srt(segments, sync_anchor=True)
    assert srt == (
        "1\n00:00:00,000 --> 00:00:02,000\nremove me\n"
        "\n"
        "2\n00:00:10,000 --> 00:00:12,000\nPrimo verso\n\n"
    )


def test_srt_sync_anchor_end_clamped_if_lyric_starts_early():
    segments = [{"start": 1.2, "end": 3.0, "text": "Subito", "words": []}]
    srt = segments_to_srt(segments, sync_anchor=True)
    assert srt == (
        "1\n00:00:00,000 --> 00:00:01,200\nremove me\n"
        "\n"
        "2\n00:00:01,200 --> 00:00:03,000\nSubito\n\n"
    )


def test_srt_sync_anchor_skipped_if_lyric_starts_at_zero():
    segments = [{"start": 0.0, "end": 2.0, "text": "Dallo zero", "words": []}]
    srt = segments_to_srt(segments, sync_anchor=True)
    assert "remove me" not in srt
    assert srt.startswith("1\n00:00:00,000 --> 00:00:02,000\nDallo zero\n")


def test_srt_min_duration_enforced_for_very_short_words():
    segments = [
        {
            "start": 5.0,
            "end": 5.05,
            "text": "Hi",
            "words": [{"word": "Hi", "start": 5.0, "end": 5.05}],
        }
    ]
    srt = segments_to_srt(segments)
    assert "00:00:05,000 --> 00:00:05,300" in srt


def test_srt_blocks_never_overlap():
    segments = [
        {"start": 1.0, "end": 5.0, "text": "Primo", "words": []},
        {"start": 1.5, "end": 6.0, "text": "Sovrapposto", "words": []},
    ]
    srt = segments_to_srt(segments)
    # Il primo blocco viene troncato all'inizio del secondo: nessuna sovrapposizione.
    assert "00:00:01,000 --> 00:00:01,500" in srt
    assert "00:00:01,500 --> 00:00:06,000" in srt


def test_has_speech():
    assert not has_speech([])
    assert not has_speech([{"start": 0, "end": 1, "text": "  ", "words": []}])
    assert has_speech([{"start": 0, "end": 1, "text": "La", "words": []}])


def _words(text, start=12.0, step=0.5):
    return [
        {"word": w, "start": start + i * step, "end": start + i * step + 0.4}
        for i, w in enumerate(text.split())
    ]


def test_enhanced_lrc_word_tags():
    words = _words("Nel mezzo del cammin")
    segments = [{"start": 12.0, "end": 14.0, "text": "Nel mezzo del cammin", "words": words}]
    lrc = segments_to_lrc(segments, enhanced=True)
    assert lrc == (
        "[00:12.00] <00:12.00> Nel <00:12.50> mezzo <00:13.00> del "
        "<00:13.50> cammin <00:13.90>\n"
    )


def test_enhanced_lrc_disabled_by_default():
    words = _words("Nel mezzo")
    segments = [{"start": 12.0, "end": 13.0, "text": "Nel mezzo", "words": words}]
    assert "<" not in segments_to_lrc(segments)


def test_enhanced_lrc_falls_back_to_plain_line_without_words():
    segments = [{"start": 5.0, "end": 8.0, "text": "Senza parole", "words": []}]
    assert segments_to_lrc(segments, enhanced=True) == "[00:05.00] Senza parole\n"


def test_enhanced_lrc_split_lines_keep_their_own_words():
    text = "una frase molto lunga che sicuramente supera il limite massimo per riga"
    words = _words(text, start=10.0, step=1.0)
    segments = [{"start": 10.0, "end": 25.0, "text": text, "words": words}]
    lines = segments_to_lrc(segments, max_line_len=30, enhanced=True).strip().split("\n")
    assert len(lines) > 1
    tagged_words = []
    for line in lines:
        line_ts, _, body = line.partition("] ")
        # il timestamp di riga coincide con il tag della prima parola
        assert body.startswith(f"<{line_ts[1:]}>")
        tagged_words += [t for t in body.split() if not t.startswith("<")]
    assert tagged_words == text.split()


def test_enhanced_lrc_with_sync_anchor():
    words = _words("Primo verso", start=10.0)
    segments = [{"start": 10.0, "end": 11.0, "text": "Primo verso", "words": words}]
    lines = segments_to_lrc(segments, enhanced=True, sync_anchor=True).strip().split("\n")
    assert lines[0] == "[00:00.00] remove me"
    assert lines[1] == "[00:02.00]"
    assert lines[2].startswith("[00:10.00] <00:10.00> Primo")
