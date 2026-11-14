# Guida di avvio rapido

## 1. Requisiti
- Python **3.11 o 3.12** (su Mac Apple Silicon: `brew install python@3.12 espeak-ng` per la voce naturale locale)
- Una chiave API: **OpenRouter** (<https://openrouter.ai>, a consumo: un'intervista Pirelli completa costa ~0,03 $; usa Claude Sonnet + Haiku), **Gemini** (gratis su <https://aistudio.google.com>, ma solo ~20 richieste al giorno sul modello Flash) oppure **Claude** (<https://platform.claude.com>). Ne basta **una**.
- Chrome o Edge per l'intervista a voce (su Firefox si risponde scrivendo).

## 2. Installazione
```bash
git clone git@github.com:Keevin16/StudentClash-PreCooked.git
cd StudentClash-PreCooked
python3 -m venv .venv
source .venv/bin/activate          # fish: source .venv/bin/activate.fish · Windows: .venv\Scripts\activate
pip install -e ".[voice,dev]"      # Mac Apple Silicon: voce naturale locale (Whisper + Kokoro)
# pip install -r requirements.txt  # altri computer: si usa la voce del browser
```
Al primo avvio con la voce locale il server scarica i modelli (~2,5 GB) in `.cache/`: attendi la riga `local voice ready` nel terminale.

## 3. Configurazione
```bash
cp .env.example .env
```
Apri `.env` e imposta **una** chiave, per esempio:
```
OPENROUTER_API_KEY=la-tua-chiave
```
(oppure `GEMINI_API_KEY=...` o `ANTHROPIC_API_KEY=...`). Verifica che funzioni:
```bash
python -m pythagoras check            # deve stampare tre righe "ok" (reasoner, judge, talker)
```
Senza chiave puoi comunque provare l'interfaccia con risposte finte: `PYTHAGORAS_FAKE_LLM=1 python -m pythagoras serve`.

## 4. Avvio
```bash
python -m pythagoras serve            # http://localhost:8000
```
Lancialo dalla cartella del progetto. **Dopo ogni modifica a `.env` o al codice riavvialo** (Ctrl+C e di nuovo), oppure usa `--reload`.

## 5. Demo veloce: caso Pirelli
Serve la cartella `cases/pirelli/` (i due PDF e `team1.json`), che non è nel repository: chiedila al team.
- **Studenti** → <http://localhost:8000/> → **Use the Pirelli case**: si apre l'intervista senza compilare nulla. Il primo clic prepara il piano (~2 minuti, la pagina va avanti da sola), i successivi partono subito.
- **Professore** → <http://localhost:8000/prof>: nella consegna Pirelli c'è già il report completo di **Team_1** (3 temi a livelli diversi, citazioni, domande per l'orale).

Senza `cases/pirelli/` il pulsante usa il caso sintetico della rete neurale, e nella sua consegna ci sono i report completi di **Team_1** e **Team_2** (`cases/ffnn/team1.json`, `team2.json`). Le interviste avviate dal pulsante demo non compaiono nella pagina professore.

## 6. Prova completa con il caso di test (`cases/ffnn/`)
1. **Professore** → <http://localhost:8000/prof>, senza password (per richiederne una: `PYTHAGORAS_PROFESSOR_PASSWORD` in `.env`).
   *New assignment*: copia titolo, corso, descrizione e obiettivi da `cases/ffnn/assignment.md`, allega `cases/ffnn/course/ffnn-notes.md`.
2. **Team** → <http://localhost:8000/>: scegli l'assignment, nome del team (es. Team_1), email di contatto, carica `cases/ffnn/deliverable/report.pdf` e `mlp.py`.
3. Dopo ~20 s l'intervista è pronta. Senza SMTP il link compare a schermo (e la mail viene salvata in `data/outbox/`).
4. Apri il link in **Chrome**, consenti il microfono, *Start interview* e rispondi a voce. Con la voce locale non serve premere nulla: Pythagoras capisce da solo quando hai finito (e aspetta se stai pensando). **"I'm done answering"** / Spazio chiude subito il turno; con la voce del browser va premuto dopo ogni risposta.
5. **Professore** → apri l'assignment: ✓ sull'intervista completata → *View report*.

Gli errori nascosti nel progetto di test sono elencati in `cases/ffnn/README.md`: confrontali con il report.

## 7. Email vere (opzionale)
In `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` (es. Gmail: `smtp.gmail.com`, porta 587, una *password per le app*). Imposta anche `PYTHAGORAS_PUBLIC_URL` con l'indirizzo che gli studenti useranno per aprire il link.

## 8. Problemi comuni
| Sintomo | Soluzione |
|---|---|
| "Could not resolve authentication method…" | Il server non vede la chiave: controlla `.env`, lancia dalla cartella del progetto e **riavvia** il server. Poi *Try again* nella pagina dell'intervista |
| 429 / 503 da Gemini, risposte lente | Limiti del free tier: il codice ritenta da solo; aspetta qualche secondo |
| "Report queued" nel report | Quota della chiave finita: l'intervista è salvata e il report viene rigenerato da solo (dopo 1, 5, 15, 30, 60 min, poi ogni ora, anche dopo un riavvio). Oppure *Retry now* |
| Il microfono non parte | Serve `localhost` o HTTPS e il permesso del browser; usa Chrome/Edge |
| "Natural voice unavailable" | La voce locale non è partita (vedi il terminale; spesso manca `espeak-ng`): l'intervista continua con la voce del browser. Per forzarla: `PYTHAGORAS_VOICE=browser` |
| Pythagoras non risponde dopo che ho parlato | La risposta finisce solo con **I'm done** (o la barra spaziatrice): le pause non la chiudono più |
| La voce è sgranata | Succede su Firefox / Zen: usa Chrome o Edge |
| Verifica completa automatica | Con il server avviato: `python scripts/smoke.py` (studente sintetico, stampa le latenze) |
| Ricominciare da zero | Ferma il server e cancella la cartella `data/` |

## 9. Test
```bash
python -m pytest                   # offline, nessuna chiave necessaria
```

⚠️ Il free tier di Gemini può usare i prompt per addestrare i modelli: usalo **solo con dati finti** (come `cases/ffnn`), mai con consegne reali di studenti.
