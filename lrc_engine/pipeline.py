"""Pipeline condivisa: separazione + trascrizione + formattazione sottotitoli.

Indipendente dalla UI (desktop CustomTkinter, CLI, ...): riceve una
configurazione esplicita invece di leggere stato globale, cosi' la stessa
logica puo' girare in un thread di lavoro senza toccare widget.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .alignment import align_local
from .errors import NoVocalsError, PipelineError
from .hardware import detect_device
from .lrc import DEFAULT_MAX_LINE_LEN, segments_to_lrc, segments_to_srt
from .separation import separate_vocals_cloud, separate_vocals_local
from .transcription import has_speech, transcribe_cloud, transcribe_local

OnPhase = Callable[[str], None]
OnSegment = Callable[[str], None]

# Testi di fase condivisi: la UI li usa per mappare la progress bar senza
# duplicare le stringhe letterali.
PHASE_SEPARATION = "Separazione della traccia vocale (Demucs)..."
PHASE_TRANSCRIPTION = "Trascrizione e sincronizzazione word-level..."
PHASE_ALIGNMENT = "Riallineamento del testo importato con l'audio..."
PHASE_FORMATTING = "Formattazione sottotitoli..."

# Estensione di file per ciascun formato esportabile.
FORMAT_EXTENSIONS = {"lrc": ".lrc", "srt": ".srt"}

# Suffisso del file vocale mantenuto accanto all'originale (RF-02 + keep_vocals).
VOCALS_SUFFIX = "_vocals"


@dataclass
class PipelineConfig:
    is_local: bool = True
    language: str | None = None
    do_separation: bool = True
    keep_vocals: bool = False
    whisper_model: str = "small"
    demucs_model: str = "htdemucs"
    stt_provider: str = "openai"
    stt_api_key: str = ""
    replicate_api_key: str = ""
    max_line_len: int = DEFAULT_MAX_LINE_LEN
    sync_anchor: bool = False
    export_lrc: bool = True
    export_srt: bool = False
    # LRC in formato Enhanced (tag <mm:ss.xx> per ogni parola), stesso file .lrc
    lrc_enhanced: bool = False

    def output_path_for(self, audio_path: Path, fmt: str) -> Path:
        return audio_path.with_suffix(FORMAT_EXTENSIONS[fmt])

    def expected_outputs(self, audio_path: Path) -> list[Path]:
        formats = [f for f, enabled in self.enabled_formats.items() if enabled]
        return [self.output_path_for(audio_path, fmt) for fmt in formats]

    @property
    def enabled_formats(self) -> dict[str, bool]:
        return {"lrc": self.export_lrc, "srt": self.export_srt}


@dataclass
class PipelineResult:
    texts: dict[str, str] = field(default_factory=dict)
    vocals_path: Path | None = None


def generate_subtitles(
    input_path: Path,
    tmp_dir: Path,
    config: PipelineConfig,
    on_phase: OnPhase = lambda msg: None,
    on_segment: OnSegment | None = None,
    reference_lyrics: list[str] | None = None,
) -> PipelineResult:
    """Esegue le macro-fasi della SRS su un singolo file audio.

    Se fornito, ``on_segment`` viene chiamato con il testo di ogni frase non
    appena viene trascritta (in locale: durante la decodifica stessa; in
    cloud: subito dopo la ricezione della risposta, in ordine), cosi' la UI
    puo' mostrare il testo mentre viene generato invece di attendere la
    formattazione finale. Non si applica quando si passa ``reference_lyrics``
    (il testo e' gia' noto in anticipo).

    Se fornito, ``reference_lyrics`` (una riga per elemento, tipicamente
    importata da un .lrc esistente con ``lrc_engine.lrc_import``) sostituisce
    la trascrizione con un **riallineamento forzato**: il testo resta quello
    fornito, solo i timestamp vengono ricalcolati su questo specifico file
    audio. Utile per correggere un .lrc con tempi non piu' corretti perche'
    riferito a una versione diversa del brano. Richiede la modalita' Locale e
    una lingua esplicita (non Auto-detect).

    Restituisce un ``PipelineResult`` con i testi dei formati richiesti da
    ``config`` (almeno uno, garantito dal chiamante/UI) e, se
    ``config.keep_vocals`` e' attivo, il percorso della traccia vocale
    salvata accanto a ``input_path``. ``tmp_dir`` ospita gli eventuali file
    intermedi della separazione; il chiamante e' responsabile di crearla ed
    eliminarla (RNF-03).

    Se un errore interrompe la pipeline dopo che la voce e' gia' stata
    salvata, il percorso resta comunque raggiungibile tramite l'attributo
    ``vocals_path`` aggiunto all'eccezione sollevata.
    """
    if not config.export_lrc and not config.export_srt:
        raise PipelineError("Nessun formato di esportazione selezionato (LRC/SRT).")

    if reference_lyrics is not None:
        if not config.is_local:
            raise PipelineError(
                "Il riallineamento di un .lrc importato richiede la modalità "
                "Locale: le API cloud trascrivono soltanto, non allineano un "
                "testo già noto."
            )
        if not config.language:
            raise PipelineError(
                "Seleziona una lingua specifica (non Auto-detect) per "
                "riallineare un testo importato."
            )

    vocals_saved_path: Path | None = None

    if config.do_separation:
        on_phase(PHASE_SEPARATION)
        if config.is_local:
            device, _ = detect_device()
            vocal_path = separate_vocals_local(
                input_path, tmp_dir, model=config.demucs_model, device=device
            )
        else:
            vocal_path = separate_vocals_cloud(
                input_path, tmp_dir, api_key=config.replicate_api_key
            )
        if config.keep_vocals:
            vocals_saved_path = _save_vocals_copy(input_path, vocal_path)
    else:
        vocal_path = input_path

    try:
        if reference_lyrics is not None:
            on_phase(PHASE_ALIGNMENT)
            device, _ = detect_device()
            segments = align_local(
                vocal_path,
                reference_lyrics,
                language=config.language,
                model_size=config.whisper_model,
                device=device,
            )
        else:
            on_phase(PHASE_TRANSCRIPTION)
            if config.is_local:
                device, _ = detect_device()
                segments = transcribe_local(
                    vocal_path,
                    model_size=config.whisper_model,
                    language=config.language,
                    device=device,
                    on_segment=on_segment,
                )
            else:
                segments = transcribe_cloud(
                    vocal_path,
                    provider=config.stt_provider,
                    api_key=config.stt_api_key,
                    language=config.language,
                    on_segment=on_segment,
                )

        # Edge case 1: brano strumentale
        if not has_speech(segments):
            raise NoVocalsError()

        on_phase(PHASE_FORMATTING)
        texts: dict[str, str] = {}
        if config.export_lrc:
            texts["lrc"] = segments_to_lrc(
                segments,
                max_line_len=config.max_line_len,
                sync_anchor=config.sync_anchor,
                enhanced=config.lrc_enhanced,
            )
        if config.export_srt:
            texts["srt"] = segments_to_srt(
                segments, max_line_len=config.max_line_len, sync_anchor=config.sync_anchor
            )
    except Exception as exc:
        # La voce e' gia' su disco anche se la trascrizione fallisce dopo:
        # non la si fa perdere, la si segnala attraverso l'eccezione stessa.
        if vocals_saved_path is not None:
            exc.vocals_path = vocals_saved_path
        raise

    return PipelineResult(texts=texts, vocals_path=vocals_saved_path)


def _save_vocals_copy(input_path: Path, vocal_path: Path) -> Path:
    """Copia la traccia vocale isolata accanto al file audio originale."""
    dest = input_path.with_name(f"{input_path.stem}{VOCALS_SUFFIX}{vocal_path.suffix}")
    try:
        shutil.copy2(vocal_path, dest)
    except OSError as exc:
        raise PipelineError(
            f"Impossibile salvare la traccia vocale in {dest}: {exc}"
        ) from exc
    return dest
