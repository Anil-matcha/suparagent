import json
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.routers import speech as speech_router
from app.routers.agents import get_runtime
from app.services import speech, user_credential_service
from app.services.local_access_policy import require_local_access
from app.services.thread_runtime import LOCAL_OWNER_ID

KEY = "muapi-secret-key-0123456789"
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
CDN = "https://cdn.muapi.ai/outputs/audio/abc.mp3"
REAL_CLIENT = httpx.AsyncClient  # the stub below always wraps this, never a patched one


# ---- text made fit for the ear ----

def test_markdown_becomes_plain_sentences():
    text = "# Title\n\nSome **bold** and _italic_ text with `code` and a [link](https://example.com/x).\n\n- one\n- two\n\n1. first\n2. second"
    assert speech.normalize_for_speech(text) == "Title. Some bold and italic text with code and a link. one. two. first. second."


def test_code_blocks_images_urls_and_tables_are_handled():
    text = "Intro\n\n```python\nprint('secret plan')\n```\n\n![diagram](http://x/y.png) see https://example.com/a?b=c now\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    spoken = speech.normalize_for_speech(text)
    assert "print" not in spoken and "secret plan" not in spoken and "Code omitted." in spoken
    assert "diagram" in spoken and "http" not in spoken and "link" in spoken
    assert "---" not in spoken and "|" not in spoken


def test_an_unclosed_code_fence_is_still_left_out():
    assert "hidden" not in speech.normalize_for_speech("Before\n\n```\nhidden text with no end")


def test_credentials_are_never_read_aloud():
    spoken = speech.normalize_for_speech("Use api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789 and password: hunter2hunter2 now.")
    assert "sk-abcdef" not in spoken and "hunter2" not in spoken and "secret omitted" in spoken


def test_empty_and_symbol_only_text_normalizes_to_nothing():
    assert speech.normalize_for_speech("") == ""
    assert speech.normalize_for_speech("---\n\n```\ncode\n```") == "Code omitted."


def test_cut_at_boundary_prefers_a_sentence_end():
    text = "First sentence here. Second sentence here. Third sentence goes on and on"
    cut, truncated = speech.cut_at_boundary(text, 45)
    assert truncated and cut == "First sentence here. Second sentence here."
    assert speech.cut_at_boundary("short", 45) == ("short", False)


def test_cut_falls_back_to_a_space_then_a_hard_cut():
    cut, truncated = speech.cut_at_boundary("word " * 40, 22)
    assert truncated and not cut.endswith(" ") and len(cut) <= 22
    cut, truncated = speech.cut_at_boundary("x" * 100, 30)
    assert truncated and cut == "x" * 30


def test_audio_formats_are_recognised_by_their_bytes():
    assert speech.sniff_audio(MP3) == "audio/mpeg"
    assert speech.sniff_audio(b"\xff\xfb\x90\x00" + b"\x00" * 8) == "audio/mpeg"
    assert speech.sniff_audio(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert speech.sniff_audio(b"OggS" + b"\x00" * 8) == "audio/ogg"
    assert speech.sniff_audio(b"<html>not audio</html>") is None
    assert speech.sniff_audio(b"") is None


def test_the_audio_link_is_found_wherever_muapi_puts_it():
    assert speech.find_audio_url({"output": CDN}) == CDN
    assert speech.find_audio_url({"output": [CDN]}) == CDN
    assert speech.find_audio_url({"outputs": [{"url": CDN}]}) == CDN
    assert speech.find_audio_url({"result": {"audio_url": CDN}}) == CDN
    assert speech.find_audio_url({"output": "not a link"}) is None
    assert speech.find_audio_url({"output": "http://insecure/x.mp3"}) is None


# ---- the route, with MuAPI stubbed ----

@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(speech, "POLL_SECONDS", 0)
    speech._recent.clear()


@pytest.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
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
    app.include_router(speech_router.router)
    app.dependency_overrides[get_runtime] = lambda: RT()
    app.dependency_overrides[require_local_access] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.sessions = sessions
        yield c
    await engine.dispose()


async def save_key(client, key=KEY):
    async with client.sessions() as db:
        await user_credential_service.save_token(
            db, user_id=LOCAL_OWNER_ID, provider="integration:muapi:data-api", file_path="-", plaintext=json.dumps(key))


class Muapi:
    """A stand-in for MuAPI: records every request and answers by URL."""

    def __init__(self, monkeypatch, *, statuses=("processing", "completed"), audio=MP3, link=CDN, fail_submit=None,
                 task_error=None):
        self.requests, self.statuses, self.audio, self.link = [], list(statuses), audio, link
        self.fail_submit, self.task_error, self.polls = fail_submit, task_error, 0
        monkeypatch.setattr(speech.media_fetch.httpx, "AsyncClient",
                            lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(self.handle), **kw))

    def handle(self, request):
        url = str(request.url)
        self.requests.append((request.method, url, request.headers.get("x-api-key"), request.content))
        if request.method == "GET" and url.endswith("/models/elevenlabs-v4-turbo") or url.endswith("/models/fish-s2-1-pro"):
            return httpx.Response(200, json={"name": "x", "endpoint_url": "elevenlabs-v4-turbo"})
        if url.endswith("/estimate-cost"):
            return httpx.Response(200, json={"cost": 0.04, "currency": "USD"})
        if request.method == "POST":
            if self.fail_submit:
                return httpx.Response(self.fail_submit, json={"detail": "provider says: " + KEY})
            return httpx.Response(200, json={"request_id": "req-1", "status": "processing",
                                             "cost": {"amount_usd": 0.05}})
        if "/predictions/" in url:
            self.polls += 1
            if self.task_error:
                return httpx.Response(400, json={"detail": {"id": "req-1", "status": "failed", "error": self.task_error}})
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            body = {"id": "req-1", "status": status}
            if status == "completed":
                body["output"] = self.link
            return httpx.Response(200, json=body)
        if url == CDN or url.startswith("https://cdn.muapi.ai/"):
            return httpx.Response(200, content=self.audio)
        return httpx.Response(404)


async def test_voices_list_names_the_default_and_whether_muapi_is_connected(client):
    before = (await client.get("/speech/voices")).json()
    assert before["available"] is False and before["default"] == "eleven-daniel"
    assert [v["id"] for v in before["voices"]] == ["eleven-daniel", "eleven-liam", "fish-narrator"]
    await save_key(client)
    assert (await client.get("/speech/voices")).json()["available"] is True
    assert KEY not in (await client.get("/speech/voices")).text


async def test_speaking_returns_audio_cost_and_uses_the_saved_key(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch)
    r = await client.post("/speech/speak", json={"text": "Hello **world**. Here is a [link](https://x.y)."})
    assert r.status_code == 200 and r.content == MP3 and r.headers["content-type"] == "audio/mpeg"
    assert r.headers["x-speech-cost-usd"] == "0.0500" and r.headers["x-speech-truncated"] == "0"
    assert "x-speech-cost-usd" in r.headers["access-control-expose-headers"].lower()
    assert r.headers["cache-control"] == "no-store" and KEY not in r.text
    posts = [req for req in muapi.requests if req[0] == "POST" and not req[1].endswith("/estimate-cost")]
    body = json.loads(posts[0][3])
    assert body == {"prompt": "Hello world. Here is a link.", "voice_id": "voice_daniel"}
    assert posts[0][1] == "https://api.muapi.ai/api/v1/elevenlabs-v4-turbo"
    assert all(req[2] == KEY for req in muapi.requests if "api.muapi.ai" in req[1])
    assert all(req[2] is None for req in muapi.requests if "cdn.muapi.ai" in req[1])
    assert muapi.polls == 2


async def test_another_voice_and_a_long_reply_cut_at_a_sentence_and_say_so(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch)
    text = "This is a sentence of some length. " * 400
    r = await client.post("/speech/speak", json={"text": text, "voice": "eleven-liam"})
    assert r.status_code == 200 and r.headers["x-speech-truncated"] == "1"
    spoken = json.loads([q for q in muapi.requests if q[0] == "POST" and not q[1].endswith("/estimate-cost")][0][3])
    assert spoken["voice_id"] == "voice_liam" and len(spoken["prompt"]) <= 5000 and spoken["prompt"].endswith(".")
    assert int(r.headers["x-speech-chars"]) == len(spoken["prompt"])


async def test_without_a_key_it_says_where_to_connect_muapi(client):
    r = await client.post("/speech/speak", json={"text": "Hello there."})
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "provider_not_configured"
    assert "Integrations" in r.json()["detail"]["message"]


async def test_unknown_voice_and_nothing_to_say(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch)
    assert (await client.post("/speech/speak", json={"text": "Hi.", "voice": "nope"})).status_code == 422
    r = await client.post("/speech/speak", json={"text": "   \n\n   "})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "nothing_to_say"
    assert (await client.post("/speech/speak", json={"text": "x" * 20001})).status_code == 422


async def test_a_failed_task_gives_a_plain_message_and_no_provider_text(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch, task_error="Enrichment service authentication failed " + KEY)
    r = await client.post("/speech/speak", json={"text": "Hello there."})
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "provider_failed"
    assert KEY not in r.text and "Enrichment" not in r.text


async def test_a_rejected_key_and_missing_credit_are_told_apart(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch, fail_submit=401)
    r = await client.post("/speech/speak", json={"text": "Hello there."})
    assert r.status_code == 409 and "Reconnect" in r.json()["detail"]["message"] and KEY not in r.text
    Muapi(monkeypatch, fail_submit=402)
    assert (await client.post("/speech/speak", json={"text": "Hello there."})).status_code == 402


async def test_a_link_off_the_muapi_cdn_is_never_followed(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch, link="https://evil.example/x.mp3")
    r = await client.post("/speech/speak", json={"text": "Hello there."})
    assert r.status_code == 502
    assert all("evil.example" not in req[1] for req in muapi.requests)


async def test_bytes_that_are_not_audio_are_refused(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch, audio=b"<html>login</html>")
    r = await client.post("/speech/speak", json={"text": "Hello there."})
    assert r.status_code == 502 and r.json()["detail"]["error_code"] == "not_audio"


async def test_a_task_that_never_finishes_times_out(client, monkeypatch):
    await save_key(client)
    monkeypatch.setattr(speech, "WAIT_SECONDS", 0.05)
    Muapi(monkeypatch, statuses=("processing",))
    r = await client.post("/speech/speak", json={"text": "Hello there."})
    assert r.status_code == 504 and r.json()["detail"]["error_code"] == "timed_out"


async def test_too_many_spoken_replies_in_a_window_are_refused(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch, statuses=("completed",))
    monkeypatch.setattr(speech, "RATE_MAX", 2)
    assert (await client.post("/speech/speak", json={"text": "One."})).status_code == 200
    assert (await client.post("/speech/speak", json={"text": "Two."})).status_code == 200
    r = await client.post("/speech/speak", json={"text": "Three."})
    assert r.status_code == 429 and r.json()["detail"]["error_code"] == "rate_limited"


# ---- saved audio: reading the same reply again costs nothing ----

def _paid_posts(muapi):
    return [r for r in muapi.requests if r[0] == "POST" and not r[1].endswith("/estimate-cost")]


async def test_the_second_reading_of_a_reply_is_played_from_saved_audio(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch)
    first = await client.post("/speech/speak", json={"text": "Hello **world**. Nice day."})
    second = await client.post("/speech/speak", json={"text": "Hello **world**. Nice day."})
    assert first.headers["x-speech-cached"] == "0" and "x-speech-cost-usd" in first.headers
    assert second.status_code == 200 and second.content == first.content == MP3
    assert second.headers["x-speech-cached"] == "1" and "x-speech-cost-usd" not in second.headers
    assert len(_paid_posts(muapi)) == 1
    assert speech.cache_info()["clips"] == 1


async def test_the_same_words_in_the_same_voice_share_a_clip_even_if_the_markdown_differs(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch)
    await client.post("/speech/speak", json={"text": "Sales are **up**."})
    again = await client.post("/speech/speak", json={"text": "## Sales are _up_."})
    assert again.headers["x-speech-cached"] == "1" and len(_paid_posts(muapi)) == 1


async def test_another_voice_is_a_new_clip(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch)
    await client.post("/speech/speak", json={"text": "Hello there.", "voice": "eleven-daniel"})
    other = await client.post("/speech/speak", json={"text": "Hello there.", "voice": "eleven-liam"})
    assert other.headers["x-speech-cached"] == "0" and len(_paid_posts(muapi)) == 2
    assert speech.cache_info()["clips"] == 2


async def test_a_saved_clip_of_a_long_reply_still_says_it_was_cut(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch)
    text = "This is a sentence of some length. " * 400
    await client.post("/speech/speak", json={"text": text})
    again = await client.post("/speech/speak", json={"text": text})
    assert again.headers["x-speech-cached"] == "1" and again.headers["x-speech-truncated"] == "1"


async def test_saved_replies_do_not_count_against_the_rate_limit(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch, statuses=("completed",))
    monkeypatch.setattr(speech, "RATE_MAX", 1)
    for _ in range(4):
        assert (await client.post("/speech/speak", json={"text": "Only once."})).status_code == 200
    assert (await client.post("/speech/speak", json={"text": "Something else."})).status_code == 429


async def test_a_double_press_makes_one_request(client, monkeypatch):
    import asyncio
    await save_key(client)
    muapi = Muapi(monkeypatch)
    results = await asyncio.gather(*[client.post("/speech/speak", json={"text": "Hello there."}) for _ in range(3)])
    assert [r.status_code for r in results] == [200, 200, 200]
    assert len(_paid_posts(muapi)) == 1
    assert sorted(r.headers["x-speech-cached"] for r in results) == ["0", "1", "1"]


async def test_only_the_newest_clips_are_kept(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch, statuses=("completed",))
    monkeypatch.setattr(speech, "KEEP_CLIPS", 2)
    for n in range(4):
        await client.post("/speech/speak", json={"text": f"Reply number {n}."})
    assert speech.cache_info()["clips"] == 2


async def test_a_damaged_saved_clip_is_dropped_and_made_again(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch)
    await client.post("/speech/speak", json={"text": "Hello there."})
    for clip in speech.cache_dir().iterdir():
        clip.write_bytes(b"<html>not audio</html>")
    again = await client.post("/speech/speak", json={"text": "Hello there."})
    assert again.status_code == 200 and again.headers["x-speech-cached"] == "0" and len(_paid_posts(muapi)) == 2


async def test_the_saved_audio_can_be_emptied(client, monkeypatch):
    await save_key(client)
    muapi = Muapi(monkeypatch)
    await client.post("/speech/speak", json={"text": "Hello there."})
    assert (await client.get("/speech/voices")).json()["saved"]["clips"] == 1
    r = await client.delete("/speech/cache")
    assert r.status_code == 200 and r.json() == {"removed": 1}
    assert (await client.get("/speech/voices")).json()["saved"] == {"clips": 0, "bytes": 0}
    again = await client.post("/speech/speak", json={"text": "Hello there."})
    assert again.headers["x-speech-cached"] == "0" and len(_paid_posts(muapi)) == 2
    assert (await client.delete("/speech/cache")).json() == {"removed": 1}
    assert (await client.delete("/speech/cache")).json() == {"removed": 0}


async def test_a_failed_reading_is_not_saved(client, monkeypatch):
    await save_key(client)
    Muapi(monkeypatch, task_error="boom")
    assert (await client.post("/speech/speak", json={"text": "Hello there."})).status_code == 502
    assert speech.cache_info()["clips"] == 0


# ---- the voice setting ----

async def test_the_saved_voice_is_the_default_and_a_request_can_still_choose_another(client, monkeypatch):
    from app.core.config import settings
    await save_key(client)
    muapi = Muapi(monkeypatch)
    monkeypatch.setattr(settings, "SPEECH_VOICE", "fish-narrator")
    assert (await client.get("/speech/voices")).json()["default"] == "fish-narrator"
    await client.post("/speech/speak", json={"text": "Hello there."})
    assert json.loads(_paid_posts(muapi)[0][3])["voice_id"] == "voice_fish_narrator"
    await client.post("/speech/speak", json={"text": "Hello there.", "voice": "eleven-liam"})
    assert json.loads(_paid_posts(muapi)[1][3])["voice_id"] == "voice_liam"


async def test_a_voice_that_no_longer_exists_falls_back_to_the_default(client, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "SPEECH_VOICE", "removed-voice")
    assert (await client.get("/speech/voices")).json()["default"] == "eleven-daniel"


async def test_the_voice_list_carries_a_price_hint_for_each_voice(client):
    voices = (await client.get("/speech/voices")).json()["voices"]
    assert all(v["price_hint"].startswith("from about $") for v in voices)
