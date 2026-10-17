"""Eccezioni tipizzate della pipeline, mappate sui casi limite della SRS (sez. 6)."""


class PipelineError(Exception):
    """Errore generico e gestito della pipeline."""


class NoVocalsError(PipelineError):
    """La trascrizione non ha prodotto testo: nessuna traccia vocale rilevata."""

    def __init__(self, message: str = "Nessuna traccia vocale rilevata nel file"):
        super().__init__(message)


class NetworkError(PipelineError):
    """Timeout o irraggiungibilita' dei server cloud."""


class ApiError(PipelineError):
    """Risposta di errore esplicita da un servizio API esterno."""


class MissingDependencyError(PipelineError):
    """Dipendenza locale (demucs, stable-ts, torch...) non installata."""
