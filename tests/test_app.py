import pytest
from fastapi.testclient import TestClient

from pythagoras.app import app


@pytest.fixture
def client():
    return TestClient(app)


def login(c):
    assert c.post("/api/prof/login", json={"password": "pw"}).status_code == 200


def test_prof_api_needs_login(client):
    assert client.get("/api/prof/assignments").status_code == 401
    assert client.post("/api/prof/login", json={"password": "nope"}).status_code == 401


def test_full_flow_upload_interview_report(client):
    login(client)
    a = client.post("/api/prof/assignments", data={"title": "Project 1", "course": "ML",
                                                   "objectives": "LO1 x"}).json()
    assert any(x["id"] == a["id"] for x in client.get("/api/assignments").json())

    r = client.post("/api/submissions", data={"assignment_id": a["id"], "name": "Ada Lovelace",
                                              "email": "ada@example.com"},
                    files=[("files", ("report.md", b"# My project\nI used trees.", "text/markdown"))])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email_configured"] is False and "/interview/" in body["invite_url"]
    token = body["invite_url"].rsplit("/", 1)[1]

    status = client.get(f"/api/interview/{token}").json()  # TestClient runs background tasks before returning
    assert status["status"] == "ready" and status["topics_total"] == 3

    step = client.post(f"/api/interview/{token}/start").json()
    assert step["kind"] == "opening"
    step = client.post(f"/api/interview/{token}/answer", json={"text": "I built a classifier", "duration_s": 3}).json()
    while not step["done"]:
        step = client.post(f"/api/interview/{token}/answer", json={"text": "I don't know"}).json()
    assert client.post(f"/api/interview/{token}/answer", json={"text": "x"}).status_code == 409

    detail = client.get(f"/api/prof/assignments/{a['id']}").json()
    sub = detail["submissions"][0]
    assert sub["completed"] and sub["files"] == ["report.md"] and detail["assignment"]["completed"] == 1

    rep = client.get(f"/api/prof/submissions/{sub['id']}").json()
    assert rep["report"]["ownership"] == "mixed" and rep["report"]["unverified_quotes"] == []
    assert rep["plan"]["topics"] and rep["turns"][0]["move"]["kind"] == "opening"
    assert client.get(f"/api/prof/submissions/{sub['id']}/files/report.md").content.startswith(b"# My project")


def test_bad_token_and_bad_email(client):
    assert client.get("/api/interview/nope").status_code == 404
    login(client)
    a = client.post("/api/prof/assignments", data={"title": "P2"}).json()
    r = client.post("/api/submissions", data={"assignment_id": a["id"], "name": "A", "email": "not-an-email"},
                    files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 422


def test_retry_after_a_failed_preparation(client, monkeypatch):
    from pythagoras import app as app_module, planner
    login(client)
    a = client.post("/api/prof/assignments", data={"title": "P3"}).json()
    real = planner.make_plan
    monkeypatch.setattr(app_module.planner, "make_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key")))
    r = client.post("/api/submissions", data={"assignment_id": a["id"], "name": "B", "email": "b@example.com"},
                    files=[("files", ("a.md", b"x", "text/markdown"))]).json()
    token = r["invite_url"].rsplit("/", 1)[1]
    assert client.get(f"/api/interview/{token}").json()["status"] == "error"
    monkeypatch.setattr(app_module.planner, "make_plan", real)
    assert client.post(f"/api/interview/{token}/retry").status_code == 200
    assert client.get(f"/api/interview/{token}").json()["status"] == "ready"
    assert client.post(f"/api/interview/{token}/retry").status_code == 409


def test_failed_report_is_queued_and_retried(client, monkeypatch):
    from pythagoras import app as app_module, db, report
    login(client)
    a = client.post("/api/prof/assignments", data={"title": "P4"}).json()
    r = client.post("/api/submissions", data={"assignment_id": a["id"], "name": "C", "email": "c@example.com"},
                    files=[("files", ("a.md", b"x", "text/markdown"))]).json()
    token = r["invite_url"].rsplit("/", 1)[1]
    real = report.build
    monkeypatch.setattr(app_module.report, "build", lambda *a: (_ for _ in ()).throw(RuntimeError("429 quota")))
    step = client.post(f"/api/interview/{token}/start").json()
    while not step["done"]:
        step = client.post(f"/api/interview/{token}/answer", json={"text": "I don't know"}).json()

    sub = client.get(f"/api/prof/submissions/{r['id']}").json()
    assert sub["report"] is None and sub["submission"]["completed"]
    assert sub["submission"]["report_attempts"] == 1 and "429" in sub["submission"]["report_error"]
    assert [s["id"] for s in db.reports_due(sub["submission"]["report_next_at"] + 1)] == [r["id"]]

    monkeypatch.setattr(app_module.report, "build", real)  # the key works again
    assert client.post(f"/api/prof/submissions/{r['id']}/report").status_code == 200
    sub = client.get(f"/api/prof/submissions/{r['id']}").json()
    assert sub["report"] is not None and sub["submission"]["report_error"] is None
    assert r["id"] not in [s["id"] for s in db.reports_due(10 ** 12)]


def test_queue_thread_writes_due_reports(monkeypatch):
    import time
    from pythagoras import app as app_module, db
    calls = []
    monkeypatch.setattr(app_module, "QUEUE_TICK_S", 0.05)
    monkeypatch.setattr(app_module.db, "reports_due", lambda now: calls.append(now) or [])
    with TestClient(app):  # runs the lifespan, i.e. starts the queue thread
        time.sleep(0.3)
    assert len(calls) >= 2


def test_llm_outage_resume_and_finish(client, monkeypatch):
    from pythagoras import app as app_module, supervisor
    login(client)
    a = client.post("/api/prof/assignments", data={"title": "P5"}).json()
    r = client.post("/api/submissions", data={"assignment_id": a["id"], "name": "D", "email": "d@example.com"},
                    files=[("files", ("a.md", b"x", "text/markdown"))]).json()
    token = r["invite_url"].rsplit("/", 1)[1]
    client.post(f"/api/interview/{token}/start")
    first = client.post(f"/api/interview/{token}/answer", json={"text": "my intro"}).json()

    monkeypatch.setattr(supervisor, "evaluate", lambda *a: (_ for _ in ()).throw(RuntimeError("429 quota")))
    assert client.post(f"/api/interview/{token}/answer", json={"text": "lost?"}).status_code == 503
    resumed = client.post(f"/api/interview/{token}/start").json()  # reopening the link resumes, no wipe
    assert resumed["question"] == first["question"]

    end = client.post(f"/api/interview/{token}/finish", json={"text": "my unsent answer", "duration_s": 5}).json()
    assert end["done"]
    turns = client.get(f"/api/prof/submissions/{r['id']}").json()["turns"]
    assert [t["answer"] for t in turns] == ["my intro", "my unsent answer", None]
    assert client.get(f"/api/interview/{token}").json()["status"] == "completed"
    assert client.post(f"/api/interview/{token}/finish").status_code == 409


def test_one_click_demo_reuses_its_plan(client):
    info = client.get("/api/demo").json()
    assert info["documents"] >= 2
    first = client.post("/api/demo").json()  # runs the planner (offline stub) in the background task
    token = first["invite_url"].rsplit("/", 1)[1]
    assert client.get(f"/api/interview/{token}").json()["status"] == "ready"
    again = client.post("/api/demo").json()
    assert again["ready"] and again["invite_url"] != first["invite_url"]
    assert client.post(f"/api/interview/{again['invite_url'].rsplit('/', 1)[1]}/start").json()["kind"] == "opening"


def test_professor_pages_are_open_without_a_password(monkeypatch):
    from pythagoras import config
    monkeypatch.setattr(config, "PROFESSOR_PASSWORD", "")
    c = TestClient(app)
    assert c.get("/api/prof/assignments").status_code == 200 and c.get("/api/prof/auth").json() == {"password": False}


def test_demo_reports_are_seeded_once_and_demo_runs_are_hidden(client):
    from pythagoras.app import seed_demo_report
    seed_demo_report()  # synthetic cases/ffnn/team*.json (startup may have added them already)
    assert not seed_demo_report()  # never twice
    client.post("/api/demo")  # a one-click demo run: not shown to the professor
    login(client)
    a = next(x for x in client.get("/api/prof/assignments").json() if x["title"].startswith("Project 1 - A feed"))
    subs = client.get(f"/api/prof/assignments/{a['id']}").json()["submissions"]
    assert sorted(s["student_name"] for s in subs) == ["Team_1", "Team_2"] and a["submitted"] == 2
    for s in subs:
        rep = client.get(f"/api/prof/submissions/{s['id']}").json()
        assert rep["submission"]["status"] == "completed" and len(rep["report"]["topics"]) == 3


def test_oversized_file_is_refused_and_not_kept(client, monkeypatch):
    from pythagoras import app as app_module
    login(client)
    monkeypatch.setattr(app_module, "MAX_UPLOAD", 1500 * 1024)
    aid = client.post("/api/prof/assignments", data={"title": "Big"}, files=[("materials", ("notes.md", b"x" * (2 << 20)))])
    assert aid.status_code == 413 and "larger than 50 MB" in aid.json()["detail"]
    assert not any(a["title"] == "Big" for a in client.get("/api/prof/assignments").json())

    aid = client.post("/api/prof/assignments", data={"title": "Small"}).json()["id"]
    r = client.post("/api/submissions", data={"assignment_id": aid, "name": "T", "email": "t@u.edu"},
                    files=[("files", ("ok.md", b"fine")), ("files", ("huge.pdf", b"x" * (2 << 20)))])
    assert r.status_code == 413
    assert client.get(f"/api/prof/assignments/{aid}").json()["submissions"] == []  # nothing stuck in "preparing"


def test_files_with_the_same_name_are_both_kept(client):
    login(client)
    aid = client.post("/api/prof/assignments", data={"title": "Twins"}).json()["id"]
    client.post("/api/submissions", data={"assignment_id": aid, "name": "T", "email": "t@u.edu"},
                files=[("files", ("report.md", b"first")), ("files", ("report.md", b"second"))])
    (sub,) = client.get(f"/api/prof/assignments/{aid}").json()["submissions"]
    assert sub["files"] == ["report-2.md", "report.md"]
    assert client.get(f"/api/prof/submissions/{sub['id']}/files/report-2.md").content == b"second"


def test_a_failed_invite_email_keeps_the_interview_ready(client, monkeypatch):
    from pythagoras import mailer
    monkeypatch.setattr(mailer, "send_invite", lambda *a: (_ for _ in ()).throw(OSError("outbox not writable")))
    login(client)
    aid = client.post("/api/prof/assignments", data={"title": "Mail"}).json()["id"]
    client.post("/api/submissions", data={"assignment_id": aid, "name": "T", "email": "t@u.edu"},
                files=[("files", ("r.md", b"x"))])
    (sub,) = client.get(f"/api/prof/assignments/{aid}").json()["submissions"]
    assert sub["status"] == "ready" and sub["error"] is None
