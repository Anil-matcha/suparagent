"""Remote MCP client parsing and the tool-list bounds."""

import json

import pytest

from app import community_mcp
from app.community_mcp import McpError, RemoteMcpSession, _normalize_tool, validate_arguments


def session():
    return RemoteMcpSession("https://example.com/mcp")


def test_a_notification_before_the_result_is_ignored_but_a_server_request_is_refused():
    note = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
    result = {"jsonrpc": "2.0", "id": 3, "result": {"ok": True}}
    body = f"data: {json.dumps(note)}\n\ndata: {json.dumps(result)}\n\n".encode()
    assert session()._parse_rpc(body, "text/event-stream", 3) == {"ok": True}
    request = {"jsonrpc": "2.0", "id": 9, "method": "sampling/createMessage"}
    with pytest.raises(McpError) as caught:
        session()._parse_rpc(json.dumps(request).encode(), "application/json", 3)
    assert caught.value.code == "unsupported_protocol"


@pytest.mark.asyncio
async def test_one_unsupported_tool_is_skipped_and_the_rest_are_listed(monkeypatch):
    page = {"tools": [
        {"name": "ok", "description": "fine", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "fancy", "inputSchema": {"type": "object", "properties": {"x": {"anyOf": [{"type": "string"}]}}}},
    ]}
    s = session()

    async def rpc(method, params=None):
        return page

    monkeypatch.setattr(s, "_rpc", rpc)
    assert [t["name"] for t in await s.list_tools()] == ["ok"]


def test_schema_and_argument_validation_reject_unsafe_shapes():
    with pytest.raises(McpError):
        _normalize_tool({"name": "t", "inputSchema": {"type": "object", "$ref": "#/x"}})
    tool = _normalize_tool({"name": "t", "inputSchema": {
        "type": "object", "properties": {"n": {"type": "integer", "maximum": 5}},
        "required": ["n"], "additionalProperties": False}})
    schema = tool["inputSchema"]
    validate_arguments(schema, {"n": 3})
    for bad in ({}, {"n": 9}, {"n": True}, {"n": 1, "extra": 1}):
        with pytest.raises(McpError):
            validate_arguments(schema, bad)


def test_schema_supports_bounded_uri_defaults_and_model_enums():
    schema = _normalize_tool({"name": "media", "inputSchema": {
        "type": "object", "additionalProperties": False, "properties": {
            "image_url": {"type": "string", "format": "uri"},
            "model": {"type": "string", "enum": [f"model-{n}" for n in range(217)], "default": "model-0"},
            "duration": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
            "enabled": {"type": "boolean", "default": False},
        }}})["inputSchema"]
    assert schema["properties"]["image_url"]["format"] == "uri"
    assert schema["properties"]["model"]["default"] == "model-0"
    validate_arguments(schema, {"image_url": "https://cdn.example/image.png"})
    validate_arguments(schema, {"image_url": "https://cdn.example/image.png", "duration": 5})
    for bad in (
        {"image_url": "http://cdn.example/image.png"},
        {"image_url": "https://localhost/image.png"},
        {"image_url": "https://cdn.example/image.png", "duration": 6},
        {"image_url": "https://cdn.example/image.png", "model": "unknown"},
    ):
        with pytest.raises(McpError):
            validate_arguments(schema, bad)


def test_schema_rejects_unsafe_formats_defaults_and_oversized_enums():
    for input_schema in (
        {"type": "object", "properties": {"x": {"type": "string", "format": "uri-reference"}}},
        {"type": "object", "properties": {"x": {"type": "string", "default": 3}}},
        {"type": "object", "properties": {"x": {"type": "integer", "minimum": 2, "default": 1}}},
        {"type": "object", "properties": {"x": {"type": "object", "default": {}}}},
        {"type": "object", "properties": {"x": {"type": "string", "enum": [f"model-{n}" for n in range(257)]}}},
    ):
        with pytest.raises(McpError):
            _normalize_tool({"name": "unsafe", "inputSchema": input_schema})


@pytest.mark.parametrize("url", [
    "http://example.com/mcp", "https://localhost/mcp", "https://10.0.0.1/mcp",
    "https://user:pw@example.com/mcp", "https://example.com:8443/mcp", "https://example.com/mcp?x=1",
    "https://[::1]/mcp", "https://169.254.169.254/mcp",
])
def test_unsafe_endpoints_are_refused(url):
    with pytest.raises(McpError):
        community_mcp._validate_public_url(url, "endpoint", require_standard_port=True)


@pytest.mark.asyncio
async def test_an_invalid_live_catalog_is_not_hidden_by_bundled_data(monkeypatch):
    async def invalid(path, *, limit):
        raise McpError("invalid_catalog", "bad", 502)

    monkeypatch.setattr(community_mcp, "_catalog_cache", None)
    monkeypatch.setattr(community_mcp, "_raw_file", invalid)
    with pytest.raises(McpError) as caught:
        await community_mcp._get_catalog_data()
    assert caught.value.code == "invalid_catalog"


def test_the_credential_is_redacted_in_common_encodings():
    from app.services.community_mcp_bridge import _redact
    import base64
    secret = 'tok/en"1'
    text = f"{secret} tok%2Fen%221 {base64.b64encode(secret.encode()).decode()} tok/en\\\"1"
    assert secret not in _redact(text, secret) and "tok%2Fen" not in _redact(text, secret)


@pytest.mark.asyncio
async def test_the_servers_own_name_is_kept_as_bounded_display_text(monkeypatch):
    s = session()

    async def post(message, *, expect_result):
        if message.get("method") == "initialize":
            return {"protocolVersion": "2025-06-18", "serverInfo": {"name": "  Notes  " + "x" * 300, "version": 7}}
        return None

    monkeypatch.setattr(s, "_post", post)
    await s.start()
    assert s.server_info["name"].startswith("Notes") and len(s.server_info["name"]) == 100
    assert s.server_info["version"] is None


# A tool shaped like the ones AgentPhone's hosted server returned on a live account (2026-11-17): the newer
# `execution` field sits beside `annotations`, and every one of its 35 tools carried it.
AGENTPHONE_TOOL = {
    "name": "list_numbers", "title": "List numbers", "description": "List phone numbers",
    "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer"}, "offset": {"type": "integer"}}},
    "outputSchema": {"type": "object", "properties": {}},
    "annotations": {"readOnlyHint": True, "idempotentHint": True, "destructiveHint": False, "openWorldHint": False},
    "execution": {"taskSupport": "forbidden"},
}


def test_a_tool_with_the_newer_execution_and_icons_fields_is_accepted_and_they_are_not_kept():
    tool = _normalize_tool({**AGENTPHONE_TOOL, "icons": [{"src": "https://example.com/i.png"}]})
    assert tool["name"] == "list_numbers" and set(tool) == {"name", "description", "inputSchema", "schema_digest"}


def test_an_unknown_tool_field_is_still_refused():
    with pytest.raises(McpError):
        _normalize_tool({**AGENTPHONE_TOOL, "surprise": {"run": "this"}})


@pytest.mark.asyncio
async def test_a_server_whose_tools_all_have_the_newer_fields_lists_them(monkeypatch):
    s = session()

    async def rpc(method, params=None):
        return {"tools": [AGENTPHONE_TOOL, {**AGENTPHONE_TOOL, "name": "get_usage"}]}

    monkeypatch.setattr(s, "_rpc", rpc)
    assert [t["name"] for t in await s.list_tools()] == ["list_numbers", "get_usage"]


@pytest.mark.asyncio
async def test_a_server_with_tools_none_of_which_can_be_read_is_an_error_not_zero_tools(monkeypatch):
    s = session()

    async def rpc(method, params=None):
        return {"tools": [{**AGENTPHONE_TOOL, "surprise": 1}, {**AGENTPHONE_TOOL, "name": "other", "surprise": 1}]}

    monkeypatch.setattr(s, "_rpc", rpc)
    with pytest.raises(McpError) as caught:
        await s.list_tools()
    assert caught.value.code == "unsupported_tools" and "2 tools" in str(caught.value)


@pytest.mark.asyncio
async def test_a_server_that_really_has_no_tools_still_lists_none(monkeypatch):
    s = session()

    async def rpc(method, params=None):
        return {"tools": []}

    monkeypatch.setattr(s, "_rpc", rpc)
    assert await s.list_tools() == []


def test_bounds_up_to_the_largest_safe_javascript_integer_are_accepted_and_larger_ones_refused():
    big = {"name": "t", "inputSchema": {"type": "object", "properties": {
        "offset": {"type": "integer", "minimum": 0, "maximum": 9007199254740991}}}}
    assert _normalize_tool(big)["inputSchema"]["properties"]["offset"]["maximum"] == 9007199254740991
    too_big = {"name": "t", "inputSchema": {"type": "object", "properties": {
        "offset": {"type": "integer", "maximum": 9007199254740992}}}}
    with pytest.raises(McpError):
        _normalize_tool(too_big)
    for bad in (float("inf"), True, "10"):
        with pytest.raises(McpError):
            _normalize_tool({"name": "t", "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "maximum": bad}}}})


def test_the_no_op_property_names_rule_is_accepted_and_dropped_but_a_real_one_is_refused():
    plain = {"name": "t", "inputSchema": {"type": "object", "properties": {
        "variables": {"type": "object", "propertyNames": {"type": "string"}, "additionalProperties": True}}}}
    assert "propertyNames" not in _normalize_tool(plain)["inputSchema"]["properties"]["variables"]
    for rule in ({"type": "string", "pattern": "^a+$"}, {"type": "string", "maxLength": 3}, {}, "x"):
        strict = {"name": "t", "inputSchema": {"type": "object", "properties": {
            "variables": {"type": "object", "propertyNames": rule}}}}
        with pytest.raises(McpError):
            _normalize_tool(strict)


# ---- typed key/value maps (`additionalProperties` given as a schema), as AgentPhone's send_message/make_call use ----

def _map_tool(extra, **more):
    return {"name": "make_call", "inputSchema": {"type": "object", "required": ["to"], "properties": {
        "to": {"type": "string"},
        "variables": {"type": "object", "propertyNames": {"type": "string"}, "additionalProperties": extra, **more}}}}


def test_a_map_of_text_values_is_accepted_and_its_values_are_checked():
    tool = _normalize_tool(_map_tool({"type": "string", "maxLength": 5}))
    schema = tool["inputSchema"]
    assert schema["properties"]["variables"]["additionalProperties"]["type"] == "string"
    validate_arguments(schema, {"to": "+1", "variables": {"name": "Ada", "city": "Paris"}})
    for bad in ({"n": 5}, {"n": "toolong"}, {"n": None}, {"n": ["x"]}, {"n": {"a": "b"}}):
        with pytest.raises(McpError):
            validate_arguments(schema, {"to": "+1", "variables": bad})


def test_an_empty_value_schema_means_any_value_like_true():
    schema = _normalize_tool(_map_tool({}))["inputSchema"]
    assert schema["properties"]["variables"]["additionalProperties"] is True
    validate_arguments(schema, {"to": "+1", "variables": {"a": 1, "b": [1], "c": {"d": None}}})


def test_listed_properties_keep_their_own_schema_and_only_unlisted_keys_use_the_map_schema():
    tool = {"name": "t", "inputSchema": {"type": "object", "properties": {"count": {"type": "integer"}},
                                        "additionalProperties": {"type": "string"}}}
    schema = _normalize_tool(tool)["inputSchema"]
    validate_arguments(schema, {"count": 3, "note": "hi"})
    with pytest.raises(McpError):
        validate_arguments(schema, {"count": "3"})
    with pytest.raises(McpError):
        validate_arguments(schema, {"count": 3, "note": 4})


@pytest.mark.parametrize("extra", [
    {"type": "string", "pattern": "^a+$"},          # a server-supplied regex is never run here
    {"anyOf": [{"type": "string"}, {"type": "null"}]},
    {"$ref": "#/definitions/x"},
    {"type": "object", "additionalProperties": {"type": "string", "pattern": "x"}},
    "string", 5, [],
])
def test_other_dynamic_shapes_are_still_refused(extra):
    with pytest.raises(McpError):
        _normalize_tool(_map_tool(extra))


def test_a_map_value_schema_is_depth_limited_like_any_other():
    nested = {"type": "string"}
    for _ in range(community_mcp.MAX_DEPTH + 2):
        nested = {"type": "object", "additionalProperties": nested}
    with pytest.raises(McpError):
        _normalize_tool(_map_tool(nested))


def test_a_value_schema_on_a_non_object_is_refused():
    with pytest.raises(McpError):
        _normalize_tool({"name": "t", "inputSchema": {"type": "object", "properties": {
            "x": {"type": "string", "additionalProperties": {"type": "string"}}}}})
