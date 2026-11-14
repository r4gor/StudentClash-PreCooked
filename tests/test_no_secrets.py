"""No API key, password or private key in a tracked file. Keys belong in .env, which git ignores."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "Anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "OpenRouter key": r"sk-or-[A-Za-z0-9_-]{20,}",
    "OpenAI-style key": r"\bsk-[A-Za-z0-9]{32,}",
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{36,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "value in a secret setting": r"^\s*[A-Z_]*(API_KEY|PASSWORD|SECRET)=(?!change-me\b|la-tua-chiave\b|\.\.\.)[^\s#]{8,}",
}


def tracked() -> list:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [ROOT / p for p in out.splitlines()]


def test_no_secrets_in_tracked_files():
    found = []
    for path in tracked():
        if not path.is_file() or path.suffix in {".pdf", ".png", ".mp4"}:
            continue
        text = path.read_text(errors="ignore")
        found += [f"{path.relative_to(ROOT)}: {what}" for what, rx in PATTERNS.items() if re.search(rx, text, re.M)]
    assert not found, "remove these and put the values in .env:\n" + "\n".join(found)


def test_env_files_are_not_tracked():
    names = {p.name for p in tracked()}
    assert ".env" not in names and not any(n.endswith((".pem", ".key")) for n in names)


def test_the_check_would_catch_a_key():
    assert re.search(PATTERNS["Anthropic key"], "ANTHROPIC_API_KEY=sk-ant-api03-" + "x" * 40)
    assert re.search(PATTERNS["value in a secret setting"], "SMTP_PASSWORD=hunter2hunter2", re.M)
    assert not re.search(PATTERNS["value in a secret setting"], "PYTHAGORAS_SECRET=change-me", re.M)
    assert not re.search(PATTERNS["value in a secret setting"], 'PROFESSOR_PASSWORD = _env("X")', re.M)  # code, not a value
