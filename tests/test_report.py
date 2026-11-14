"""Offline: what the code enforces on the report, whatever the writer model says."""
from pythagoras import fake, llm, report
from pythagoras.models import (Evidence, InterviewPlan, InterviewReport, InterviewState, Move, TopicResult, TopicState,
                               Turn)

ANSWER = "I did the scaling before the split because it's easier, and honestly I never checked the leakage."


def state(*answers) -> InterviewState:
    turns = [Turn(t_s=10.0 * i, move=Move(kind="ask", topic_id="method", level=2), question="?", answer=a)
             for i, a in enumerate(answers)]
    return InterviewState(topics=[TopicState(id="method", level=3)], turns=turns)


def rep(*quotes, level=5) -> InterviewReport:
    return InterviewReport(summary="s", ownership="mixed", strengths=[], concerns=[], suggested_questions=[],
                           topics=[TopicResult(topic_id="method", title="Method", level_reached=level, assessment="a",
                                               evidence=[Evidence(quote=q, t_s=0, note="n") for q in quotes])])


def test_verbatim_quote_is_verified_whatever_the_case_and_punctuation():
    assert report.unverified(state(ANSWER), rep("the scaling before the split", "\"Because it's easier.\"")) == []


def test_invented_quote_is_listed():
    assert report.unverified(state(ANSWER), rep("I used five-fold cross-validation")) == \
        ["I used five-fold cross-validation"]


def test_shortened_quote_is_checked_piece_by_piece():
    ok = "I did the scaling before the split … I never checked the leakage"
    bad = "I did the scaling before the split ... and it improved the accuracy"
    assert report.unverified(state(ANSWER), rep(ok, bad)) == [bad]


def test_pieces_must_come_from_one_answer_in_order():
    swapped = "I never checked the leakage … I did the scaling before the split"
    stitched = "I did the scaling … we used a validation set"
    assert report.unverified(state(ANSWER, "Yes, we used a validation set."), rep(swapped, stitched)) == \
        [swapped, stitched]


def test_empty_quote_is_not_evidence():
    assert report.unverified(state(ANSWER), rep("", " … ")) == ["", " … "]


def test_accented_words_are_kept():
    assert report.unverified(state("Perché la rete è più semplice così."), rep("perché la rete è più semplice")) == []


def test_levels_come_from_the_engine_not_from_the_writer(monkeypatch):
    plan = fake.parse(InterviewPlan, [])
    st = state(ANSWER)
    st.topics[0].id = plan.topics[0].id
    written = rep("the scaling before the split", level=5)
    written.topics[0].topic_id = plan.topics[0].id
    monkeypatch.setattr(llm, "parse", lambda *a, **k: written)
    out = report.build(plan, st)
    assert out.topics[0].level_reached == 3 and out.unverified_quotes == []
