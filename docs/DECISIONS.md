# Decisions for the v0.3 rewrite (2026-11-08)

The v0.2 proof of concept (group voice call over LiveKit, local Whisper/Kokoro, multi-provider LLM) is tagged `legacy-v0.2`. v0.3 restarts the code from scratch as a general web platform. The **spec stays**: three phases (preparation → real-time interview → professor package), Bloom-level progression, anchored topics, "plan first, structured state, one agent talks and one thinks", Pythagoras never grades.

| # | Decision | Why |
|---|---|---|
| D1 | ~~**1-on-1** interview (one student, one AI)~~ superseded by D23 (team interviews) | Simpler and fairer per student; no turn-taking/speaker attribution problems |
| D2 | Two roles, **one key**: a reasoner (plan, per-answer Bloom judgement, report) and a fast talker. Providers: **Gemini** (3.5 Flash + 3.5 Flash Lite, current default via `PYTHAGORAS_PROVIDER=gemini`) or **Claude** (Sonnet 5.5 + Haiku 5.5) | Started Claude-only; switched to Gemini on 2026-11-08 because a Claude subscription has no API key and AI Studio has a free tier. Gemini free tier may use prompts for training: synthetic test data only. Sonnet calls opt into server-side refusal fallbacks |
| D3 | Deliverable PDFs are sent to Claude **as documents** (no local text extraction); text/code/notebooks as text | Claude reads layout, figures and tables; fewer dependencies |
| D4 | **Browser speech** (Web Speech API: `speechSynthesis` + `SpeechRecognition`), typing fallback | No extra keys or local models; works on any laptop with Chrome/Edge |
| D5 | Interview rules are **deterministic code** (`engine.py`); the LLM only judges single answers and phrases questions | Predictable, testable progression (same idea as v0.2's supervisor) |
| D6 | One FastAPI process + SQLite + static HTML/CSS/JS (no Node build) | Runs anywhere Python runs; easy to deploy and demo |
| D7 | Student flow: pick assignment → name + email + files → when the interview is ready (plan generated) the invite link is emailed; opening it lands on a one-tap "Start interview" screen (browsers need one click for mic + speech). Without SMTP: link shown on screen + mail saved in `data/outbox/` | Email invite as requested; still demoable without a mail server |
| D8 | Professor: single shared password (cookie signed with `PYTHAGORAS_SECRET`); creates assignments (description, learning objectives, optional course material), sees per-assignment submissions with ✓ when the assessment is done, opens the report | MVP; real accounts later |
| D9 | Students don't see their report; the professor does | The report prepares the oral exam |
| D11 | **The interview never gets stuck on the LLM**: when time is over the next answer closes it with a fixed line (no LLM call); "End interview & save" (shown when an answer can't reach the AI) closes it keeping the unsent answer; reopening the link **resumes** instead of restarting. Answers are saved after every turn | Free-tier quota can run out mid-interview; the student must be able to finish and the professor must still get the report |
| D10 | **Report queue**: a failed report (quota exhausted, provider down) keeps the whole interview saved and is retried by a background thread after 1, 5, 15, 30, 60 min, then hourly, also after restarts; the professor sees "Report queued" + "Retry now" | Free-tier API keys run out of requests; the report must never be lost |

## v0.4: the platform of v0.3 with the voice of v0.2 (2026-11-14)

| # | Decision | Why |
|---|---|---|
| D12 | **Local voice by default** when installed (`pip install -e ".[voice]"`, Apple Silicon): Whisper (MLX) + Kokoro + Silero VAD + Smart Turn v3, ported from the v0.2 agent; no LiveKit, one WebSocket from the page. Browser speech (D4) stays as the automatic fallback | The browser voice was robotic and needed a button after every answer; the v0.2 voice is natural and hears the end of the answer by itself. No extra key, audio never leaves the machine |
| D13 | ~~**End of turn**~~ superseded by D24: Smart Turn is asked after 1.0 s of pause (0.7 s cut students between two sentences in tests); "not finished" waits for 2.5 s of silence. "I'm done" / Space still closes the turn at once | In an exam, cutting the student is worse than a second of waiting |
| D14 | **Latency**: (a) a pre-synthesized "Okay." plays the instant the turn ends; (b) the judge runs without extended thinking (`fast=True`: 3.5 s → 1.4 s on Gemini Flash); (c) while it runs, the talker already phrases the follow-up a *solid* answer leads to, used only if the verdict agrees (`PYTHAGORAS_SPECULATE`); (d) the talker opens with a short bridge sentence and each sentence is synthesized on its own, so audio starts after ~0.7 s | From "press, wait 5 s, robot voice" to a reply about 3 s after the student stops, with the silence covered |
| D15 | **Half-duplex**: the microphone is closed while Pythagoras speaks; no barge-in | No echo problems without headphones; simple and predictable |
| D16 | English by default; `PYTHAGORAS_LANGUAGE=it` switches prompts, Whisper and the Kokoro voice | The MVP demo is in English |
| D17 | **Integrity signals**: leaving the interview tab, or a camera that is unavailable / turned off, is logged with its time and shown in the report as a review prompt (own DB column, cannot race with answers) | Cheap, real signals from the spec |
| D19 | **Webcam on, not recorded** (`PYTHAGORAS_CAMERA`): the team sees its live preview with a REC badge and timer during the interview, but the badge is visual only: no video is stored or sent anywhere | The spec wants the camera on; for the MVP recording would mean storage, consent and GDPR work |
| D18 | Topic titles are neutral and planned questions never give away a flaw | The title is on the student's screen; the flaw must surface from their answer |

| D20 | **Student pages use the green template of v0.2** (`static/room.css`: sidebar shell, orb, room with the topic thread, camera panel and running transcript), in English; the professor pages share its palette and fonts through `app.css` | The team preferred that look; the backend and the page logic are unchanged |

| D21 | **3 topics** per interview (`PYTHAGORAS_TOPICS`) | Ten minutes are enough to climb Bloom's levels on three topics, not on four or five |
| D22 | **OpenRouter** as a third provider (`OPENROUTER_API_KEY`, plain HTTP, no new dependency): Claude Sonnet 5.5 (plan, report) + Haiku 5.5 (judge, talker) by default, PDFs sent as files; the account's OpenRouter guardrails block Google and OpenAI models | Paid per token with no daily quota: the Gemini free tier allows ~20 Flash requests a day |

| D23 | **Teams**: the form asks for a team name and contact email (stored in the existing `student_name` / `email` fields); prompts and pages address the team, and whoever worked on a part answers it. A finished **Team_1 report** is seeded at startup from the demo case's `team1.json` (synthetic `cases/ffnn/team1.json` in the repository, Pirelli when that case is present) | The course assesses team projects; the professor pages need a complete example for the demo |

| D24 | **The answer ends only on "I'm done"** (button or Space; safety cap 120 s). The automatic end of turn of D13 is switched off in the microphone stream; `audio.TurnDetector` and its tests stay | Even with Smart Turn, a long pause to think could end the answer. In an exam the team decides when it has finished |
| D25 | **Demo rules** (`PYTHAGORAS_DEMO_RULES`, on by default for now, `engine._demo_rule`): "I don't know" always gets one retry, a hint and an easier question one Bloom level lower, and a second one in a row moves on; on the weights / initialisation topic any real first answer climbs to level 3 whatever the judge said | A predictable path for the stage demo. The second rule overrides the judge: turn the flag off (`=0`) for any real assessment |

## Interview rules (engine.py)

Opening question (summary of the project) → topics in plan order. Each topic starts at level 2; solid answer → next level (up to 5); first non-solid answer → one probe (partial) or scaffold hint (weak / don't know); second non-solid answer, topic budget spent, or level 5 reached → next topic. 30 s before the end, or when topics run out → closing line, interview completed, report generated in the background. With the demo rules on (D25) two overrides run before these.

## Open for later

Professor accounts / multiple professors, re-sending or regenerating an invite, webcam recording, audio recording for "click to play" evidence, barge-in, hosted STT/TTS for machines without Apple Silicon (the local voice serves one interview at a time well), multi-model report council, deployment (HTTPS, real SMTP), deadlines per assignment.
