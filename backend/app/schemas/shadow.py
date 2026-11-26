"""The shadow-mode comparison report (services/shadow_report.py). Counts, ids, ranks, categories and times only."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ShadowSample(BaseModel):
    predictions: int
    with_owner_reply: int
    drafts: int


class ShadowRate(BaseModel):
    rate: float | None
    n: int


class ShadowRanking(BaseModel):
    top5_answered_24h: ShadowRate
    baseline_answered_24h: ShadowRate
    too_few: bool


class ShadowDrafts(BaseModel):
    as_is: int
    light_edit: int
    rewrite: int
    no_reply_seen: int
    usable_rate: float | None
    too_few: bool


class ShadowDisagreement(BaseModel):
    thread_id: str
    kind: Literal["ranked_high_unanswered", "answered_fast_ranked_low"]
    rank: int
    category: str
    predicted_at: str


class ShadowReport(BaseModel):
    agent_id: int
    window_days: int
    generated_at: str
    sample: ShadowSample
    ranking: ShadowRanking
    drafts: ShadowDrafts
    disagreements: list[ShadowDisagreement]
    accounts: list[str]


# -- feedback and the share suggestion (BL-175) ---------------------------------------------------------------------------

class ShadowFeedbackIn(BaseModel):
    """One piece of the owner's feedback. The note is optional; mail text is never part of any of this."""
    model_config = ConfigDict(extra="forbid")

    thread_id: str = Field(min_length=1, max_length=200)
    kind: Literal["draft", "ranking"]
    reason: Literal["wrong_tone", "wrong_policy", "missing_context", "wrong_priority", "other"]
    note: str | None = Field(default=None, max_length=300)
    account_id: str | None = Field(default=None, min_length=1, max_length=200)


class ShadowFeedbackOut(BaseModel):
    thread_id: str
    kind: Literal["draft", "ranking"]
    reason: str
    recorded: bool


class ShadowFeedbackCounts(BaseModel):
    agent_id: int
    window_days: int
    total: int
    by_reason: dict[str, int]
    by_kind: dict[str, int]


class ShareSuggestionEvidence(BaseModel):
    evaluated: int
    usable: int
    usable_rate: float | None
    lower: float | None
    upper: float | None
    days: int
    wrong_policy: int


class ShareSuggestion(BaseModel):
    agent_id: int
    state: Literal["none", "raise", "lower"]
    account_id: str | None
    current_percent: int
    suggested_percent: int | None
    evidence: ShareSuggestionEvidence
    reason: str
