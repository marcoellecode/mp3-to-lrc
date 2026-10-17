# Specifiche dei Requisiti Software (SRS)
## Progetto: MP3-to-LRC Lyric Sync Generator (Multi-Engine)

---

## 1. Introduzione ed Obiettivi del Progetto

### 1.1 Scopo del Documento
Questo documento definisce in modo dettagliato i requisiti funzionali, non funzionali, l'architettura di sistema e le tecnologie necessarie per lo sviluppo di un'applicazione (desktop/web locale) in grado di convertire file audio `.mp3` musicali in file di sottotitoli sincronizzati in formato `.lrc`.

### 1.2 Obiettivo del Software
L'applicazione deve automatizzare il processo di creazione di file lirici sincronizzati (`.lrc`). Per massimizzare la precisione, il sistema deve includere una fase preliminare di **separazione della traccia vocale** (isolamento dell'acapella dalla base strumentale) prima di inviare l'audio al motore di trascrizione Speech-to-Text con timestamp a livello di parola (word-level timestamping). L'applicazione deve poter operare in due modalità alternative:

*   **Modalità Locale:** Elaborazione interamente offline sfruttando l'hardware dell'utente.
*   **Modalità Cloud:** Elaborazione rapida appoggiandosi ad API esterne, utile per macchine con scarse risorse computazionali.

---

## 2. Architettura di Sistema e Flusso dei Dati

Il flusso logico dell'applicazione è lineare e si articola in 4 macro-fasi:

[ File MP3 ] ──> [ 1. Separazione Vocale ] ──> [ File Vocale (Solo Voce) ]
│
[ File LRC ] <── [ 3. Formattazione LRC ]  <── [ 2. Trascrizione con Timestamp ]

### 2.1 Flusso di Lavoro Dettagliato
1.  **Ingresso (Ingest):** L'utente carica un file `.mp3` e seleziona i parametri (Lingua, Modalità Locale/Cloud, Modello IA).
2.  **Separazione Audio (Audio Demixing):** Il file viene elaborato per estrarre la sola traccia vocale, eliminando la base musicale che potrebbe confondere l'IA.
3.  **Riconoscimento Vocale (Speech-to-Text):** La traccia vocale isolata viene passata a un modello di trascrizione ottimizzato per la musica, che restituisce i segmenti testuali completi di timestamp millisecondati.
4.  **Generazione LRC (Formatting):** I dati di testo e tempo vengono formattati secondo lo standard LRC `[mm:ss.xx] Testo` e salvati in un file scaricabile.

---

## 3. Requisiti Funzionali (Functional Requirements)

### RF-01: Interfaccia Utente (UI) e Input
*   **RF-01.1:** L'applicazione deve presentare un'interfaccia grafica pulita (sviluppata preferibilmente con **Streamlit** o framework desktop equivalente come **Flet / CustomTkinter**).
*   **RF-01.2:** L'utente deve poter caricare un file audio tramite drag-and-drop o file explorer. Formati supportati in ingresso: `.mp3`, `.wav`, `.m4a`.
*   **RF-01.3:** Pannello di controllo laterale (Sidebar) per la configurazione dei parametri:
    *   *Switch di Modalità:* "Locale (Offline/GPU)" o "Cloud (API)".
    *   *Selettore Lingua:* Menu a tendina con opzioni (Auto-detect, Italiano, Inglese, Spagnolo, Francese, ecc.).
    *   *Selettore Modello Whisper (solo Locale):* Scelta tra `tiny`, `base`, `small`, `medium` (default: `small`).
    *   *Input Chiavi API (solo Cloud):* Campi di testo mascherati per inserire le API key di terze parti (es. OpenAI API Key, Replicate API Key).

### RF-02: Modulo di Separazione Audio (Demixing)
Questo modulo deve accettare l'MP3 originale e restituire un file audio temporaneo contenente solo la voce (`vocals`).
*   **RF-02.1 (Locale):** Integrazione della libreria open-source **Meta Demucs** (modello `htdemucs` o `htdemucs_ft`). Il codice deve verificare la disponibilità di accelerazione hardware CUDA (GPU Nvidia) e, in caso negativo, ripiegare sulla CPU.
*   **RF-02.2 (Cloud):** Integrazione tramite chiamata API a servizi di terze parti (es. **LALAL.AI API** o endpoint dedicati su **Replicate** per il modello Demucs).

### RF-03: Modulo di Trascrizione e Sincronizzazione (Speech-to-Text)
Questo modulo elabora la traccia vocale isolata e calcola i timestamp precisi per ogni riga/parola.
*   **RF-03.1 (Locale):** Utilizzo della libreria Python **`stable-ts`** (Stable Whisper) basata su `faster-whisper`.
    *   Il sistema deve mappare i segmenti e le parole minimizzando i falsi silenzi indotti dalle pause musicali.
*   **RF-03.2 (Cloud):** Chiamata API all'endpoint di **OpenAI (Whisper-1)** o tramite **Groq API** (usando il modello `whisper-large-v3` per la massima velocità). I dati ricevuti (JSON con segmenti e timestamp) devono essere processati dallo script.

### RF-04: Formattatore LRC (Output)
*   **RF-04.1:** Il sistema deve convertire l'output della trascrizione nel formato standard `.lrc`:
    > Formato richiesto: `[minuti:secondi.centesimi] Testo della riga`
    > Esempio: `[00:14.32] Nel mezzo del cammin di nostra vita`
*   **RF-04.2:** Lo script deve gestire la frammentazione delle frasi in modo che le righe visualizzate a schermo non siano troppo lunghe (consigliato: max 40-50 caratteri per riga).
*   **RF-04.3:** L'applicazione deve mostrare un'anteprima testuale del file `.lrc` generato e fornire un pulsante "Download `.lrc`".

---

## 4. Stack Tecnologico Consigliato (Tech Stack)

Lo sviluppatore dovrà attenersi preferibilmente al seguente stack tecnologico per garantire manutenibilità e rapidità di sviluppo:

*   **Linguaggio di Programmazione:** Python 3.10+
*   **Framework UI:** Streamlit (scelta ottimale per un prototipo rapido e condivisibile anche via browser locale) o CustomTkinter.
*   **Librerie Core Audio & AI (Locale):**
    *   `stable-ts` (per l'allineamento temporale preciso e la generazione nativa del file LRC).
    *   `faster-whisper` (motore di inferenza Whisper ottimizzato).
    *   `demucs` (libreria ufficiale di Meta per la separazione delle sorgenti audio).
    *   `soundfile` / `pydub` (per la manipolazione preliminare dei file audio).
*   **Librerie API (Cloud):**
    *   `openai` (per l'invio delle tracce audio ai server OpenAI).
    *   `requests` (per le chiamate HTTP generiche ad altre API di separazione/trascrizione).

---

## 5. Requisiti Non Funzionali (Non-Functional Requirements)

### RNF-01: Performance e Hardware (Modalità Locale)
*   L'applicazione deve poter girare su sistemi Windows, macOS e Linux.
*   In modalità locale, se è presente una GPU NVIDIA con CUDA installato, il tempo di elaborazione per una canzone di 3 minuti non deve superare i **60 secondi** (usando il modello Whisper `small`).

### RNF-02: Usabilità ed Esperienza Utente (UX)
*   Durante le fasi di elaborazione (separazione e trascrizione), l'interfaccia deve mostrare indicatori di progresso visivi (loading spinner o progress bar) per evitare che l'utente pensi che l'applicazione si sia bloccata.

### RNF-03: Gestione dei File Temporanei
*   I file audio intermedi (la traccia vocale estratta ed eventuali conversioni in `.wav`) devono essere salvati in una cartella temporanea di sistema (es. `tempfile` in Python) e tassativamente eliminati al termine del processo di esportazione del file `.lrc`, per non saturare lo storage dell'utente.

---

## 6. Gestione degli Errori e Casi Limite (Edge Cases)

Lo sviluppatore deve prevedere e gestire i seguenti scenari di errore:
1.  **Assenza di traccia vocale:** Se la canzone è interamente strumentale, l'IA di trascrizione potrebbe restituire un testo vuoto o allucinazioni. Il sistema deve intercettare l'output vuoto e notificare l'utente con un messaggio di avviso (*"Nessuna traccia vocale rilevata nel file"*).
2.  **Mancanza di GPU in locale:** Se l'utente seleziona la modalità Locale ma non ha una GPU compatibile, l'applicazione deve deviare automaticamente l'esecuzione sulla CPU, avvisando l'utente che il processo richiederà più tempo.
3.  **Timeout delle API (Cloud):** In caso di problemi di connessione o timeout dei server cloud, l'applicazione deve mostrare l'errore specifico (es. *"Errore di rete: Impossibile raggiungere i server di trascrizione"*), senza crashare.

---

## 7. Criteri di Accettazione (Acceptance Criteria)

Il software si considererà completato e pronto al rilascio quando:
1.  Sarà in grado di elaborare un file `.mp3` di test in lingua italiana e generare un file `.lrc` i cui timestamp corrispondano all'audio con uno scarto massimo di ±150 millisecondi.
2.  Il file `.lrc` generato sarà importabile e correttamente letto da un software di video editing (es. CapCut o plugin per After Effects).
3.  Lo switch tra modalità Locale e Cloud funzionerà senza richiedere il riavvio dell'applicazione.