# 🎵 MP3-to-LRC Lyric Sync Generator (Multi-Engine)

Converte file audio musicali (`.mp3`, `.wav`, `.m4a`) in sottotitoli
sincronizzati **`.lrc`** (`[mm:ss.xx] Testo`) e/o **`.srt`**
(`HH:MM:SS,mmm --> HH:MM:SS,mmm`), con separazione preliminare della traccia
vocale per massimizzare la precisione dei timestamp.

Interfaccia desktop nativa (**CustomTkinter**), nessun browser richiesto.

## Download (Windows)

Scarica lo zip dell'ultima versione dalla pagina
[Releases](https://github.com/marcoellecode/mp3-to-lrc/releases), estrai
**l'intera cartella** e avvia `MP3-to-LRC.exe`. Non serve Python.

- Windows 10/11 a 64 bit; la versione scaricabile usa solo la CPU.
- I modelli Demucs e Whisper vengono scaricati al primo uso della modalità
  Locale, quindi il primo avvio richiede internet.

Per eseguire l'app dai sorgenti o crearsi l'eseguibile da soli, vedi sotto e
[BUILD.md](BUILD.md).

## Pipeline

```
[ File MP3 ] → [ 1. Separazione Vocale ] → [ Solo Voce ]
                                                ↓
[ .lrc / .srt ] ← [ 3. Formattazione ] ← [ 2. Trascrizione word-level ]
```

## Due modalità (switch senza riavvio)

| Fase          | Locale (Offline/GPU)                  | Cloud (API)                         |
|---------------|---------------------------------------|-------------------------------------|
| Separazione   | Meta **Demucs** (`htdemucs`/`_ft`)    | **Replicate** (modello Demucs)      |
| Trascrizione  | **stable-ts** + faster-whisper        | **OpenAI Whisper-1** o **Groq** `whisper-large-v3` |

In locale l'app rileva automaticamente CUDA (GPU NVIDIA) e ripiega sulla CPU
avvisando l'utente. I file intermedi vengono creati in una cartella temporanea
di sistema ed eliminati automaticamente a fine processo.

## Installazione

Prerequisiti: **Python 3.10+** e **FFmpeg** nel PATH (necessario a Demucs e
alla decodifica audio).

```bash
# Solo modalità Cloud (leggera)
pip install -r requirements.txt

# Modalità Locale completa (Demucs + Whisper locali, ~2-3 GB di dipendenze)
pip install -r requirements-local.txt
```

Per l'accelerazione GPU installare PyTorch con CUDA seguendo le istruzioni su
<https://pytorch.org/get-started/locally/>.

## Avvio

```bash
python desktop_app.py
```

Si apre la finestra dell'applicazione (nessun server, nessun browser).

## Utilizzo

1. Nella **sidebar** scegli la modalità (Locale/Cloud), la lingua (default
   **Auto-detect**: non forzare una lingua diversa da quella reale del
   brano, altrimenti Whisper può arrivare a **saltare interi versi iniziali**
   anziché limitarsi a trascriverli male), il modello Whisper (locale) o le
   chiavi API (cloud: OpenAI/Groq per la trascrizione, Replicate per la
   separazione vocale).
   - **🎙️ Mantieni traccia vocale estratta**: visibile quando la separazione è
     attiva; salva la voce isolata da Demucs/Replicate accanto al file audio
     originale come `nome_vocals.wav` (o l'estensione restituita dal
     servizio), anche se la trascrizione successiva fallisce.
   - Nelle *Opzioni avanzate*: **Formati di esportazione** (`.lrc` e `.srt`
     entrambi attivi di default), con l'opzione **Enhanced (tempi per
     parola)** per l'`.lrc`: genera un *Enhanced LRC* (estensione A2) con un
     tag `<mm:ss.xx>` davanti a ogni parola, per i player karaoke che
     evidenziano la parola cantata
     (`[00:12.00] <00:12.00> Nel <00:12.50> mezzo <00:13.00> del <00:13.40>`).
     Stesso file `.lrc`, spenta di default perché i player che non la
     supportano mostrano i tag nel testo. Il **Marker "remove me"** a inizio file
     (durata 2 s), utile come ancora di sincronizzazione nei programmi di
     video editing.
2. Scegli la scheda:
   - **🎵 Singolo file**: seleziona il brano dal file explorer e premi
     **Genera sottotitoli**. Durante la fase di trascrizione, il testo
     compare **in tempo reale** nel riquadro di anteprima man mano che il
     motore lo riconosce (in locale, riga per riga durante la decodifica
     stessa; in cloud, non appena arriva la risposta del server, dato che le
     API non offrono risultati parziali). Al termine, l'anteprima si aggiorna
     con l'output formattato definitivo (LRC/SRT) e ogni formato attivo viene
     **salvato automaticamente accanto al file audio originale**, senza
     bisogno di premere altro. **Salva una copia altrove...** resta
     disponibile per esportare i file anche in un'altra cartella.
   - **🎤 File vocale**: importa un file che contiene *già solo la voce*
     (per esempio un `nome_vocals.wav` salvato in precedenza): salta la
     separazione Demucs e passa direttamente a trascrizione e formattazione
     (stesso salvataggio automatico della scheda "Singolo file").
   - **🔄 Risincronizza LRC**: importa un file `.lrc` già compilato (il testo
     viene estratto, i vecchi timestamp scartati) e lo **riallinea sulla
     traccia audio caricata** con un riallineamento forzato — utile quando i
     tempi non coincidono più perché l'.lrc proviene da una versione diversa
     del brano (radio edit vs extended mix, remaster con intro più
     lungo/corto...). Il testo resta quello importato, solo i tempi vengono
     ricalcolati da zero su quel file audio specifico. Richiede la modalità
     **Locale** e una **lingua esplicita** (non Auto-detect) nella sidebar,
     perché il riallineamento forzato ha bisogno di sapere in anticipo la
     lingua del testo e sfrutta i pesi del modello Whisper, non disponibili
     via API cloud.
   - **📁 Batch cartella**: seleziona una cartella locale (opzionalmente con
     sottocartelle); ogni output viene salvato **accanto al file audio
     originale, con lo stesso nome** (es. `canzone.mp3` → `canzone.lrc` e/o
     `canzone.srt`, più `canzone_vocals.wav` se "Mantieni traccia vocale" è
     attivo). I brani già completi (tutti i formati richiesti già presenti)
     possono essere saltati. Un errore su un file non ferma il batch: a fine
     corsa una tabella riepiloga l'esito di ogni traccia.
3. L'avanzamento è mostrato con progress bar e indicatori di fase; la
   generazione gira in background, l'interfaccia resta reattiva. Il riquadro
   **📋 Log esecuzione** in basso registra con timestamp ogni step di ogni
   elaborazione (separazione, trascrizione, formattazione, salvataggi,
   errori), utile per capire a che punto è un batch lungo o diagnosticare un
   problema.

## Struttura del progetto

```
desktop_app.py               # UI desktop nativa (CustomTkinter) — punto di ingresso
lrc_engine/
├── pipeline.py               # orchestrazione condivisa delle fasi (trascrizione o resync)
├── constants.py               # liste di selezione condivise (lingue, modelli, provider)
├── separation.py              # RF-02: Demucs locale + Replicate cloud
├── transcription.py           # RF-03: stable-ts locale + OpenAI/Groq cloud
├── alignment.py                # riallineamento forzato di un testo noto (resync)
├── lrc_import.py               # parsing di un .lrc esistente (estrae solo il testo)
├── lrc.py                     # RF-04: formattatori LRC e SRT con line-wrapping word-level
├── hardware.py                # rilevamento CUDA / fallback CPU
├── runtime.py                 # adattamenti per l'eseguibile (ffmpeg incluso, niente console)
├── selftest.py                # autotest senza interfaccia (--selftest), per verificare la build
└── errors.py                  # eccezioni tipizzate (edge cases SRS §6)
mp3_to_lrc.spec              # configurazione PyInstaller
build.ps1                    # script di build dell'eseguibile
tests/
├── test_lrc.py                # test unitari dei formattatori LRC/SRT
├── test_pipeline.py           # test di orchestrazione della pipeline
├── test_transcription.py      # test dello streaming del testo (on_segment)
├── test_alignment.py           # test del riallineamento forzato (resync)
└── test_lrc_import.py          # test del parser di import .lrc
```

## Gestione errori (edge cases)

- **Brano strumentale** → avviso *"Nessuna traccia vocale rilevata nel file"*.
- **GPU assente** → fallback automatico su CPU con avviso sui tempi.
- **Timeout/errori API** → messaggio d'errore specifico, l'app non crasha.

## Test

```bash
python -m pytest tests/ -v
```

## Creare l'eseguibile (Windows)

Guida completa, con verifiche eseguite e note per la distribuzione:
[BUILD.md](BUILD.md).

1. Venv di build (una volta sola; fuori da Dropbox perché pesa ~1,2 GB):

   ```powershell
   py -3.13 -m venv $env:USERPROFILE\venvs\mp3-to-lrc
   $py = "$env:USERPROFILE\venvs\mp3-to-lrc\Scripts\python.exe"
   & $py -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
   & $py -m pip install -r requirements-build.txt
   ```

   Versioni esatte verificate: `requirements-lock.txt`. Serve anche
   **FFmpeg** nel PATH (oppure `$env:FFMPEG_DIR`): `ffmpeg.exe` e
   `ffprobe.exe` vengono inclusi nel pacchetto.

2. Build:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\build.ps1
   ```

   Esegue i test e crea `%USERPROFILE%\builds\MP3-to-LRC\MP3-to-LRC.exe`.
   Va distribuita **l'intera cartella** `MP3-to-LRC` (non solo l'.exe).
   Opzioni: `-Console` (build con console, per diagnosticare errori),
   `-SelfTest <file audio>` (verifica l'eseguibile creato con una
   trascrizione locale completa), `-DistDir <cartella>`, `-SkipTests`.

I modelli (Demucs e Whisper) non sono inclusi: vengono scaricati al primo
utilizzo della modalità Locale e conservati nella cache dell'utente
(`%USERPROFILE%\.cache`), quindi il primo avvio richiede internet.

## Licenza

[MIT](LICENSE). La build scaricabile include FFmpeg in versione LGPL (licenza in
`_internal/ffmpeg/LICENSE-ffmpeg.txt`).
