"""The bundled template catalog and its read-only routes."""

import dataclasses
import re

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.templates import router as templates_router
from app.schemas.agent import AgentCreateRequest
from app.services import muapi_data_api
from app.templates import catalog

# The catalog is public text: it must not name any other product.
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "FRONTEND_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


def client(host=("127.0.0.1", 50000)):
    app = FastAPI()
    app.include_router(templates_router, prefix="/api/v1")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=host), base_url="http://localhost")


def test_ids_are_unique_and_stable_looking():
    ids = [t.id for t in catalog.TEMPLATES]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z]+(-[a-z]+)+", i) for i in ids)
    assert len(ids) == 8


def test_every_template_is_complete_and_bounded():
    for t in catalog.TEMPLATES:
        assert t.category in catalog.CATEGORIES, t.id
        assert t.id.startswith(t.category + "-"), t.id
        assert 0 < len(t.full_instructions) <= catalog.INSTRUCTIONS_MAX, t.id
        assert t.name and t.role and t.description and t.example_requests, t.id
        assert re.fullmatch(r"#[0-9a-f]{6}", t.color), t.id
        assert len(t.name) <= 80 and len(t.role) <= 200 and len(t.description) <= 4000
        assert len(t.starter_notes or "") <= catalog.NOTES_MAX, t.id
        for skill in t.skills:
            assert SKILL_NAME.match(skill.name) and len(skill.name) <= 64, (t.id, skill.name)
            assert 0 < len(skill.body) <= catalog.SKILL_BODY_MAX
            assert skill.description


def test_a_template_fills_a_valid_agent_request():
    for t in catalog.TEMPLATES:
        AgentCreateRequest(name=t.name, title=t.role, description=t.description, instructions=t.full_instructions, starter_notes=t.starter_notes)


def test_every_category_has_a_template_and_most_roles_need_no_connection():
    assert {t.category for t in catalog.TEMPLATES} == set(catalog.CATEGORIES)
    assert sum(1 for t in catalog.TEMPLATES if t.available) > len(catalog.TEMPLATES) // 2


def test_a_missing_connection_makes_a_template_unavailable():
    needs_email = dataclasses.replace(catalog.get("web-web-developer"), requires=("email",))
    assert not needs_email.available
    assert catalog.get("web-web-developer").available



# --- Worker templates: shared rules, starter routines, budgets and wording ---

# Budgets in words: role text, each routine skill, a routine note, the shared rules,
# and the setup text by depth.
ROLE_WORDS = 120
SKILL_WORDS = 350
NOTE_WORDS = 60
RULES_WORDS = 520
SETUP_WORDS = {"express": 80, "standard": 150, "guided": 250}
DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun", "first-weekday", "last-weekday"}

CLOCK_TIME = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b", re.I)
OUTBOUND = re.compile(r"\b(?:publish(?:es|ed|ing)?|send(?:s|ing)?|post(?:s|ed|ing)?|submit(?:s|ted|ting)?|buy(?:s|ing)?|purchas(?:e|es|ed|ing)|email(?:s|ed|ing)?|sign(?:s|ed)? in)\b", re.I)
NEGATION = re.compile(r"\b(?:never|not|no|without|cannot|nor|neither|only reads?)\b", re.I)


def words(text: str) -> int:
    return len(text.split())


def workers():
    return [t for t in catalog.TEMPLATES if t.routines or t.uses_run_rules]


def all_text(t):
    return [t.name, t.role, t.description, t.instructions, t.full_instructions, t.setup_text or "", t.starter_notes or ""] + [
        x for s in t.skills for x in (s.name, s.description, s.body)
    ] + [r.note for r in t.routines]


def test_there_are_eight_worker_templates_with_plain_names():
    assert [t.id for t in workers()] == [
        "operations-analyst", "growth-gtm-engineer", "sales-sales", "marketing-seo",
        "marketing-social-media", "marketing-ad-manager", "support-customer-satisfaction", "web-web-developer",
    ]
    assert [t.name for t in workers()] == [
        "Operations analyst", "GTM engineer", "Sales", "SEO",
        "Social media", "Ad manager", "Customer satisfaction", "Web developer",
    ]
    assert [t.category for t in workers()] == ["operations", "growth", "sales", "marketing", "marketing", "marketing", "support", "web"]
    assert catalog.CATALOG_VERSION == 10
    assert "marketing" in catalog.CATEGORIES


def test_every_worker_needs_muapi_and_only_suggests_its_tools():
    paid = {t.id for t in workers()}
    for t in workers():
        if t.id in paid:
            assert t.requires == ("muapi",), t.id
            media = t.id in {"marketing-social-media", "marketing-ad-manager"}
            assert t.suggested_tools == (catalog.MUAPI_TOOLS + catalog.MUAPI_MEDIA_TOOLS if media else catalog.MUAPI_TOOLS), t.id
    assert set(catalog.MUAPI_TOOLS) == {"muapi_data_api_search", "muapi_data_api_inspect", "muapi_data_api_estimate", "muapi_data_api_call", "muapi_predict_result"}


def test_paid_skills_name_real_tools_and_media_skills_are_capped():
    known = set(catalog.MUAPI_TOOLS) | set(catalog.MUAPI_MEDIA_TOOLS)
    for t in workers():
        for skill in t.skills:
            for name in re.findall(r"`(muapi_[a-z_]+)`", skill.body):
                assert name in known, (t.id, skill.name, name)
                assert t.requires == ("muapi",), (t.id, skill.name)
    for t in (catalog.get("marketing-social-media"), catalog.get("marketing-ad-manager")):
        text = " ".join(s.body for s in t.skills)
        assert "cheapest" in text and "approved" in text and "muapi_media_generate" in text and "max_cost_usd" in text
        assert "muapi_image_generate" not in text and "muapi_video_generate" not in text
    assert all(x in catalog.RUN_RULES for x in catalog.MUAPI_MEDIA_TOOLS)
    assert "muapi_image_generate" not in catalog.RUN_RULES
    assert set(catalog.MUAPI_MEDIA_TOOLS) <= set(muapi_data_api.TOOL_BY_NAME)


# Endpoint names the skills may mention, checked against MuAPI's model list (domains seo, lead_enrichment,
# scraping and market_data, plus `research-web-answer`; no social-publishing or sandbox names).
# Add a name here only after finding it in MuAPI's model list.
VETTED_ENDPOINTS = frozenset({
    "ad-library-search",
    "company-buying-signals",
    "company-enrich",
    "company-funding",
    "company-headcount-growth",
    "company-job-postings",
    "company-products",
    "company-public-financials",
    "company-technographics",
    "creators-analytics",
    "creators-lookalike",
    "creators-profile",
    "creators-search",
    "ecommerce-keyword-metrics",
    "ecommerce-marketplace-search",
    "email-verify",
    "facebook-fetch-reels",
    "instagram-fetch-reels",
    "linkedin-company-profile",
    "linkedin-employees",
    "linkedin-people-search",
    "market-stock-history",
    "market-stock-screener",
    "news-search",
    "people-rank-decision-makers",
    "people-search",
    "research-web-answer",
    "seo-ai-mentions",
    "seo-ai-mentions-metrics",
    "seo-ai-response",
    "seo-app-reviews",
    "seo-backlinks",
    "seo-backlinks-history",
    "seo-backlinks-pages",
    "seo-business-listings",
    "seo-business-profile",
    "seo-business-reviews",
    "seo-business-updates",
    "seo-domain-overview",
    "seo-google-qa",
    "seo-google-serp",
    "seo-keyword-overview",
    "seo-keyword-research",
    "seo-keyword-trends",
    "seo-keywords-for-keywords",
    "seo-keywords-search-volume",
    "seo-lighthouse-audit",
    "seo-local-serp",
    "seo-rank-track-batch",
    "seo-related-keywords",
    "seo-relevant-pages",
    "seo-yelp-reviews",
    "seo-youtube-organic",
    "seo-youtube-video-comments",
    "seo-youtube-video-info",
    "seo-youtube-video-subtitles",
    "social-read-posts",
    "social-search-posts",
    "tiktok-fetch-videos",
    "twitter-fetch-posts",
    "youtube-fetch-shorts",
})


def test_every_endpoint_a_skill_names_is_on_the_vetted_list_and_each_worker_has_five_to_twelve_skills():
    skill_names = {s.name for t in workers() for s in t.skills}
    for t in workers():
        assert 5 <= len(t.skills) <= 12, (t.id, len(t.skills))
        for skill in t.skills:
            for name in re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", skill.body):
                assert name in VETTED_ENDPOINTS or name in skill_names, (t.id, skill.name, name)
    assert not any(n.startswith(("social-publish", "sandbox")) for n in VETTED_ENDPOINTS)


def test_the_operations_analyst_worker_is_not_a_team_lead_role():
    t = catalog.get("operations-analyst")
    assert t.name == "Operations analyst" and "lead" not in t.role.lower()
    assert "manage other workers" in t.instructions


def test_run_rules_are_written_once_and_composed_into_each_worker():
    for t in workers():
        assert t.uses_run_rules, t.id
        assert t.full_instructions.endswith(t.run_rules.rstrip()), t.id
        assert t.full_instructions.count("Shared run rules") == 1, t.id
        # Never copied into the role text or into a skill.
        assert "Profile first" not in t.instructions, t.id
        for skill in t.skills:
            assert "Profile first" not in skill.body and "[shared run rules]" not in skill.body, (t.id, skill.name)
    assert all(t.run_rules == catalog.RUN_RULES for t in workers())
    ordinary = dataclasses.replace(catalog.get("web-web-developer"), uses_run_rules=False)
    assert ordinary.full_instructions == ordinary.instructions


def test_worker_text_stays_within_the_word_budgets():
    assert words(catalog.RUN_RULES) <= RULES_WORDS and words(catalog.RUN_RULES_LOCAL) <= RULES_WORDS
    for t in workers():
        assert words(t.instructions) <= ROLE_WORDS, t.id
        assert len(re.findall(r"[.!?](?:\s|$)", t.description)) == 1, f"{t.id}: description must be one sentence"
        assert t.setup_depth in catalog.SETUP_DEPTHS, t.id
        assert t.setup_text and words(t.setup_text) <= SETUP_WORDS[t.setup_depth], t.id
        for skill in t.skills:
            assert words(skill.body) <= SKILL_WORDS, (t.id, skill.name)
            assert len(re.findall(r"[.!?](?:\s|$)", skill.description)) == 1, (t.id, skill.name)
        for routine in t.routines:
            assert words(routine.note) <= NOTE_WORDS, (t.id, routine.id)


def test_every_routine_is_paused_and_points_at_a_skill():
    assert workers()
    for t in workers():
        names = {s.name for s in t.skills}
        assert t.routines, t.id
        for r in t.routines:
            assert r.enabled_after_install is False, (t.id, r.id)
            assert r.skill in names, (t.id, r.id)
            assert r.days and set(r.days) <= DAYS, (t.id, r.id)
            assert 0 < r.budget_minutes <= 120, (t.id, r.id)
    for t in catalog.TEMPLATES:
        assert all(r.enabled_after_install is False for r in t.routines), t.id


def test_no_clock_times_in_routine_text():
    for t in workers():
        texts = [t.run_rules] + [s.body for s in t.skills] + [r.note for r in t.routines] + [r.window for r in t.routines]
        for text in texts:
            assert not CLOCK_TIME.search(text), (t.id, CLOCK_TIME.search(text).group(0))


def test_outbound_actions_only_appear_inside_a_prohibition():
    for t in workers():
        for text in all_text(t):
            for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
                # A backticked name is a lookup or tool identifier (for example `company-buying-signals`), not an action.
                if OUTBOUND.search(re.sub(r"`[^`]*`", "", sentence)):
                    assert NEGATION.search(sentence), (t.id, sentence)


def test_workers_pass_validation_as_new_agents():
    for t in workers():
        AgentCreateRequest(name=t.name, title=t.role, description=t.description, instructions=t.full_instructions)


async def test_the_api_returns_the_composed_instructions_and_paused_routines():
    async with client() as c:
        body = (await c.get("/api/v1/templates/marketing-seo")).json()
        listing = (await c.get("/api/v1/templates", params={"category": "marketing"})).json()
    assert body["instructions"].endswith(catalog.RUN_RULES.rstrip())
    assert body["suggested_tools"] == list(catalog.MUAPI_TOOLS)
    assert body["setup_depth"] == "standard"
    assert [r["id"] for r in body["routines"]] == [
        "seo-keyword-brief", "seo-draft-article", "seo-rank-check", "seo-ai-visibility",
        "seo-competitor-gap", "seo-backlink-opportunities", "seo-site-check", "seo-seasonal-calendar",
    ]
    assert all(r["enabled_after_install"] is False for r in body["routines"])
    assert listing["version"] == catalog.CATALOG_VERSION and "marketing" in listing["categories"]
    assert [x["id"] for x in listing["templates"]] == ["marketing-seo", "marketing-social-media", "marketing-ad-manager"]
