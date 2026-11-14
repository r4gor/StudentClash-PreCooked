"""python -m pythagoras {serve|seed|check}"""
import argparse
import time


def check() -> None:
    """One real call per model, to verify the key and the model ids."""
    from . import config, llm
    from .models import Evaluation
    t = time.monotonic()
    llm.parse(Evaluation, "Judge the answer.", '{"question": "What is 2+2?", "student_answer": "4"}', effort="low",
              max_tokens=2000)
    print(f"ok  reasoner  {config.PROVIDER}/{config.REASONER_MODEL}  {time.monotonic() - t:.1f}s")
    t = time.monotonic()
    llm.parse(Evaluation, "Judge the answer.", '{"question": "What is 2+2?", "student_answer": "4"}', fast=True)
    print(f"ok  judge     {config.PROVIDER}/{config.JUDGE_MODEL}  {time.monotonic() - t:.1f}s")
    t = time.monotonic()
    llm.say("Reply with one short sentence.", "Say hello to the student.")
    print(f"ok  talker    {config.PROVIDER}/{config.TALKER_MODEL}  {time.monotonic() - t:.1f}s")


def seed() -> None:
    """A demo assignment so the student page is not empty (plus the finished example reports of the demo case)."""
    from . import db
    from .app import seed_demo_report
    if seed_demo_report():
        print("added the example reports (Team_1, Team_2...)")
    if any(a["title"] == "Final project" for a in db.list_assignments()):
        print("demo assignment already there")
        return
    a = db.create_assignment("Final project", "Machine Learning for Social Good",
                             "Build, evaluate and document a small ML system that solves a real problem.",
                             "LO1 Frame a real problem and its stakeholders\nLO2 Choose and justify a method\n"
                             "LO3 Evaluate results critically and discuss limitations")
    print(f"created assignment {a['id']}: {a['title']}")


def main() -> None:
    p = argparse.ArgumentParser(prog="pythagoras")
    p.add_argument("cmd", choices=["serve", "seed", "check"])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    a = p.parse_args()
    if a.cmd == "check":
        return check()
    if a.cmd == "seed":
        return seed()
    import uvicorn
    uvicorn.run("pythagoras.app:app", host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
