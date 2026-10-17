"""Autotest della pipeline locale senza interfaccia grafica.

Pensato per verificare un eseguibile appena costruito (dove non c'e' una
console su cui stampare): elabora un file audio con la pipeline Locale
completa (Demucs + stable-ts), salva .lrc/.srt accanto al file come fa
l'app e scrive l'esito in ``<audio>.selftest.log``. Codice di uscita 0 se
tutto e' andato a buon fine, 1 altrimenti.

    MP3-to-LRC.exe --selftest canzone.mp3 [--model tiny] [--lang it] [--no-separation]
"""

from __future__ import annotations

import argparse
import tempfile
import time
import traceback
from pathlib import Path

from .pipeline import PipelineConfig, generate_subtitles

SELFTEST_FLAG = "--selftest"


def run_selftest(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=SELFTEST_FLAG)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", default="tiny", help="modello Whisper (default: tiny)")
    parser.add_argument("--lang", default=None, help="lingua (default: auto-detect)")
    parser.add_argument("--no-separation", action="store_true", help="salta Demucs")
    args = parser.parse_args(argv)

    audio: Path = args.audio.resolve()
    log_path = audio.with_name(audio.name + ".selftest.log")
    log: list[str] = []
    t0 = time.monotonic()

    def note(msg: str) -> None:
        log.append(f"[{time.monotonic() - t0:7.1f}s] {msg}")

    config = PipelineConfig(
        is_local=True,
        language=args.lang,
        do_separation=not args.no_separation,
        whisper_model=args.model,
        export_lrc=True,
        export_srt=True,
        lrc_enhanced=True,
    )
    exit_code = 0
    try:
        with tempfile.TemporaryDirectory(prefix="lrc_selftest_") as tmp:
            result = generate_subtitles(audio, Path(tmp), config, on_phase=note)
        for fmt, text in result.texts.items():
            out = config.output_path_for(audio, fmt)
            out.write_text(text, encoding="utf-8")
            note(f"Salvato {out.name} ({text.count(chr(10))} righe)")
        note("OK")
    except Exception:
        note("ERRORE\n" + traceback.format_exc())
        exit_code = 1

    log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
    return exit_code
