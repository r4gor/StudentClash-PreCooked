"""Natural voice, all on this machine: the interview page streams 16 kHz microphone PCM over a WebSocket,
the answer ends when the team presses "I'm done" (a "finish" message; never on silence) and Whisper transcribes it;
Pythagoras's lines are synthesized by Kokoro and streamed back sentence by sentence.

Without the `voice` extra (or with PYTHAGORAS_VOICE=browser) the page falls back to the browser's speech APIs.

  GET  /api/voice/status
  POST /api/voice/speak   {text} -> NDJSON, one {"audio": base64 WAV} per sentence
  WS   /api/voice/stream?token=...
       page -> server: binary PCM frames · {"type": "listen", "enabled", "epoch"} · {"type": "finish"}
       server -> page: ready · speech_start · turn_end · transcript {text, speech_s} · empty · error {message}"""
import asyncio
import base64
import importlib.util
import io
import json
import logging
import threading
import wave

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import config, db

log = logging.getLogger("pythagoras.voice")
router = APIRouter(prefix="/api/voice")
PACKAGES = ["numpy", "mlx_whisper", "kokoro", "silero_vad", "onnxruntime", "transformers"]
MAX_ANSWER_S = 120  # a longer monologue is cut and judged: bounded memory, bounded Whisper time
TTS_LOCK, STT_LOCK, _warm_lock = threading.Lock(), threading.Lock(), threading.Lock()
_state = {"warmed": False, "error": ""}


def missing() -> list:
    return [p for p in PACKAGES if importlib.util.find_spec(p) is None]


def mode() -> str:
    """"local" (this module) or "browser" (Web Speech API in the page)."""
    if config.VOICE == "browser" or (config.VOICE == "auto" and missing()):
        return "browser"
    return "local"


def warm_up() -> None:
    """Load the models once (first run: downloads them). Called at startup in the background and by the page."""
    with _warm_lock:
        if _state["warmed"]:
            return
        try:
            if missing():
                raise RuntimeError("missing packages: " + ", ".join(missing()) + " (pip install -e '.[voice]')")
            from . import audio
            audio.warm_up()
            _state.update(warmed=True, error="")
            log.info("local voice ready: %s + Kokoro %s", audio.WHISPER_MODEL, audio.KOKORO_VOICE)
        except Exception as e:
            _state["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            log.exception("local voice warm-up failed")
            raise


@router.get("/status")
def status():
    return {"mode": mode(), "language": config.LANGUAGE, "missing": missing(), **_state}


@router.post("/warmup")
def warmup():
    try:
        warm_up()
    except Exception:
        raise HTTPException(503, "The local voice could not start: " + _state["error"])
    return status()


class Speech(BaseModel):
    text: str = Field(min_length=1, max_length=1600)


def _wav(pcm, rate: int) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())
    return b.getvalue()


@router.post("/speak")
def speak(body: Speech):
    if not _state["warmed"]:
        raise HTTPException(503, "The local voice is not ready yet")
    from . import audio

    def chunks():
        try:
            gen = audio.synthesize_stream(body.text)
            while True:
                with TTS_LOCK:  # released between sentences
                    pcm = next(gen, None)
                if pcm is None:
                    break
                yield json.dumps({"audio": base64.b64encode(_wav(pcm, audio.TTS_SAMPLE_RATE)).decode()}) + "\n"
        except Exception:
            log.exception("speech synthesis failed")
            yield json.dumps({"error": "Speech synthesis failed"}) + "\n"
    return StreamingResponse(chunks(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


def _glossary(token: str) -> str:
    """The project's own words, so Whisper spells them right (its prompt holds ~200 tokens)."""
    sub = db.submission_by_token(token)
    if not sub or not sub["plan"]:
        return ""
    topics = json.loads(sub["plan"]).get("topics", [])
    return ("Oral exam about a student project. " + ". ".join(f"{t['title']}: {t['claim']}" for t in topics))[:700]


@router.websocket("/stream")
async def stream(ws: WebSocket, token: str = ""):
    await ws.accept()
    if not _state["warmed"]:
        await ws.send_json({"type": "error", "message": "The local voice is not ready yet"})
        return await ws.close(code=1013)
    import numpy as np
    from . import audio
    glossary = await asyncio.to_thread(_glossary, token)
    # the answer ends only when the team presses "I'm done" (a "finish" message): no automatic end on silence
    detector = await asyncio.to_thread(audio.TurnDetector, silence_s=float("inf"), smart_turn=lambda _: None)
    listening, epoch, announced = False, 0, False

    def transcribe(pcm) -> str:
        with STT_LOCK:
            return audio.transcribe(pcm, glossary)

    async def end_turn(pcm) -> None:
        """The answer is over: tell the page at once (it plays an acknowledgement), then transcribe."""
        nonlocal listening, announced
        listening, announced, mine = False, False, epoch  # the page re-opens the microphone after Pythagoras spoke
        if pcm is None:
            return await ws.send_json({"type": "empty", "epoch": mine})
        await ws.send_json({"type": "turn_end", "epoch": mine})
        try:
            text = await asyncio.to_thread(transcribe, pcm)
        except Exception:
            log.exception("transcription failed")
            return await ws.send_json({"type": "error", "message": "I couldn't transcribe that. Please repeat, "
                                                                   "or type your answer."})
        if text:
            await ws.send_json({"type": "transcript", "text": text, "epoch": mine,
                                "speech_s": round(len(pcm) / audio.SAMPLE_RATE, 1)})
        else:
            await ws.send_json({"type": "empty", "epoch": mine})

    try:
        await ws.send_json({"type": "ready"})
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("text"):
                try:
                    msg = json.loads(message["text"])
                except ValueError:
                    continue
                if msg.get("type") == "listen":
                    listening, epoch, announced = bool(msg.get("enabled")), int(msg.get("epoch", epoch)), False
                    detector.take()  # drop anything half-heard
                elif msg.get("type") == "finish" and listening:  # the student pressed "I'm done"
                    await end_turn(detector.take())
                continue
            data = message.get("bytes") or b""
            if not listening or not data:
                continue
            if len(data) % 2 or len(data) > 6400:
                await ws.close(code=1009)
                break
            done = await asyncio.to_thread(detector.push, np.frombuffer(data, dtype="<i2"))
            if not announced and detector.speech_s >= audio.MIN_SPEECH_S:
                announced = True
                await ws.send_json({"type": "speech_start", "epoch": epoch})
            if done is None and sum(len(x) for x in detector.buf) > audio.SAMPLE_RATE * MAX_ANSWER_S:
                done = detector.take()
            if done is not None:
                await end_turn(done)
    except (WebSocketDisconnect, RuntimeError):
        pass
