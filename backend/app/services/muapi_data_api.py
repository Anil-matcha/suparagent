"""First-party MuAPI data API client.

The API key is sent only to MuAPI's fixed HTTPS API origin. This client exposes
the read-only data API domains used by worker templates and capped image, video
and audio generation; it does not use the MuAPI Community MCP endpoint or the
cloud-computer credential store.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import weakref
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import settings

API_ROOT = "https://api.muapi.ai/api/v1"
# MuAPI's public GET /api/v1/models and /models/{name} expose a `domain` field
# (seo, lead_enrichment, scraping, market_data, social, sandbox; "ai" for
# generation models). `family` is a model grouping, not the domain. Only these
# read-only domains are allowed; social publishing and sandbox control are not.
DATA_DOMAINS = frozenset({"seo", "lead_enrichment", "scraping", "market_data"})
# MuAPI's own server reports this endpoint, filed under lead_enrichment, as web research.
DATA_EXTRA_NAMES = {"research-web-answer": "web_research"}
SEARCH_DOMAINS = DATA_DOMAINS | frozenset({"web_research"})
# Generation models carry domain "ai" and one of these categories.
MEDIA_KINDS = {
    "image": frozenset({"Text to Image", "Image to Image"}),
    "video": frozenset({"Text to Video", "Image to Video", "Video to Video", "Audio to Video"}),
    "audio": frozenset({"Text to Audio"}),
}
API_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PREDICTION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
MAX_RESPONSE_BYTES = 1_000_000
MAX_REQUEST_BYTES = 64_000

TOOL_DEFINITIONS = (
    {
        "name": "muapi_data_api_search",
        "description": "Search MuAPI's live data model catalog (SEO, lead enrichment, scraping, market data, web research). Returns current input fields and price metadata. Publishing and sandbox-control endpoints are excluded.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
            "query": {"type": "string", "maxLength": 200,
                      "description": "Task or endpoint search, such as backlinks, company enrichment or public posts."},
            "domain": {"type": "string",
                       "enum": ["seo", "lead_enrichment", "scraping", "market_data", "web_research"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        }},
    },
    {
        "name": "muapi_data_api_inspect",
        "description": "Inspect one searched MuAPI SEO/data model for its current full input and output schemas, endpoint family, and pricing. This is read-only and free.",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["api_name"],
                         "properties": {"api_name": {"type": "string", "maxLength": 128,
                                                        "description": "Model name returned by muapi_data_api_search."}}},
    },
    {
        "name": "muapi_data_api_estimate",
        "description": "Get MuAPI's current USD quote for one searched data API and its input before submitting it.",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["api_name", "input"],
                         "properties": {
            "api_name": {"type": "string", "maxLength": 128,
                         "description": "Model name returned by muapi_data_api_search or muapi_data_api_inspect."},
            "input": {"type": "object", "additionalProperties": True,
                      "description": "The JSON input for the selected endpoint."},
        }},
    },
    {
        "name": "muapi_data_api_call",
        "description": "Submit one supported, non-publishing MuAPI data lookup. This spends MuAPI credits. MuAPI supplies a fresh quote and the app refuses the call when it exceeds max_cost_usd. Returns a request_id to check with muapi_predict_result. Publishing and sandbox-control endpoints are excluded.",
        "inputSchema": {"type": "object", "additionalProperties": False,
                         "required": ["api_name", "input", "max_cost_usd"], "properties": {
            "api_name": {"type": "string", "maxLength": 128,
                         "description": "Model name returned by muapi_data_api_search or muapi_data_api_inspect."},
            "input": {"type": "object", "additionalProperties": True,
                      "description": "The JSON input for the selected endpoint."},
            "max_cost_usd": {"type": "number", "minimum": 0,
                              "description": "Maximum quoted USD cost allowed for this call."},
        }},
    },
    {
        "name": "muapi_media_search",
        "description": "Search MuAPI's live image, video and audio generation models. Returns each model's price metadata and input fields. Free. Use muapi_media_generate to make one item.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
            "query": {"type": "string", "maxLength": 200,
                      "description": "Model or task search, such as flux, product photo or text to video."},
            "kind": {"type": "string", "enum": ["image", "video", "audio"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
        }},
    },
    {
        "name": "muapi_media_generate",
        "description": "Generate one image, video or audio item with a model found by muapi_media_search. This spends MuAPI credits. MuAPI supplies a fresh quote and the app refuses the call when it exceeds max_cost_usd. Returns a request_id to check with muapi_predict_result.",
        "inputSchema": {"type": "object", "additionalProperties": False,
                         "required": ["api_name", "input", "max_cost_usd"], "properties": {
            "api_name": {"type": "string", "maxLength": 128,
                         "description": "Model name returned by muapi_media_search."},
            "input": {"type": "object", "additionalProperties": True,
                      "description": "The JSON input for the selected model."},
            "max_cost_usd": {"type": "number", "minimum": 0,
                              "description": "Maximum quoted USD cost allowed for this item."},
        }},
    },
    {
        "name": "muapi_predict_result",
        "description": "Fetch the current status and result of an asynchronous MuAPI prediction by request ID.",
        "inputSchema": {"type": "object", "additionalProperties": False, "required": ["request_id"],
                         "properties": {"request_id": {"type": "string", "maxLength": 128,
                                                          "description": "Prediction request ID returned by MuAPI."}}},
    },
)
TOOL_BY_NAME = {tool["name"]: tool for tool in TOOL_DEFINITIONS}
_lock = asyncio.Lock()


def connection_lock() -> asyncio.Lock:
    """Serialize MuAPI key changes and direct requests in this server process."""
    return _lock


_agent_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()


def agent_grant_lock(agent_id: int) -> asyncio.Lock:
    lock = _agent_locks.get(agent_id)
    if lock is None:
        lock = asyncio.Lock()
        _agent_locks[agent_id] = lock
    return lock


def data_domain(model: dict) -> str | None:
    """The allowed read-only data domain of a model, or None (social, sandbox and generation models)."""
    extra = DATA_EXTRA_NAMES.get(model.get("name"))
    if extra and model.get("domain") == "lead_enrichment":
        return extra
    domain = model.get("domain")
    return domain if isinstance(domain, str) and domain in DATA_DOMAINS else None


def media_kind(model: dict) -> str | None:
    """image, video or audio for an ordinary generation model; None for anything else."""
    if model.get("domain") != "ai":
        return None
    name, category = model.get("name"), model.get("category")
    if not isinstance(name, str) or "spicy" in name.lower() or not isinstance(category, str):
        return None  # adult-tier models are not offered to workers
    return next((kind for kind, categories in MEDIA_KINDS.items() if category in categories), None)


def _model_endpoint(model: dict) -> str:
    """Resolve MuAPI's catalog endpoint while keeping requests on its fixed API origin."""
    raw = model.get("endpoint_url") or model.get("endpoint")
    if not isinstance(raw, str) or not raw:
        raise MuapiError("unsafe_endpoint", "MuAPI did not return a valid model endpoint.", 502, not_run=True)
    parsed = urlsplit(raw)
    if not parsed.scheme and not parsed.netloc and not raw.startswith("/"):
        # MuAPI's catalog reports endpoint_url as the bare model path, such as "seo-google-serp".
        parsed = urlsplit("/api/v1/" + raw)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc.lower() != "api.muapi.ai":
            raise MuapiError("unsafe_endpoint", "MuAPI returned an endpoint outside its API origin.", 502,
                             not_run=True)
    path = parsed.path
    if parsed.query or parsed.fragment or not path.startswith("/api/v1/"):
        raise MuapiError("unsafe_endpoint", "MuAPI did not return a valid model endpoint.", 502, not_run=True)
    relative = path[len("/api/v1/"):]
    if (not relative or any(part in ("", ".", "..") for part in relative.split("/"))
            or re.fullmatch(r"[A-Za-z0-9._/-]+", relative) is None):
        raise MuapiError("unsafe_endpoint", "MuAPI did not return a valid model endpoint.", 502, not_run=True)
    return relative


def _small(value: object, depth: int = 0):
    if depth >= 5:
        return "[nested value omitted]"
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_small(item, depth + 1) for item in value[:30]]
    if isinstance(value, dict):
        return {str(key)[:100]: _small(item, depth + 1) for key, item in list(value.items())[:30]}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def _clean_input(value: object):
    """Drop empty strings, empty lists and nulls; MuAPI's validators reject them."""
    if isinstance(value, dict):
        cleaned = {key: _clean_input(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if not _is_empty(item)}
    if isinstance(value, list):
        return [item for item in (_clean_input(item) for item in value) if not _is_empty(item)]
    return value


def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def _input_summary(schema: object) -> dict | None:
    """Flat field list (type, allowed values, default) that survives the depth limit of _small."""
    try:
        data = schema["schemas"]["input_data"]  # type: ignore[index]
        properties = data["properties"]
    except (KeyError, TypeError):
        return None
    if not isinstance(properties, dict):
        return None
    fields = {}
    for name, spec in list(properties.items())[:30]:
        if not isinstance(spec, dict):
            continue
        entry = {"type": spec.get("type") if isinstance(spec.get("type"), str) else None}
        if isinstance(spec.get("enum"), list):
            entry["allowed_values"] = [item for item in spec["enum"][:20] if isinstance(item, (str, int, float, bool))]
        if "default" in spec and isinstance(spec["default"], (str, int, float, bool)):
            entry["default"] = spec["default"]
        if isinstance(spec.get("description"), str):
            entry["description"] = spec["description"][:300]
        fields[str(name)[:100]] = entry
    required = data.get("required") if isinstance(data, dict) else None
    return {"fields": fields,
            "required": [item for item in (required or [])[:30] if isinstance(item, str)]}


def valid_api_name(value: object) -> bool:
    return isinstance(value, str) and API_NAME.fullmatch(value) is not None


def _quote(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


class MuapiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, *, not_run: bool = False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.not_run = not_run


async def _read_failed_result(response: httpx.Response) -> dict | None:
    """MuAPI answers a failed prediction with 400 and {"detail": {"status": "failed", "error": ...}}."""
    raw = b""
    async for chunk in response.aiter_bytes():
        raw += chunk
        if len(raw) > 16_000:
            return None
    try:
        detail = json.loads(raw).get("detail")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    if not isinstance(detail, dict) or detail.get("status") != "failed":
        return None
    return {"id": str(detail.get("id") or "")[:128], "status": "failed",
            "error": str(detail.get("error") or "The request failed.")[:500]}


class MuapiDataApi:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    async def close(self) -> None:
        return None

    async def _request(self, method: str, path: str, *, body: dict | None = None,
                       timeout: float = 30.0, failed_result_ok: bool = False) -> object:
        url = f"{API_ROOT}/{path.lstrip('/')}"
        may_spend = method == "POST" and not path.endswith("/estimate-cost")
        if not url.startswith(API_ROOT + "/"):
            raise MuapiError("unsafe_endpoint", "The MuAPI request was refused.", 400, not_run=True)
        try:
            raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() if body is not None else b""
        except (TypeError, ValueError):
            raise MuapiError("bad_arguments", "The MuAPI input must be valid JSON.", 422, not_run=True) from None
        if len(raw_body) > MAX_REQUEST_BYTES:
            raise MuapiError("request_too_large", "The MuAPI request is too large.", 413, not_run=True)
        headers = {"x-api-key": self.api_key, "accept": "application/json"}
        if body is not None:
            headers["content-type"] = "application/json"
        try:
            async with httpx.AsyncClient(timeout=timeout, verify=settings.SSL_CERT_FILE or True,
                                         follow_redirects=False, trust_env=False) as client:
                async with asyncio.timeout(timeout + 2):
                    async with client.stream(method, url, headers=headers, json=body) as response:
                        if response.is_redirect or 300 <= response.status_code < 400:
                            raise MuapiError("redirect_denied", "MuAPI returned an unexpected redirect.", 502,
                                             not_run=True)
                        if failed_result_ok and response.status_code == 400:
                            failed = await _read_failed_result(response)
                            if failed is not None:
                                return failed
                        if response.status_code >= 400:
                            status = response.status_code
                            code = "authentication_failed" if status in (401, 403) else f"muapi_http_{status}"
                            not_run = not may_spend or status in (400, 401, 402, 403, 404, 409, 422, 429)
                            message = ("MuAPI rejected the saved API key." if status in (401, 403) else
                                       "MuAPI rejected the request." if not_run else
                                       "MuAPI did not confirm whether the request completed.")
                            raise MuapiError(code, message, status, not_run=not_run)
                        pieces: list[bytes] = []
                        size = 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise MuapiError("response_too_large", "MuAPI returned a response that is too large.",
                                                 502, not_run=False)
                            pieces.append(chunk)
        except MuapiError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise MuapiError("timeout", "MuAPI did not answer in time. The call may have run.", 504,
                             not_run=not may_spend) from None
        except httpx.HTTPError:
            raise MuapiError("connect_failed", "Could not reach MuAPI. Check the connection and try again.", 502,
                             not_run=True) from None
        content = b"".join(pieces)
        if response.status_code == 204 or not content:
            return {}
        try:
            return json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MuapiError("invalid_response", "MuAPI returned an unreadable response.", 502) from None

    async def validate_key(self) -> None:
        result = await self._request("GET", "account/balance")
        if not isinstance(result, dict):
            raise MuapiError("invalid_response", "MuAPI returned an unreadable account balance.", 502)

    async def _models(self) -> list[dict]:
        result = await self._request("GET", "models")
        models = result.get("models") if isinstance(result, dict) else None
        if not isinstance(models, list):
            raise MuapiError("invalid_response", "MuAPI returned an unreadable model catalog.", 502)
        return [item for item in models if isinstance(item, dict)]

    async def _model(self, name: str, *, media: bool = False) -> dict:
        if not valid_api_name(name):
            raise MuapiError("bad_arguments", "Choose an API name returned by MuAPI search.", 422, not_run=True)
        result = await self._request("GET", "models/" + quote(name, safe=""))
        allowed = media_kind if media else data_domain
        if (not isinstance(result, dict) or result.get("name") != name or not allowed(result)
                or result.get("is_coming_soon") or result.get("is_enabled") in (0, False)):
            raise MuapiError("unsupported_api",
                             "This is not an available MuAPI " + ("media model." if media else "data API."),
                             404, not_run=True)
        return result

    async def _data_model(self, name: str) -> dict:
        return await self._model(name)

    async def _estimate(self, name: str, payload: dict, *, media: bool = False) -> tuple[dict, dict]:
        model = await self._model(name, media=media)
        if model.get("dynamic_pricing") is True:
            result = await self._request("POST", f"models/{quote(name, safe='')}/estimate-cost", body=payload)
        else:
            result = {"cost": model.get("cost"), "currency": model.get("cost_currency", "USD")}
        if not isinstance(result, dict):
            raise MuapiError("invalid_quote", "MuAPI returned an invalid cost quote.", 502)
        price = _quote(result.get("cost"))
        currency = str(result.get("currency", "USD")).upper()
        if price is None or currency != "USD":
            raise MuapiError("invalid_quote", "MuAPI returned an invalid USD cost quote.", 502)
        return {**result, "cost": price, "currency": "USD"}, model

    async def _submit(self, model: dict, estimate: dict, payload: dict, raw_max_cost: object) -> dict:
        """Submit a quoted model call unless the fresh quote is above the cap. Nothing is sent when it is."""
        max_cost = _quote(raw_max_cost)
        cost = _quote(estimate.get("cost"))
        if max_cost is None or cost is None:
            raise MuapiError("bad_arguments", "Give a valid non-negative max_cost_usd.", 422, not_run=True)
        if cost > max_cost:
            raise MuapiError("quote_over_cap", f"MuAPI quoted ${cost:.6f} USD, above the approved cap; no request was submitted.",
                             422, not_run=True)
        submission = await self._request("POST", _model_endpoint(model), body=payload, timeout=90.0)
        if not isinstance(submission, dict):
            submission = {"result": submission}
        request_id = submission.get("request_id") or submission.get("id")
        if not isinstance(request_id, str) or PREDICTION_ID.fullmatch(request_id) is None:
            raise MuapiError("invalid_submission", "MuAPI did not return a valid request ID. The call may have run.",
                             502)
        actual_cost = submission.get("cost")
        actual_cost = _quote(actual_cost.get("amount_usd")) if isinstance(actual_cost, dict) else None
        return {"quoted_cost_usd": cost, "cost_usd": actual_cost if actual_cost is not None else cost,
                "currency": "USD", "request_id": request_id, "submission": submission}

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        if tool_name == "muapi_data_api_search":
            query = arguments.get("query", "")
            domain = arguments.get("domain")
            limit = arguments.get("limit", 20)
            if not isinstance(query, str) or len(query) > 200 or (domain is not None and domain not in SEARCH_DOMAINS):
                raise MuapiError("bad_arguments", "Use a short search and an optional supported data domain.", 422,
                                 not_run=True)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
                raise MuapiError("bad_arguments", "The search limit must be from 1 to 50.", 422, not_run=True)
            needle = query.strip().lower()
            matches = []
            for model in await self._models():
                current_domain = data_domain(model)
                if (not valid_api_name(model.get("name")) or current_domain is None
                        or (domain and current_domain != domain) or model.get("is_coming_soon")
                        or model.get("is_enabled") in (0, False)):
                    continue
                haystack = " ".join(str(model.get(field) or "") for field in
                                     ("name", "description", "category", "domain", "endpoint", "endpoint_url")).lower()
                if needle and needle not in haystack:
                    continue
                matches.append({
                    "api_name": model.get("name"), "description": str(model.get("description") or "")[:1000],
                    "domain": current_domain, "endpoint": str(model.get("endpoint_url") or model.get("endpoint") or "")[:200], "cost": model.get("cost"),
                    "cost_currency": model.get("cost_currency", "USD"),
                    "dynamic_pricing": model.get("dynamic_pricing", False),
                    "cost_strategy": _small(model.get("cost_strategy")),
                    "input_fields": _small(model.get("input_fields", [])),
                    "required_fields": _small(model.get("required_fields", [])),
                })
            return {"apis": matches[:limit], "total_matches": len(matches)}

        if tool_name == "muapi_data_api_inspect":
            name = arguments.get("api_name")
            if not valid_api_name(name):
                raise MuapiError("bad_arguments", "Choose a model name returned by MuAPI search.", 422, not_run=True)
            model = await self._data_model(name)
            return {
                "api_name": name,
                "description": str(model.get("description") or "")[:1000],
                "category": str(model.get("category") or "")[:100],
                "family": data_domain(model), "domain": data_domain(model),
                "cost": model.get("cost"),
                "cost_currency": model.get("cost_currency", "USD"),
                "dynamic_pricing": model.get("dynamic_pricing", False),
                "cost_strategy": _small(model.get("cost_strategy")),
                "endpoint": str(model.get("endpoint_url") or model.get("endpoint") or "")[:300],
                "input_summary": _input_summary(model.get("input_schema")),
                "input_schema": _small(model.get("input_schema")),
                "output_schema": _small(model.get("output_schema")),
            }

        if tool_name in ("muapi_data_api_estimate", "muapi_data_api_call"):
            name, payload = arguments.get("api_name"), arguments.get("input")
            if isinstance(payload, dict):
                payload = _clean_input(payload)
            if not valid_api_name(name) or not isinstance(payload, dict):
                raise MuapiError("bad_arguments", "Choose a searched API and provide its JSON input.", 422,
                                 not_run=True)
            estimate, model = await self._estimate(name, payload)
            if tool_name == "muapi_data_api_estimate":
                return estimate
            return await self._submit(model, estimate, payload, arguments.get("max_cost_usd"))

        if tool_name == "muapi_media_search":
            query, kind, limit = arguments.get("query", ""), arguments.get("kind"), arguments.get("limit", 20)
            if not isinstance(query, str) or len(query) > 200 or (kind is not None and kind not in MEDIA_KINDS):
                raise MuapiError("bad_arguments", "Use a short search and an optional kind: image, video or audio.",
                                 422, not_run=True)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
                raise MuapiError("bad_arguments", "The search limit must be from 1 to 50.", 422, not_run=True)
            needle = query.strip().lower()
            matches = []
            for model in await self._models():
                current_kind = media_kind(model)
                if (not valid_api_name(model.get("name")) or current_kind is None or (kind and current_kind != kind)
                        or model.get("is_coming_soon") or model.get("is_enabled") in (0, False)):
                    continue
                haystack = " ".join(str(model.get(field) or "") for field in ("name", "description", "category")).lower()
                if needle and needle not in haystack:
                    continue
                matches.append({
                    "api_name": model.get("name"), "kind": current_kind,
                    "description": str(model.get("description") or "")[:500],
                    "cost": model.get("cost"), "cost_currency": model.get("cost_currency", "USD"),
                    "dynamic_pricing": model.get("dynamic_pricing", False),
                    "cost_strategy": _small(model.get("cost_strategy")),
                    "input_fields": _small(model.get("input_fields", [])),
                    "required_fields": _small(model.get("required_fields", [])),
                })
            return {"models": matches[:limit], "total_matches": len(matches)}

        if tool_name == "muapi_media_generate":
            name, payload = arguments.get("api_name"), arguments.get("input")
            if isinstance(payload, dict):
                payload = _clean_input(payload)
            if not valid_api_name(name) or not isinstance(payload, dict):
                raise MuapiError("bad_arguments", "Choose a searched media model and provide its JSON input.", 422,
                                 not_run=True)
            if _quote(arguments.get("max_cost_usd")) is None:
                raise MuapiError("bad_arguments", "Give a valid non-negative max_cost_usd.", 422, not_run=True)
            estimate, model = await self._estimate(name, payload, media=True)
            return await self._submit(model, estimate, payload, arguments.get("max_cost_usd"))

        if tool_name == "muapi_predict_result":
            request_id = arguments.get("request_id")
            if not isinstance(request_id, str) or PREDICTION_ID.fullmatch(request_id) is None:
                raise MuapiError("bad_arguments", "Give a prediction request ID returned by MuAPI.", 422,
                                 not_run=True)
            result = await self._request("GET", f"predictions/{quote(request_id, safe='')}/result",
                                         failed_result_ok=True)
            return result if isinstance(result, dict) else {"result": result}

        raise MuapiError("unknown_tool", "That MuAPI tool is not available.", 404, not_run=True)
