"""The Composio client's trigger and webhook methods against a fake with the live response shapes (BL-177)."""
import json

import httpx
import pytest

from app.services.composio_client import (ComposioClient, ComposioError, TRIGGER_EVENT, TriggerInstance, WEBHOOK_EVENTS,
                                           WebhookSubscription)
from tests.services.composio_trigger_fake import SECRET_LENGTH, TriggerFake

KEY = "ak_TRIGGERKEY_abcdef123456"


@pytest.fixture
def fake():
    return TriggerFake()


@pytest.fixture
def client(fake):
    return ComposioClient(KEY, transport=httpx.MockTransport(fake.handler))


async def test_trigger_types_say_the_new_message_trigger_is_a_poll_with_its_config_fields(client):
    found = {t.slug: t for t in await client.list_trigger_types("gmail")}
    assert found["GMAIL_NEW_GMAIL_MESSAGE"].delivery == "poll"
    assert found["GMAIL_NEW_GMAIL_MESSAGE"].config_fields == ("interval", "labelIds", "query", "userId")


async def test_upsert_sends_only_the_allowed_config_and_returns_the_instance_id(client, fake):
    instance = await client.upsert_trigger("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {"query": "label:support"})
    assert instance == TriggerInstance(instance.id, "ca_g", {"query": "label:support"}, "GMAIL_NEW_GMAIL_MESSAGE", False, 2)
    assert fake.bodies[-1] == {"connected_account_id": "ca_g", "trigger_config": {"query": "label:support"}}
    again = await client.upsert_trigger("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {"query": "label:support"})
    assert again.id == instance.id and len(fake.triggers) == 1   # identical setting, same instance


@pytest.mark.parametrize("slug, account, config", [
    ("gmail_new", "ca_g", {}), ("GMAIL_NEW_GMAIL_MESSAGE", "../x", {}), ("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {"userId": "someone"}),
    ("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {"query": "x" * 201}), ("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {"query": 5}),
])
async def test_bad_trigger_arguments_never_reach_composio(client, fake, slug, account, config):
    with pytest.raises(ComposioError):
        await client.upsert_trigger(slug, account, config)
    assert fake.calls == []


async def test_pausing_and_resuming_an_instance_and_an_already_there_answer_is_fine(client, fake):
    instance = await client.upsert_trigger("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {})
    await client.set_trigger_enabled(instance.id, False)
    assert fake.triggers[instance.id]["disabled_at"]
    await client.set_trigger_enabled(instance.id, False)         # 409: already disabled, which is what was asked
    await client.set_trigger_enabled(instance.id, True)
    assert not fake.triggers[instance.id]["disabled_at"]
    assert [b for b in fake.bodies if "status" in b] == [{"status": "disable"}, {"status": "disable"}, {"status": "enable"}]


async def test_other_failures_while_pausing_are_raised(client, fake):
    fake.fail["PATCH /trigger_instances/manage/"] = 500
    with pytest.raises(ComposioError) as caught:
        await client.set_trigger_enabled("ti_1", False)
    assert caught.value.kind == "unavailable"


async def test_deleting_an_instance_and_a_missing_one(client, fake):
    instance = await client.upsert_trigger("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {})
    await client.delete_trigger(instance.id)
    assert fake.triggers == {}
    with pytest.raises(ComposioError) as caught:
        await client.delete_trigger(instance.id)
    assert caught.value.kind == "not_found"


async def test_active_instances_are_read_in_either_naming_and_show_disabled_ones(client, fake):
    one = await client.upsert_trigger("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {"query": "a"})
    two = await client.upsert_trigger("GMAIL_NEW_GMAIL_MESSAGE", "ca_g", {"query": "b"})
    await client.set_trigger_enabled(two.id, False)
    found = {t.id: t for t in await client.list_active_triggers(["ca_g"])}
    assert set(found) == {one.id, two.id} and found[two.id].disabled and not found[one.id].disabled
    assert found[one.id].account_id == "ca_g" and found[one.id].config == {"query": "a"}
    assert fake.calls[-1] == ("GET", "/trigger_instances/active")


async def test_the_webhook_secret_is_returned_once_kept_off_the_repr_and_readable_later(client, fake):
    created = await client.create_webhook_subscription("https://dots.example.com/hooks/composio/abc", WEBHOOK_EVENTS)
    assert isinstance(created, WebhookSubscription) and len(created.secret) == SECRET_LENGTH
    assert created.secret not in repr(created) and TRIGGER_EVENT in created.events
    assert fake.bodies[-1] == {"webhook_url": "https://dots.example.com/hooks/composio/abc", "enabled_events": list(WEBHOOK_EVENTS)}
    assert (await client.get_webhook_subscription(created.id)).secret == created.secret
    listed = await client.list_webhook_subscriptions()
    assert [s.id for s in listed] == [created.id]
    await client.delete_webhook_subscription(created.id)
    assert await client.list_webhook_subscriptions() == []


async def test_a_webhook_address_that_is_not_https_never_reaches_composio(client, fake):
    with pytest.raises(ComposioError):
        await client.create_webhook_subscription("http://dots.example.com/hooks", WEBHOOK_EVENTS)
    assert fake.calls == []


async def test_composio_refusing_the_address_is_a_rejection_that_keeps_its_status_and_never_echoes_the_key(client, fake):
    with pytest.raises(ComposioError) as caught:
        await client.create_webhook_subscription("https://example.invalid/hook", WEBHOOK_EVENTS)
    assert caught.value.kind == "rejected" and caught.value.status == 400
    assert "SSRF" in caught.value.message and KEY not in caught.value.message


async def test_a_subscription_without_a_secret_in_the_answer_is_not_trusted(fake):
    def handler(request):
        return httpx.Response(201, json={"id": "ws_1", "webhook_url": "https://x.example/h", "enabled_events": [TRIGGER_EVENT]})
    with pytest.raises(ComposioError) as caught:
        await ComposioClient(KEY, transport=httpx.MockTransport(handler)).create_webhook_subscription("https://x.example/h", WEBHOOK_EVENTS)
    assert caught.value.kind == "bad_response"


async def test_updating_a_saved_subscription_patches_its_url_and_keeps_existing_events_and_secret(fake):
    url = "https://dots.example.com/hooks/composio/abc"
    saved = WebhookSubscription("ws_saved", url, ("vendor.custom.event", TRIGGER_EVENT), "saved-secret")
    patch_request = {}

    def handler(request):
        path = request.url.path.removeprefix("/api/v3.1")
        if request.method == "PATCH":
            patch_request["path"] = path
            patch_request["body"] = json.loads(request.content)
            return httpx.Response(200, json={
                "id": saved.id, "webhook_url": url,
                "enabled_events": patch_request["body"]["enabled_events"],
            })
        return fake.handler(request)

    updated = await ComposioClient(KEY, transport=httpx.MockTransport(handler)).update_webhook_subscription(
        saved, WEBHOOK_EVENTS,
    )

    assert patch_request == {
        "path": f"/webhook_subscriptions/{saved.id}",
        "body": {"webhook_url": url, "enabled_events": list(dict.fromkeys((*saved.events, *WEBHOOK_EVENTS)))},
    }
    assert "vendor.custom.event" in updated.events
    assert "composio.trigger.disabled" in updated.events
    assert updated.secret == "saved-secret"


@pytest.mark.parametrize("subscription_id", ["../foreign", "", "x" * 129])
async def test_updating_a_subscription_rejects_an_invalid_path_id_without_a_request(client, fake, subscription_id):
    saved = WebhookSubscription(subscription_id, "https://dots.example.com/hooks", (TRIGGER_EVENT,), "secret")
    with pytest.raises(ComposioError) as caught:
        await client.update_webhook_subscription(saved, WEBHOOK_EVENTS)
    assert caught.value.kind == "not_found"
    assert fake.calls == []


async def test_updating_a_subscription_rejects_an_untrusted_saved_url_without_a_request(client, fake):
    saved = WebhookSubscription("ws_1", "http://foreign.example/hook", (TRIGGER_EVENT,), "secret")
    with pytest.raises(ComposioError) as caught:
        await client.update_webhook_subscription(saved, WEBHOOK_EVENTS)
    assert caught.value.kind == "rejected"
    assert fake.calls == []


async def test_updating_a_subscription_rejects_a_response_for_a_different_url():
    saved = WebhookSubscription("ws_1", "https://dots.example.com/hooks", (TRIGGER_EVENT,), "secret")

    def handler(request):
        return httpx.Response(200, json={
            "id": saved.id, "webhook_url": "https://foreign.example/hook",
            "enabled_events": list(WEBHOOK_EVENTS),
        })

    with pytest.raises(ComposioError) as caught:
        await ComposioClient(KEY, transport=httpx.MockTransport(handler)).update_webhook_subscription(saved, WEBHOOK_EVENTS)
    assert caught.value.kind == "bad_response"
