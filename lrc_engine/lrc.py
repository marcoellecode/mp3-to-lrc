"""Formattatori LRC (RF-04) e SRT.

Entrambi condividono la stessa segmentazione in righe (spezzate sui timestamp
delle singole parole quando disponibili, RF-04.2) tramite ``_build_lines``,
che calcola per ogni riga sia l'istante di inizio sia quello di fine — il
primo e' sufficiente per l'LRC (che mostra una riga finche' non arriva la
successiva), mentre l'SRT richiede esplicitamente entrambi.

L'LRC puo' essere generato anche in formato *Enhanced* (estensione A2), con
un tag ``<mm:ss.xx>`` davanti a ogni parola e uno finale che ne marca la fine:
``[00:12.00] <00:12.00> Nel <00:12.40> mezzo <00:12.90> del <00:13.30>``.
"""

from __future__ import annotations

DEFAULT_MAX_LINE_LEN = 45

# Marker di sincronizzazione per il video editing: riga fittizia all'istante
# zero che l'utente eliminera' dopo aver allineato il file alla traccia audio.
SYNC_ANCHOR_TEXT = "remove me"
SYNC_ANCHOR_DURATION = 2.0

# Durata minima di un blocco SRT: alcuni player ignorano sottotitoli
# praticamente istantanei generati da parole molto brevi e ravvicinate.
SRT_MIN_DURATION = 0.3


def format_timestamp(seconds: float) -> str:
    """Converte secondi in ``[mm:ss.xx]`` (centesimi di secondo, formato LRC)."""
    seconds = max(0.0, float(seconds))
    centiseconds = int(round(seconds * 100))
    minutes, rest = divmod(centiseconds, 6000)
    secs, cents = divmod(rest, 100)
    return f"[{minutes:02d}:{secs:02d}.{cents:02d}]"


def format_srt_timestamp(seconds: float) -> str:
    """Converte secondi in ``HH:MM:SS,mmm`` (millisecondi, formato SRT)."""
    seconds = max(0.0, float(seconds))
    milliseconds = int(round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_word_timestamp(seconds: float) -> str:
    """Come ``format_timestamp`` ma tra parentesi angolari: ``<mm:ss.xx>``,
    il tag per-parola dell'Enhanced LRC."""
    return f"<{format_timestamp(seconds)[1:-1]}>"


def segments_to_lrc(
    segments: list[dict],
    max_line_len: int = DEFAULT_MAX_LINE_LEN,
    sync_anchor: bool = False,
    enhanced: bool = False,
) -> str:
    """Genera il contenuto completo del file .lrc.

    Con ``sync_anchor=True`` inserisce la riga "remove me" a [00:00.00]
    (durata 2 secondi), a meno che un testo reale non inizi gia' da zero.

    Con ``enhanced=True`` produce un Enhanced LRC: ogni parola e' preceduta
    dal suo tag ``<mm:ss.xx>``. Le righe prive di timestamp word-level (es.
    fallback cloud senza parole) restano righe LRC semplici.
    """
    lines = [
        (start, _enhanced_text(words) if enhanced and words else text)
        for start, _end, text, words in _build_chunks(segments, max_line_len)
    ]
    lines.sort(key=lambda item: item[0])

    if sync_anchor:
        lines = _prepend_sync_anchor_lrc(lines)

    return (
        "\n".join(
            f"{format_timestamp(t)} {txt}".rstrip() for t, txt in lines
        )
        + "\n"
    )


def segments_to_srt(
    segments: list[dict],
    max_line_len: int = DEFAULT_MAX_LINE_LEN,
    sync_anchor: bool = False,
) -> str:
    """Genera il contenuto completo del file .srt.

    Con ``sync_anchor=True`` inserisce il blocco "remove me" da 00:00:00,000
    (durata 2 secondi), a meno che un testo reale non inizi gia' da zero.
    """
    lines = _build_lines(segments, max_line_len)
    lines.sort(key=lambda item: item[0])

    if sync_anchor:
        lines = _prepend_sync_anchor_srt(lines)

    lines = _clamp_srt_durations(lines)

    blocks = [
        f"{idx}\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\n{text}\n"
        for idx, (start, end, text) in enumerate(lines, start=1)
    ]
    return "\n".join(blocks) + "\n"


def _starts_at_zero(t: float) -> bool:
    return format_timestamp(t) == format_timestamp(0.0)


def _prepend_sync_anchor_lrc(
    lines: list[tuple[float, str]]
) -> list[tuple[float, str]]:
    """Antepone il marker "remove me" se nessuna riga parte da [00:00.00].

    La riga vuota a +2s chiude il marker; viene omessa se il primo testo
    reale inizia prima, perche' in tal caso e' lui a sostituire il marker.
    """
    if lines and _starts_at_zero(lines[0][0]):
        return lines

    anchor: list[tuple[float, str]] = [(0.0, SYNC_ANCHOR_TEXT)]
    if not lines or lines[0][0] > SYNC_ANCHOR_DURATION:
        anchor.append((SYNC_ANCHOR_DURATION, ""))
    return anchor + lines


def _prepend_sync_anchor_srt(
    lines: list[tuple[float, float, str]]
) -> list[tuple[float, float, str]]:
    """Come sopra ma per l'SRT: il blocco ha sempre una fine esplicita."""
    if lines and _starts_at_zero(lines[0][0]):
        return lines

    anchor_end = SYNC_ANCHOR_DURATION
    if lines and lines[0][0] < SYNC_ANCHOR_DURATION:
        anchor_end = lines[0][0]
    return [(0.0, anchor_end, SYNC_ANCHOR_TEXT)] + lines


def _clamp_srt_durations(
    lines: list[tuple[float, float, str]]
) -> list[tuple[float, float, str]]:
    """Garantisce blocchi validi: fine > inizio e nessuna sovrapposizione."""
    result = []
    for i, (start, end, text) in enumerate(lines):
        next_start = lines[i + 1][0] if i + 1 < len(lines) else None
        eff_end = max(end, start + SRT_MIN_DURATION)
        if next_start is not None and next_start > start:
            eff_end = min(eff_end, next_start)
        result.append((start, eff_end, text))
    return result


def _enhanced_text(words: list[dict]) -> str:
    """Testo di una riga Enhanced LRC: ``<t1> parola1 <t2> parola2 <fine>``."""
    parts = [f"{format_word_timestamp(w['start'])} {w['word'].strip()}" for w in words]
    parts.append(format_word_timestamp(words[-1]["end"]))
    return " ".join(parts)


def _build_lines(
    segments: list[dict], max_line_len: int
) -> list[tuple[float, float, str]]:
    """Segmenta i testi in righe ``(inizio, fine, testo)`` pronte per l'export."""
    return [
        (start, end, text)
        for start, end, text, _words in _build_chunks(segments, max_line_len)
    ]


def _build_chunks(
    segments: list[dict], max_line_len: int
) -> list[tuple[float, float, str, list[dict]]]:
    """Come ``_build_lines`` ma conserva anche le parole (con i loro tempi)
    di ciascuna riga; lista vuota se la riga non ha dati word-level."""
    lines: list[tuple[float, float, str, list[dict]]] = []

    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue

        words = [w for w in (seg.get("words") or []) if w["word"].strip()]
        if len(text) <= max_line_len:
            if words:
                lines.append((words[0]["start"], words[-1]["end"], text, words))
            else:
                lines.append((seg["start"], seg["end"], text, []))
        elif words:
            lines.extend(_split_by_words(words, max_line_len))
        else:
            lines.extend(
                (start, end, chunk, [])
                for start, end, chunk in _split_by_text(
                    text, seg["start"], seg["end"], max_line_len
                )
            )

    return lines


def _split_by_words(
    words: list[dict], max_line_len: int
) -> list[tuple[float, float, str, list[dict]]]:
    """Spezza usando i timestamp word-level: ogni riga copre le sue parole."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_len = 0

    for w in words:
        token = w["word"].strip()
        if not token:
            continue
        extra = len(token) + (1 if current else 0)
        if current and current_len + extra > max_line_len:
            chunks.append(current)
            current = [w]
            current_len = len(token)
        else:
            current.append(w)
            current_len += extra
    if current:
        chunks.append(current)

    return [
        (
            chunk[0]["start"],
            chunk[-1]["end"],
            " ".join(w["word"].strip() for w in chunk),
            chunk,
        )
        for chunk in chunks
    ]


def _split_by_text(
    text: str, start: float, end: float, max_line_len: int
) -> list[tuple[float, float, str]]:
    """Fallback senza word-level: interpola i tempi in proporzione ai caratteri."""
    tokens = text.split()
    chunks: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current} {token}".strip()
        if current and len(candidate) > max_line_len:
            chunks.append(current)
            current = token
        else:
            current = candidate
    if current:
        chunks.append(current)

    duration = max(0.0, end - start)
    total_chars = max(1, sum(len(c) for c in chunks))
    boundaries = [start]
    consumed = 0
    for chunk in chunks:
        consumed += len(chunk)
        boundaries.append(start + duration * (consumed / total_chars))
    boundaries[-1] = end

    return [
        (boundaries[i], boundaries[i + 1], chunk) for i, chunk in enumerate(chunks)
    ]
