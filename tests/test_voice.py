"""Offline: turn detection and the microphone stream with fake models, sentence splitting, speculation, flags.
No audio models are loaded."""
import numpy as np
from fastapi.testclient import TestClient

from pythagoras import audio, engine, fake, talker
from pythagoras.app import app
from pythagoras.engine import Interview, new_state
from pythagoras.models import InterviewPlan

FRAME = np.zeros(320, dtype=np.int16)  # 20 ms


def detector(finished):
    """VAD = "non-zero frames are speech"; Smart Turn answers `finished`."""
    return audio.TurnDetector(is_speech=lambda pcm: bool(pcm.any()), smart_turn=lambda _: finished, silence_s=2.5)


def feed(d, speech_s, silence_s):
    out = None
    for _ in range(int(speech_s / 0.02)):
        out = d.push(FRAME + 1000) if out is None else out
    for _ in range(int(silence_s / 0.02)):
        out = d.push(FRAME) if out is None else out
    return out


def test_finished_sentence_ends_the_turn_after_a_short_pause():
    assert feed(detector(True), speech_s=2, silence_s=audio.SMART_TURN_SILENCE_S + 0.1) is not None


def test_thinking_pause_is_not_cut_until_the_safety_net():
    d = detector(False)
    assert feed(d, speech_s=2, silence_s=2.0) is None      # "so... uhm": still the student's turn
    assert feed(d, speech_s=0, silence_s=0.6) is not None  # 2.5 s of silence closes it anyway


def test_a_cough_is_not_an_answer():
    assert feed(detector(True), speech_s=0.1, silence_s=3) is None


def test_done_button_closes_the_turn_at_once():
    d = detector(False)
    feed(d, speech_s=1, silence_s=0.1)
    assert d.take() is not None and d.take() is None


def test_sentences_put_a_short_bridge_first():
    assert audio.sentences("So you chose a sigmoid. Why not a ReLU, given the learning rate you needed?") == \
        ["So you chose a sigmoid.", "Why not a ReLU, given the learning rate you needed?"]
    assert audio.sentences("Ok. Tell me more.") == ["Ok. Tell me more."]  # too short to stand alone


def _interview(monkeypatch):
    calls = []
    real = talker.phrase
    monkeypatch.setattr(talker, "phrase", lambda *a, **k: (calls.append(a[2].kind), real(*a, **k))[1])
    p = fake.parse(InterviewPlan, [])
    iv = Interview(p, new_state(p))
    iv.start()
    iv.answer("intro")
    calls.clear()
    return iv, calls


def test_solid_answer_uses_the_line_phrased_while_judging(monkeypatch):
    iv, calls = _interview(monkeypatch)
    step = iv.answer("a long specific and correct answer about my own project choices")
    assert step.level == 3 and calls == ["ask"]  # one talker call, made in parallel with the judge


def test_wrong_guess_is_discarded(monkeypatch):
    iv, calls = _interview(monkeypatch)
    assert iv.answer("it uses trees").kind == "probe"
    assert "probe" in calls and iv.state.turns[-1].move.kind == "probe"  # the guessed "ask" line is dropped
    assert iv.state.topics[0].level == 0  # the guess never touched the real state


def test_flags_reach_the_professor():
    c = TestClient(app)
    c.post("/api/prof/login", json={"password": "pw"})
    aid = c.post("/api/prof/assignments", data={"title": "T"}).json()["id"]
    sid = c.post("/api/submissions", data={"assignment_id": aid, "name": "A", "email": "a@b.co"},
                 files=[("files", ("r.md", b"hello"))]).json()["id"]
    token = c.get(f"/api/prof/assignments/{aid}").json()["submissions"][0]["invite_url"].rsplit("/", 1)[1]
    assert c.get(f"/api/interview/{token}").json()["voice"] in ("local", "browser")
    c.post(f"/api/interview/{token}/start")
    assert c.post(f"/api/interview/{token}/flag", json={"text": "Left the interview tab for 9 s"}).json()["ok"]
    c.post(f"/api/interview/{token}/answer", json={"text": "my project is a classifier"})  # saving an answer keeps it
    flags = c.get(f"/api/prof/submissions/{sid}").json()["submission"]["flags"]
    assert len(flags) == 1 and flags[0].endswith("Left the interview tab for 9 s")


def test_talker_lines_are_trimmed_to_bridge_and_question():
    assert talker.trim("So you used ROIC. That is on page 12. You also compare peers. How did you compute it?") == \
        "So you used ROIC. How did you compute it?"
    assert talker.trim("Let's move on.\nWhy Michelin?") == "Let's move on. Why Michelin?"


def _stream(monkeypatch):
    """The microphone WebSocket with fake models: non-zero frames are speech, Whisper returns a fixed text."""
    from pythagoras import voice
    monkeypatch.setattr(audio, "SileroVad", lambda: (lambda pcm: bool(pcm.any())))
    monkeypatch.setattr(audio, "transcribe", lambda pcm, glossary="": "zeros for the first layer")
    monkeypatch.setitem(voice._state, "warmed", True)
    return TestClient(app).websocket_connect("/api/voice/stream?token=none")


def test_stream_ends_the_answer_only_on_finish(monkeypatch):
    with _stream(monkeypatch) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "listen", "enabled": True, "epoch": 7})
        for _ in range(50):                                # 1 s of speech
            ws.send_bytes((FRAME + 1000).tobytes())
        for _ in range(200):                               # a 4 s pause to think: far beyond the old safety net
            ws.send_bytes(FRAME.tobytes())
        ws.send_json({"type": "finish"})                   # the "I'm done" button
        assert [ws.receive_json()["type"] for _ in range(2)] == ["speech_start", "turn_end"]
        heard = ws.receive_json()
        assert (heard["type"], heard["text"], heard["epoch"]) == ("transcript", "zeros for the first layer", 7)
        assert heard["speech_s"] >= 5.0                    # the pause is inside the answer: nothing cut it


def test_stream_finish_without_speech_is_empty(monkeypatch):
    with _stream(monkeypatch) as ws:
        ws.receive_json()
        ws.send_json({"type": "listen", "enabled": True, "epoch": 1})
        for _ in range(50):
            ws.send_bytes(FRAME.tobytes())
        ws.send_json({"type": "finish"})
        assert ws.receive_json() == {"type": "empty", "epoch": 1}


def test_stream_refuses_until_the_models_are_loaded(monkeypatch):
    from pythagoras import voice
    monkeypatch.setitem(voice._state, "warmed", False)
    with TestClient(app).websocket_connect("/api/voice/stream?token=none") as ws:
        assert ws.receive_json()["type"] == "error"
