"""The shadow-mode comparison report (services/shadow_report.py). Counts, ids, ranks, categories and times only."""
from typing import Literal

from pydantic import BaseModel


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
