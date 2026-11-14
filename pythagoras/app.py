"""FastAPI app: JSON API under /api plus the static pages (contract in docs/API.md).

    uvicorn pythagoras.app:app --reload"""
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, mailer, planner, report, voice
from .engine import Interview
from .models import InterviewPlan, InterviewReport, InterviewState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pythagoras")
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 50 * 1024 * 1024
COOKIE = "pythagoras_prof"

REPORT_BACKOFF_S = [60, 300, 900, 1800, 3600]  # then hourly, forever: the report is never given up
QUEUE_TICK_S = 30


def _report_queue(stop: threading.Event) -> None:
    """Retries reports that failed (quota exhausted, provider down) until they succeed, across restarts."""
    while not stop.wait(QUEUE_TICK_S):
        for sub in db.reports_due(time.time()):
            _write_report(sub["id"])


def _warm_voice() -> None:
    try:
        voice.warm_up()
    except Exception:
        pass  # logged by voice.warm_up; the interview page shows the error and offers the browser voice


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    threading.Thread(target=_report_queue, args=(stop,), daemon=True, name="report-queue").start()
    try:
        if seed_demo_report():
            log.info("demo reports added")
    except Exception:
        log.exception("could not add the demo report")
    if voice.mode() == "local":  # load Whisper / Kokoro now, so the first interview starts without waiting
        threading.Thread(target=_warm_voice, daemon=True, name="voice-warm-up").start()
    yield
    stop.set()


app = FastAPI(title="Pythagoras", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    """Browsers must revalidate pages and scripts: after an update nobody keeps running an old page."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.include_router(voice.router)


# ---------- pages ----------

def _page(name: str):
    return lambda: FileResponse(STATIC / name)


@app.get("/api/prof/auth")
def prof_auth():
    return {"password": bool(config.PROFESSOR_PASSWORD)}


for route, page in [("/", "index.html"), ("/interview/{token}", "interview.html"), ("/prof/login", "prof-login.html"),
                    ("/prof", "prof.html"), ("/prof/assignments/{aid}", "prof-assignment.html"),
                    ("/prof/submissions/{sid}", "prof-report.html")]:
    app.add_api_route(route, _page(page), methods=["GET"], include_in_schema=False)


# ---------- helpers ----------

def _safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\- ]", "_", Path(name or "file").name).strip(" .")
    return name or "file"


def _unique(name: str, taken: List[str]) -> str:
    """Two files called report.pdf (from two folders) are both kept: report.pdf, report-2.pdf (a name that
    _safe_name keeps as it is, so the professor can download it)."""
    stem, ext, n = Path(name).stem, Path(name).suffix, 2
    while name in taken:
        name, n = f"{stem}-{n}{ext}", n + 1
    return name


async def _save(files: List[UploadFile], folder: Path) -> List[str]:
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        if not f.filename:
            continue
        name = _unique(_safe_name(f.filename), saved)
        size = 0
        with open(folder / name, "wb") as out:  # in chunks: an oversized file is never held in memory whole
            while chunk := await f.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    out.close()
                    (folder / name).unlink()
                    raise HTTPException(413, f"{f.filename} is larger than 50 MB")
                out.write(chunk)
        saved.append(name)
    return saved


def _load(sub: dict):
    plan = InterviewPlan.model_validate_json(sub["plan"]) if sub["plan"] else None
    state = InterviewState.model_validate_json(sub["state"]) if sub["state"] else None
    rep = InterviewReport.model_validate_json(sub["report"]) if sub["report"] else None
    return plan, state, rep


def _public_sub(sub: dict) -> dict:
    return {"id": sub["id"], "assignment_id": sub["assignment_id"], "student_name": sub["student_name"], "email": sub["email"],
            "created_at": sub["created_at"], "status": sub["status"], "error": sub["error"],
            "completed": sub["status"] == "completed", "completed_at": sub["completed_at"],
            "files": db.list_files(db.submission_dir(sub["id"])), "invite_url": mailer.invite_url(sub["token"]),
            "report_ready": bool(sub["report"]), "report_attempts": sub["report_attempts"],
            "report_next_at": sub["report_next_at"], "report_error": sub["report_error"],
            "flags": json.loads(sub["flags"] or "[]")}


def _public_assignment(a: dict) -> dict:
    return {k: a[k] for k in ("id", "title", "course", "description", "objectives", "created_at",
                              "submitted", "completed") if k in a}


# ---------- background jobs ----------

def _prepare(sid: int) -> None:
    sub = db.get_submission(sid)
    a = db.get_assignment(sub["assignment_id"])
    try:
        plan = planner.make_plan(db.submission_dir(sid), db.assignment_dir(a["id"]) / "materials", a,
                                 config.DURATION_S)
        db.update_submission(sid, plan=plan, status="ready", error=None)
        log.info("plan ready for submission %s: %d topics", sid, len(plan.topics))
        mailer.send_invite(sub["email"], sub["student_name"], a["title"], sub["token"])  # link works from now on
    except Exception as e:  # shown to the student and the professor; the student can be re-invited
        log.exception("plan failed for submission %s", sid)
        db.update_submission(sid, status="error", error=f"Could not prepare the interview: {e}")


_writing: set = set()
_writing_lock = threading.Lock()


def _write_report(sid: int) -> None:
    with _writing_lock:  # the queue and a request may race for the same report
        if sid in _writing:
            return
        _writing.add(sid)
    try:
        sub = db.get_submission(sid)
        if sub["report"]:
            return
        plan, state, _ = _load(sub)
        try:
            db.update_submission(sid, report=report.build(plan, state, json.loads(sub["flags"] or "[]")),
                                 report_error=None, report_next_at=None)
            log.info("report ready for submission %s", sid)
        except Exception as e:  # keep everything; the queue retries later
            n = sub["report_attempts"] + 1
            wait = REPORT_BACKOFF_S[min(n, len(REPORT_BACKOFF_S)) - 1]
            log.warning("report for submission %s failed (attempt %d), retrying in %ds: %s", sid, n, wait, e)
            db.update_submission(sid, report_attempts=n, report_next_at=time.time() + wait,
                                 report_error=str(e)[:300])
    finally:
        with _writing_lock:
            _writing.discard(sid)


# ---------- student API ----------

@app.get("/api/assignments")
def assignments():
    return [_public_assignment(a) for a in db.list_assignments(hide_team=DEMO_STUDENT)]


@app.post("/api/submissions")
async def submit(bg: BackgroundTasks, assignment_id: int = Form(...), name: str = Form(...), email: str = Form(...),
                 files: List[UploadFile] = File(...)):
    a = db.get_assignment(assignment_id)
    if a is None:
        raise HTTPException(404, "Assignment not found")
    name, email = name.strip(), email.strip()
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "Please enter your name and a valid email address")
    if not any(f.filename for f in files):
        raise HTTPException(422, "Please attach at least one file")
    sub = db.create_submission(assignment_id, name, email)
    try:
        await _save(files, db.submission_dir(sub["id"]))
    except Exception:  # a refused file must not leave an interview "preparing" forever
        db.delete_submission(sub["id"])
        raise
    bg.add_task(_prepare, sub["id"])  # emails the invite link once the interview is ready
    return {"id": sub["id"], "email": email, "email_configured": mailer.configured(),
            "invite_url": None if mailer.configured() else mailer.invite_url(sub["token"])}


# ---------- one-click demo: a ready-made case, nothing to fill in ----------

CASES = Path(__file__).resolve().parent.parent / "cases"
DEMO_STUDENT = "Demo team"


def _demo_case() -> dict:
    """The Pirelli report when it is on this machine (cases/pirelli is not in the repository: it is a real
    student deliverable), otherwise the synthetic feed-forward network case."""
    if (CASES / "pirelli" / "PirelliReportGroup21.pdf").exists():
        return {"label": "Pirelli", "title": "Financial analysis of a listed company: Pirelli",
                "course": "Accounting, Finance & Control", "headline": "Inside the Pirelli analysis.",
                "blurb": "Group 21's report and the Accounting, Finance & Control course notes. Ready for the conversation.",
                "description": "Analyse the financial statements of a listed company over several years: profitability, "
                               "value creation, liquidity and a benchmark against its peers. Hand in a written report.",
                "objectives": "LO1 Compute and interpret profitability and value-creation ratios (ROE, ROIC, WACC)\n"
                              "LO2 Assess liquidity and cash flows\nLO3 Choose comparable peers and benchmark critically\n"
                              "LO4 Distinguish recurring performance from one-off effects",
                "deliverable": [CASES / "pirelli" / "PirelliReportGroup21.pdf"], "materials": [CASES / "pirelli" / "afc.pdf"],
                "dir": CASES / "pirelli"}
    ffnn = CASES / "ffnn"
    return {"label": "neural network", "title": "Project 1 - A feed-forward network from scratch",
            "course": "Introduction to Deep Learning", "headline": "Inside a neural network project.",
            "blurb": "A synthetic student report and its code, with the course notes. Ready for the conversation.",
            "description": "Implement a small feed-forward neural network from scratch in NumPy and write a short report.",
            "objectives": "LO1 Explain forward propagation and the role of non-linear activations\nLO2 Derive and implement "
                          "backpropagation\nLO3 Choose and justify initialisation, learning rate and architecture\n"
                          "LO4 Evaluate a model correctly and recognise overfitting",
            "deliverable": [ffnn / "deliverable" / "report.pdf", ffnn / "deliverable" / "mlp.py"],
            "materials": [ffnn / "course" / "ffnn-notes.md"], "dir": ffnn}


def _demo_assignment(case: dict) -> dict:
    same = [x for x in db.list_assignments() if x["title"] == case["title"]]
    # keep the one that already has a demo plan: an assignment with the same title (e.g. from scripts/smoke.py)
    # must not take over the demo and change its questions
    a = next((x for x in same if any(s["student_name"] == DEMO_STUDENT and s["plan"] for s in db.list_submissions(x["id"]))),
             same[0] if same else None)
    if a is None:
        a = db.create_assignment(case["title"], case["course"], case["description"], case["objectives"])
        folder = db.assignment_dir(a["id"]) / "materials"
        folder.mkdir(parents=True, exist_ok=True)
        for f in case["materials"]:
            (folder / f.name).write_bytes(f.read_bytes())
    return a


def seed_demo_report() -> bool:
    """Finished example interviews with their reports, so the professor pages have complete examples: one per
    team<N>.json of the demo case, shown as Team_<N> (cases/ffnn has synthetic ones; cases/pirelli is not in the
    repository). Each team is added once. True when something was added."""
    case = _demo_case()
    files = sorted(case["dir"].glob("team*.json"))
    if not files:
        return False
    a = _demo_assignment(case)
    have = {s["student_name"] for s in db.list_submissions(a["id"])}
    added = False
    for n, data in enumerate(files):
        team = "Team_" + data.stem.removeprefix("team")
        if team in have:
            continue
        d = json.loads(data.read_text())
        d["state"]["started_at"] = time.time() - (2 + n) * 3600  # "this morning", whenever the demo runs
        sub = db.create_submission(a["id"], team, f"{data.stem}@university.edu")
        folder = db.submission_dir(sub["id"])
        folder.mkdir(parents=True, exist_ok=True)
        for f in case["deliverable"]:
            (folder / f.name).write_bytes(f.read_bytes())
        db.update_submission(sub["id"], plan=d["plan"], state=d["state"], report=d["report"], flags=d["flags"],
                             status="completed", completed_at=d["state"]["started_at"] + d["state"]["turns"][-1]["t_s"])
        added = True
    return added


@app.get("/api/demo")
def demo_info():
    case = _demo_case()
    return {"label": case["label"], "headline": case["headline"], "blurb": case["blurb"],
            "documents": len(case["deliverable"]) + len(case["materials"])}


@app.post("/api/demo")
def demo_start(bg: BackgroundTasks):
    """Creates the demo assignment (once) and a new interview on it. The plan of an earlier demo run is reused,
    so from the second click on the interview is ready at once and costs no planner call."""
    case = _demo_case()
    a = _demo_assignment(case)
    earlier = next((s for s in db.list_submissions(a["id"]) if s["student_name"] == DEMO_STUDENT and s["plan"]
                    and len(json.loads(s["plan"])["topics"]) == config.TOPICS), None)
    sub = db.create_submission(a["id"], DEMO_STUDENT, "demo@university.edu")
    folder = db.submission_dir(sub["id"])
    folder.mkdir(parents=True, exist_ok=True)
    for f in case["deliverable"]:
        (folder / f.name).write_bytes(f.read_bytes())
    if earlier:
        db.update_submission(sub["id"], plan=json.loads(earlier["plan"]), status="ready")
    else:
        bg.add_task(_prepare, sub["id"])
    return {"invite_url": f"/interview/{sub['token']}", "ready": bool(earlier)}


def _sub_by_token(token: str) -> dict:
    sub = db.submission_by_token(token)
    if sub is None:
        raise HTTPException(404, "This interview link is not valid")
    return sub


@app.get("/api/interview/{token}")
def interview_status(token: str):
    sub = _sub_by_token(token)
    plan, _, _ = _load(sub)
    a = db.get_assignment(sub["assignment_id"])
    return {"status": sub["status"], "student_name": sub["student_name"], "assignment_title": a["title"],
            "duration_s": plan.duration_s if plan else config.DURATION_S,
            "topics_total": len(plan.topics) if plan else 0, "error": sub["error"],
            "voice": voice.mode(), "language": config.LANGUAGE, "camera": config.CAMERA}


@app.post("/api/interview/{token}/start")
def interview_start(token: str):
    sub = _sub_by_token(token)
    if sub["status"] not in ("ready", "in_progress"):
        raise HTTPException(409, {"preparing": "Your interview is still being prepared",
                                  "completed": "This interview is already completed"}.get(sub["status"], sub["error"] or "Not available"))
    plan, state, _ = _load(sub)
    if sub["status"] == "in_progress" and state and state.turns:  # reopened link: resume, never wipe answers
        return Interview(plan, state).current()
    iv = Interview(plan, InterviewState(topics=[]))
    step = iv.start()
    db.update_submission(sub["id"], state=iv.state, status="in_progress")
    return step


@app.post("/api/interview/{token}/retry")
def interview_retry(token: str, bg: BackgroundTasks):
    """Re-run the preparation after an error (e.g. the LLM was unreachable)."""
    sub = _sub_by_token(token)
    if sub["status"] != "error":
        raise HTTPException(409, "Nothing to retry")
    db.update_submission(sub["id"], status="preparing", error=None)
    bg.add_task(_prepare, sub["id"])
    return {"ok": True}


class Answer(BaseModel):
    text: str = ""
    duration_s: Optional[float] = None


@app.post("/api/interview/{token}/answer")
def interview_answer(token: str, body: Answer, bg: BackgroundTasks):
    sub = _sub_by_token(token)
    if sub["status"] != "in_progress":
        raise HTTPException(409, "The interview has not started" if sub["status"] == "ready" else "The interview is over")
    plan, state, _ = _load(sub)
    iv = Interview(plan, state)
    try:
        step = iv.answer(body.text, body.duration_s)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:  # LLM unavailable (quota, outage): nothing new is saved, earlier answers are
        log.warning("answer failed for submission %s: %s", sub["id"], e)
        raise HTTPException(503, "Pythagoras can't reach the AI right now (the API key may be over its limit). Your "
                                 "previous answers are saved: try again in a minute, or end the interview.")
    _save_progress(sub["id"], iv, bg)
    return step


@app.post("/api/interview/{token}/finish")
def interview_finish(token: str, bg: BackgroundTasks, body: Optional[Answer] = None):
    """End the interview now, keeping the unsent answer if any. Needs no LLM; the report is queued."""
    sub = _sub_by_token(token)
    if sub["status"] != "in_progress":
        raise HTTPException(409, "The interview is not in progress")
    plan, state, _ = _load(sub)
    iv = Interview(plan, state)
    step = iv.finish(body.text if body else None, body.duration_s if body else None)
    _save_progress(sub["id"], iv, bg)
    return step


class Flag(BaseModel):
    text: str


@app.post("/api/interview/{token}/flag")
def interview_flag(token: str, body: Flag):
    """Integrity signal from the page (the tab lost focus...). Shown to the professor as a review prompt."""
    sub = _sub_by_token(token)
    if sub["status"] != "in_progress" or not body.text.strip():
        return {"ok": False}
    _, state, _ = _load(sub)
    s = int(time.time() - state.started_at) if state and state.started_at else 0
    db.add_flag(sub["id"], f"[{s // 60:02}:{s % 60:02}] {body.text.strip()[:200]}")
    return {"ok": True}


def _save_progress(sid: int, iv: Interview, bg: BackgroundTasks) -> None:
    if iv.state.finished:
        db.update_submission(sid, state=iv.state, status="completed", completed_at=time.time())
        bg.add_task(_write_report, sid)
    else:
        db.update_submission(sid, state=iv.state)


# ---------- professor API ----------

def _prof_token() -> str:
    return hmac.new(config.SECRET.encode(), b"professor", hashlib.sha256).hexdigest()


def prof(request: Request) -> None:
    if not config.PROFESSOR_PASSWORD:  # MVP default: no login
        return
    if not hmac.compare_digest(request.cookies.get(COOKIE, ""), _prof_token()):
        raise HTTPException(401, "Please sign in")


class Login(BaseModel):
    password: str


@app.post("/api/prof/login")
def login(body: Login, response: Response):
    if not hmac.compare_digest(body.password, config.PROFESSOR_PASSWORD):
        raise HTTPException(401, "Wrong password")
    response.set_cookie(COOKIE, _prof_token(), httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return {"ok": True}


@app.post("/api/prof/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/prof/assignments", dependencies=[Depends(prof)])
def prof_assignments():
    return [_public_assignment(a) for a in db.list_assignments(hide_team=DEMO_STUDENT)]


@app.post("/api/prof/assignments", dependencies=[Depends(prof)])
async def create_assignment(title: str = Form(...), course: str = Form(""), description: str = Form(""),
                            objectives: str = Form(""), materials: List[UploadFile] = File(default=[])):
    if not title.strip():
        raise HTTPException(422, "Title is required")
    a = db.create_assignment(title.strip(), course.strip(), description.strip(), objectives.strip())
    try:
        await _save(materials, db.assignment_dir(a["id"]) / "materials")
    except Exception:  # no half-made assignment without its course material
        db.delete_assignment(a["id"])
        raise
    return _public_assignment(a)


def _assignment_or_404(aid: int) -> dict:
    a = db.get_assignment(aid)
    if a is None:
        raise HTTPException(404, "Assignment not found")
    return a


@app.get("/api/prof/assignments/{aid}", dependencies=[Depends(prof)])
def prof_assignment(aid: int):
    a = _assignment_or_404(aid)
    # the one-click demo interviews ("Demo team") stay in the database (their plan is reused) but not on these pages
    subs = [s for s in db.list_submissions(aid) if s["student_name"] != DEMO_STUDENT]
    a = {**a, "submitted": len(subs), "completed": sum(s["status"] == "completed" for s in subs),
         "materials": db.list_files(db.assignment_dir(aid) / "materials")}
    return {"assignment": a, "submissions": [_public_sub(s) for s in subs]}


def _sub_or_404(sid: int) -> dict:
    sub = db.get_submission(sid)
    if sub is None:
        raise HTTPException(404, "Submission not found")
    return sub


@app.get("/api/prof/submissions/{sid}", dependencies=[Depends(prof)])
def prof_submission(sid: int):
    sub = _sub_or_404(sid)
    plan, state, rep = _load(sub)
    return {"submission": _public_sub(sub), "assignment": _public_assignment(db.get_assignment(sub["assignment_id"])),
            "plan": json.loads(plan.model_dump_json()) if plan else None,
            "report": json.loads(rep.model_dump_json()) if rep else None,
            "turns": json.loads(state.model_dump_json())["turns"] if state else []}


@app.post("/api/prof/submissions/{sid}/report", dependencies=[Depends(prof)])
def retry_report(sid: int, bg: BackgroundTasks):
    """Try the report again now instead of waiting for the queue."""
    sub = _sub_or_404(sid)
    if sub["status"] != "completed":
        raise HTTPException(409, "The interview is not completed yet")
    if not sub["report"]:
        db.update_submission(sid, report_next_at=None)
        bg.add_task(_write_report, sid)
    return {"ok": True}


@app.get("/api/prof/submissions/{sid}/files/{name}", dependencies=[Depends(prof)])
def prof_file(sid: int, name: str):
    _sub_or_404(sid)
    path = db.submission_dir(sid) / _safe_name(name)
    if not path.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(path, filename=path.name)
