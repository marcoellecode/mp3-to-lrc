"""Modulo di separazione audio / demixing (RF-02).

- Locale: Meta Demucs (htdemucs / htdemucs_ft) via CLI, con fallback CPU se CUDA
  non e' disponibile.
- Cloud: modello Demucs ospitato su Replicate (upload file -> prediction -> download).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import requests

from .errors import ApiError, MissingDependencyError, NetworkError
from .hardware import detect_device

REPLICATE_API = "https://api.replicate.com/v1"
REPLICATE_DEMUCS_MODEL = "ryan5453/demucs"

_NET_ERR_MSG = (
    "Errore di rete: Impossibile raggiungere i server di separazione vocale (Replicate)."
)


# ---------------------------------------------------------------------------
# Modalita' LOCALE (RF-02.1)
# ---------------------------------------------------------------------------

# Demucs gira in un processo separato (memoria liberata a fine separazione).
# Dentro l'eseguibile PyInstaller ``sys.executable`` e' l'app stessa e non
# esiste "python -m": l'app rilancia se stessa con questo flag, che
# desktop_app.py intercetta all'avvio passando il resto a ``run_demucs_cli``.
DEMUCS_CLI_FLAG = "--run-demucs"


def demucs_command() -> list[str]:
    """Prefisso del comando che avvia la CLI di Demucs in un sottoprocesso."""
    if getattr(sys, "frozen", False):
        return [sys.executable, DEMUCS_CLI_FLAG]
    return [sys.executable, "-m", "demucs"]


def run_demucs_cli(argv: list[str]) -> int:
    """Equivalente di ``python -m demucs <argv>`` nel processo corrente."""
    from demucs.separate import main

    main(argv)
    return 0


def separate_vocals_local(
    input_path: Path,
    work_dir: Path,
    model: str = "htdemucs",
    device: str | None = None,
) -> Path:
    """Estrae la traccia vocale con Demucs in locale.

    Restituisce il percorso del file ``vocals.wav`` generato dentro ``work_dir``.
    """
    if importlib.util.find_spec("demucs") is None:
        raise MissingDependencyError(
            "La libreria 'demucs' non e' installata. "
            "Installa le dipendenze locali con: pip install -r requirements-local.txt"
        )

    if device is None:
        device, _ = detect_device()

    cmd = [
        *demucs_command(),
        "--two-stems", "vocals",
        "-n", model,
        "-d", device,
        "-o", str(work_dir),
        str(input_path),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise ApiError(
            "La separazione vocale locale (Demucs) e' fallita:\n" + "\n".join(tail)
        )

    vocals = work_dir / model / input_path.stem / "vocals.wav"
    if not vocals.exists():
        raise ApiError(
            f"Demucs non ha prodotto il file vocale atteso: {vocals}"
        )
    return vocals


# ---------------------------------------------------------------------------
# Modalita' CLOUD (RF-02.2) - Replicate
# ---------------------------------------------------------------------------

def separate_vocals_cloud(
    input_path: Path,
    work_dir: Path,
    api_key: str,
    poll_interval: float = 3.0,
    timeout: float = 900.0,
) -> Path:
    """Estrae la traccia vocale tramite il modello Demucs su Replicate."""
    if not api_key:
        raise ApiError("Chiave API Replicate mancante: inseriscila nella sidebar.")

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        # 1) Upload del file audio sui server Replicate
        with open(input_path, "rb") as f:
            resp = requests.post(
                f"{REPLICATE_API}/files",
                headers=headers,
                files={"content": (input_path.name, f, "application/octet-stream")},
                timeout=120,
            )
        _raise_for_replicate(resp, "upload del file audio")
        file_url = resp.json()["urls"]["get"]

        # 2) Creazione della prediction sul modello Demucs
        resp = requests.post(
            f"{REPLICATE_API}/models/{REPLICATE_DEMUCS_MODEL}/predictions",
            headers={**headers, "Prefer": "wait=60"},
            json={"input": {"audio": file_url, "model": "htdemucs"}},
            timeout=120,
        )
        _raise_for_replicate(resp, "avvio della separazione")
        prediction = resp.json()

        # 3) Polling fino al completamento
        deadline = time.monotonic() + timeout
        while prediction.get("status") in ("starting", "processing"):
            if time.monotonic() > deadline:
                raise NetworkError(
                    "Timeout: la separazione vocale su Replicate non e' terminata in tempo."
                )
            time.sleep(poll_interval)
            resp = requests.get(
                prediction["urls"]["get"], headers=headers, timeout=60
            )
            _raise_for_replicate(resp, "verifica dello stato")
            prediction = resp.json()

        if prediction.get("status") != "succeeded":
            raise ApiError(
                f"Separazione cloud fallita (stato: {prediction.get('status')}): "
                f"{prediction.get('error') or 'nessun dettaglio'}"
            )

        vocals_url = _extract_vocals_url(prediction.get("output"))
        if not vocals_url:
            raise ApiError(
                "Il servizio di separazione non ha restituito la traccia 'vocals'."
            )

        # 4) Download della traccia vocale
        out_path = work_dir / f"vocals_cloud{_guess_ext(vocals_url)}"
        with requests.get(vocals_url, stream=True, timeout=300) as dl:
            dl.raise_for_status()
            with open(out_path, "wb") as out:
                for chunk in dl.iter_content(chunk_size=1 << 16):
                    out.write(chunk)
        return out_path

    except requests.RequestException as exc:
        raise NetworkError(f"{_NET_ERR_MSG} ({exc.__class__.__name__})") from exc


def _raise_for_replicate(resp: requests.Response, step: str) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise ApiError(
            f"Errore API Replicate durante {step} (HTTP {resp.status_code}): {detail}"
        )


def _extract_vocals_url(output) -> str | None:
    """L'output del modello e' tipicamente un dict {stem: url}; gestisce anche liste."""
    if isinstance(output, dict):
        return output.get("vocals")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, str) and "vocals" in item.lower():
                return item
    if isinstance(output, str):
        return output
    return None


def _guess_ext(url: str) -> str:
    path = url.split("?", 1)[0]
    for ext in (".wav", ".mp3", ".flac", ".m4a"):
        if path.lower().endswith(ext):
            return ext
    return ".wav"
