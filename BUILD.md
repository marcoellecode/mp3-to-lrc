# MP3-to-LRC — Creare l'eseguibile

Aggiornato al 17 ottobre 2026.

## In breve

L'eseguibile si crea in due modi, con lo stesso script `build.ps1`:

- **GitHub Actions** (consigliato): a ogni tag `v*` GitHub costruisce, verifica e pubblica lo zip nella pagina [Releases](https://github.com/marcoellecode/mp3-to-lrc/releases). Vedi "Build automatica con GitHub Actions" più sotto.
- **In locale**: `build.ps1` crea `%USERPROFILE%\builds\MP3-to-LRC\MP3-to-LRC.exe`, una cartella da circa 975 MB con ffmpeg incluso.

L'autotest fa girare dentro l'eseguibile la pipeline Locale completa (Demucs, trascrizione, Enhanced LRC) su un audio parlato in italiano (`tests/fixtures/speech_it.mp3`), in circa 34 secondi.

## Ambiente di build

Il venv di build sta in `%USERPROFILE%\venvs\mp3-to-lrc` (Python 3.13, 1,2 GB), fuori dalla cartella del progetto (che è sincronizzata con Dropbox) per non sincronizzare decine di migliaia di file.

| Scelta | Motivo |
| --- | --- |
| PyTorch in versione CPU | Il PC ha solo una Intel UHD 620, nessuna GPU NVIDIA; l'eseguibile pesa molto meno |
| `av<19` (PyAV 18.1) | PyAV 19 ha rimosso un parametro usato da faster-whisper 1.2.x, che va in errore |
| PyInstaller 6.22 + pytest | Build e test prima della build |

I file dei requisiti nel progetto:

- `requirements-build.txt`: tutto il necessario per la build (include `requirements-local.txt`)
- `requirements-lock.txt`: le versioni esatte verificate, per ricreare lo stesso ambiente

Per ricreare il venv da zero:

```powershell
py -3.13 -m venv $env:USERPROFILE\venvs\mp3-to-lrc
$py = "$env:USERPROFILE\venvs\mp3-to-lrc\Scripts\python.exe"
& $py -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
& $py -m pip install -r requirements-build.txt
```

Serve anche FFmpeg nel PATH (oppure la variabile `FFMPEG_DIR`): `ffmpeg.exe` e `ffprobe.exe` vengono copiati nel pacchetto.

## Come ricostruire l'eseguibile

Dalla cartella del progetto, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Lo script fa, in ordine:

1. Esegue i test (62); se uno fallisce, la build si ferma.
2. Lancia PyInstaller con `mp3_to_lrc.spec` (circa 5 minuti).
3. Crea `%USERPROFILE%\builds\MP3-to-LRC\MP3-to-LRC.exe` e ne stampa la dimensione.
4. Se richiesto, esegue l'autotest sull'eseguibile appena creato.

I file temporanei di PyInstaller vanno in `%LOCALAPPDATA%\mp3-to-lrc-build`, anche loro fuori da Dropbox.

| Opzione | Effetto |
| --- | --- |
| `-SelfTest <file audio>` | Verifica l'eseguibile con una trascrizione locale completa (modello `tiny`) |
| `-Console` | Build con finestra console, per vedere errori e log |
| `-DistDir <cartella>` | Dove creare la cartella `MP3-to-LRC` (default `%USERPROFILE%\builds`) |
| `-Venv <cartella>` | Venv da usare (default `%USERPROFILE%\venvs\mp3-to-lrc`) |
| `-SkipTests` | Salta i test prima della build |

Esempio con verifica finale:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -SelfTest C:\musica\canzone.mp3
```

## Build automatica con GitHub Actions

Il workflow `.github/workflows/build.yml` gira su un server Windows di GitHub e fa, in ordine:

1. Installa Python 3.13 e le versioni esatte di `requirements-lock.txt` (torch CPU).
2. Scarica FFmpeg in build **LGPL** ([BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)), ridistribuibile senza vincoli sulla licenza del progetto.
3. Esegue `build.ps1` con test, build e autotest su `tests/fixtures/speech_it.mp3`.
4. Comprime la cartella in `MP3-to-LRC-<versione>-windows-x64.zip`, con le licenze del progetto e di FFmpeg.
5. Pubblica lo zip in una Release (tag) oppure come artifact scaricabile per 7 giorni (avvio manuale).

Per pubblicare una nuova versione:

```powershell
git tag v1.0.1
git push origin v1.0.1
```

Per una build di prova senza Release: scheda **Actions** del repository, workflow "Build Windows", pulsante **Run workflow**.

## Modifiche al codice per l'eseguibile

Due cose si rompevano dentro un `.exe` e sono state sistemate; da Python normale l'app funziona come prima.

| File | Cosa fa |
| --- | --- |
| `lrc_engine/separation.py` | Dentro l'eseguibile non esiste `python -m demucs`: l'app rilancia se stessa con il flag interno `--run-demucs` |
| `lrc_engine/runtime.py` | Solo nell'eseguibile: mette ffmpeg nel PATH, gestisce `stdout`/`stderr` assenti (le barre di avanzamento farebbero crashare l'app) e nasconde le finestre console di ffmpeg e Demucs |
| `lrc_engine/selftest.py` | Autotest senza finestra: `MP3-to-LRC.exe --selftest canzone.mp3`, esito in `canzone.mp3.selftest.log` |
| `desktop_app.py` | Nuova funzione `main()` che smista tra finestra, Demucs e autotest |
| `mp3_to_lrc.spec` | Configurazione PyInstaller: modalità cartella, file di dati delle librerie, ffmpeg incluso |
| `build.ps1` | Script di build descritto sopra |

L'autotest accetta anche `--model` (default `tiny`), `--lang` (default rilevamento automatico) e `--no-separation`. Genera `.lrc` (Enhanced) e `.srt` accanto al file audio.

## Verifiche eseguite

La modalità Locale è verificata dentro l'eseguibile; la modalità Cloud e un PC senza Python non sono stati provati.

| Verifica | Esito |
| --- | --- |
| 62 test automatici | Tutti passati |
| Autotest da Python normale (audio parlato in italiano) | OK in 27 s |
| Autotest dentro l'eseguibile, stesso audio | OK in 34 s: Demucs 25 s, trascrizione 9 s |
| Avvio normale dell'eseguibile | Si apre la finestra "MP3 → LRC Lyric Sync Generator" |
| Avvisi di PyInstaller | 3, innocui: tensorboard escluso apposta, gli altri due riguardano moduli non usati |

Non ancora provato:

- La modalità Cloud (OpenAI, Groq, Replicate) dentro l'eseguibile.
- L'eseguibile su un PC senza Python installato.
- Una canzone vera con musica: l'audio di prova era sintesi vocale di Windows.

## Note per la distribuzione

Va distribuita l'intera cartella `MP3-to-LRC`, non solo il file `.exe`.

- **Cartella e non file singolo**: con torch dentro, un file singolo dovrebbe scompattare circa 1 GB a ogni avvio, con decine di secondi di attesa.
- **Modelli non inclusi**: Demucs e Whisper vengono scaricati al primo uso della modalità Locale e salvati in `%USERPROFILE%\.cache`. Il primo avvio richiede internet.
- **Solo CPU**: con PyTorch CPU l'eseguibile non usa GPU NVIDIA nemmeno se il PC ne ha una.
- **Dimensione**: 975 MB in totale, di cui circa 200 MB sono ffmpeg e ffprobe.

## Possibili sviluppi futuri

- [ ] Installer con Inno Setup: distribuisce la cartella con collegamento sul desktop e disinstallazione.
- [ ] Modelli inclusi nel pacchetto: funziona offline dal primo avvio, ma la cartella cresce di centinaia di MB.
- [ ] Provare la modalità Cloud dentro l'eseguibile.
- [ ] Provare l'eseguibile su un PC senza Python.
