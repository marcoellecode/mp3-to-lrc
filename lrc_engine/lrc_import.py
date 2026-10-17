"""Import di un file .lrc esistente per il riallineamento forzato (resync).

Estrae solo il testo delle righe, scartando timestamp e tag di metadati
(``[ti:...]``, ``[ar:...]``, ecc.) gia' presenti: serve come testo di
riferimento noto da riallineare sulla traccia audio effettivamente caricata,
utile quando l'.lrc a disposizione proviene da una versione diversa del
brano (radio edit, extended mix, remaster con intro piu' lungo/corto...) e i
tempi non coincidono piu' con il file in mano.
"""

from __future__ import annotations

import re

from .lrc import SYNC_ANCHOR_TEXT

# Ogni riga .lrc puo' avere uno o piu' tag "[...]" in testa (timestamp
# multipli per la stessa riga, oppure metadati come [ti:...]/[ar:...]):
# si scartano tutti, tenendo solo il testo libero che segue.
_LEADING_TAGS_RE = re.compile(r"^(?:\[[^\]]*\]\s*)*")

# Tag per-parola dell'Enhanced LRC ("<00:12.40>"), sparsi dentro la riga.
_WORD_TAGS_RE = re.compile(r"<\d+:\d+(?:[.:]\d+)?>")


def parse_lrc_lyrics(content: str) -> list[str]:
    """Restituisce le righe di testo del file .lrc, in ordine, senza
    timestamp/tag (inclusi i tag per-parola ``<mm:ss.xx>`` di un Enhanced
    LRC). Righe vuote e il marker di sincronizzazione "remove me"
    (se presente, generato da questa stessa app) vengono scartati perche'
    non fanno parte del testo cantato da riallineare."""
    lines: list[str] = []
    for raw_line in content.splitlines():
        text = _LEADING_TAGS_RE.sub("", raw_line)
        text = " ".join(_WORD_TAGS_RE.sub(" ", text).split())
        if not text or text == SYNC_ANCHOR_TEXT:
            continue
        lines.append(text)
    return lines
