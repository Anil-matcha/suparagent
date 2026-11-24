import base64

import pytest

from app import composio_tools as ct

SHA = "a" * 40


def _file(text, **more):
    body = text.encode()
    return {"data": {"content": base64.b64encode(body).decode(), "encoding": "base64", "sha": SHA, "size": len(body), **more}}


def test_insert_goes_right_after_the_one_matching_line():
    text = "# List\n- [A](a)\n- [B](b)\n- [C](c)\n"
    assert ct.insert_after_line(text, "- [B](b)", "- [New](n)") == "# List\n- [A](a)\n- [B](b)\n- [New](n)\n- [C](c)\n"


def test_insert_keeps_windows_line_endings():
    assert ct.insert_after_line("a\r\nb\r\n", "a", "x") == "a\r\nx\r\nb\r\n"


@pytest.mark.parametrize("anchor", ["missing", "dup"])
def test_insert_refuses_a_missing_or_repeated_line(anchor):
    with pytest.raises(ct.BadArguments):
        ct.insert_after_line("dup\ndup\nother\n", anchor, "x")


def test_edit_source_reads_text_and_blob_id():
    assert ct.edit_source(_file("hello\n")) == ("hello\n", SHA)


def test_edit_source_refuses_a_file_that_was_not_read_in_full():
    data = _file("hello\n")
    data["data"]["size"] = 500_000
    with pytest.raises(ct.BadArguments):
        ct.edit_source(data)
    with pytest.raises(ct.BadArguments):
        ct.edit_source({"data": {"content": "x", "encoding": "base64", "size": 1}})  # no blob id


def _args(**more):
    base = {"owner": "o", "repo": "r", "path": "README.md", "branch": "campaign/x", "message": "m"}
    return {**base, **more}


def test_commit_accepts_content_or_an_edit_never_both():
    tool = ct.BY_NAME["github_commit_file"]
    assert "content" in ct.clean_arguments(tool, _args(content="text"))[0]
    sent = ct.clean_arguments(tool, _args(insert_after="- [B](b)", insert_text="- [New](n)"))[0]
    assert "content" not in sent and sent["insert_after"] == "- [B](b)"
    for bad in (_args(), _args(content="t", insert_after="a", insert_text="b"), _args(insert_after="a"),
                _args(insert_after="a", insert_text="b", sha=SHA)):
        with pytest.raises(ct.BadArguments):
            ct.clean_arguments(tool, bad)


def test_a_large_file_can_be_edited_without_sending_it():
    tool = ct.BY_NAME["github_commit_file"]
    assert ct.clean_arguments(tool, _args(insert_after="x" * 400, insert_text="y" * 4000))[0]["insert_text"] == "y" * 4000


@pytest.mark.asyncio
async def test_edited_commit_reads_then_sends_the_whole_new_file(monkeypatch):
    from types import SimpleNamespace
    from app.services import composio_service as cs

    calls = []

    async def fake_execute(client, session_id, slug, arguments, account_id):
        calls.append((slug, arguments))
        return SimpleNamespace(result_type="completed", data=_file("one\ntwo\nthree\n")["data"])

    monkeypatch.setattr(cs, "_execute", fake_execute)
    sent = await cs._edited_commit(None, "s", "acct", _args(insert_after="two", insert_text="2.5"))
    assert calls == [("GITHUB_GET_REPOSITORY_CONTENT", {"owner": "o", "repo": "r", "path": "README.md", "ref": "campaign/x"})]
    assert base64.b64decode(sent["content"]).decode() == "one\ntwo\n2.5\nthree\n"
    assert sent["sha"] == SHA and "insert_after" not in sent and "insert_text" not in sent


@pytest.mark.asyncio
async def test_edited_commit_changes_nothing_when_the_read_fails(monkeypatch):
    from types import SimpleNamespace
    from app.services import composio_service as cs

    async def failed(*a):
        return SimpleNamespace(result_type="failed", data=None)

    monkeypatch.setattr(cs, "_execute", failed)
    with pytest.raises(ct.BadArguments):
        await cs._edited_commit(None, "s", "acct", _args(insert_after="two", insert_text="x"))
