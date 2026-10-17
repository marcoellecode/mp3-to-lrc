"""Rilevamento dell'accelerazione hardware disponibile (RF-02.1, Edge case 2)."""

from __future__ import annotations


def detect_device() -> tuple[str, bool]:
    """Restituisce (device, cpu_fallback).

    device: "cuda" se e' presente una GPU NVIDIA utilizzabile, altrimenti "cpu".
    cpu_fallback: True quando si ripiega sulla CPU (l'utente va avvisato che
    il processo richiedera' piu' tempo).
    """
    try:
        import torch
    except ImportError:
        return "cpu", True

    try:
        if torch.cuda.is_available():
            return "cuda", False
    except Exception:
        pass
    return "cpu", True
