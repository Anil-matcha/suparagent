import base64
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base import Base
from app.routers import images as images_router
from app.routers.agents import get_runtime
from app.services import agent_store as store
from app.services import attachments, image_gen

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class FakeProvider:
    id = "fake"
    label = "Fake"

    def __init__(self, data=PNG, ok=True, delay=0):
        self.data, self.ok, self.delay, self.calls = data, ok, delay, []

    def available(self):
        return self.ok

    async def generate(self, prompt, size):
        import asyncio
        self.calls.append((prompt, size))
        await asyncio.sleep(self.delay)
        return self.data


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "root_dir", lambda: tmp_path / "attachments")


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setitem(image_gen._PROVIDERS, "fake", provider)
    return provider


@pytest.fixture
async def client(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'i.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    class RT:
        owner_id = "o1"

        @asynccontextmanager
        async def session(self):
            async with sessions() as db:
                yield db

    app = FastAPI()
    app.include_router(images_router.router)
    app.dependency_overrides[get_runtime] = lambda: RT()
    from app.services.local_access_policy import require_local_access
    app.dependency_overrides[require_local_access] = lambda: None
    async with sessions() as db:
        agent_id = (await store.create_agent(db, owner_id="o1", name="Ada", instructions="x")).id
        other = (await store.create_agent(db, owner_id="o2", name="Eve", instructions="x")).id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.agent_id, c.other = agent_id, other
        yield c
    await engine.dispose()


async def test_generate_stores_and_serves_the_image(client, fake):
    r = await client.post("/images/generate", json={"prompt": "  a  red\nfox ", "provider": "fake"})
    assert r.status_code == 201 and r.json()["mime"] == "image/png" and r.json()["bytes"] == len(PNG)
    assert fake.calls == [("a red fox", "1024x1024")]
    got = await client.get(f"/images/{r.json()['id']}")
    assert got.content == PNG and got.headers["content-type"] == "image/png"


async def test_bounds_and_validation(client, fake):
    assert (await client.post("/images/generate", json={"prompt": "  ", "provider": "fake"})).json()["detail"]["error_code"] == "empty_prompt"
    long = await client.post("/images/generate", json={"prompt": "x" * 1001, "provider": "fake"})
    assert long.status_code == 422 and long.json()["detail"]["error_code"] == "prompt_too_long"
    assert (await client.post("/images/generate", json={"prompt": "a", "provider": "fake", "size": "9x9"})).status_code == 422
    assert (await client.post("/images/generate", json={"prompt": "a", "provider": "nope"})).status_code == 422
    assert (await client.get("/images/..%2f..%2fetc")).status_code == 404
    assert (await client.get("/images/" + "0" * 32)).status_code == 404


async def test_unconfigured_provider_and_bad_output(client, fake, monkeypatch):
    fake.ok = False
    r = await client.post("/images/generate", json={"prompt": "a", "provider": "fake"})
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "provider_not_configured"
    fake.ok, fake.data = True, b"<html>not an image</html>"
    r = await client.post("/images/generate", json={"prompt": "a", "provider": "fake"})
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "not_an_image"
    fake.data = PNG + b"\x00" * image_gen.IMAGE_MAX_BYTES
    assert (await client.post("/images/generate", json={"prompt": "a", "provider": "fake"})).json()["detail"]["error_code"] == "too_large"


async def test_slow_provider_times_out(client, fake, monkeypatch):
    monkeypatch.setattr(image_gen.tool_timeout, "seconds", lambda: 0.05)
    fake.delay = 1
    r = await client.post("/images/generate", json={"prompt": "a", "provider": "fake"})
    assert r.status_code == 504 and r.json()["detail"]["error_code"] == "timed_out"


async def test_set_read_and_remove_avatar_owner_only(client, fake):
    image = (await client.post("/images/generate", json={"prompt": "a", "provider": "fake"})).json()["id"]
    assert (await client.get(f"/agents/{client.agent_id}/avatar")).status_code == 404
    assert (await client.put(f"/agents/{client.agent_id}/avatar", json={"image_id": image})).status_code == 204
    assert (await client.get(f"/agents/{client.agent_id}/avatar")).content == PNG
    assert (await client.put(f"/agents/{client.other}/avatar", json={"image_id": image})).status_code == 404
    assert (await client.put(f"/agents/{client.agent_id}/avatar", json={"image_id": "z" * 32})).status_code == 404
    assert (await client.delete(f"/agents/{client.agent_id}/avatar")).status_code == 204
    assert (await client.get(f"/agents/{client.agent_id}/avatar")).status_code == 404


async def test_only_the_newest_generated_images_are_kept(client, fake, monkeypatch):
    monkeypatch.setattr(image_gen, "KEEP_GENERATED", 3)
    for _ in range(5):
        await client.post("/images/generate", json={"prompt": "a", "provider": "fake"})
    assert len(list(image_gen.root().glob("*.png"))) == 3


async def test_openai_adapter_decodes_b64_and_hides_errors(monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_API_KEY", "sk-secretsecretsecretsecret")
    seen = {}

    def handler(request):
        seen["auth"], seen["body"] = request.headers["authorization"], request.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(image_gen.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert await image_gen.OpenAICompatibleProvider().generate("fox", "1024x1024") == PNG
    assert seen["auth"] == "Bearer sk-secretsecretsecretsecret" and b'"n":1' in seen["body"].replace(b" ", b"")
    monkeypatch.setattr(image_gen.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key sk-secretsecretsecretsecret")), **kw))
    with pytest.raises(image_gen.ImageError) as e:
        await image_gen.OpenAICompatibleProvider().generate("fox", "1024x1024")
    assert "sk-" not in e.value.message
    assert image_gen.OpenAICompatibleProvider().available()
