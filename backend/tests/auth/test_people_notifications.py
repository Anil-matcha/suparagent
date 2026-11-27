"""Notification preferences are one setting for the whole workspace: members may read it, only admins change it."""

import pytest

from tests.auth.test_auth_api import PASSPHRASE, app, auth_settings, client, error_code, factory, pairing_code  # noqa: F401
from tests.auth.test_people import clean_people, member, owner_api, write  # noqa: F401

URL = "/api/v1/notifications/preferences"


async def flipped(api):
    current = (await api.get(URL)).json()
    return {**current, "sound_enabled": not current["sound_enabled"]}, current["sound_enabled"]


async def test_a_member_can_read_but_not_change_the_shared_notification_settings(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    joined, session = await member(app, api, csrf)
    assert (await joined.get(URL)).status_code == 200
    body, before = await flipped(joined)
    refused = await joined.put(URL, json=body, headers=write(session["csrf_token"]))
    assert refused.status_code == 403 and error_code(refused) == "admin_only"
    assert (await api.get(URL)).json()["sound_enabled"] == before  # nothing changed for the owner


async def test_the_owner_and_an_admin_can_change_them(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    admin_api, admin = await member(app, api, csrf, "ann@example.com", role="admin")
    body, before = await flipped(api)
    assert (await api.put(URL, json=body, headers=write(csrf))).status_code == 200
    assert (await api.get(URL)).json()["sound_enabled"] is (not before)
    assert (await admin_api.put(URL, json={**body, "sound_enabled": before}, headers=write(admin["csrf_token"]))).status_code == 200
    assert (await api.get(URL)).json()["sound_enabled"] is before


async def test_a_paired_phone_keeps_its_access(app, factory):  # noqa: F811
    """A paired device has no person behind it: this change must not take the phone's setting away."""
    api, csrf = await owner_api(app, factory)
    code = await pairing_code(api, csrf, scope="chat")
    phone = client(app)
    paired = await phone.post("/api/v1/auth/pair", json={"code": code, "label": "phone"})
    assert paired.status_code == 200
    bearer = {"Authorization": f"Bearer {paired.json()['token']}"}
    body, _ = await flipped(api)
    assert (await phone.put(URL, json=body, headers=bearer)).status_code == 200
