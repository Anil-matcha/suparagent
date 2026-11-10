"""Opt-in automatic routing for room messages that tag nobody.

Deterministic and cheap: a message is matched against each candidate's name,
title and description, and the most recent responder gets a small tiebreak so
a conversation stays with one agent. When the match is weak or tied, the
caller falls back to the room's default member, or to no one.
"""

import re

_WORD = re.compile(r"[a-z0-9][a-z0-9'_-]+")
_STOP = frozenset(
    "the and for you your with that this have has are was were will can could would should from into about what when where which who how why not but all any our out get got let new use using please need want make just also then than them they there their been being its it's i'm you're don't".split()
)
MIN_SCORE = 2.0
MIN_MARGIN = 1.0


def _tokens(text, minimum=3):
    return {w for w in _WORD.findall((text or "").lower()) if len(w) >= minimum and w not in _STOP}


def score(text, candidate):
    """Score one candidate: a name hit counts most, then title and description words."""
    message = _tokens(text)
    total = 0.0
    if _tokens(text, 2) & _tokens(candidate.get("name"), 2):
        total += 3.0
    total += 1.5 * len(message & _tokens(candidate.get("title")))
    total += 1.0 * len(message & _tokens(candidate.get("description")))
    return total


def route(text, candidates, last_responder=None):
    """Return the agent id to answer, or None when the router is not sure.

    candidates: list of dicts with id, name, title, description. The caller
    must already have filtered them to active, unheld room members.
    """
    ranked = sorted(
        ((score(text, c) + (0.5 if c["id"] == last_responder else 0.0), c["id"]) for c in candidates),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not ranked or ranked[0][0] < MIN_SCORE:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MIN_MARGIN:
        return None
    return ranked[0][1]
