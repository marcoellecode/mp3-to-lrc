"""Riallineamento forzato di un testo gia' noto sull'audio (resync).

Dato un testo lirico gia' noto (importato da un file .lrc esistente) e una
traccia audio (idealmente la sola voce isolata), calcola nuovi timestamp
word-level accurati per QUELLA specifica versione dell'audio. Utile quando
l'.lrc a disposizione proviene da una versione diversa del brano (radio
edit, extended mix, remaster con intro piu' lungo/corto...) e i tempi non
coincidono piu' con il file in mano: il testo resta quello importato, solo
i tempi vengono ricalcolati da zero.

Disponibile solo in modalita' locale: la forced-alignment usa i pesi
cross-attention del modello Whisper, non esposti dalle API cloud di
trascrizione (OpenAI/Groq trascrivono soltanto, non allineano testo noto).
"""

from __future__ import annotations

from pathlib import Path

from .errors import PipelineError
from .transcription import _load_local_model, whisper_result_to_segments


def align_local(
    audio_path: Path,
    lyrics_lines: list[str],
    language: str,
    model_size: str = "small",
    device: str = "cpu",
) -> list[dict]:
    """Riallinea ``lyrics_lines`` (testo gia' noto, una riga per elemento)
    su ``audio_path``, preservando l'ordine e la suddivisione in righe."""
    if not lyrics_lines:
        raise PipelineError(
            "Il file .lrc importato non contiene righe di testo da riallineare."
        )
    if not language:
        raise PipelineError(
            "Seleziona una lingua specifica (non Auto-detect) per riallineare "
            "un testo importato: la forced-alignment richiede di sapere in "
            "anticipo la lingua del testo."
        )

    compute_type = "float16" if device == "cuda" else "int8"
    model = _load_local_model(model_size, device, compute_type)

    text = "\n".join(lyrics_lines)
    result = model.align(
        str(audio_path),
        text,
        language=language,
        original_split=True,
        vad=True,
    )

    return whisper_result_to_segments(result)
