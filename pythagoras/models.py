"""Data contracts shared by the planner, the interview engine, the report and the API."""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

BLOOM = {1: "Remember", 2: "Understand", 3: "Apply", 4: "Analyze", 5: "Evaluate", 6: "Create"}
MIN_LEVEL, MAX_LEVEL = 2, 5  # the interview climbs Understand -> Evaluate; Create is out of reach in 10 minutes


# ---------- Phase 1: plan ----------

class BloomQuestion(BaseModel):
    level: int = Field(description="Bloom level 2-5")
    question: str
    expected_points: List[str] = Field(description="What a good answer must mention")


class Topic(BaseModel):
    id: str = Field(description="short slug, e.g. 'metrics'")
    title: str = Field(description="Neutral name of the area, shown to the student: never names a flaw")
    claim: str = Field(description="What the deliverable claims or decides about this topic")
    anchor: str = Field(description="Where in the deliverable: page / section / file:line")
    priority: Literal["high", "medium", "low"]
    time_budget_s: int
    questions: List[BloomQuestion]


class Anchored(BaseModel):
    text: str
    anchor: str


class Analysis(BaseModel):
    summary: str = Field(description="What the project does, 3-5 sentences")
    strengths: List[Anchored]
    weak_spots: List[Anchored]
    objectives_coverage: List[str] = Field(description="One line per learning objective: covered / partial / missing + why")


class InterviewPlan(BaseModel):
    duration_s: int = 600
    topics: List[Topic]
    analysis: Analysis


# ---------- Phase 2: interview ----------

class Evaluation(BaseModel):
    verdict: Literal["solid", "partial", "weak", "dont_know", "off_topic"]
    covered_points: List[str]
    missing_points: List[str]
    note: str = Field(description="One sentence for the professor: what this answer shows")


class Move(BaseModel):
    kind: Literal["opening", "ask", "probe", "scaffold", "next_topic", "wrap_up"]
    topic_id: Optional[str] = None
    level: int = 0
    intent: str = ""  # what the talker should do, in plain words


class Turn(BaseModel):
    t_s: float
    move: Move
    question: str
    answer: Optional[str] = None
    answer_s: Optional[float] = None
    evaluation: Optional[Evaluation] = None


class TopicState(BaseModel):
    id: str
    level: int = 0  # highest Bloom level answered solidly
    current: int = 0  # level being asked now
    misses: int = 0  # weak answers at the current level
    status: Literal["pending", "open", "closed"] = "pending"
    spent_s: float = 0


class InterviewState(BaseModel):
    topics: List[TopicState]
    turns: List[Turn] = []
    started_at: Optional[float] = None
    finished: bool = False


# ---------- Phase 3: report ----------

class Evidence(BaseModel):
    quote: str = Field(description="Verbatim words of the student from the transcript")
    t_s: float = Field(description="Seconds from the start of the interview of that turn")
    note: str


class TopicResult(BaseModel):
    topic_id: str
    title: str
    level_reached: int
    assessment: str = Field(description="2-3 sentences")
    evidence: List[Evidence]


class InterviewReport(BaseModel):
    summary: str = Field(description="4-6 sentences for the professor")
    ownership: Literal["strong", "mixed", "weak"] = Field(description="How well the team knows its own project")
    topics: List[TopicResult]
    strengths: List[str]
    concerns: List[str]
    suggested_questions: List[str] = Field(description="Questions the professor could ask at the oral exam")
    unverified_quotes: List[str] = []  # filled by code: quotes not found in the transcript


class Step(BaseModel):
    """What the interview page needs after every call."""
    kind: str
    question: str
    turn: int = 0  # index of this question: the page sends it back with the answer
    topic_title: Optional[str] = None
    level: int = 0
    level_name: Optional[str] = None
    topic_index: int = 0
    topics_total: int = 0
    elapsed_s: float = 0
    duration_s: int = 600
    done: bool = False
    transcript: Optional[str] = None
    levels: Dict[str, int] = {}  # topic_id -> level reached so far
