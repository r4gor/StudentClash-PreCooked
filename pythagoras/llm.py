"""The only place that talks to an LLM. Two roles: the reasoner (structured output: plan, answer judgement,
report) and the talker (short spoken lines). Provider: Claude (Sonnet + Haiku), Gemini (Flash + Flash Lite) or
OpenRouter (any model; default the same Claude pair), chosen by PYTHAGORAS_PROVIDER or by which key is set. Content uses Anthropic-style blocks
({"type": "text"} / {"type": "document"} with a base64 PDF); the Gemini path converts them."""
import base64
import logging
import time
from functools import lru_cache
from typing import List, Type, TypeVar, Union

from pydantic import BaseModel

from . import config

T = TypeVar("T", bound=BaseModel)
Content = Union[str, List[dict]]
log = logging.getLogger("pythagoras.llm")


def parse(schema: Type[T], system: str, content: Content, effort: str = "medium", max_tokens: int = 16000,
          fast: bool = False) -> T:
    """Structured call on the reasoner model; returns a validated instance of `schema`.
    fast=True is for the live interview (the student is waiting): no extended thinking, short retries."""
    if config.FAKE_LLM:
        from . import fake
        return fake.parse(schema, content)
    if config.PROVIDER == "gemini":
        return _gemini_parse(schema, system, content, fast)
    if config.PROVIDER == "openrouter":
        return _openrouter_parse(schema, system, content, fast, max_tokens)
    return _claude_parse(schema, system, content, "low" if fast else effort, max_tokens,
                         config.JUDGE_MODEL if fast else config.REASONER_MODEL)


def say(system: str, prompt: str) -> str:
    """Fast short reply on the talker model (what Pythagoras actually says)."""
    if config.FAKE_LLM:
        from . import fake
        return fake.say(prompt)
    text = {"gemini": _gemini_say, "openrouter": _openrouter_say}.get(config.PROVIDER, _claude_say)(system, prompt)
    if not text:
        raise RuntimeError("Empty talker reply")
    return text


# ---------- Claude ----------

_fallbacks = {"on": True}  # server-side refusal fallback for Sonnet 5.5; turned off if the API rejects it
FALLBACK_HEADERS = {"anthropic-beta": "server-side-fallback-2026-07-01"}


@lru_cache(maxsize=1)
def _claude():
    import anthropic
    return anthropic.Anthropic(max_retries=3)


def _claude_parse(schema, system, content, effort, max_tokens, model):
    import anthropic
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": content}],
                  output_config={"effort": effort}, output_format=schema)
    if _fallbacks["on"] and model == "claude-sonnet-5-5":
        try:
            return _claude_parsed(_claude().messages.parse(**kwargs, extra_headers=FALLBACK_HEADERS,
                                                           extra_body={"fallbacks": "default"}))
        except anthropic.BadRequestError as e:
            if "fallback" not in str(e).lower():
                raise
            log.warning("refusal fallbacks rejected, continuing without: %s", e)
            _fallbacks["on"] = False
    return _claude_parsed(_claude().messages.parse(**kwargs))


def _claude_parsed(resp):
    if resp.stop_reason == "refusal":
        raise RuntimeError("The model declined this request")
    if resp.parsed_output is None:
        raise RuntimeError(f"No structured output (stop_reason={resp.stop_reason})")
    return resp.parsed_output


def _claude_say(system, prompt):
    resp = _claude().messages.create(model=config.TALKER_MODEL, max_tokens=1024, system=system,
                                     messages=[{"role": "user", "content": prompt}],
                                     output_config={"effort": "low"})
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------- Gemini ----------

RETRIES = 4  # 429 / 503 ("high demand") are common on the free tier


@lru_cache(maxsize=1)
def _gemini():
    from google import genai
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _gemini_parts(content: Content) -> list:
    from google.genai import types
    if isinstance(content, str):
        return [types.Part(text=content)]
    parts = []
    for b in content:
        if b["type"] == "document":
            if b.get("title"):
                parts.append(types.Part(text=f"===== {b['title']} ====="))
            parts.append(types.Part.from_bytes(data=base64.b64decode(b["source"]["data"]),
                                               mime_type=b["source"]["media_type"]))
        else:
            parts.append(types.Part(text=b["text"]))
    return parts


LIVE_RETRIES = 2  # during the interview a long backoff is worse than the "try again" button
_no_thinking = {"ok": True}  # thinking_budget=0 cuts a Flash judge from ~3.5 s to ~1.4 s; not every model accepts it


def _gemini_call(model: str, system: str, content: Content, retries: int = RETRIES, **cfg):
    from google.genai import errors, types
    for attempt in range(retries + 1):
        try:
            return _gemini().models.generate_content(
                model=model, contents=_gemini_parts(content),
                config=types.GenerateContentConfig(system_instruction=system, **cfg))
        except errors.APIError as e:
            if e.code not in (429, 500, 503) or attempt == retries:
                raise
            wait = 2 ** attempt
            log.warning("gemini %s: %s, retrying in %ss", model, e.code, wait)
            time.sleep(wait)


def _gemini_parse(schema, system, content, fast=False):
    from google.genai import errors, types
    cfg = dict(response_mime_type="application/json", response_schema=schema)
    model, resp = (config.JUDGE_MODEL if fast else config.REASONER_MODEL), None
    if fast and _no_thinking["ok"] and "lite" not in model:
        try:
            resp = _gemini_call(model, system, content, LIVE_RETRIES,
                                thinking_config=types.ThinkingConfig(thinking_budget=0), **cfg)
        except errors.APIError as e:
            if e.code != 400:
                raise
            log.warning("%s does not accept thinking_budget=0, continuing with thinking on", model)
            _no_thinking["ok"] = False
    if resp is None:
        resp = _gemini_call(model, system, content, LIVE_RETRIES if fast else RETRIES, **cfg)
    if isinstance(resp.parsed, schema):
        return resp.parsed
    return schema.model_validate_json(resp.text or "")  # raises with a clear message if the JSON is bad


def _gemini_say(system, prompt):
    return (_gemini_call(config.TALKER_MODEL, system, prompt, LIVE_RETRIES).text or "").strip()


# ---------- OpenRouter (OpenAI-compatible chat completions) ----------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


@lru_cache(maxsize=1)
def _openrouter():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(180, connect=10), headers={
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}", "X-Title": "Pythagoras"})


def _openrouter_parts(content: Content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    parts = []
    for b in content:
        if b["type"] == "document":  # PDFs go as files: Gemini / Claude read them natively, layout and figures included
            parts.append({"type": "file", "file": {"filename": (b.get("title") or "document").split(": ")[-1],
                                                    "file_data": f"data:{b['source']['media_type']};base64,{b['source']['data']}"}})
        else:
            parts.append({"type": "text", "text": b["text"]})
    return parts


def _openrouter_call(model: str, system: str, content: Content, retries: int, **body) -> str:
    import httpx
    payload = {"model": model, "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": _openrouter_parts(content)}], **body}
    for attempt in range(retries + 1):
        try:
            r = _openrouter().post(OPENROUTER_URL, json=payload)
            if r.status_code in (429, 500, 502, 503) and attempt < retries:
                log.warning("openrouter %s: %s, retrying in %ss", model, r.status_code, 2 ** attempt)
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:300]}")
            data = r.json()
            if "error" in data:
                raise RuntimeError(f"OpenRouter: {data['error']}")
            return (data["choices"][0]["message"].get("content") or "").strip()
        except httpx.TransportError as e:
            if attempt == retries:
                raise RuntimeError(f"OpenRouter unreachable: {e}") from e
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def _openrouter_parse(schema, system, content, fast, max_tokens):
    import json
    import re
    body = {"response_format": {"type": "json_schema", "json_schema": {
                "name": schema.__name__, "strict": False, "schema": schema.model_json_schema()}},
            "max_tokens": 4000 if fast else max_tokens}
    if fast:  # live judge: no extended thinking
        body["reasoning"] = {"enabled": False}
    text = _openrouter_call(config.JUDGE_MODEL if fast else config.REASONER_MODEL,
                            system + "\n\nReply with JSON only, matching the given schema.", content,
                            LIVE_RETRIES if fast else RETRIES, **body)
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return schema.model_validate_json(m.group(1) if m else text)


def _openrouter_say(system, prompt):
    return _openrouter_call(config.TALKER_MODEL, system, prompt, LIVE_RETRIES, max_tokens=400,
                            reasoning={"enabled": False})
