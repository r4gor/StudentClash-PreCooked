# Pythagoras web app: pages and API contract

One FastAPI process serves the API under `/api` and the static frontend (`pythagoras/static/`, plain HTML/CSS/JS, no build step).

## Pages

| Route | File | Who | Does |
|---|---|---|---|
| `/` | `static/index.html` | team | Pick an assignment, team name, team contact email, upload deliverable file(s); or one click on **Use the Pirelli case** (`POST /api/demo`). On success: "we'll email your link when the interview is ready" (+ the invite link itself when email is not configured, `invite_url` in the response) |
| `/interview/{token}` | `static/interview.html` | team | Voice interview with Pythagoras (the team answers; webcam panel with a visual-only REC badge). States: preparing (poll every 3 s) → intro (rules, mic check, Start) → interview loop → done ("Thanks, your professor will receive the report") |
| `/prof/login` | `static/prof-login.html` | professor | Password form; redirects to `/prof` when no password is set (the default) |
| `/prof` | `static/prof.html` | professor | Assignment cards (title, course, submitted / completed counts) + "New assignment" form |
| `/prof/assignments/{id}` | `static/prof-assignment.html` | professor | Table of deliverables: team name, email, submitted at, files, status, ✓ when the AI assessment is completed, "View report" when done |
| `/prof/submissions/{id}` | `static/prof-report.html` | professor | The AI assessment report: summary, ownership, Bloom level per topic, evidence quotes with mm:ss, strengths, concerns, suggested oral-exam questions, critical analysis of the deliverable, full transcript, deliverable files |

Prof pages redirect to `/prof/login` when an `/api/prof/*` call returns 401.

## Voice (local models, no keys)

`GET /api/interview/{token}` returns `voice: "local" | "browser"`, `language` and `camera` (webcam preview on, never recorded). With `"local"` the page (`static/voice-local.js`) uses:

| | | |
|---|---|---|
| GET | `/api/voice/status` | `{mode, language, missing, warmed, error}` |
| POST | `/api/voice/warmup` | loads the models (done at server start too); 503 with the reason if it fails |
| POST | `/api/voice/speak` `{text}` | NDJSON stream, one `{"audio": <base64 WAV>}` per sentence |
| WS | `/api/voice/stream?token=` | page → server: binary 16 kHz int16 PCM frames (20 ms), `{"type":"listen","enabled","epoch"}`, `{"type":"finish"}` · server → page: `ready`, `speech_start`, `turn_end` (the answer is over: play the acknowledgement; sent only after `finish`, or at the 120 s cap, never on silence), `transcript {text, speech_s}`, `empty`, `error {message}` |

The answer ends when the team presses **I'm done** (or Space), which sends `finish`. The page then posts the transcript to `/api/interview/{token}/answer` as usual. `POST /api/interview/{token}/flag {text}` records an integrity signal (tab left); it appears as `submission.flags` for the professor.

## Voice fallback (in the browser)

- Pythagoras speaks with `speechSynthesis` (pick an English voice, rate ~1).
- The student answers with `SpeechRecognition` / `webkitSpeechRecognition` (continuous, interim results shown live). The student presses "I'm done" (or Space) to send; the transcript is editable before sending. Measure speaking time (`duration_s`).
- No speech API (e.g. Firefox): type the answer instead.

## API

All JSON unless noted. Errors: `{"detail": "..."}` with 4xx/5xx.

### Student

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/assignments` | | `[{id, title, course, description, created_at}]` |
| POST | `/api/submissions` | multipart: `assignment_id`, `name`, `email`, `files` (1+) | `{id, email, email_configured: bool, invite_url: str \| null}` (`invite_url` only when email is not configured). The invite email is sent when the plan is ready, not now |
| GET | `/api/interview/{token}` | | `{status, student_name, assignment_title, duration_s, topics_total, error}`; `status`: `preparing` \| `ready` \| `in_progress` \| `completed` \| `error`; 404 bad token |
| POST | `/api/interview/{token}/start` | | `Step`; when already `in_progress` it **resumes** (returns the pending question, answers are never wiped); 409 if still preparing or completed |
| POST | `/api/interview/{token}/retry` | | re-runs the preparation when `status` is `error` (the "Try again" button); 409 otherwise |
| POST | `/api/interview/{token}/answer` | `{text, duration_s}` | next `Step` (`done: true` on the closing line); after the time is over it closes without calling the LLM; 409 if not started; 503 if the LLM is unavailable (nothing new saved) |
| POST | `/api/interview/{token}/finish` | `{text, duration_s}` (optional: the unsent answer) | closing `Step` (`done: true`); no LLM needed; status → `completed`, report queued; 409 if not in progress |

`Step` (see `pythagoras/models.py`):
```json
{"kind": "ask", "question": "...", "topic_title": "Evaluation metrics", "level": 3, "level_name": "Apply",
 "topic_index": 2, "topics_total": 4, "elapsed_s": 75.2, "duration_s": 600, "done": false,
 "transcript": "what the student said last", "levels": {"metrics": 2}}
```

### Demo

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/demo` | | `{label, headline, blurb, documents}`: the ready-made case (Pirelli when `cases/pirelli/` is present, else the synthetic `cases/ffnn`) |
| POST | `/api/demo` | | `{invite_url, ready}`: a new interview on that case; `ready` when an earlier demo plan was reused |

### Professor (cookie session only when `PYTHAGORAS_PROFESSOR_PASSWORD` is set)

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/api/prof/auth` | | `{password: bool}`: false = professor pages are open, no login (default) |
| POST | `/api/prof/login` | `{password}` | `{ok: true}` + cookie; 401 wrong password |
| POST | `/api/prof/logout` | | `{ok: true}` |
| GET | `/api/prof/assignments` | | `[{id, title, course, description, created_at, submitted, completed}]` |
| POST | `/api/prof/assignments` | multipart: `title`, `course`, `description`, `objectives` (text, one per line), `materials` (files, optional) | assignment |
| GET | `/api/prof/assignments/{id}` | | `{assignment, submissions: [{id, student_name, email, created_at, status, completed, completed_at, files: [names], invite_url}]}` |
| GET | `/api/prof/submissions/{id}` | | `{submission, assignment, plan: InterviewPlan \| null, report: InterviewReport \| null, turns: [Turn]}` |
| POST | `/api/prof/submissions/{id}/report` | | retry the report now (otherwise the queue retries by itself); 409 if the interview isn't completed |
| GET | `/api/prof/submissions/{id}/files/{name}` | | the file |
