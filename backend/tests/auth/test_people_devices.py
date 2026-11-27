"""The device list names a member's browser after them and shows what they may do today."""

from tests.auth.test_auth_api import app, auth_settings, client, factory  # noqa: F401
from tests.auth.test_people import clean_people, member, owner_api, write  # noqa: F401
from tests.auth.test_people_permissions import member_with  # noqa: F401


async def test_a_members_browser_is_named_and_shows_their_real_access(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    await member(app, api, csrf, "sam@example.com", name="Sam")
    await member_with(app, api, csrf, ["chat"], "rae@example.com", "Rae")
    devices = (await api.get("/api/v1/auth/devices")).json()
    by_name = {d["label"].split(" · ")[0]: d for d in devices}
    assert set(by_name["Sam"]["scopes"]) == {"chat", "files", "manage"}
    assert by_name["Rae"]["scopes"] == ["chat"]
    owner_row = next(d for d in devices if d["current"])
    assert owner_row["label"].startswith("Owner · ") and "admin" in owner_row["scopes"]


async def test_changing_a_members_permissions_changes_what_the_list_shows(app, factory):  # noqa: F811
    api, csrf = await owner_api(app, factory)
    _, sam = await member(app, api, csrf, "sam@example.com", name="Sam")
    await api.patch(f"/api/v1/people/{sam['person_id']}", json={"permissions": ["chat", "files"]}, headers=write(csrf))
    devices = (await api.get("/api/v1/auth/devices")).json()
    row = next(d for d in devices if d["label"].startswith("Sam"))
    assert set(row["scopes"]) == {"chat", "files"}
