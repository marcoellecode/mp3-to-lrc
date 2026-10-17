"""Test del meccanismo di streaming del testo trascritto (on_segment).

Verificano il "tap" sul generatore lazy di faster-whisper usato in locale
(senza caricare un vero modello Whisper) e il callback per-segmento usato
in cloud (senza chiamate di rete reali).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lrc_engine.transcription import _live_segment_tap, transcribe_cloud


class _FakeSegment:
    def __init__(self, text: str):
        self.text = text


class _FakeModelWithHook:
    """Simula un modello stable-ts con l'hook grezzo di faster-whisper."""

    def __init__(self):
        def raw_transcribe(*_args, **_kwargs):
            def generator():
                yield _FakeSegment("Ciao")
                yield _FakeSegment("Mondo")

            return generator(), object()

        self.transcribe_original = raw_transcribe


class _FakeModelWithoutHook:
    """Simula un modello che non espone l'hook (fallback sicuro)."""


def test_live_segment_tap_forwards_text_and_preserves_segments():
    model = _FakeModelWithHook()
    seen: list[str] = []

    with _live_segment_tap(model, seen.append):
        gen, _info = model.transcribe_original()
        collected = list(gen)

    assert seen == ["Ciao", "Mondo"]
    assert [s.text for s in collected] == ["Ciao", "Mondo"]


def test_live_segment_tap_restores_original_after_use():
    model = _FakeModelWithHook()
    original = model.transcribe_original

    with _live_segment_tap(model, lambda text: None):
        assert model.transcribe_original is not original

    assert model.transcribe_original is original


def test_live_segment_tap_noop_without_callback():
    model = _FakeModelWithHook()
    original = model.transcribe_original

    with _live_segment_tap(model, None):
        assert model.transcribe_original is original


def test_live_segment_tap_noop_without_hook():
    model = _FakeModelWithoutHook()
    with _live_segment_tap(model, lambda text: None):
        pass  # non deve sollevare eccezioni se il modello non espone l'hook


def test_live_segment_tap_swallows_callback_errors():
    model = _FakeModelWithHook()

    def failing_callback(_text):
        raise RuntimeError("boom")

    with _live_segment_tap(model, failing_callback):
        gen, _info = model.transcribe_original()
        collected = list(gen)  # non deve propagare l'errore del callback

    assert [s.text for s in collected] == ["Ciao", "Mondo"]


def test_transcribe_cloud_calls_on_segment_in_order():
    fake_resp = MagicMock()
    fake_resp.segments = [
        {"start": 0.0, "end": 1.0, "text": "Prima riga"},
        {"start": 1.0, "end": 2.0, "text": "Seconda riga"},
    ]
    fake_resp.words = []
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = fake_resp

    calls: list[str] = []
    with patch("openai.OpenAI", return_value=fake_client), \
         patch("builtins.open", mock_open(read_data=b"fake-audio")):
        transcribe_cloud(
            Path("vocals.wav"), provider="openai", api_key="x", on_segment=calls.append
        )

    assert calls == ["Prima riga", "Seconda riga"]


def test_transcribe_cloud_without_on_segment_does_not_raise():
    fake_resp = MagicMock()
    fake_resp.segments = [{"start": 0.0, "end": 1.0, "text": "Ciao"}]
    fake_resp.words = []
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = fake_resp

    with patch("openai.OpenAI", return_value=fake_client), \
         patch("builtins.open", mock_open(read_data=b"fake-audio")):
        segments = transcribe_cloud(Path("vocals.wav"), provider="openai", api_key="x")

    assert segments[0]["text"] == "Ciao"


def _import_raising(exc):
    """Sostituto di __import__ che fa fallire solo l'import di stable_whisper."""
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "stable_whisper":
            raise exc
        return real_import(name, *args, **kwargs)

    return fake_import


def test_missing_stable_ts_reports_not_installed():
    import pytest

    from lrc_engine.errors import MissingDependencyError
    from lrc_engine.transcription import _load_local_model

    exc = ModuleNotFoundError("No module named 'stable_whisper'", name="stable_whisper")
    with patch("builtins.__import__", _import_raising(exc)):
        with pytest.raises(MissingDependencyError, match="non e' installata"):
            _load_local_model("tiny", "cpu", "int8")


def test_broken_stable_ts_import_reports_real_cause():
    import pytest

    from lrc_engine.errors import MissingDependencyError
    from lrc_engine.transcription import _load_local_model

    exc = ImportError("DLL load failed while importing _nrt_python", name="numba.core.runtime._nrt_python")
    with patch("builtins.__import__", _import_raising(exc)):
        with pytest.raises(MissingDependencyError, match="DLL load failed") as info:
            _load_local_model("tiny", "cpu", "int8")
    assert "non e' installata" not in str(info.value)
