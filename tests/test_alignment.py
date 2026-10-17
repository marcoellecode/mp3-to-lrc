"""Test del riallineamento forzato (resync di un .lrc importato).

Il modello stable-ts viene sostituito con uno stub: qui si verifica solo
l'orchestrazione (parametri passati a ``model.align``, conversione del
risultato, validazioni), non la qualita' reale dell'allineamento (gia'
verificata manualmente con un file audio reale).
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lrc_engine.alignment import align_local
from lrc_engine.errors import PipelineError


class _FakeWord:
    def __init__(self, word, start, end):
        self.word = word
        self.start = start
        self.end = end


class _FakeSegment:
    def __init__(self, start, end, text, words=None):
        self.start = start
        self.end = end
        self.text = text
        self.words = words or []


class _FakeResult:
    def __init__(self, segments):
        self.segments = segments


class _FakeModel:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def align(self, audio, text, **kwargs):
        self.calls.append((audio, text, kwargs))
        return self._result


def test_align_local_converts_result_to_shared_segment_shape():
    fake_result = _FakeResult([
        _FakeSegment(
            8.0, 10.0, "It's complicated",
            [_FakeWord("It's", 8.0, 8.5), _FakeWord("complicated", 8.5, 10.0)],
        ),
    ])
    model = _FakeModel(fake_result)
    with patch("lrc_engine.alignment._load_local_model", return_value=model):
        segments = align_local(Path("vocals.wav"), ["It's complicated"], language="en")

    assert segments == [
        {
            "start": 8.0,
            "end": 10.0,
            "text": "It's complicated",
            "words": [
                {"word": "It's", "start": 8.0, "end": 8.5},
                {"word": "complicated", "start": 8.5, "end": 10.0},
            ],
        }
    ]


def test_align_local_forwards_language_and_preserves_line_splits():
    model = _FakeModel(_FakeResult([]))
    with patch("lrc_engine.alignment._load_local_model", return_value=model):
        align_local(Path("vocals.wav"), ["Riga uno", "Riga due"], language="it")

    audio_arg, text_arg, kwargs = model.calls[0]
    assert audio_arg == "vocals.wav"
    assert text_arg == "Riga uno\nRiga due"
    assert kwargs["language"] == "it"
    assert kwargs["original_split"] is True


def test_align_local_requires_lyrics_lines():
    raised = False
    try:
        align_local(Path("vocals.wav"), [], language="en")
    except PipelineError:
        raised = True
    assert raised, "Doveva sollevare PipelineError con lista di righe vuota"


def test_align_local_requires_explicit_language():
    raised = False
    try:
        align_local(Path("vocals.wav"), ["Testo"], language=None)
    except PipelineError:
        raised = True
    assert raised, "Doveva sollevare PipelineError senza una lingua esplicita"
