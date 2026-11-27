"""The MuAPI data client reaches the read-only lookup domains and never the publishing one."""

import pytest

from app.services import muapi_data_api as api

CATALOG = [
    {"name": "research-web-answer", "domain": "lead_enrichment", "description": "Cited web answer", "cost": 0.02},
    {"name": "seo-google-serp", "domain": "seo", "description": "Google results", "cost": 0.01},
    {"name": "ecommerce-product-detail", "domain": "market_data", "description": "Product", "cost": 0.1},
    {"name": "social-publish", "domain": "social", "description": "Publish a post", "cost": 0.0},
    {"name": "social-search-posts-copy", "domain": "other", "description": "lookalike", "cost": 0.0},
]


@pytest.fixture
def client(monkeypatch):
    c = api.MuapiDataApi("key")

    async def fake_models():
        return CATALOG

    async def fake_request(method, path, **kwargs):
        if path.startswith("models/"):
            name = path.split("/", 1)[1]
            return next((m for m in CATALOG if m["name"] == name), {})
        raise AssertionError(f"unexpected request {method} {path}")

    monkeypatch.setattr(c, "_models", fake_models)
    monkeypatch.setattr(c, "_request", fake_request)
    return c


def test_data_domain_follows_the_domain_field():
    for allowed in ("seo", "lead_enrichment", "scraping", "market_data"):
        assert api.data_domain({"name": "x", "domain": allowed}) == allowed
    assert api.data_domain({"name": "research-web-answer", "domain": "lead_enrichment"}) == "web_research"
    # The model grouping called family is not the domain and decides nothing.
    assert api.data_domain({"name": "x", "family": "seo"}) is None
    for blocked in ("social", "sandbox", "ai", "other", None):
        assert api.data_domain({"name": "research-web-answer", "domain": blocked}) is None


def test_search_schema_lists_the_read_only_domains():
    tool = api.TOOL_BY_NAME["muapi_data_api_search"]
    assert set(tool["inputSchema"]["properties"]["domain"]["enum"]) == {
        "seo", "lead_enrichment", "scraping", "market_data", "web_research"}


@pytest.mark.asyncio
async def test_search_finds_research_but_never_publishing(client):
    result = await client.call_tool("muapi_data_api_search", {"query": ""})
    names = {item["api_name"] for item in result["apis"]}
    assert names == {"research-web-answer", "seo-google-serp", "ecommerce-product-detail"}
    only = await client.call_tool("muapi_data_api_search", {"domain": "web_research"})
    assert [item["api_name"] for item in only["apis"]] == ["research-web-answer"]
    with pytest.raises(api.MuapiError):
        await client.call_tool("muapi_data_api_search", {"domain": "social"})


@pytest.mark.asyncio
async def test_inspect_accepts_research_and_refuses_other(client):
    ok = await client.call_tool("muapi_data_api_inspect", {"api_name": "research-web-answer"})
    assert ok["domain"] == "web_research"
    with pytest.raises(api.MuapiError) as err:
        await client.call_tool("muapi_data_api_inspect", {"api_name": "social-publish"})
    assert err.value.code == "unsupported_api" and err.value.not_run


@pytest.mark.parametrize("raw,expected", [
    ("research-web-answer", "research-web-answer"),
    ("/api/v1/seo-google-serp", "seo-google-serp"),
    ("https://api.muapi.ai/api/v1/seo-google-serp", "seo-google-serp"),
])
def test_endpoint_forms_resolve_inside_the_api_root(raw, expected):
    assert api._model_endpoint({"endpoint_url": raw}) == expected


@pytest.mark.parametrize("raw", [
    "../account/balance", "research/../x", "a//b", "https://evil.example/api/v1/x",
    "http://api.muapi.ai/api/v1/x", "/other/x", "x?y=1", "", "x#frag", "bad name",
])
def test_endpoint_forms_outside_the_api_root_are_refused(raw):
    with pytest.raises(api.MuapiError) as err:
        api._model_endpoint({"endpoint_url": raw})
    assert err.value.code == "unsafe_endpoint" and err.value.not_run


# ---- empty values, failed results, and the mode list ----

import httpx  # noqa: E402


def test_clean_input_drops_empty_strings_lists_and_nulls_but_keeps_zero_and_false():
    cleaned = api._clean_input({"mode": "answer", "question": "q", "query": "", "urls": [], "x": None,
                                "n": 0, "flag": False, "nested": {"a": " ", "b": [""]}, "keep": ["u", ""]})
    assert cleaned == {"mode": "answer", "question": "q", "n": 0, "flag": False, "keep": ["u"]}


@pytest.mark.asyncio
async def test_call_sends_cleaned_input(monkeypatch):
    c = api.MuapiDataApi("key")
    sent = {}
    model = {"name": "research-web-answer", "domain": "lead_enrichment", "endpoint_url": "research-web-answer",
             "dynamic_pricing": True}

    async def fake_request(method, path, body=None, **kwargs):
        if path == "models/research-web-answer":
            return model
        if path.endswith("/estimate-cost"):
            return {"cost": 0.02, "currency": "USD"}
        sent["path"], sent["body"] = path, body
        return {"request_id": "abc-123", "status": "queued"}

    monkeypatch.setattr(c, "_request", fake_request)
    out = await c.call_tool("muapi_data_api_call", {
        "api_name": "research-web-answer", "max_cost_usd": 0.05,
        "input": {"mode": "answer", "question": "Who?", "query": "", "urls": []}})
    assert sent == {"path": "research-web-answer", "body": {"mode": "answer", "question": "Who?"}}
    assert out["request_id"] == "abc-123"


def _patch_transport(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items()
                                                                                    if k in ("timeout", "follow_redirects")}))


@pytest.mark.asyncio
async def test_failed_prediction_is_returned_with_its_error(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(
        400, json={"detail": {"id": "abc-123", "status": "failed", "error": "Enrichment service authentication failed"}}))
    result = await api.MuapiDataApi("key").call_tool("muapi_predict_result", {"request_id": "abc-123"})
    assert result == {"id": "abc-123", "status": "failed", "error": "Enrichment service authentication failed"}


@pytest.mark.asyncio
async def test_other_400_on_result_is_still_an_error(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(400, json={"detail": "bad id"}))
    with pytest.raises(api.MuapiError) as err:
        await api.MuapiDataApi("key").call_tool("muapi_predict_result", {"request_id": "abc-123"})
    assert err.value.code == "muapi_http_400"


@pytest.mark.asyncio
async def test_failed_body_is_not_trusted_on_non_result_calls(monkeypatch):
    _patch_transport(monkeypatch, lambda request: httpx.Response(
        400, json={"detail": {"status": "failed", "error": "x"}}))
    with pytest.raises(api.MuapiError):
        await api.MuapiDataApi("key")._request("GET", "models")


def test_input_summary_keeps_mode_values_the_depth_limit_hides():
    schema = {"schemas": {"input_data": {"properties": {
        "mode": {"type": "string", "enum": ["answer", "search"], "default": "answer", "description": "d"},
        "urls": {"type": "array"}}, "required": ["mode"]}}}
    summary = api._input_summary(schema)
    assert summary["fields"]["mode"] == {"type": "string", "allowed_values": ["answer", "search"],
                                         "default": "answer", "description": "d"}
    assert summary["required"] == ["mode"]
    assert api._input_summary({"schemas": {}}) is None and api._input_summary("x") is None
