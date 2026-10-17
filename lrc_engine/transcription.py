"""Modulo di trascrizione e sincronizzazione word-level (RF-03).

- Locale: stable-ts (Stable Whisper) su backend faster-whisper.
- Cloud: OpenAI Whisper-1 oppure Groq whisper-large-v3 (API OpenAI-compatibile).

Entrambe le vie restituiscono la stessa rappresentazione intermedia:
    [{"start": float, "end": float, "text": str,
      "words": [{"word": str, "start": float, "end": float}, ...]}, ...]
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .errors import ApiError, MissingDependencyError, NetworkError

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

_NET_ERR_MSG = "Errore di rete: Impossibile raggiungere i server di trascrizione"

OnSegment = Callable[[str], None]


# ---------------------------------------------------------------------------
# Modalita' LOCALE (RF-03.1)
# ---------------------------------------------------------------------------

# Cache dell'ultimo modello caricato: evita di ricaricare Whisper a ogni
# brano in modalita' batch. Un solo modello alla volta per contenere la RAM.
_MODEL_CACHE: dict[tuple, object] = {}


def _load_local_model(model_size: str, device: str, compute_type: str):
    try:
        import stable_whisper
    except ImportError as exc:
        raise MissingDependencyError(
            "La libreria 'stable-ts' non e' installata. "
            "Installa le dipendenze locali con: pip install -r requirements-local.txt"
        ) from exc

    key = (model_size, device, compute_type)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = stable_whisper.load_faster_whisper(
            model_size, device=device, compute_type=compute_type
        )
    return _MODEL_CACHE[key]


@contextmanager
def _live_segment_tap(model, on_segment: OnSegment | None):
    """Notifica ``on_segment`` per ogni frase non appena faster-whisper la
    decodifica, invece di attendere il completamento dell'intera traccia.

    ``model.transcribe_original`` e' l'hook grezzo di faster-whisper che
    stable-ts espone appositamente (impostato da ``load_faster_whisper``):
    restituisce ``(generatore_di_segmenti, info)`` in modo lazy, quindi il
    ciclo interno di stable-ts consuma i segmenti man mano che il modello li
    produce durante la stessa chiamata bloccante. Qui lo si avvolge con un
    generatore-ponte che inoltra ogni segmento invariato dopo aver chiamato
    ``on_segment``: e' una semplice "presa" sullo stream, non una
    reimplementazione della logica di post-processing (VAD, regroup) di
    stable-ts, che resta gestita internamente e identica a prima.
    """
    if on_segment is None or not hasattr(model, "transcribe_original"):
        yield
        return

    original = model.transcribe_original

    def tapped(*args, **kwargs):
        segments_gen, info = original(*args, **kwargs)

        def bridged():
            for segment in segments_gen:
                try:
                    on_segment((segment.text or "").strip())
                except Exception:
                    pass  # un errore lato UI non deve interrompere la trascrizione
                yield segment

        return bridged(), info

    model.transcribe_original = tapped
    try:
        yield
    finally:
        model.transcribe_original = original


def transcribe_local(
    audio_path: Path,
    model_size: str = "small",
    language: str | None = None,
    device: str = "cpu",
    on_segment: OnSegment | None = None,
) -> list[dict]:
    compute_type = "float16" if device == "cuda" else "int8"
    model = _load_local_model(model_size, device, compute_type)

    # Compatibilita' tra versioni di stable-ts: nelle release nuove il metodo
    # "stabilizzato" e' transcribe (e l'originale e' transcribe_original),
    # in quelle vecchie e' transcribe_stable.
    if hasattr(model, "transcribe_original"):
        transcribe = model.transcribe
    else:
        transcribe = getattr(model, "transcribe_stable", None) or model.transcribe

    # vad=True + suppress_silence minimizzano i falsi silenzi indotti dalle
    # pause musicali (RF-03.1).
    with _live_segment_tap(model, on_segment):
        result = transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad=True,
        )

    return whisper_result_to_segments(result)


def whisper_result_to_segments(result) -> list[dict]:
    """Converte un ``WhisperResult`` di stable-ts nella rappresentazione
    intermedia condivisa. Usata sia da ``transcribe_local`` (trascrizione)
    sia da ``lrc_engine.alignment.align_local`` (riallineamento di un testo
    gia' noto), che restituisce lo stesso tipo di risultato."""
    segments: list[dict] = []
    for seg in result.segments:
        words = [
            {"word": w.word.strip(), "start": float(w.start), "end": float(w.end)}
            for w in (seg.words or [])
            if w.word and w.word.strip()
        ]
        segments.append(
            {
                "start": float(seg.start),
                "end": float(seg.end),
                "text": seg.text.strip(),
                "words": words,
            }
        )
    return segments


# ---------------------------------------------------------------------------
# Modalita' CLOUD (RF-03.2)
# ---------------------------------------------------------------------------

def transcribe_cloud(
    audio_path: Path,
    provider: str,
    api_key: str,
    language: str | None = None,
    on_segment: OnSegment | None = None,
) -> list[dict]:
    """provider: "openai" (Whisper-1) oppure "groq" (whisper-large-v3)."""
    if not api_key:
        raise ApiError(
            f"Chiave API {provider.capitalize()} mancante: inseriscila nella sidebar."
        )

    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        OpenAI,
    )

    if provider == "groq":
        client = OpenAI(api_key=api_key, base_url=GROQ_BASE_URL, timeout=600)
        model_name = "whisper-large-v3"
    else:
        client = OpenAI(api_key=api_key, timeout=600)
        model_name = "whisper-1"

    kwargs = {"language": language} if language else {}
    try:
        with open(audio_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model=model_name,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment", "word"],
                **kwargs,
            )
    except (APIConnectionError, APITimeoutError) as exc:
        raise NetworkError(f"{_NET_ERR_MSG} ({provider})") from exc
    except APIStatusError as exc:
        raise ApiError(
            f"Errore API {provider} (HTTP {exc.status_code}): {exc.message}"
        ) from exc

    segments = _parse_verbose_json(resp)
    if on_segment is not None:
        # L'API cloud non offre risultati parziali: il testo arriva tutto
        # insieme, ma lo si notifica comunque segmento per segmento in
        # ordine per mantenere lo stesso comportamento della UI in locale.
        for seg in segments:
            try:
                on_segment(seg["text"])
            except Exception:
                pass
    return segments


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_verbose_json(resp) -> list[dict]:
    raw_segments = _get(resp, "segments") or []
    raw_words = _get(resp, "words") or []

    segments: list[dict] = []
    for seg in raw_segments:
        segments.append(
            {
                "start": float(_get(seg, "start", 0.0)),
                "end": float(_get(seg, "end", 0.0)),
                "text": str(_get(seg, "text", "")).strip(),
                "words": [],
            }
        )

    # L'API restituisce le parole in una lista globale: le riassegniamo al
    # segmento di appartenenza in base al punto medio temporale della parola.
    for w in raw_words:
        word = {
            "word": str(_get(w, "word", "")).strip(),
            "start": float(_get(w, "start", 0.0)),
            "end": float(_get(w, "end", 0.0)),
        }
        if not word["word"]:
            continue
        mid = (word["start"] + word["end"]) / 2
        target = None
        for seg in segments:
            if seg["start"] <= mid < seg["end"]:
                target = seg
                break
        if target is None and segments:
            target = min(
                segments, key=lambda s: abs(s["start"] - word["start"])
            )
        if target is not None:
            target["words"].append(word)

    # Nessun segmento ma testo presente (caso raro): un unico segmento fittizio.
    if not segments:
        text = str(_get(resp, "text", "")).strip()
        if text:
            duration = float(_get(resp, "duration", 0.0) or 0.0)
            segments.append(
                {"start": 0.0, "end": duration, "text": text, "words": []}
            )
    return segments


def has_speech(segments: list[dict]) -> bool:
    """True se la trascrizione contiene testo reale (Edge case 1)."""
    return any(seg["text"].strip() for seg in segments)
