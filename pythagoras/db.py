"""SQLite storage. Files (deliverables, course materials) live on disk next to the database."""
import json
import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    course TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    objectives TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id),
    student_name TEXT NOT NULL,
    email TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'preparing',  -- preparing | ready | in_progress | completed | error
    error TEXT,
    plan TEXT,
    state TEXT,
    report TEXT,
    created_at REAL NOT NULL,
    completed_at REAL,
    report_attempts INTEGER NOT NULL DEFAULT 0,
    report_next_at REAL,  -- when the report queue retries (rate limits, provider down)
    report_error TEXT,
    flags TEXT  -- JSON list of integrity signals from the page ("[mm:ss] tab lost focus"); review prompts only
);
"""
MIGRATIONS = ["ALTER TABLE submissions ADD COLUMN report_attempts INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE submissions ADD COLUMN report_next_at REAL",
              "ALTER TABLE submissions ADD COLUMN report_error TEXT",
              "ALTER TABLE submissions ADD COLUMN flags TEXT"]

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DATA_DIR / "pythagoras.db", check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(submissions)")}
        for sql in MIGRATIONS:  # databases created before the report queue
            if sql.split()[5] not in cols:
                _conn.execute(sql)
        _conn.commit()
    return _conn


def _exec(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur


def _rows(sql: str, args: tuple = ()) -> List[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def _row(sql: str, args: tuple = ()) -> Optional[dict]:
    rows = _rows(sql, args)
    return rows[0] if rows else None


# ---------- files ----------

def assignment_dir(aid: int) -> Path:
    return config.DATA_DIR / "assignments" / str(aid)


def submission_dir(sid: int) -> Path:
    return config.DATA_DIR / "submissions" / str(sid)


def list_files(folder: Path) -> List[str]:
    return sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.exists() else []


# ---------- assignments ----------

def create_assignment(title: str, course: str, description: str, objectives: str) -> dict:
    cur = _exec("INSERT INTO assignments (title, course, description, objectives, created_at) VALUES (?,?,?,?,?)",
                (title, course, description, objectives, time.time()))
    return get_assignment(cur.lastrowid)


def get_assignment(aid: int) -> Optional[dict]:
    return _row("SELECT * FROM assignments WHERE id = ?", (aid,))


def list_assignments(hide_team: str = "") -> List[dict]:
    """With counts of submissions; `hide_team` leaves one team name out of them (the one-click demo)."""
    return _rows("""SELECT a.*, COUNT(s.id) AS submitted,
                           COALESCE(SUM(s.status = 'completed'), 0) AS completed
                    FROM assignments a LEFT JOIN submissions s ON s.assignment_id = a.id AND s.student_name != ?
                    GROUP BY a.id ORDER BY a.created_at DESC""", (hide_team,))


# ---------- submissions ----------

def create_submission(aid: int, name: str, email: str) -> dict:
    cur = _exec("INSERT INTO submissions (assignment_id, student_name, email, token, created_at) VALUES (?,?,?,?,?)",
                (aid, name, email, secrets.token_urlsafe(16), time.time()))
    return get_submission(cur.lastrowid)


def get_submission(sid: int) -> Optional[dict]:
    return _row("SELECT * FROM submissions WHERE id = ?", (sid,))


def submission_by_token(token: str) -> Optional[dict]:
    return _row("SELECT * FROM submissions WHERE token = ?", (token,))


def list_submissions(aid: int) -> List[dict]:
    return _rows("SELECT * FROM submissions WHERE assignment_id = ? ORDER BY created_at DESC", (aid,))


def reports_due(now: float) -> List[dict]:
    """Completed interviews still without a report whose next attempt is due."""
    return _rows("""SELECT * FROM submissions WHERE status = 'completed' AND report IS NULL
                    AND (report_next_at IS NULL OR report_next_at <= ?)""", (now,))


def add_flag(sid: int, text: str) -> None:
    """Own column and one locked read-append-write: a flag can never race with the saving of an answer."""
    with _lock:
        c = conn()
        row = c.execute("SELECT flags FROM submissions WHERE id = ?", (sid,)).fetchone()
        flags = json.loads(row["flags"] or "[]") + [text]
        c.execute("UPDATE submissions SET flags = ? WHERE id = ?", (json.dumps(flags[-200:]), sid))
        c.commit()


def delete_submission(sid: int) -> None:
    """Only for a submission whose upload failed: nothing else refers to it yet."""
    _exec("DELETE FROM submissions WHERE id = ?", (sid,))
    shutil.rmtree(submission_dir(sid), ignore_errors=True)


def delete_assignment(aid: int) -> None:
    """Only for an assignment whose upload failed, before any submission."""
    _exec("DELETE FROM assignments WHERE id = ?", (aid,))
    shutil.rmtree(assignment_dir(aid), ignore_errors=True)


def update_submission(sid: int, **fields) -> None:
    for k, v in fields.items():
        if hasattr(v, "model_dump_json"):
            fields[k] = v.model_dump_json()
        elif isinstance(v, (dict, list)):
            fields[k] = json.dumps(v)
    cols = ", ".join(f"{k} = ?" for k in fields)
    _exec(f"UPDATE submissions SET {cols} WHERE id = ?", (*fields.values(), sid))
