"""Adattamenti di runtime per l'eseguibile creato con PyInstaller.

Da Python "normale" (``python desktop_app.py``) queste funzioni non fanno
nulla. Dentro l'eseguibile (``sys.frozen``) servono a:

- rendere raggiungibili ``ffmpeg``/``ffprobe`` inclusi nel pacchetto, che
  Demucs e stable-ts invocano come programmi esterni;
- sostituire ``sys.stdout``/``sys.stderr``, che in un'app senza console
  valgono ``None`` e farebbero crashare le barre di avanzamento (tqdm)
  stampate dalle librerie;
- evitare che ogni sottoprocesso (ffmpeg, Demucs) apra una finestra console.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Sottocartella del pacchetto che contiene ffmpeg.exe / ffprobe.exe
# (deve coincidere con la destinazione indicata in mp3_to_lrc.spec).
FFMPEG_BUNDLE_DIR = "ffmpeg"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Cartella con i file del pacchetto (``_internal`` in modalita' onedir)."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def prepare_frozen_runtime() -> None:
    if not is_frozen():
        return

    ffmpeg_dir = bundle_dir() / FFMPEG_BUNDLE_DIR
    if ffmpeg_dir.is_dir():
        os.environ["PATH"] = str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    if sys.platform == "win32":
        _hide_subprocess_consoles()


def _hide_subprocess_consoles() -> None:
    """Aggiunge CREATE_NO_WINDOW a ogni sottoprocesso che non specifica gia'
    dei creationflags: le librerie lanciano ffmpeg senza preoccuparsene e,
    da un'app senza console, ogni lancio farebbe lampeggiare una finestra."""
    original_init = subprocess.Popen.__init__

    def patched_init(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched_init
