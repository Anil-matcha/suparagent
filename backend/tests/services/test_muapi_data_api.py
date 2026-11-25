"""MuAPI client: widened read-only data domains and the capped media tools."""

import pytest

from app.routers.muapi import MuapiGrantRequest
from app.services import muapi_data_api as api


def model(name, domain, category="Text to Text", **extra):
    # Shape of MuAPI's public GET /api/v1/models: `domain` is the domain, `family` only a grouping.
    return {"name": name, "domain": domain, "family": "enrichment", "category": category, "cost": 0.01,
            "cost_currency": "USD", "dynamic_pricing": False, "is_enabled": 1, "is_coming_soon": False,
            "endpoint": f"/api/v1/{name}", "endpoint_url": name, "description": f"{name} model",
            "input_fields": ["prompt"], "required_fields": ["prompt"], **extra}


CATALOG = [
    model("seo-google-serp", "seo"), model("company-enrich", "lead_enrichment"),
    model("social-read-posts", "scraping"), model("market-stock-history", "market_data"),
    model("research-web-answer", "lead_enrichment"),
    model("youtube-publish", "social", "other"), model("sandbox-run-command", "sandbox"),
    model("flux-dev", "ai", "Text to Image", cost=0.5), model("veo-video", "ai", "Text to Video"),
    model("mmaudio", "ai", "Text to Audio"), model("img-spicy", "ai", "Text to Image"),
    model("llm-chat", "ai", "Text to Text"),
]


class Fake(api.MuapiDataApi):
    def __init__(self, models=CATALOG, quote=0.25, dynamic=False):
        super().__init__("key")
        self.models, self.quote, self.dynamic, self.calls = models, quote, dynamic, []

    async def _request(self, method, path, *, body=None, timeout=30.0):
        self.calls.append((method, path))
        if method == "GET" and path == "models":
            return {"models": self.models}
        if method == "GET" and path.startswith("models/"):
            name = path.split("/", 1)[1]
            found = next((m for m in self.models if m["name"] == name), None)
            if found is None:
                raise api.MuapiError("muapi_http_404", "nope", 404, not_run=True)
            return {**found, "dynamic_pricing": self.dynamic, "cost": self.quote}
        if path.endswith("/estimate-cost"):
            return {"cost": self.quote, "currency": "USD"}
        return {"request_id": "req_1", "cost": {"amount_usd": self.quote}}

    def submissions(self):
        return [c for c in self.calls if c[0] == "POST" and not c[1].endswith("/estimate-cost")]


async def test_widened_domains_are_accepted_and_others_stay_out():
    found = await Fake().call_tool("muapi_data_api_search", {"limit": 50})
    names = {a["api_name"]: a["domain"] for a in found["apis"]}
    assert names == {"seo-google-serp": "seo", "company-enrich": "lead_enrichment", "social-read-posts": "scraping",
                     "market-stock-history": "market_data", "research-web-answer": "web_research"}
    only = await Fake().call_tool("muapi_data_api_search", {"domain": "web_research"})
    assert [a["api_name"] for a in only["apis"]] == ["research-web-answer"]
    with pytest.raises(api.MuapiError):
        await Fake().call_tool("muapi_data_api_search", {"domain": "social"})


@pytest.mark.parametrize("name", ["youtube-publish", "sandbox-run-command", "flux-dev", "llm-chat", "missing"])
async def test_data_tools_reject_publishing_sandbox_and_media_models(name):
    client = Fake()
    for tool in ("muapi_data_api_estimate", "muapi_data_api_call"):
        with pytest.raises(api.MuapiError) as err:
            await client.call_tool(tool, {"api_name": name, "input": {}, "max_cost_usd": 1})
        assert err.value.not_run
    assert client.submissions() == []


async def test_family_alone_no_longer_makes_a_data_model():
    assert api.data_domain({"name": "x", "family": "seo"}) is None
    assert api.data_domain({"name": "x", "domain": "seo"}) == "seo"
    assert api.data_domain({"name": "research-web-answer", "domain": "ai"}) is None


async def test_data_call_submits_to_a_bare_catalog_endpoint():
    client = Fake()
    result = await client.call_tool("muapi_data_api_call", {"api_name": "company-enrich", "input": {"a": 1}, "max_cost_usd": 1})
    assert result["request_id"] == "req_1" and client.submissions() == [("POST", "company-enrich")]


async def test_media_search_lists_only_enabled_generation_models():
    client = Fake()
    found = await client.call_tool("muapi_media_search", {"limit": 50})
    assert {m["api_name"]: m["kind"] for m in found["models"]} == {"flux-dev": "image", "veo-video": "video", "mmaudio": "audio"}
    one = await client.call_tool("muapi_media_search", {"kind": "video", "query": "veo"})
    assert [m["api_name"] for m in one["models"]] == ["veo-video"]
    assert {"cost", "cost_strategy", "input_fields", "description"} <= set(one["models"][0])
    for bad in ({"kind": "text"}, {"limit": 0}, {"query": 5}):
        with pytest.raises(api.MuapiError):
            await client.call_tool("muapi_media_search", bad)


async def test_media_generate_over_cap_makes_no_submission():
    client = Fake(quote=0.5)
    with pytest.raises(api.MuapiError) as err:
        await client.call_tool("muapi_media_generate", {"api_name": "flux-dev", "input": {"prompt": "x"}, "max_cost_usd": 0.25})
    assert err.value.code == "quote_over_cap" and err.value.not_run
    assert client.submissions() == []


async def test_media_generate_uses_a_fresh_dynamic_quote_then_submits():
    client = Fake(quote=0.2, dynamic=True)
    result = await client.call_tool("muapi_media_generate", {"api_name": "flux-dev", "input": {"prompt": "x"}, "max_cost_usd": 0.25})
    assert ("POST", "models/flux-dev/estimate-cost") in client.calls
    assert result["request_id"] == "req_1" and result["quoted_cost_usd"] == 0.2 and result["cost_usd"] == 0.2
    assert client.submissions() == [("POST", "flux-dev")]


@pytest.mark.parametrize("name", ["company-enrich", "youtube-publish", "sandbox-run-command", "llm-chat", "img-spicy", "missing", "../x"])
async def test_media_generate_rejects_non_media_models(name):
    client = Fake()
    with pytest.raises(api.MuapiError) as err:
        await client.call_tool("muapi_media_generate", {"api_name": name, "input": {}, "max_cost_usd": 5})
    assert err.value.not_run and client.submissions() == []


@pytest.mark.parametrize("args", [
    {"api_name": "flux-dev", "input": {}},
    {"api_name": "flux-dev", "input": {}, "max_cost_usd": -1},
    {"api_name": "flux-dev", "input": {}, "max_cost_usd": True},
    {"api_name": "flux-dev", "input": "x", "max_cost_usd": 1},
])
async def test_media_generate_needs_a_valid_cap_and_input(args):
    client = Fake()
    with pytest.raises(api.MuapiError) as err:
        await client.call_tool("muapi_media_generate", args)
    assert err.value.code == "bad_arguments" and client.calls == []


def test_endpoint_checks_keep_the_fixed_origin():
    assert api._model_endpoint({"endpoint_url": "seo-x"}) == "seo-x"
    assert api._model_endpoint({"endpoint_url": "https://api.muapi.ai/api/v1/seo-x"}) == "seo-x"
    for bad in ("https://evil.example/api/v1/x", "../x", "/api/v1/../x", "x?y=1", "http://api.muapi.ai/api/v1/x"):
        with pytest.raises(api.MuapiError):
            api._model_endpoint({"endpoint_url": bad})


def test_new_tools_are_registered_paid_guarded_and_grantable():
    from app.services import spend
    assert {"muapi_media_search", "muapi_media_generate"} <= set(api.TOOL_BY_NAME)
    assert spend.is_guarded(api.TOOL_BY_NAME["muapi_media_generate"]["inputSchema"])
    assert not spend.is_guarded(api.TOOL_BY_NAME["muapi_media_search"]["inputSchema"])
    assert "max_cost_usd" in api.TOOL_BY_NAME["muapi_media_generate"]["inputSchema"]["required"]
    request = MuapiGrantRequest(tools=list(api.TOOL_BY_NAME))
    assert len(request.tools) == len(api.TOOL_DEFINITIONS) == 7
