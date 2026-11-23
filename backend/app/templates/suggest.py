"""Rank bundled workers for a few stated goals ("Help me choose").

Pure and read-only: it looks only at the bundled catalog, makes no model call
or network request, reads no workspace data and changes nothing. It returns
template ids with a one-line reason; the person still ticks and confirms.
"""

import re
from dataclasses import dataclass

from app.templates import catalog

# A short closed list the screen offers as choices. Each maps to the workers
# that serve it, in the order they are preferred.
GOALS: dict[str, tuple[str, tuple[str, ...]]] = {
    "briefing": ("Start each day with a short summary", ("operations-chief-of-staff",)),
    "leads": ("Find companies and people who could buy", ("sales-sales", "growth-gtm-engineer")),
    "outreach": ("Write to people who could become customers", ("sales-sales", "growth-gtm-engineer")),
    "content": ("Write articles that bring in visitors", ("marketing-seo", "marketing-social-media")),
    "social": ("Keep my social accounts active", ("marketing-social-media",)),
    "ads": ("Get more from my advertising", ("marketing-ad-manager",)),
    "support": ("Answer customers and keep them happy", ("support-customer-satisfaction",)),
    "website": ("Keep my website healthy", ("web-web-developer",)),
}

SETUP_RANK = {"express": 0, "standard": 1, "guided": 2}

MAX_SUGGESTIONS = 8
GOAL_POINTS = 10
TEXT_POINTS_MAX = 5

_STOP = frozenset(
    "the and for with that this from your you are our have has not but can will about into what when who how "
    "get make need want help use using also any all more some their them they his her its was were been".split()
)


@dataclass(frozen=True)
class Suggestion:
    template_id: str
    score: int
    reason: str


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def _template_words(template: catalog.Template) -> set[str]:
    return _words(" ".join((template.name, template.role, template.description, template.category, *template.example_requests)))


def suggest(
    goals: list[str] | tuple[str, ...] = (),
    about: str = "",
    *,
    max_setup: str = "guided",
    limit: int = 5,
) -> list[Suggestion]:
    """Up to `limit` workers ranked by the stated goals and a short description of the business.

    A goal that is one of the known keys scores its workers directly. Any other
    goal text, and `about`, count as words matched against each worker's name,
    role, description, category and example requests. Nothing matching scores
    nothing, so no goals and no text returns an empty list.
    """
    limit = max(1, min(limit, MAX_SUGGESTIONS))
    depth_cap = SETUP_RANK.get(max_setup, SETUP_RANK["guided"])
    keys = [g for g in goals if g in GOALS]
    free = " ".join([*(g for g in goals if g not in GOALS), about])
    wanted = _words(free)

    scored: list[tuple[int, int, Suggestion]] = []
    for order, template in enumerate(catalog.TEMPLATES):
        if SETUP_RANK[template.setup_depth] > depth_cap:
            continue
        score, reason = 0, ""
        for key in keys:
            label, ids = GOALS[key]
            if template.id in ids:
                score += GOAL_POINTS - ids.index(template.id)
                reason = reason or f"Fits your goal: {label[0].lower()}{label[1:]}"
        hits = min(len(wanted & _template_words(template)), TEXT_POINTS_MAX)
        if hits:
            score += hits
            reason = reason or "Matches what you described"
        if score > 0:
            scored.append((-score, order, Suggestion(template.id, score, reason)))
    scored.sort(key=lambda item: item[:2])
    return [item[2] for item in scored[:limit]]
