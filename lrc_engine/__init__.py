"""MP3-to-LRC Lyric Sync Generator - core engine package."""

import os
import shutil
from pathlib import Path

__version__ = "1.0.0"

# FFmpeg installato in %LOCALAPPDATA%\Programs\ffmpeg ma non ancora nel PATH
# del processo corrente (es. terminale aperto prima dell'installazione):
# lo aggiunge a runtime, dato che demucs e stable-ts lo invocano via subprocess.
_FFMPEG_BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ffmpeg" / "bin"
if shutil.which("ffmpeg") is None and (_FFMPEG_BIN / "ffmpeg.exe").exists():
    os.environ["PATH"] += os.pathsep + str(_FFMPEG_BIN)
