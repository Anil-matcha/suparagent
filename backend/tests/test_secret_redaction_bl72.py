import logging

from app.core import logging as app_logging
from app.services.agent_memory import redact_json, redact_secrets

SAMPLES = [
    "sk-ant-api03-abcdefghijklmnopqrstuv",
    "ghp_abcdefghijklmnopqrstuvwxyz1234",
    "github_pat_abcdefghijklmnopqrstu",
    "xoxb-1234567890-abcdefghij",
    "AKIAABCDEFGHIJKLMNOP",
    "AIzaSyA1234567890abcdefghijklmnopqrstuv",
    "sk_live_abcdefghijklmnop1234",
    "glpat-abcdefghijklmnopqrstu",
    "123456789:AAEhBOweik6ad6PsWvTHr8kAYeKrxdv5wUw",
    "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.abcdefghijkl",
]


def test_known_shapes_redacted():
    for secret in SAMPLES:
        out, n = redact_secrets(f"key {secret} end")
        assert secret not in out and n >= 1, secret


def test_headers_and_urls_and_private_key():
    out, _ = redact_secrets("Authorization: Basic dXNlcjpwYXNzd29yZA==")
    assert "dXNlcjpw" not in out
    out, _ = redact_secrets("Authorization: Bearer abcdefghijklmnop12345")
    assert "abcdefghijklmnop12345" not in out
    out, _ = redact_secrets("X-Api-Key: supersecretvalue123")
    assert "supersecretvalue123" not in out
    out, _ = redact_secrets("clone https://bob:hunter22pw@example.com/repo.git")
    assert "hunter22pw" not in out and "bob:" in out
    out, _ = redact_secrets("-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----")
    assert "abc" not in out


def test_plain_text_untouched():
    text = "Meet at https://example.com/a/b at 10:30, see sk-learn docs"
    assert redact_secrets(text)[0] == text


def test_redact_json_nested():
    out = redact_json({"a": [{"t": "token=abcdefghij1234"}], "n": 3})
    assert "abcdefghij1234" not in str(out) and out["n"] == 3


def test_log_filter_and_processor():
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "key %s", ("ghp_abcdefghijklmnopqrstuvwxyz1234",), None)
    app_logging._RedactFilter().filter(rec)
    assert "ghp_" not in rec.getMessage()
    ev = app_logging._redact_event(None, "info", {"event": "sk-ant-api03-abcdefghijklmnopqrstuv"})
    assert "sk-ant" not in ev["event"]
