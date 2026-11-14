"""Phase 3: plan + transcript -> the professor's report (reasoner model). Quotes are checked against the transcript."""
import json
import re

from . import config, llm
from .models import BLOOM, InterviewPlan, InterviewReport, InterviewState

SYSTEM = """You write the report a professor reads before the oral exam of a TEAM of students. The team had a short
spoken conversation with an AI interviewer about their own project (whoever worked on a part answered about it); each topic climbed Bloom's levels
(2 Understand, 3 Apply, 4 Analyze, 5 Evaluate) while answers were solid. You get the plan, the transcript with
per-answer judgements, and the level reached per topic.
- Never assign a grade and never single out individual members. Describe what the team showed, with evidence.
- Evidence quotes must be copied VERBATIM from the team's answers (short excerpts are fine) with that turn's t_s.
- Keep the level_reached given for each topic.
- ownership: strong = explains and defends their choices; mixed = knows parts; weak = cannot explain their own work.
- suggested_questions: 3-5 questions for the oral exam that target what remained unclear.
- Integrity flags, if any, are only review prompts: mention them neutrally, never as a verdict.
Write in {language}."""


def build(plan: InterviewPlan, state: InterviewState, flags: list = ()) -> InterviewReport:
    levels = {t.id: t.level for t in state.topics}
    payload = {
        "topics": [{"topic_id": t.id, "title": t.title, "claim": t.claim, "anchor": t.anchor,
                    "level_reached": levels.get(t.id, 0), "level_name": BLOOM.get(levels.get(t.id, 0), "none")}
                   for t in plan.topics],
        "transcript": [{"t_s": turn.t_s, "topic_id": turn.move.topic_id, "level": turn.move.level,
                        "kind": turn.move.kind, "pythagoras": turn.question, "student": turn.answer,
                        "judgement": turn.evaluation.model_dump() if turn.evaluation else None}
                       for turn in state.turns],
        "integrity_flags": list(flags),
    }
    rep = llm.parse(InterviewReport, SYSTEM.format(language=config.LANGUAGE_NAME),
                    json.dumps(payload, indent=1, ensure_ascii=False), effort="high")
    for tr in rep.topics:  # the rules decide the level, not the writer
        tr.level_reached = levels.get(tr.topic_id, tr.level_reached)
    rep.unverified_quotes = unverified(state, rep)
    return rep


def _norm(s: str) -> str:
    return re.sub(r"[^\w ]", "", re.sub(r"\s+", " ", s.lower())).strip()  # \w keeps accented letters


def _fragments(quote: str) -> list:
    """A quote the writer shortened ("I scaled first … because it's easier") is checked piece by piece."""
    return [f for f in (_norm(p) for p in re.split(r"…|\.{3,}|\[\.\.\.\]", quote)) if f]


def unverified(state: InterviewState, rep: InterviewReport) -> list:
    answers = [_norm(t.answer or "") for t in state.turns]

    def found(quote: str) -> bool:  # every piece inside ONE answer, in order: an empty quote is not evidence
        parts = _fragments(quote)
        return bool(parts) and any(_in_order(parts, a) for a in answers)
    return [e.quote for tr in rep.topics for e in tr.evidence if not found(e.quote)]


def _in_order(parts: list, answer: str) -> bool:
    at = 0
    for p in parts:
        at = answer.find(p, at)
        if at < 0:
            return False
        at += len(p)
    return True
