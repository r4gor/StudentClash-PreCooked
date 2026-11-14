# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Pythagoras: a team uploads a project deliverable, gets an invite link by email, and has a ~10-minute spoken interview with an AI about its own project (3 topics; whoever worked on a part answers); the professor gets a report per team. The team name is stored in the `student_name` field. Pythagoras never grades. v0.3 is a from-scratch rewrite (old group/LiveKit code is at git tag `legacy-v0.2`); v0.4 adds that version's local voice pipeline back; rationale in `docs/DECISIONS.md`, page/API contract in `docs/API.md`, original spec `docs/SPEC.md`.

## Commands

```bash
.venv/bin/python -m pytest                      # offline: tests force PYTHAGORAS_FAKE_LLM=1 and a temp data dir (tests/conftest.py)
.venv/bin/python -m pytest tests/test_engine.py::test_partial_gets_a_probe
.venv/bin/python -m pythagoras serve --reload      # http://localhost:8000 (team) and /prof (professor, no login by default)
PYTHAGORAS_FAKE_LLM=1 .venv/bin/python -m pythagoras serve   # whole app without an API key
.venv/bin/python -m pythagoras check               # one real call to each model
.venv/bin/python -m pythagoras seed                # demo assignment
.venv/bin/python scripts/smoke.py               # spoken end-to-end run against a running server, prints latencies
```
Use `.venv/bin/python` (3.12: MLX needs <= 3.12). The frontend has no build step.

## Architecture

- **One FastAPI process** (`pythagoras/app.py`) serves `/api/*` and the static pages in `pythagoras/static/` (one HTML + one JS file per page, shared `app.css` / `app.js`). Storage: SQLite `data/pythagoras.db` (`db.py`) with plan/state/report stored as JSON columns on `submissions`; uploaded files under `data/submissions/<id>/` and `data/assignments/<id>/materials/`.
- **Phase 1** — upload triggers a background `planner.make_plan` (PDFs go to the model as documents, code as line-numbered text) → `InterviewPlan` (anchored topics, one question per Bloom level 2–5, critical analysis). Status `preparing` → `ready`.
- **Phase 2** — `engine.Interview` is rebuilt from the stored `InterviewState` on every request (stateless server). Each answer: `supervisor.evaluate` (reasoner judges one answer) → deterministic rules in `engine._after_answer` (climb / probe / scaffold / close / wrap) → `talker.phrase` (talker writes the spoken line). The wrap-up line sets `state.finished`; status becomes `completed` and `report.build` runs in the background.
- **Phase 3** — `report.build` (reasoner) → `InterviewReport`; levels come from the engine, not the writer, and `unverified_quotes` lists evidence not found verbatim in the transcript.
- **LLM** — `llm.py` is the only LLM client: `parse(schema, system, content)` (structured output, reasoner model) and `say(system, prompt)` (talker). Provider from `PYTHAGORAS_PROVIDER` / whichever key is set: OpenRouter (`OPENROUTER_API_KEY`, plain httpx, default Claude Sonnet 5.5 + Haiku 5.5), Gemini (3.5 Flash / 3.5 Flash Lite, used now, free tier: retries 429/503) or Claude (Sonnet 5.5 / Haiku 5.5). Content is passed as Anthropic-style blocks; the Gemini path converts them. `fake.py` is the deterministic offline stand-in for both; update it when a schema in `models.py` changes.
- **Voice**: `voice.py` + `audio.py` (optional `voice` extra, Apple Silicon): the page streams microphone PCM over `WS /api/voice/stream`; the answer ends only on the page's `finish` message ("I'm done" / Space; D24: the Silero VAD + Smart Turn v3 detector in `audio.TurnDetector` is kept but no longer closes turns), then the server transcribes (Whisper MLX) and sends the text back; `POST /api/voice/speak` streams Kokoro audio per sentence. `static/voice-local.js` is the client; `static/interview.js` falls back to the Web Speech API when `voice.mode()` is `"browser"`. The interview API itself only ever receives answer text.
- **Latency rules** (keep them when editing): the judge is called with `fast=True`; `engine.answer` phrases the "solid" follow-up in parallel (`_if_solid` works on a copy of the state) and uses it only if the real move is equal; the talker's first sentence must stay short because audio starts when the first sentence is synthesized.
- **Demo rules** (`PYTHAGORAS_DEMO_RULES`, on by default, D25): `engine._demo_rule` runs before the normal rules; tests turn it off in `tests/conftest.py` and on in `tests/test_demo_rules.py`.
- **Demo**: `POST /api/demo` (button "Use the Pirelli case") creates an interview on `cases/pirelli` (or `cases/ffnn`), reusing an earlier demo plan with the current topic count; `seed_demo_report()` adds a finished report per `team<N>.json` of the demo case at startup (`cases/ffnn/team1.json`, `team2.json`: synthetic, tracked). The demo runs themselves ("Demo team") are hidden from the professor pages. `cases/pirelli` is untracked (real student work).
- **Pages**: student pages use `static/room.css` (green template); professor pages use `static/app.css` with the same palette. The webcam panel shows a visual-only REC badge.
- **Professor auth**: none by default (MVP); with `PYTHAGORAS_PROFESSOR_PASSWORD` set, one password, HMAC cookie signed with `PYTHAGORAS_SECRET`. Email: SMTP if `SMTP_HOST` is set, otherwise the invite is saved to `data/outbox/` and its URL is returned to the student page.

## Conventions

- `pythagoras/models.py` + `docs/API.md` are the frontend/backend contract: change them together.
- Commit messages: title only, no description, no AI co-author / "Generated with" lines. Work on `main`.
- Never commit `.env` or `data/` (student data). Only synthetic cases (`cases/ffnn`) are tracked; never send real student data to the Gemini free tier.
