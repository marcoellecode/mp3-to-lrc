"""Test della pipeline condivisa (RF-02/03/04 orchestrati insieme).

Le funzioni di separazione/trascrizione sono I/O-pesanti (subprocess, rete):
qui vengono sostituite con stub per verificare solo l'orchestrazione
(ordine delle fasi, propagazione degli errori, formati esportati, gestione
della traccia vocale mantenuta), non il loro contenuto.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lrc_engine.errors import NoVocalsError, PipelineError
from lrc_engine.pipeline import PipelineConfig, generate_subtitles


def _dummy_segments():
    return [{"start": 1.0, "end": 2.0, "text": "Ciao mondo", "words": []}]


def test_generate_subtitles_cloud_without_separation():
    config = PipelineConfig(is_local=False, do_separation=False, stt_api_key="x")
    phases: list[str] = []
    with patch(
        "lrc_engine.pipeline.transcribe_cloud", return_value=_dummy_segments()
    ) as mock_transcribe:
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_subtitles(
                Path("song.mp3"), Path(tmp), config, on_phase=phases.append
            )
    assert "Ciao mondo" in result.texts["lrc"]
    assert "srt" not in result.texts
    assert result.vocals_path is None
    assert mock_transcribe.called
    # Senza separazione, il file passato alla trascrizione e' l'originale.
    assert mock_transcribe.call_args.args[0] == Path("song.mp3")
    assert any("Trascrizione" in p for p in phases)


def test_generate_subtitles_raises_no_vocals_on_empty_transcription():
    config = PipelineConfig(is_local=False, do_separation=False, stt_api_key="x")
    empty = [{"start": 0.0, "end": 1.0, "text": "   ", "words": []}]
    with patch("lrc_engine.pipeline.transcribe_cloud", return_value=empty):
        raised = False
        try:
            with tempfile.TemporaryDirectory() as tmp:
                generate_subtitles(Path("song.mp3"), Path(tmp), config)
        except NoVocalsError:
            raised = True
        assert raised, "Doveva sollevare NoVocalsError"


def test_generate_subtitles_local_calls_separation_then_transcription():
    config = PipelineConfig(is_local=True, do_separation=True, whisper_model="tiny")
    calls: list[str] = []

    def fake_separate(*_args, **_kwargs):
        calls.append("separation")
        return Path("vocals.wav")

    def fake_transcribe(*_args, **_kwargs):
        calls.append("transcription")
        return _dummy_segments()

    with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
         patch("lrc_engine.pipeline.separate_vocals_local", side_effect=fake_separate), \
         patch("lrc_engine.pipeline.transcribe_local", side_effect=fake_transcribe):
        with tempfile.TemporaryDirectory() as tmp:
            generate_subtitles(Path("song.mp3"), Path(tmp), config)

    assert calls == ["separation", "transcription"]


def test_generate_subtitles_skips_separation_when_disabled():
    config = PipelineConfig(is_local=True, do_separation=False, whisper_model="tiny")
    received_path = {}

    def fake_transcribe(audio_path, **_kwargs):
        received_path["path"] = audio_path
        return _dummy_segments()

    with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
         patch("lrc_engine.pipeline.separate_vocals_local") as mock_separate, \
         patch("lrc_engine.pipeline.transcribe_local", side_effect=fake_transcribe):
        with tempfile.TemporaryDirectory() as tmp:
            generate_subtitles(Path("song.mp3"), Path(tmp), config)

    assert not mock_separate.called
    assert received_path["path"] == Path("song.mp3")


def test_generate_subtitles_both_formats():
    config = PipelineConfig(
        is_local=False, do_separation=False, stt_api_key="x", export_lrc=True, export_srt=True
    )
    with patch("lrc_engine.pipeline.transcribe_cloud", return_value=_dummy_segments()):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_subtitles(Path("song.mp3"), Path(tmp), config)

    assert set(result.texts.keys()) == {"lrc", "srt"}
    assert "Ciao mondo" in result.texts["lrc"]
    assert "-->" in result.texts["srt"]


def test_generate_subtitles_srt_only():
    config = PipelineConfig(
        is_local=False, do_separation=False, stt_api_key="x", export_lrc=False, export_srt=True
    )
    with patch("lrc_engine.pipeline.transcribe_cloud", return_value=_dummy_segments()):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_subtitles(Path("song.mp3"), Path(tmp), config)

    assert set(result.texts.keys()) == {"srt"}


def test_generate_subtitles_requires_at_least_one_format():
    config = PipelineConfig(
        is_local=False, do_separation=False, stt_api_key="x", export_lrc=False, export_srt=False
    )
    raised = False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            generate_subtitles(Path("song.mp3"), Path(tmp), config)
    except PipelineError:
        raised = True
    assert raised, "Doveva sollevare PipelineError se nessun formato e' selezionato"


def test_expected_outputs_reflects_enabled_formats():
    config = PipelineConfig(export_lrc=True, export_srt=True)
    outputs = config.expected_outputs(Path("song.mp3"))
    assert outputs == [Path("song.lrc"), Path("song.srt")]

    config_lrc_only = PipelineConfig(export_lrc=True, export_srt=False)
    assert config_lrc_only.expected_outputs(Path("song.mp3")) == [Path("song.lrc")]


def test_keep_vocals_saves_copy_next_to_input():
    config = PipelineConfig(is_local=True, do_separation=True, keep_vocals=True, whisper_model="tiny")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / "song.mp3"
        input_path.write_bytes(b"fake-audio")
        vocals_source = tmp_dir / "raw_vocals.wav"
        vocals_source.write_bytes(b"fake-vocals")

        with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
             patch("lrc_engine.pipeline.separate_vocals_local", return_value=vocals_source), \
             patch("lrc_engine.pipeline.transcribe_local", return_value=_dummy_segments()):
            result = generate_subtitles(input_path, tmp_dir, config)

        expected_path = tmp_dir / "song_vocals.wav"
        assert result.vocals_path == expected_path
        assert expected_path.exists()
        assert expected_path.read_bytes() == b"fake-vocals"


def test_keep_vocals_ignored_when_disabled():
    config = PipelineConfig(is_local=True, do_separation=True, keep_vocals=False, whisper_model="tiny")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / "song.mp3"
        vocals_source = tmp_dir / "raw_vocals.wav"
        vocals_source.write_bytes(b"fake-vocals")

        with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
             patch("lrc_engine.pipeline.separate_vocals_local", return_value=vocals_source), \
             patch("lrc_engine.pipeline.transcribe_local", return_value=_dummy_segments()):
            result = generate_subtitles(input_path, tmp_dir, config)

    assert result.vocals_path is None
    assert not (tmp_dir / "song_vocals.wav").exists()


def test_keep_vocals_path_attached_to_exception_on_later_failure():
    config = PipelineConfig(is_local=True, do_separation=True, keep_vocals=True, whisper_model="tiny")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / "song.mp3"
        vocals_source = tmp_dir / "raw_vocals.wav"
        vocals_source.write_bytes(b"fake-vocals")
        empty = [{"start": 0.0, "end": 1.0, "text": "   ", "words": []}]

        with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
             patch("lrc_engine.pipeline.separate_vocals_local", return_value=vocals_source), \
             patch("lrc_engine.pipeline.transcribe_local", return_value=empty):
            caught = None
            try:
                generate_subtitles(input_path, tmp_dir, config)
            except NoVocalsError as exc:
                caught = exc

    assert caught is not None
    assert caught.vocals_path == tmp_dir / "song_vocals.wav"


def test_on_segment_forwarded_to_transcribe_cloud():
    config = PipelineConfig(is_local=False, do_separation=False, stt_api_key="x")
    received = {}

    def fake_transcribe(*_args, **kwargs):
        received["on_segment"] = kwargs.get("on_segment")
        return _dummy_segments()

    on_segment = lambda text: None  # noqa: E731
    with patch("lrc_engine.pipeline.transcribe_cloud", side_effect=fake_transcribe):
        with tempfile.TemporaryDirectory() as tmp:
            generate_subtitles(
                Path("song.mp3"), Path(tmp), config, on_segment=on_segment
            )

    assert received["on_segment"] is on_segment


def test_on_segment_forwarded_to_transcribe_local():
    config = PipelineConfig(is_local=True, do_separation=False, whisper_model="tiny")
    received = {}

    def fake_transcribe(*_args, **kwargs):
        received["on_segment"] = kwargs.get("on_segment")
        return _dummy_segments()

    on_segment = lambda text: None  # noqa: E731
    with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
         patch("lrc_engine.pipeline.transcribe_local", side_effect=fake_transcribe):
        with tempfile.TemporaryDirectory() as tmp:
            generate_subtitles(
                Path("song.mp3"), Path(tmp), config, on_segment=on_segment
            )

    assert received["on_segment"] is on_segment


def test_vocals_only_import_skips_separation():
    """Simula l'import di un file gia' vocale: do_separation=False forzato dalla UI."""
    config = PipelineConfig(is_local=True, do_separation=False, whisper_model="tiny")
    received_path = {}

    def fake_transcribe(audio_path, **_kwargs):
        received_path["path"] = audio_path
        return _dummy_segments()

    with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
         patch("lrc_engine.pipeline.separate_vocals_local") as mock_separate, \
         patch("lrc_engine.pipeline.transcribe_local", side_effect=fake_transcribe):
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_subtitles(Path("vocals_only.wav"), Path(tmp), config)

    assert not mock_separate.called
    assert received_path["path"] == Path("vocals_only.wav")
    assert result.vocals_path is None


def test_reference_lyrics_use_align_local_instead_of_transcription():
    """Simula la scheda "Risincronizza LRC": il testo importato sostituisce
    la trascrizione con un riallineamento forzato."""
    config = PipelineConfig(is_local=True, do_separation=False, whisper_model="tiny", language="en")
    received = {}

    def fake_align(audio_path, lyrics_lines, **kwargs):
        received["audio_path"] = audio_path
        received["lyrics_lines"] = lyrics_lines
        received["kwargs"] = kwargs
        return _dummy_segments()

    with patch("lrc_engine.pipeline.detect_device", return_value=("cpu", True)), \
         patch("lrc_engine.pipeline.align_local", side_effect=fake_align) as mock_align, \
         patch("lrc_engine.pipeline.transcribe_local") as mock_transcribe:
        with tempfile.TemporaryDirectory() as tmp:
            result = generate_subtitles(
                Path("song.mp3"), Path(tmp), config,
                reference_lyrics=["Riga uno", "Riga due"],
            )

    assert mock_align.called
    assert not mock_transcribe.called
    assert received["audio_path"] == Path("song.mp3")
    assert received["lyrics_lines"] == ["Riga uno", "Riga due"]
    assert received["kwargs"]["language"] == "en"
    assert "Ciao mondo" in result.texts["lrc"]


def test_reference_lyrics_requires_local_mode():
    config = PipelineConfig(is_local=False, do_separation=False, stt_api_key="x", language="en")
    raised = False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            generate_subtitles(
                Path("song.mp3"), Path(tmp), config, reference_lyrics=["Riga"]
            )
    except PipelineError:
        raised = True
    assert raised, "Il riallineamento in modalità Cloud deve essere rifiutato"


def test_reference_lyrics_requires_explicit_language():
    config = PipelineConfig(is_local=True, do_separation=False, whisper_model="tiny", language=None)
    raised = False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            generate_subtitles(
                Path("song.mp3"), Path(tmp), config, reference_lyrics=["Riga"]
            )
    except PipelineError:
        raised = True
    assert raised, "Il riallineamento senza lingua esplicita (Auto-detect) deve essere rifiutato"


def test_lrc_enhanced_option_adds_word_tags():
    segments = [
        {
            "start": 1.0,
            "end": 2.0,
            "text": "Ciao mondo",
            "words": [
                {"word": "Ciao", "start": 1.0, "end": 1.4},
                {"word": "mondo", "start": 1.5, "end": 2.0},
            ],
        }
    ]
    base = dict(is_local=False, do_separation=False, stt_api_key="x", export_srt=True)
    with patch("lrc_engine.pipeline.transcribe_cloud", return_value=segments):
        with tempfile.TemporaryDirectory() as tmp:
            plain = generate_subtitles(Path("song.mp3"), Path(tmp), PipelineConfig(**base))
            enhanced = generate_subtitles(
                Path("song.mp3"), Path(tmp), PipelineConfig(**base, lrc_enhanced=True)
            )

    assert plain.texts["lrc"] == "[00:01.00] Ciao mondo\n"
    assert enhanced.texts["lrc"] == "[00:01.00] <00:01.00> Ciao <00:01.50> mondo <00:02.00>\n"
    # l'SRT non e' influenzato dall'opzione Enhanced
    assert enhanced.texts["srt"] == plain.texts["srt"]
