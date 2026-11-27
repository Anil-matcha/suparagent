"""Real engine usage limits and quota windows.

Reads actual provider usage limits (e.g. Google CloudCode PA internal quota
for Antigravity) without ever inventing dummy data.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.services import antigravity_runtime as runtime

logger = logging.getLogger(__name__)

# Cache access tokens and usage reports in memory to avoid redundant Google API calls
_ACCESS_CACHE: dict[str, tuple[str, float]] = {}  # token_path -> (access_token, expires_at)
_USAGE_CACHE: dict[str, tuple[dict[str, Any] | None, float]] = {}  # engine_id -> (report, expires_at)

DAILY_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
PROD_ENDPOINT = "https://cloudcode-pa.googleapis.com"
PERSONAL_ENDPOINTS = [DAILY_ENDPOINT, PROD_ENDPOINT]


def _find_antigravity_token_file() -> Path | None:
    try:
        profiles_dir = runtime.data_root() / "profiles"
        if not profiles_dir.exists():
            return None
        # Look for acp_token.json in any profile folder
        for token_file in sorted(profiles_dir.glob("*/antigravity-acp/acp_token.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if token_file.is_file() and token_file.stat().st_size <= 1024 * 1024:
                return token_file
    except Exception as exc:
        logger.debug("Could not search for antigravity token file: %s", exc)
    return None


async def _get_antigravity_access_token(token_file: Path) -> str | None:
    cache_key = str(token_file)
    now = time.time()
    cached = _ACCESS_CACHE.get(cache_key)
    if cached and cached[1] > now + 60:
        return cached[0]

    try:
        raw = token_file.read_text(encoding="utf-8")
        creds = json.loads(raw)
        client_id = creds.get("client_id")
        client_secret = creds.get("client_secret")
        refresh_token = creds.get("refresh_token")
        if not client_id or not client_secret or not refresh_token:
            return None

        token_url = creds.get("token_uri") or "https://oauth2.googleapis.com/token"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                logger.warning("Antigravity token refresh failed: status %s", resp.status_code)
                return None
            data = resp.json()
            access_token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            if access_token and isinstance(access_token, str):
                _ACCESS_CACHE[cache_key] = (access_token, now + float(expires_in))
                return access_token
    except Exception as exc:
        logger.warning("Error refreshing antigravity access token: %s", exc)
    return None


async def get_antigravity_usage_limits() -> dict[str, Any] | None:
    now = time.time()
    cached = _USAGE_CACHE.get("antigravity")
    if cached and cached[1] > now:
        return cached[0]

    token_file = _find_antigravity_token_file()
    if not token_file:
        return None

    access_token = await _get_antigravity_access_token(token_file)
    if not access_token:
        return None

    try:
        creds = json.loads(token_file.read_text(encoding="utf-8"))
        project_id = creds.get("project_id")
    except Exception:
        project_id = None

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "antigravity/hub/2.24.0 (aidev_client; os_type=win32; arch=x86_64; cl=963137146)",
        "Content-Type": "application/json",
    }
    body = json.dumps({"project": project_id} if project_id else {})

    quota_data = None
    async with httpx.AsyncClient(timeout=8.0) as client:
        for ep in PERSONAL_ENDPOINTS:
            try:
                url = f"{ep}/v1internal:retrieveUserQuotaSummary"
                res = await client.post(url, headers=headers, content=body)
                if res.status_code == 200:
                    quota_data = res.json()
                    break
            except Exception as exc:
                logger.debug("Quota check failed on %s: %s", ep, exc)

    if not quota_data or not isinstance(quota_data, dict):
        return None

    groups = quota_data.get("groups")
    if not isinstance(groups, list):
        return None

    windows: list[dict[str, Any]] = []
    # Primary group is usually the first (Gemini Models)
    for group in groups:
        if not isinstance(group, dict):
            continue
        buckets = group.get("buckets")
        if not isinstance(buckets, list):
            continue

        for bucket in buckets:
            if not isinstance(bucket, dict) or bucket.get("disabled") is True:
                continue
            bucket_id = str(bucket.get("bucketId", ""))
            remaining = bucket.get("remainingFraction")
            if remaining is None or not isinstance(remaining, (int, float)):
                continue

            left_percent = max(0, min(100, round(float(remaining) * 100)))
            used_percent = 100 - left_percent
            reset_time = bucket.get("resetTime")

            # Determine user-friendly label
            window_name = "Quota"
            order = 10
            if "5h" in bucket_id or bucket.get("window") == "5h":
                if "gemini" in bucket_id:
                    window_name = "Gemini 5-hour"
                    order = 1
                else:
                    window_name = "Claude & GPT 5-hour"
                    order = 3
            elif "weekly" in bucket_id or bucket.get("window") == "weekly":
                if "gemini" in bucket_id:
                    window_name = "Gemini weekly"
                    order = 2
                else:
                    window_name = "Claude & GPT weekly"
                    order = 4
            elif bucket.get("displayName"):
                window_name = str(bucket["displayName"])

            windows.append({
                "name": window_name,
                "left_percent": left_percent,
                "used_percent": used_percent,
                "resets_at": reset_time if isinstance(reset_time, str) else None,
                "_order": order,
            })

    if not windows:
        return None

    # Sort windows so Gemini 5-hour and Gemini weekly come first
    windows.sort(key=lambda w: w["_order"])
    # Strip internal sort key
    for w in windows:
        w.pop("_order", None)

    # Primary window: the Gemini 5-hour window or the window with the lowest left_percent
    five_hour = next((w for w in windows if "5-hour" in w["name"]), windows[0])
    primary_left = five_hour["left_percent"]
    tone = "out" if primary_left <= 0 else "near" if primary_left <= 20 else "fine"

    user_email = None
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            ui_res = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if ui_res.status_code == 200:
                user_email = ui_res.json().get("email")
    except Exception as exc:
        logger.debug("Could not fetch userinfo email: %s", exc)

    report = {
        "engine": "antigravity",
        "name": "Antigravity",
        "user_email": user_email,
        "checked_at": int(now * 1000),
        "windows": windows,
        "primary_left_percent": primary_left,
        "tone": tone,
    }

    # Cache report for 30 seconds
    _USAGE_CACHE["antigravity"] = (report, now + 30)
    return report


async def get_engine_usage_limits(engine_id: str) -> dict[str, Any] | None:
    """Return real usage limits for the given engine if it reports them.

    Returns None if the engine is unsupported, not signed in, or reports no
    limits.
    """
    id_lower = engine_id.lower()
    if "antigravity" in id_lower or "gemini" in id_lower or "google" in id_lower:
        return await get_antigravity_usage_limits()
    # Other engines (local, api keys, unsigned-in CLIs) report no usage limits
    return None
