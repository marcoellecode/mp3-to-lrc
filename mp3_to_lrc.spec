# -*- mode: python ; coding: utf-8 -*-
"""Configurazione PyInstaller per MP3-to-LRC (modalita' cartella / onedir).

Da usare tramite build.ps1, oppure:
    <venv>\\Scripts\\pyinstaller.exe mp3_to_lrc.spec --noconfirm

Onedir e non onefile: con torch il pacchetto supera 1 GB, e un onefile lo
scompatterebbe in una cartella temporanea a ogni avvio (decine di secondi).

Variabili d'ambiente:
    MP3LRC_CONSOLE=1   build con finestra console (per vedere errori/log)
    FFMPEG_DIR=...     cartella con ffmpeg.exe e ffprobe.exe (default: dal PATH)
"""

import os
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

APP_NAME = "MP3-to-LRC"
CONSOLE = os.environ.get("MP3LRC_CONSOLE") == "1"


def _ffmpeg_binaries():
    """ffmpeg/ffprobe vengono invocati come programmi esterni da Demucs e
    stable-ts: si includono nella sottocartella "ffmpeg" del pacchetto, che
    lrc_engine/runtime.py aggiunge al PATH all'avvio."""
    folder = os.environ.get("FFMPEG_DIR")
    found = []
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        path = Path(folder) / name if folder else shutil.which(name)
        if not path or not Path(path).is_file():
            raise SystemExit(
                f"{name} non trovato: aggiungilo al PATH o imposta FFMPEG_DIR."
            )
        found.append((str(path), "ffmpeg"))
    return found


datas = []
for package in ("customtkinter", "demucs", "whisper", "faster_whisper", "stable_whisper"):
    # temi customtkinter, configurazioni .yaml dei modelli Demucs, filtri mel
    # e tokenizer di Whisper, modello VAD di faster-whisper
    datas += collect_data_files(package)

hiddenimports = (
    # Demucs ricostruisce i modelli da classi indicate nei checkpoint, e
    # stable-ts importa i backend in modo pigro: PyInstaller non le vede.
    collect_submodules("demucs")
    + collect_submodules("stable_whisper")
    + ["tiktoken_ext", "tiktoken_ext.openai_public"]
)

a = Analysis(
    ["desktop_app.py"],
    pathex=[],
    binaries=_ffmpeg_binaries(),
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "IPython", "notebook", "pytest", "tensorboard"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
