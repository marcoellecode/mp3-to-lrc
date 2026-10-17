"""MP3-to-LRC Lyric Sync Generator (Multi-Engine) - interfaccia desktop nativa.

Avvio:  python desktop_app.py
Eseguibile:  vedi build.ps1 / mp3_to_lrc.spec
"""

from __future__ import annotations

import multiprocessing
import queue
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from lrc_engine.constants import (
    AUDIO_EXTS,
    CLOUD_STT_PROVIDERS,
    DEMUCS_MODELS,
    LANGUAGES,
    WHISPER_MODELS,
)
from lrc_engine.errors import (
    ApiError,
    MissingDependencyError,
    NetworkError,
    NoVocalsError,
    PipelineError,
)
from lrc_engine.hardware import detect_device
from lrc_engine.lrc import DEFAULT_MAX_LINE_LEN
from lrc_engine.lrc_import import parse_lrc_lyrics
from lrc_engine.pipeline import (
    PHASE_ALIGNMENT,
    PHASE_FORMATTING,
    PHASE_SEPARATION,
    PHASE_TRANSCRIPTION,
    PipelineConfig,
    generate_subtitles,
)
from lrc_engine.runtime import prepare_frozen_runtime
from lrc_engine.selftest import SELFTEST_FLAG, run_selftest
from lrc_engine.separation import DEMUCS_CLI_FLAG, run_demucs_cli

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

MODE_LOCAL = "Locale (Offline/GPU)"
MODE_CLOUD = "Cloud (API)"

FORMAT_LABELS = {"lrc": "LRC", "srt": "SRT"}

# Mappa le fasi della pipeline (lrc_engine.pipeline) a un avanzamento indicativo
# della progress bar (0..1). L'ultimo tratto (fino a 1.0) e' riservato al
# completamento effettivo, comunicato separatamente dal worker.
PHASE_PROGRESS = {
    PHASE_SEPARATION: 0.10,
    PHASE_TRANSCRIPTION: 0.55,
    PHASE_ALIGNMENT: 0.55,
    PHASE_FORMATTING: 0.90,
}


def _count_units(fmt: str, text: str) -> int:
    """Righe per l'LRC, blocchi (cue) per l'SRT: entrambi indicano quante
    battute sincronizzate contiene il file, in unita' comparabili tra loro."""
    if fmt == "srt":
        return text.count(" --> ")
    return text.strip().count("\n") + 1 if text.strip() else 0


def _vocals_name(exc: Exception) -> str:
    vocals_path = getattr(exc, "vocals_path", None)
    return Path(vocals_path).name if vocals_path else "—"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("MP3 → LRC Lyric Sync Generator")
        self.geometry("1080x820")
        self.minsize(900, 680)

        self.msg_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None

        # Stato per-scheda (Singolo file / File vocale / Risincronizza LRC),
        # popolato da _build_file_tab / _build_resync_tab.
        self.tab_state: dict[str, dict] = {}

        self.batch_folder: Path | None = None
        self.batch_files: list[Path] = []

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)

        self._build_sidebar()
        self._build_main()
        self._build_log_panel()

        self.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # Sidebar: pannello di configurazione (RF-01.3)
    # ------------------------------------------------------------------
    def _build_sidebar(self):
        sidebar = ctk.CTkScrollableFrame(self, width=300, label_text="⚙️ Configurazione")
        sidebar.grid(row=0, column=0, rowspan=2, sticky="nswe", padx=(10, 5), pady=10)
        sidebar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(sidebar, text="Modalità di elaborazione", anchor="w").grid(
            row=0, column=0, sticky="ew", pady=(4, 0)
        )
        self.mode_var = ctk.StringVar(value=MODE_LOCAL)
        ctk.CTkSegmentedButton(
            sidebar,
            values=[MODE_LOCAL, MODE_CLOUD],
            variable=self.mode_var,
            command=lambda _: self._on_mode_change(),
        ).grid(row=1, column=0, sticky="ew", pady=(2, 10))

        ctk.CTkLabel(sidebar, text="Lingua del brano", anchor="w").grid(row=2, column=0, sticky="ew")
        self.language_var = ctk.StringVar(value="Auto-detect")
        ctk.CTkOptionMenu(sidebar, values=list(LANGUAGES.keys()), variable=self.language_var).grid(
            row=3, column=0, sticky="ew", pady=(2, 10)
        )

        self.do_separation_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            sidebar,
            text="Separazione vocale (Demucs)",
            variable=self.do_separation_var,
            command=self._on_mode_change,
        ).grid(row=4, column=0, sticky="ew", pady=(0, 4))

        self.keep_vocals_var = ctk.BooleanVar(value=False)
        self.keep_vocals_checkbox = ctk.CTkCheckBox(
            sidebar, text="🎙️ Mantieni traccia vocale estratta", variable=self.keep_vocals_var
        )
        self.keep_vocals_checkbox.grid(row=5, column=0, sticky="ew", pady=(0, 2))
        self.keep_vocals_hint = ctk.CTkLabel(
            sidebar,
            text='Salvata accanto al brano come "nome_vocals"',
            font=ctk.CTkFont(size=11),
            text_color="gray",
            anchor="w",
            justify="left",
            wraplength=260,
        )
        self.keep_vocals_hint.grid(row=6, column=0, sticky="ew", pady=(0, 10))

        # Locale e Cloud condividono la stessa cella: solo uno e' visibile
        # alla volta (si escludono a vicenda in base al radio sopra).
        self.local_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.local_frame.grid(row=7, column=0, sticky="ew")
        self._build_local_frame(self.local_frame)

        self.cloud_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.cloud_frame.grid(row=7, column=0, sticky="ew")
        self._build_cloud_frame(self.cloud_frame)

        adv_frame = ctk.CTkFrame(sidebar)
        adv_frame.grid(row=8, column=0, sticky="ew", pady=(10, 4))
        self._build_advanced_frame(adv_frame)

        self._on_mode_change()

    def _build_local_frame(self, frame: ctk.CTkFrame):
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame, text="Impostazioni locali", anchor="w", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="ew", pady=(4, 4))

        ctk.CTkLabel(frame, text="Modello Whisper", anchor="w").grid(row=1, column=0, sticky="ew")
        self.whisper_model_var = ctk.StringVar(value="small")
        ctk.CTkOptionMenu(frame, values=WHISPER_MODELS, variable=self.whisper_model_var).grid(
            row=2, column=0, sticky="ew", pady=(2, 10)
        )

        ctk.CTkLabel(frame, text="Modello Demucs", anchor="w").grid(row=3, column=0, sticky="ew")
        self.demucs_model_var = ctk.StringVar(value=DEMUCS_MODELS[0])
        ctk.CTkOptionMenu(frame, values=DEMUCS_MODELS, variable=self.demucs_model_var).grid(
            row=4, column=0, sticky="ew", pady=(2, 10)
        )

        _, cpu_fallback = detect_device()
        gpu_text = "⚠️ CPU (nessuna GPU CUDA rilevata)" if cpu_fallback else "✅ GPU CUDA rilevata"
        gpu_color = "#e0a030" if cpu_fallback else "#2fa572"
        ctk.CTkLabel(frame, text=gpu_text, text_color=gpu_color, anchor="w").grid(
            row=5, column=0, sticky="ew", pady=(0, 6)
        )

    def _build_cloud_frame(self, frame: ctk.CTkFrame):
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame, text="Chiavi API (Cloud)", anchor="w", font=ctk.CTkFont(weight="bold")
        ).grid(row=0, column=0, sticky="ew", pady=(4, 4))

        ctk.CTkLabel(frame, text="Motore di trascrizione", anchor="w").grid(row=1, column=0, sticky="ew")
        self.stt_provider_var = ctk.StringVar(value=list(CLOUD_STT_PROVIDERS.keys())[0])
        ctk.CTkOptionMenu(
            frame, values=list(CLOUD_STT_PROVIDERS.keys()), variable=self.stt_provider_var
        ).grid(row=2, column=0, sticky="ew", pady=(2, 10))

        ctk.CTkLabel(frame, text="API Key trascrizione", anchor="w").grid(row=3, column=0, sticky="ew")
        self.stt_api_key_var = ctk.StringVar()
        ctk.CTkEntry(frame, textvariable=self.stt_api_key_var, show="•").grid(
            row=4, column=0, sticky="ew", pady=(2, 10)
        )

        self.replicate_label = ctk.CTkLabel(frame, text="API Key Replicate (separazione)", anchor="w")
        self.replicate_label.grid(row=5, column=0, sticky="ew")
        self.replicate_api_key_var = ctk.StringVar()
        self.replicate_entry = ctk.CTkEntry(frame, textvariable=self.replicate_api_key_var, show="•")
        self.replicate_entry.grid(row=6, column=0, sticky="ew", pady=(2, 10))

    def _build_advanced_frame(self, frame: ctk.CTkFrame):
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text="Opzioni avanzate", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        ctk.CTkLabel(frame, text="Formati di esportazione", anchor="w").grid(
            row=1, column=0, sticky="w", padx=8
        )
        self.export_lrc_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            frame, text="Esporta .lrc", variable=self.export_lrc_var, command=self._on_export_lrc_change
        ).grid(row=2, column=0, sticky="w", padx=8, pady=(2, 0))
        # Enhanced LRC: stesso file .lrc, con un tag <mm:ss.xx> per ogni parola.
        # Spento di default: i player che non lo supportano mostrano i tag nel testo.
        self.lrc_enhanced_var = ctk.BooleanVar(value=False)
        self.lrc_enhanced_checkbox = ctk.CTkCheckBox(
            frame, text="Enhanced (tempi per parola)", variable=self.lrc_enhanced_var
        )
        self.lrc_enhanced_checkbox.grid(row=3, column=0, sticky="w", padx=(32, 8), pady=(2, 0))
        self.export_srt_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            frame, text="Esporta .srt", variable=self.export_srt_var, command=self._rescan_batch_folder
        ).grid(row=4, column=0, sticky="w", padx=8, pady=(2, 10))

        self.max_line_len_var = ctk.IntVar(value=DEFAULT_MAX_LINE_LEN)
        self.max_line_len_label = ctk.CTkLabel(
            frame, text=f"Lunghezza massima riga: {DEFAULT_MAX_LINE_LEN}"
        )
        self.max_line_len_label.grid(row=5, column=0, sticky="w", padx=8)
        ctk.CTkSlider(
            frame,
            from_=30,
            to=60,
            number_of_steps=30,
            variable=self.max_line_len_var,
            command=lambda v: self.max_line_len_label.configure(
                text=f"Lunghezza massima riga: {int(v)}"
            ),
        ).grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 10))

        self.sync_anchor_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            frame, text='Marker "remove me" iniziale', variable=self.sync_anchor_var
        ).grid(row=7, column=0, sticky="w", padx=8, pady=(0, 10))

    def _on_export_lrc_change(self):
        self.lrc_enhanced_checkbox.configure(
            state="normal" if self.export_lrc_var.get() else "disabled"
        )
        self._rescan_batch_folder()

    def _on_mode_change(self):
        is_local = self.mode_var.get() == MODE_LOCAL
        do_separation = self.do_separation_var.get()

        if do_separation:
            self.keep_vocals_checkbox.grid()
            self.keep_vocals_hint.grid()
        else:
            self.keep_vocals_checkbox.grid_remove()
            self.keep_vocals_hint.grid_remove()
            self.keep_vocals_var.set(False)

        if is_local:
            self.cloud_frame.grid_remove()
            self.local_frame.grid()
        else:
            self.local_frame.grid_remove()
            self.cloud_frame.grid()
            if do_separation:
                self.replicate_label.grid()
                self.replicate_entry.grid()
            else:
                self.replicate_label.grid_remove()
                self.replicate_entry.grid_remove()

    # ------------------------------------------------------------------
    # Area principale: tab Singolo file / File vocale / Batch cartella
    # ------------------------------------------------------------------
    def _build_main(self):
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=1, sticky="nswe", padx=(5, 10), pady=(10, 5))
        tab_single = self.tabview.add("🎵 Singolo file")
        tab_vocals = self.tabview.add("🎤 File vocale")
        tab_resync = self.tabview.add("🔄 Risincronizza LRC")
        tab_batch = self.tabview.add("📁 Batch cartella")

        self._build_file_tab(
            tab_single,
            kind="single",
            picker_title="Seleziona un file audio",
            generate_label="🎶 Genera sottotitoli",
            force_no_separation=False,
        )
        self._build_file_tab(
            tab_vocals,
            kind="vocals",
            picker_title="Seleziona un file audio già vocale",
            generate_label="🎶 Genera sottotitoli dalla voce",
            force_no_separation=True,
            hint_text="Importa un file che contiene già solo la voce isolata (per "
            "esempio un file salvato in precedenza con «Mantieni traccia vocale "
            "estratta»): la separazione Demucs viene saltata e si passa "
            "direttamente a trascrizione e formattazione.",
        )
        self._build_resync_tab(tab_resync)
        self._build_batch_tab(tab_batch)

    # --- Tab Risincronizza LRC -----------------------------------------------

    def _build_resync_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        row = 0

        ctk.CTkLabel(
            parent,
            text="Importa un file .lrc già compilato e ricalcola i suoi tempi sulla "
            "traccia audio caricata, mantenendo il testo invariato. Utile quando i "
            "timestamp non coincidono più con l'audio in mano (es. radio edit vs "
            "extended mix, remaster con intro più lungo/corto). Richiede la "
            "modalità Locale e una lingua specifica (non Auto-detect) nella sidebar.",
            anchor="w",
            justify="left",
            wraplength=560,
            text_color="gray",
        ).grid(row=row, column=0, sticky="ew", pady=(4, 8))
        row += 1

        audio_row = ctk.CTkFrame(parent, fg_color="transparent")
        audio_row.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        row += 1
        audio_row.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            audio_row, text="📂 Seleziona file audio...", command=lambda: self._pick_file("resync")
        ).grid(row=0, column=0, padx=(0, 10))
        path_label = ctk.CTkLabel(audio_row, text="Nessun file selezionato", anchor="w", text_color="gray")
        path_label.grid(row=0, column=1, sticky="ew")

        lyrics_row = ctk.CTkFrame(parent, fg_color="transparent")
        lyrics_row.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        row += 1
        lyrics_row.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(
            lyrics_row,
            text="📝 Importa .lrc esistente...",
            command=lambda: self._pick_lyrics_file("resync"),
        ).grid(row=0, column=0, padx=(0, 10))
        lyrics_path_label = ctk.CTkLabel(
            lyrics_row, text="Nessun file .lrc importato", anchor="w", text_color="gray"
        )
        lyrics_path_label.grid(row=0, column=1, sticky="ew")

        generate_btn = ctk.CTkButton(
            parent,
            text="🔄 Riallinea sottotitoli",
            state="disabled",
            command=lambda: self._start_pipeline("resync"),
            fg_color="#2fa572",
            hover_color="#26845c",
        )
        generate_btn.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        row += 1

        self.tab_state["resync"] = {
            "audio_path": None,
            "reference_lyrics": None,
            "outputs": {},
            "force_no_separation": False,
            "picker_title": "Seleziona il file audio da risincronizzare",
            "path_label": path_label,
            "lyrics_path_label": lyrics_path_label,
            "generate_btn": generate_btn,
            "ready_check": lambda s: s["audio_path"] is not None and bool(s.get("reference_lyrics")),
        }
        self._build_result_widgets(parent, "resync", row)

    # --- Tab generica: Singolo file / File vocale --------------------------

    def _build_file_tab(
        self,
        parent,
        kind: str,
        picker_title: str,
        generate_label: str,
        force_no_separation: bool,
        hint_text: str = "",
    ):
        parent.grid_columnconfigure(0, weight=1)
        row = 0

        if hint_text:
            ctk.CTkLabel(
                parent, text=hint_text, anchor="w", justify="left", wraplength=560, text_color="gray"
            ).grid(row=row, column=0, sticky="ew", pady=(4, 8))
            row += 1

        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=row, column=0, sticky="ew", pady=(4, 8))
        row += 1
        top.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(top, text="📂 Seleziona file audio...", command=lambda: self._pick_file(kind)).grid(
            row=0, column=0, padx=(0, 10)
        )
        path_label = ctk.CTkLabel(top, text="Nessun file selezionato", anchor="w", text_color="gray")
        path_label.grid(row=0, column=1, sticky="ew")

        generate_btn = ctk.CTkButton(
            parent,
            text=generate_label,
            state="disabled",
            command=lambda: self._start_pipeline(kind),
            fg_color="#2fa572",
            hover_color="#26845c",
        )
        generate_btn.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        row += 1

        self.tab_state[kind] = {
            "audio_path": None,
            "outputs": {},
            "force_no_separation": force_no_separation,
            "picker_title": picker_title,
            "path_label": path_label,
            "generate_btn": generate_btn,
            "ready_check": lambda s: s["audio_path"] is not None,
        }
        self._build_result_widgets(parent, kind, row)

    def _build_result_widgets(self, parent, kind: str, row: int) -> int:
        """Parte comune a tutte le schede basate su ``tab_state``: progress
        bar, stato, selettore anteprima LRC/SRT, textbox e pulsante di
        salvataggio manuale. Restituisce la riga successiva libera."""
        progress = ctk.CTkProgressBar(parent)
        progress.set(0)
        progress.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        row += 1

        status_label = ctk.CTkLabel(parent, text="", anchor="w", text_color="gray")
        status_label.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        row += 1

        preview_selector = ctk.CTkSegmentedButton(
            parent, values=["LRC"], command=lambda label, k=kind: self._on_tab_preview_change(k, label)
        )
        preview_selector.set("LRC")
        preview_selector.grid(row=row, column=0, sticky="w", pady=(0, 6))
        preview_selector.grid_remove()
        row += 1

        textbox_row = row
        textbox = ctk.CTkTextbox(parent, wrap="word")
        textbox.grid(row=textbox_row, column=0, sticky="nswe")
        textbox.configure(state="disabled")
        parent.grid_rowconfigure(textbox_row, weight=1)
        row += 1

        save_btn = ctk.CTkButton(
            parent,
            text="💾 Salva una copia altrove...",
            state="disabled",
            command=lambda: self._save_outputs(kind),
        )
        save_btn.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        row += 1

        self.tab_state[kind].update({
            "progress": progress,
            "status_label": status_label,
            "preview_selector": preview_selector,
            "textbox": textbox,
            "save_btn": save_btn,
        })
        return row

    def _reset_tab_outputs(self, kind: str):
        state = self.tab_state[kind]
        state["outputs"] = {}
        state["preview_selector"].grid_remove()
        state["save_btn"].configure(state="disabled")
        self._set_tab_textbox(state, "")

    def _refresh_generate_button(self, kind: str):
        state = self.tab_state[kind]
        ready = state["ready_check"](state)
        state["generate_btn"].configure(state="normal" if ready else "disabled")

    def _pick_file(self, kind: str):
        state = self.tab_state[kind]
        path = filedialog.askopenfilename(
            title=state["picker_title"],
            filetypes=[("File audio", "*.mp3 *.wav *.m4a"), ("Tutti i file", "*.*")],
        )
        if not path:
            return
        state["audio_path"] = Path(path)
        state["path_label"].configure(text=state["audio_path"].name, text_color=("black", "white"))
        self._reset_tab_outputs(kind)
        self._refresh_generate_button(kind)

    def _pick_lyrics_file(self, kind: str):
        state = self.tab_state[kind]
        path = filedialog.askopenfilename(
            title="Seleziona un file .lrc esistente",
            filetypes=[("File LRC", "*.lrc"), ("Tutti i file", "*.*")],
        )
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Errore di lettura", f"Impossibile leggere {path}:\n{exc}")
            return

        lines = parse_lrc_lyrics(content)
        if not lines:
            messagebox.showwarning(
                "Nessun testo trovato",
                f"Non è stato trovato nessun testo utilizzabile in:\n{path}",
            )
            return

        state["reference_lyrics"] = lines
        state["lyrics_path_label"].configure(
            text=f"{Path(path).name} ({len(lines)} righe)", text_color=("black", "white")
        )
        self._reset_tab_outputs(kind)
        self._refresh_generate_button(kind)

    def _set_tab_textbox(self, state: dict, text: str):
        tb = state["textbox"]
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        tb.insert("1.0", text)
        tb.configure(state="disabled")

    def _on_tab_preview_change(self, kind: str, label: str):
        state = self.tab_state[kind]
        self._set_tab_textbox(state, state["outputs"].get(label.lower(), ""))

    def _start_pipeline(self, kind: str):
        if self.worker and self.worker.is_alive():
            return
        state = self.tab_state[kind]
        config = self._build_config()
        if state["force_no_separation"]:
            config.do_separation = False
            config.keep_vocals = False  # l'input e' gia' voce isolata: nulla da estrarre/salvare
        reference_lyrics = state.get("reference_lyrics")
        error = self._validate_config(config, requires_local_alignment=reference_lyrics is not None)
        if error:
            messagebox.showerror("Configurazione incompleta", error)
            return

        state["generate_btn"].configure(state="disabled")
        state["save_btn"].configure(state="disabled")
        state["preview_selector"].grid_remove()
        state["progress"].set(0)
        self._set_tab_textbox(state, "")
        state["status_label"].configure(text="Avvio…")

        audio_path = state["audio_path"]
        log_suffix = (
            f" (riallineamento di {len(reference_lyrics)} righe importate)"
            if reference_lyrics
            else ""
        )
        self._log(f"[{kind}] Avvio elaborazione: {audio_path.name}{log_suffix}")
        self.worker = threading.Thread(
            target=self._run_tab_worker, args=(kind, audio_path, config, reference_lyrics), daemon=True
        )
        self.worker.start()

    def _run_tab_worker(
        self, kind: str, audio_path: Path, config: PipelineConfig, reference_lyrics: list[str] | None = None
    ):
        try:
            with tempfile.TemporaryDirectory(prefix="mp3lrc_") as tmp:
                result = generate_subtitles(
                    audio_path,
                    Path(tmp),
                    config,
                    on_phase=lambda msg: self.msg_queue.put((f"{kind}_phase", msg)),
                    on_segment=lambda text: self.msg_queue.put((f"{kind}_segment", text)),
                    reference_lyrics=reference_lyrics,
                )
            self.msg_queue.put((f"{kind}_done", result))
        except Exception as exc:  # difesa finale: mai crashare l'app (RNF)
            self.msg_queue.put((f"{kind}_error", exc))

    def _save_outputs(self, kind: str):
        state = self.tab_state[kind]
        outputs = state["outputs"]
        audio_path = state["audio_path"]
        if not outputs or not audio_path:
            return
        formats = list(outputs.keys())
        stem = audio_path.stem

        if len(formats) == 1:
            fmt = formats[0]
            ext = f".{fmt}"
            path = filedialog.asksaveasfilename(
                title=f"Salva file .{fmt}",
                initialdir=str(audio_path.parent),
                initialfile=stem + ext,
                defaultextension=ext,
                filetypes=[(f"File {fmt.upper()}", f"*{ext}"), ("Tutti i file", "*.*")],
            )
            if not path:
                return
            Path(path).write_text(outputs[fmt], encoding="utf-8")
            self._log(f"[{kind}] File salvato: {path}")
            messagebox.showinfo("Salvato", f"File salvato in:\n{path}")
            return

        # Più formati generati: si sceglie una cartella e si scrivono tutti
        # i file con il nome del brano, come nel batch.
        folder = filedialog.askdirectory(
            title="Seleziona la cartella di destinazione",
            initialdir=str(audio_path.parent),
        )
        if not folder:
            return
        saved_paths = []
        for fmt, text in outputs.items():
            out_path = Path(folder) / f"{stem}.{fmt}"
            out_path.write_text(text, encoding="utf-8")
            saved_paths.append(str(out_path))
        self._log(f"[{kind}] File salvati: {', '.join(saved_paths)}")
        messagebox.showinfo("Salvato", "File salvati:\n" + "\n".join(saved_paths))

    # --- Tab Batch cartella -------------------------------------------------

    def _build_batch_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(5, weight=1)

        top = ctk.CTkFrame(parent, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(4, 8))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(top, text="📁 Seleziona cartella...", command=self._pick_batch_folder).grid(
            row=0, column=0, padx=(0, 10)
        )
        self.batch_path_label = ctk.CTkLabel(
            top, text="Nessuna cartella selezionata", anchor="w", text_color="gray"
        )
        self.batch_path_label.grid(row=0, column=1, sticky="ew")

        opts = ctk.CTkFrame(parent, fg_color="transparent")
        opts.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.batch_recursive_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            opts,
            text="Includi sottocartelle",
            variable=self.batch_recursive_var,
            command=self._rescan_batch_folder,
        ).pack(side="left", padx=(0, 20))
        self.batch_skip_existing_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opts,
            text="Salta i brani già completi",
            variable=self.batch_skip_existing_var,
            command=self._rescan_batch_folder,
        ).pack(side="left")

        self.batch_files_label = ctk.CTkLabel(parent, text="", anchor="w", text_color="gray")
        self.batch_files_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        self.batch_start_btn = ctk.CTkButton(
            parent,
            text="🚀 Avvia elaborazione batch",
            state="disabled",
            command=self._start_batch_pipeline,
            fg_color="#2fa572",
            hover_color="#26845c",
        )
        self.batch_start_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        progress_frame = ctk.CTkFrame(parent, fg_color="transparent")
        progress_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        progress_frame.grid_columnconfigure(0, weight=1)
        self.batch_progress = ctk.CTkProgressBar(progress_frame)
        self.batch_progress.set(0)
        self.batch_progress.grid(row=0, column=0, sticky="ew")
        self.batch_status_label = ctk.CTkLabel(progress_frame, text="", anchor="w", text_color="gray")
        self.batch_status_label.grid(row=1, column=0, sticky="ew", pady=(2, 0))

        # ttk.Treeview: CustomTkinter non offre un widget tabellare nativo,
        # per l'elenco risultati del batch si usa quello standard di Tk.
        columns = ("file", "esito", "unita", "vocale", "output")
        self.batch_tree = ttk.Treeview(parent, columns=columns, show="headings", height=10)
        headings = {
            "file": "File",
            "esito": "Esito",
            "unita": "Righe/Cue",
            "vocale": "Vocale",
            "output": "Output",
        }
        widths = {"file": 160, "esito": 220, "unita": 70, "vocale": 130, "output": 260}
        for col in columns:
            self.batch_tree.heading(col, text=headings[col])
            self.batch_tree.column(col, width=widths[col], anchor="w")
        self.batch_tree.grid(row=5, column=0, sticky="nswe")

        tree_scroll = ttk.Scrollbar(parent, orient="vertical", command=self.batch_tree.yview)
        self.batch_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.grid(row=5, column=1, sticky="ns")

    def _pick_batch_folder(self):
        folder = filedialog.askdirectory(title="Seleziona la cartella con i file audio")
        if not folder:
            return
        self.batch_folder = Path(folder)
        self.batch_path_label.configure(text=str(self.batch_folder), text_color=("black", "white"))
        self._rescan_batch_folder()

    def _expected_outputs(self, audio: Path) -> list[Path]:
        outputs = []
        if self.export_lrc_var.get():
            outputs.append(audio.with_suffix(".lrc"))
        if self.export_srt_var.get():
            outputs.append(audio.with_suffix(".srt"))
        return outputs

    def _rescan_batch_folder(self):
        if not self.batch_folder:
            return
        iterator = (
            self.batch_folder.rglob("*")
            if self.batch_recursive_var.get()
            else self.batch_folder.glob("*")
        )
        files = sorted(p for p in iterator if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        if self.batch_skip_existing_var.get():
            files = [
                p for p in files
                if not (self._expected_outputs(p) and all(o.exists() for o in self._expected_outputs(p)))
            ]
        self.batch_files = files

        if files:
            self.batch_files_label.configure(
                text=f"🎧 {len(files)} file audio da elaborare.", text_color=("black", "white")
            )
            self.batch_start_btn.configure(state="normal")
        else:
            self.batch_files_label.configure(
                text="Nessun file audio da elaborare nella cartella indicata.", text_color="gray"
            )
            self.batch_start_btn.configure(state="disabled")

    def _start_batch_pipeline(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.batch_files:
            return
        config = self._build_config()
        error = self._validate_config(config)
        if error:
            messagebox.showerror("Configurazione incompleta", error)
            return

        self.batch_start_btn.configure(state="disabled")
        self.batch_progress.set(0)
        for item in self.batch_tree.get_children():
            self.batch_tree.delete(item)
        self.batch_status_label.configure(text="Avvio batch…")

        files = list(self.batch_files)
        self._log(f"[batch] Avvio elaborazione di {len(files)} file in {self.batch_folder}")
        self.worker = threading.Thread(
            target=self._run_batch_worker, args=(files, config), daemon=True
        )
        self.worker.start()

    def _run_batch_worker(self, files: list[Path], config: PipelineConfig):
        total = len(files)
        for index, audio in enumerate(files):
            def on_phase(msg, _index=index, _audio=audio):
                self.msg_queue.put(("batch_phase", (_index, total, _audio.name, msg)))

            try:
                with tempfile.TemporaryDirectory(prefix="mp3lrc_") as tmp:
                    result = generate_subtitles(audio, Path(tmp), config, on_phase=on_phase)
                saved_paths = []
                unit_counts = []
                for fmt, text in result.texts.items():
                    out_path = audio.with_suffix(f".{fmt}")
                    out_path.write_text(text, encoding="utf-8")
                    saved_paths.append(str(out_path))
                    unit_counts.append(str(_count_units(fmt, text)))
                self.msg_queue.put((
                    "batch_file_done",
                    {
                        "file": audio.name,
                        "esito": "✅ OK",
                        "unita": "/".join(unit_counts),
                        "vocale": result.vocals_path.name if result.vocals_path else "—",
                        "output": " | ".join(saved_paths),
                    },
                ))
            except NoVocalsError as exc:
                self.msg_queue.put((
                    "batch_file_done",
                    {
                        "file": audio.name,
                        "esito": "⚠️ Nessuna traccia vocale rilevata",
                        "unita": "0",
                        "vocale": _vocals_name(exc),
                        "output": "—",
                    },
                ))
            except PipelineError as exc:
                self.msg_queue.put((
                    "batch_file_done",
                    {
                        "file": audio.name,
                        "esito": f"❌ {exc}",
                        "unita": "0",
                        "vocale": _vocals_name(exc),
                        "output": "—",
                    },
                ))
            except Exception as exc:  # un file corrotto non deve fermare il batch
                self.msg_queue.put((
                    "batch_file_done",
                    {
                        "file": audio.name,
                        "esito": f"❌ Errore imprevisto: {exc}",
                        "unita": "0",
                        "vocale": _vocals_name(exc),
                        "output": "—",
                    },
                ))

        self.msg_queue.put(("batch_done", total))

    def _batch_progress(self, index: int, total: int, phase_msg: str) -> float:
        base = index / total
        step = PHASE_PROGRESS.get(phase_msg, 0.0) / total
        return base + step

    # ------------------------------------------------------------------
    # Configurazione condivisa e gestione errori
    # ------------------------------------------------------------------
    def _build_config(self) -> PipelineConfig:
        return PipelineConfig(
            is_local=self.mode_var.get() == MODE_LOCAL,
            language=LANGUAGES[self.language_var.get()],
            do_separation=self.do_separation_var.get(),
            keep_vocals=self.keep_vocals_var.get(),
            whisper_model=self.whisper_model_var.get(),
            demucs_model=self.demucs_model_var.get(),
            stt_provider=CLOUD_STT_PROVIDERS[self.stt_provider_var.get()],
            stt_api_key=self.stt_api_key_var.get().strip(),
            replicate_api_key=self.replicate_api_key_var.get().strip(),
            max_line_len=int(self.max_line_len_var.get()),
            sync_anchor=self.sync_anchor_var.get(),
            export_lrc=self.export_lrc_var.get(),
            export_srt=self.export_srt_var.get(),
            lrc_enhanced=self.lrc_enhanced_var.get(),
        )

    def _validate_config(
        self, config: PipelineConfig, requires_local_alignment: bool = False
    ) -> str | None:
        if not config.export_lrc and not config.export_srt:
            return "Seleziona almeno un formato di esportazione (LRC e/o SRT)."
        if requires_local_alignment:
            if not config.is_local:
                return "Il riallineamento di un .lrc importato richiede la modalità Locale."
            if not config.language:
                return (
                    "Seleziona una lingua specifica (non Auto-detect) per "
                    "riallineare il testo importato."
                )
        if not config.is_local:
            if not config.stt_api_key:
                return "Inserisci la API Key per la trascrizione cloud nella sidebar."
            if config.do_separation and not config.replicate_api_key:
                return (
                    "Inserisci la API Key Replicate per la separazione cloud, "
                    "oppure disattiva la separazione vocale."
                )
        return None

    def _handle_pipeline_error(
        self, exc: Exception, status_label: ctk.CTkLabel, vocals_path: Path | None = None
    ):
        vocals_note = (
            f"\n\n🎙️ La traccia vocale estratta è stata comunque salvata in:\n{vocals_path}"
            if vocals_path
            else ""
        )
        if isinstance(exc, NoVocalsError):
            status_label.configure(text=f"⚠️ {exc}")
            messagebox.showwarning("Nessuna traccia vocale", f"{exc}{vocals_note}")
        elif isinstance(exc, NetworkError):
            status_label.configure(text=f"🌐 {exc}")
            messagebox.showerror("Errore di rete", f"{exc}{vocals_note}")
        elif isinstance(exc, MissingDependencyError):
            status_label.configure(text="📦 Dipendenza mancante")
            messagebox.showerror("Dipendenza mancante", f"{exc}{vocals_note}")
        elif isinstance(exc, (ApiError, PipelineError)):
            status_label.configure(text=f"❌ {exc}")
            messagebox.showerror("Errore", f"{exc}{vocals_note}")
        else:
            status_label.configure(text=f"❌ Errore imprevisto: {exc}")
            messagebox.showerror(
                "Errore imprevisto", f"{exc}{vocals_note}\n\n{traceback.format_exc()}"
            )

    # ------------------------------------------------------------------
    # Box di log: cronologia testuale di tutti gli step della pipeline
    # ------------------------------------------------------------------
    def _build_log_panel(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=1, sticky="nswe", padx=(5, 10), pady=(0, 10))
        frame.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="📋 Log esecuzione", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(header, text="Pulisci", width=70, command=self._clear_log).grid(
            row=0, column=1, sticky="e"
        )

        self.log_textbox = ctk.CTkTextbox(
            frame, height=140, font=ctk.CTkFont(family="Consolas", size=11)
        )
        self.log_textbox.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        self.log_textbox.configure(state="disabled")

    def _log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_textbox.configure(state="normal")
        self.log_textbox.insert("end", f"[{timestamp}] {message}\n")
        # Evita una crescita illimitata su batch molto lunghi.
        line_count = int(self.log_textbox.index("end-1c").split(".")[0])
        if line_count > 2000:
            self.log_textbox.delete("1.0", "500.0")
        self.log_textbox.see("end")
        self.log_textbox.configure(state="disabled")

    def _clear_log(self):
        self.log_textbox.configure(state="normal")
        self.log_textbox.delete("1.0", "end")
        self.log_textbox.configure(state="disabled")

    # ------------------------------------------------------------------
    # Loop di polling: unico punto che aggiorna i widget dal thread UI
    # ------------------------------------------------------------------
    def _dispatch_tab_message(self, kind: str, payload) -> bool:
        for tab_kind in self.tab_state:
            if kind == f"{tab_kind}_phase":
                self._handle_tab_phase(tab_kind, payload)
                return True
            if kind == f"{tab_kind}_segment":
                self._handle_tab_segment(tab_kind, payload)
                return True
            if kind == f"{tab_kind}_done":
                self._handle_tab_done(tab_kind, payload)
                return True
            if kind == f"{tab_kind}_error":
                self._handle_tab_error(tab_kind, payload)
                return True
        return False

    def _handle_tab_segment(self, kind: str, text: str):
        """Aggiunge in tempo reale, riga per riga, il testo via via trascritto
        (RF-03): l'utente vede la trascrizione grezza riempirsi mentre il
        motore lavora, prima ancora che i sottotitoli finali siano formattati."""
        if not text:
            return
        state = self.tab_state[kind]
        tb = state["textbox"]
        tb.configure(state="normal")
        tb.insert("end", text + "\n")
        tb.see("end")
        tb.configure(state="disabled")

    def _handle_tab_phase(self, kind: str, msg: str):
        state = self.tab_state[kind]
        state["status_label"].configure(text=msg)
        state["progress"].set(PHASE_PROGRESS.get(msg, state["progress"].get()))
        self._log(f"[{kind}] {msg}")

    def _handle_tab_done(self, kind: str, result):
        state = self.tab_state[kind]
        state["progress"].set(1.0)

        saved_paths, save_errors = self._auto_save_outputs(kind, state["audio_path"], result.texts)

        status_text = "✅ Completato"
        if saved_paths:
            status_text += " — salvato: " + ", ".join(p.name for p in saved_paths)
        if result.vocals_path:
            status_text += f" | voce: {result.vocals_path.name}"
        if save_errors:
            status_text += " ⚠️ errori di salvataggio (vedi log)"
        state["status_label"].configure(text=status_text)

        state["outputs"] = result.texts
        formats = list(result.texts.keys())
        if len(formats) > 1:
            labels = [FORMAT_LABELS[f] for f in formats]
            state["preview_selector"].configure(values=labels)
            state["preview_selector"].set(labels[0])
            state["preview_selector"].grid()
        else:
            state["preview_selector"].grid_remove()
        self._set_tab_textbox(state, result.texts.get(formats[0], ""))
        state["save_btn"].configure(state="normal")
        state["generate_btn"].configure(state="normal")

        summary = ", ".join(f"{FORMAT_LABELS[f]}: {_count_units(f, t)}" for f, t in result.texts.items())
        log_msg = f"[{kind}] ✅ Completato ({summary})"
        if saved_paths:
            log_msg += " — file salvati: " + ", ".join(str(p) for p in saved_paths)
        if result.vocals_path:
            log_msg += f" — voce salvata in {result.vocals_path}"
        self._log(log_msg)

        if save_errors:
            messagebox.showerror(
                "Errore di salvataggio automatico",
                "Sottotitoli generati correttamente, ma il salvataggio automatico "
                "e' fallito per:\n\n" + "\n".join(save_errors) +
                "\n\nUsa «Salva una copia altrove...» per salvarli manualmente.",
            )

    def _auto_save_outputs(
        self, kind: str, audio_path: Path, texts: dict[str, str]
    ) -> tuple[list[Path], list[str]]:
        """Salva subito ogni formato generato accanto al file audio, senza
        richiedere un'azione manuale dall'utente."""
        saved: list[Path] = []
        errors: list[str] = []
        for fmt, text in texts.items():
            out_path = audio_path.with_suffix(f".{fmt}")
            try:
                out_path.write_text(text, encoding="utf-8")
                saved.append(out_path)
            except OSError as exc:
                message = f"{out_path}: {exc}"
                errors.append(message)
                self._log(f"[{kind}] ❌ Salvataggio automatico fallito per {message}")
        return saved, errors

    def _handle_tab_error(self, kind: str, exc: Exception):
        state = self.tab_state[kind]
        state["generate_btn"].configure(state="normal")
        vocals_path = getattr(exc, "vocals_path", None)
        note = f" (voce salvata in {vocals_path})" if vocals_path else ""
        self._log(f"[{kind}] ❌ {exc}{note}")
        self._handle_pipeline_error(exc, state["status_label"], vocals_path)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()

                if self._dispatch_tab_message(kind, payload):
                    continue

                if kind == "batch_phase":
                    index, total, filename, msg = payload
                    self.batch_status_label.configure(text=f"({index + 1}/{total}) {filename} — {msg}")
                    self.batch_progress.set(self._batch_progress(index, total, msg))
                    self._log(f"[batch {index + 1}/{total}] {filename}: {msg}")

                elif kind == "batch_file_done":
                    r = payload
                    self.batch_tree.insert(
                        "", "end", values=(r["file"], r["esito"], r["unita"], r["vocale"], r["output"])
                    )
                    self._log(f"[batch] {r['file']}: {r['esito']}")

                elif kind == "batch_done":
                    total = payload
                    ok_count = sum(
                        1
                        for iid in self.batch_tree.get_children()
                        if str(self.batch_tree.item(iid, "values")[1]).startswith("✅")
                    )
                    self.batch_progress.set(1.0)
                    self.batch_status_label.configure(
                        text=f"✅ Batch completato: {ok_count}/{total} file elaborati con successo."
                    )
                    self.batch_start_btn.configure(state="normal")
                    self._log(f"[batch] Completato: {ok_count}/{total} file OK")
                    self._rescan_batch_folder()
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


def main() -> int:
    multiprocessing.freeze_support()
    prepare_frozen_runtime()

    # Modalita' senza interfaccia usate dall'eseguibile: il sottoprocesso
    # Demucs (l'app rilancia se stessa, vedi separation.demucs_command) e
    # l'autotest di verifica della build.
    if len(sys.argv) > 1 and sys.argv[1] == DEMUCS_CLI_FLAG:
        return run_demucs_cli(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == SELFTEST_FLAG:
        return run_selftest(sys.argv[2:])

    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
