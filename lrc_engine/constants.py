"""Costanti di selezione condivise dall'interfaccia desktop."""

LANGUAGES = {
    "Auto-detect": None,
    "Italiano": "it",
    "Inglese": "en",
    "Spagnolo": "es",
    "Francese": "fr",
    "Tedesco": "de",
    "Portoghese": "pt",
    "Giapponese": "ja",
}

WHISPER_MODELS = ["tiny", "base", "small", "medium"]

DEMUCS_MODELS = ["htdemucs", "htdemucs_ft"]

CLOUD_STT_PROVIDERS = {
    "OpenAI (Whisper-1)": "openai",
    "Groq (whisper-large-v3)": "groq",
}

AUDIO_EXTS = (".mp3", ".wav", ".m4a")
