"""Offline: how deliverable files reach the model (planner._blocks) and what make_plan fixes afterwards."""
import json

from pythagoras import config, fake, llm, planner
from pythagoras.models import InterviewPlan


def blocks(tmp_path, **files):
    for name, body in files.items():
        (tmp_path / name.replace("__", ".")).write_bytes(body if isinstance(body, bytes) else body.encode())
    return planner._blocks(tmp_path, "Student deliverable")


def test_code_is_line_numbered_so_topics_can_be_anchored(tmp_path):
    (block,) = blocks(tmp_path, mlp__py="import numpy as np\nW1 = np.zeros((2, 8))\n")
    assert block["type"] == "text" and "mlp.py (line-numbered)" in block["text"]
    assert "   2| W1 = np.zeros((2, 8))" in block["text"]


def test_pdf_goes_as_a_document_not_as_extracted_text(tmp_path):
    (block,) = blocks(tmp_path, report__pdf=b"%PDF-1.4 fake")
    assert block["type"] == "document" and block["source"]["media_type"] == "application/pdf"
    assert block["title"] == "Student deliverable: report.pdf"


def test_notebook_sends_its_cells(tmp_path):
    nb = {"cells": [{"source": ["x = 1\n", "y = 2"]}, {"source": ["print(x + y)"]}]}
    (block,) = blocks(tmp_path, analysis__ipynb=json.dumps(nb))
    assert "x = 1\ny = 2" in block["text"] and "print(x + y)" in block["text"]


def test_long_text_is_cut_and_says_so(tmp_path):
    (block,) = blocks(tmp_path, notes__md="a" * (planner.MAX_TEXT_CHARS + 10))
    assert block["text"].endswith("[... file truncated ...]")
    assert len(block["text"]) < planner.MAX_TEXT_CHARS + 200


def test_unreadable_format_is_named_not_dropped_silently(tmp_path):
    (block,) = blocks(tmp_path, slides__key=b"\x00\x01")
    assert "slides.key - format not readable" in block["text"]


def test_missing_folder_gives_no_blocks(tmp_path):
    assert planner._blocks(tmp_path / "nope", "Course material") == []


def test_plan_keeps_the_configured_topics_duration_and_level_order(tmp_path, monkeypatch):
    plan = fake.parse(InterviewPlan, [])
    plan.topics.append(plan.topics[0].model_copy(update={"id": "extra"}))  # the model may add one more
    plan.topics[0].questions.reverse()
    monkeypatch.setattr(llm, "parse", lambda *a, **k: plan)
    out = planner.make_plan(tmp_path, tmp_path, {"title": "T", "course": "", "description": "", "objectives": ""}, 480)
    assert len(out.topics) == config.TOPICS and out.duration_s == 480
    assert [q.level for q in out.topics[0].questions] == [2, 3, 4, 5]
