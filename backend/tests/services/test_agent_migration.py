import io
import json
import sqlite3
import zipfile
import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.agents import get_runtime as agents_runtime
from app.routers.agents import router as agents_router
from app.services import openbot_database_import, agent_migration_archive, agent_package
from tests.services.thread_harness import cleanup, cli, owner, rt, sessions  # noqa: F401


def _create_openbot_db(schema_version: int = 15, profiles: list[tuple[str, str, int]] = None) -> bytes:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
    for v in range(openbot_database_import.MIN_SCHEMA_VERSION, schema_version + 1):
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (v,))
    conn.execute(
        "CREATE TABLE projection_agents (agent_id TEXT PRIMARY KEY, agent_json TEXT NOT NULL, sort_order INTEGER NOT NULL)"
    )
    if profiles is None:
        profiles = [
            (
                "agent-1",
                json.dumps({
                    "name": "Research Bot",
                    "title": "Lead Researcher",
                    "description": "Performs deep web research and summarizes findings.",
                }),
                1,
            )
        ]
    for aid, ajson, order in profiles:
        conn.execute(
            "INSERT INTO projection_agents (agent_id, agent_json, sort_order) VALUES (?, ?, ?)",
            (aid, ajson, order),
        )
    conn.commit()
    data = conn.serialize()
    conn.close()
    return data


class TestOpenBotDatabaseImport:
    def test_preview_valid_database(self):
        db_bytes = _create_openbot_db(15)
        result = openbot_database_import.preview_database(db_bytes)
        assert result["schema_version"] == 15
        assert len(result["profiles"]) == 1
        p = result["profiles"][0]
        assert p["key"] == "agent-1"
        assert p["name"] == "Research Bot"
        assert p["title"] == "Lead Researcher"
        assert p["instructions"] == "Performs deep web research and summarizes findings."
        assert p["document"]["format"] == agent_package.FORMAT
        assert p["document"]["version"] == agent_package.VERSION
        assert p["document"]["agent"]["name"] == "Research Bot"

    def test_reject_invalid_magic_header(self):
        with pytest.raises(openbot_database_import.OpenBotDatabaseImportError, match="valid OpenBot SQLite database"):
            openbot_database_import.preview_database(b"Not an sqlite database file")

    def test_reject_unsupported_schema_version(self):
        # Version 7 < MIN_SCHEMA_VERSION (8)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations (version) VALUES (7)")
        conn.execute("CREATE TABLE projection_agents (agent_id TEXT, agent_json TEXT, sort_order INTEGER)")
        conn.commit()
        data = conn.serialize()
        conn.close()
        with pytest.raises(openbot_database_import.OpenBotDatabaseImportError, match="version is not supported"):
            openbot_database_import.preview_database(data)

    def test_reject_missing_tables(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE dummy (id INTEGER PRIMARY KEY)")
        conn.commit()
        data = conn.serialize()
        conn.close()
        with pytest.raises(openbot_database_import.OpenBotDatabaseImportError, match="format is not supported"):
            openbot_database_import.preview_database(data)

    def test_reject_empty_profiles(self):
        db_bytes = _create_openbot_db(15, profiles=[])
        with pytest.raises(openbot_database_import.OpenBotDatabaseImportError, match="no agent profiles to import"):
            openbot_database_import.preview_database(db_bytes)

    def test_reject_corrupt_agent_json(self):
        db_bytes = _create_openbot_db(15, profiles=[("a-1", "not-valid-json", 1)])
        with pytest.raises(openbot_database_import.OpenBotDatabaseImportError, match="contains invalid data"):
            openbot_database_import.preview_database(db_bytes)


class TestAgentMigrationArchive:
    def test_preview_native_manifest_json(self):
        doc = {
            "format": agent_migration_archive.FORMAT,
            "version": agent_migration_archive.VERSION,
            "source": "grok",
            "agents": [
                {
                    "format": agent_package.FORMAT,
                    "version": agent_package.VERSION,
                    "agent": {
                        "name": "Planner Agent",
                        "title": "Lead",
                        "instructions": "Plan project tasks.",
                    },
                }
            ],
            "extra_setting": "ignored_val",
        }
        manifest_bytes = json.dumps(doc).encode("utf-8")
        preview = agent_migration_archive.preview_archive(manifest_bytes)
        assert preview["format"] == agent_migration_archive.FORMAT
        assert preview["source"] == "grok"
        assert len(preview["profiles"]) == 1
        assert preview["profiles"][0]["name"] == "Planner Agent"
        assert preview["profiles"][0]["title"] == "Lead"
        assert "extra_setting" in preview["skipped"]

    def test_preview_portable_manifest_json_with_secret_redaction(self):
        doc = {
            "format": "portable.manifest.v1",
            "version": 1,
            "source": {"app": "grok-bot"},
            "agents": [
                {
                    "key": "analyst-1",
                    "name": "Data Analyst",
                    "title": "Financial Analyst",
                    "description": "Analyzes market trends with secret token sk-ant-api03-1234567890abcdefghijklmnopqrstuvwxyz123456.",
                    "memories": [
                        "Always prefers CSV output.",
                        "API key is ghp_123456789012345678901234567890123456.",
                    ],
                }
            ],
            "channels": [{"id": "general", "name": "General"}],
        }
        manifest_bytes = json.dumps(doc).encode("utf-8")
        preview = agent_migration_archive.preview_archive(manifest_bytes)
        assert preview["source"] == "grok"
        profile = preview["profiles"][0]
        assert profile["name"] == "Data Analyst"
        # Secret in instructions should be redacted
        assert "sk-ant-api03" not in profile["document"]["agent"]["instructions"]
        assert "[redacted]" in profile["document"]["agent"]["instructions"]
        # Memory should be converted to notes.MEMORY.md with secret redacted
        notes = profile["document"]["notes"]["MEMORY.md"]
        assert "Always prefers CSV output." in notes
        assert "ghp_" not in notes
        assert "[redacted]" in notes
        # Channels should be in skipped
        assert any("Shared channels" in s for s in preview["skipped"])

    def test_preview_portable_zip_archive_with_instructions_and_skills(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            manifest = {
                "format": "portable.manifest.v1",
                "version": 1,
                "source": {"app": "hermes-agent"},
                "agents": [
                    {
                        "key": "engineer-1",
                        "name": "Software Engineer",
                        "title": "Backend Dev",
                        "description": "Writes code.",
                        "files": "agents/engineer-1/files",
                        "skills": ["agents/engineer-1/skills/git-helper/"],
                    }
                ],
            }
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr(
                "agents/engineer-1/files/SOUL.md",
                "# Standing Instructions\nYou are an expert systems engineer specializing in high-throughput backend services.",
            )
            zf.writestr(
                "agents/engineer-1/skills/git-helper/SKILL.md",
                "---\nname: git-helper\ndescription: Helper for git workflow\n---\nRun git status and branch checks safely.",
            )
        data = buf.getvalue()
        preview = agent_migration_archive.preview_archive(data)
        assert preview["source"] == "hermes"
        profile = preview["profiles"][0]
        assert profile["name"] == "Software Engineer"
        # Full instructions from SOUL.md were imported
        assert "expert systems engineer" in profile["document"]["agent"]["instructions"]
        # Standalone skill imported
        skills = profile["document"]["skills"]
        assert len(skills) == 1
        assert skills[0]["name"] == "git-helper"
        assert "git status" in skills[0]["body"]

    def test_reject_zip_path_traversal(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            manifest = {
                "format": "portable.manifest.v1",
                "version": 1,
                "source": {"app": "grok-bot"},
                "agents": [{"key": "bad-1", "name": "Bad", "description": "test"}],
            }
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("../evil.txt", "malicious payload")
        data = buf.getvalue()
        with pytest.raises(agent_migration_archive.MigrationArchiveError, match="unsafe path|malformed"):
            agent_migration_archive.preview_archive(data)

    def test_reject_oversized_file(self):
        huge_data = b"PK\x03\x04" + b"0" * (agent_migration_archive.MAX_SOURCE_BYTES + 10)
        with pytest.raises(agent_migration_archive.MigrationArchiveError, match="at most 10 MB"):
            agent_migration_archive.preview_archive(huge_data)


def app_for(rt) -> FastAPI:
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/v1")
    app.dependency_overrides[agents_runtime] = lambda: rt
    return app


def client_for(app, *, client=("127.0.0.1", 50000), base_url="http://localhost"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app, client=client), base_url=base_url)


class TestAgentMigrationRouter:
    async def test_preview_migration_archive_route(self, rt, monkeypatch):
        monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
        doc = {
            "format": agent_migration_archive.FORMAT,
            "version": agent_migration_archive.VERSION,
            "source": "grok",
            "agents": [
                {
                    "format": agent_package.FORMAT,
                    "version": agent_package.VERSION,
                    "agent": {
                        "name": "Router Test Bot",
                        "instructions": "Help with routing.",
                    },
                }
            ],
        }
        manifest_bytes = json.dumps(doc).encode("utf-8")
        async with client_for(app_for(rt)) as client:
            resp = await client.post("/api/v1/agents/import/migration/preview", content=manifest_bytes)
            assert resp.status_code == 200
            data = resp.json()
            assert data["source"] == "grok"
            assert data["profiles"][0]["name"] == "Router Test Bot"

    async def test_preview_migration_archive_route_invalid(self, rt, monkeypatch):
        monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
        async with client_for(app_for(rt)) as client:
            resp = await client.post("/api/v1/agents/import/migration/preview", content=b"invalid json")
            assert resp.status_code == 422
            assert resp.json()["detail"]["error_code"] == "invalid_migration_archive"

    async def test_preview_openbot_database_route(self, rt, monkeypatch):
        monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
        db_bytes = _create_openbot_db(15)
        async with client_for(app_for(rt)) as client:
            resp = await client.post(
                "/api/v1/agents/import/openbot/preview",
                files={"file": ("openbot.sqlite", db_bytes, "application/octet-stream")},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["schema_version"] == 15
            assert data["profiles"][0]["name"] == "Research Bot"

    async def test_preview_openbot_database_route_invalid(self, rt, monkeypatch):
        monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
        monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
        async with client_for(app_for(rt)) as client:
            resp = await client.post(
                "/api/v1/agents/import/openbot/preview",
                files={"file": ("openbot.sqlite", b"not-a-sqlite-db", "application/octet-stream")},
            )
            assert resp.status_code == 422
            assert resp.json()["detail"]["error_code"] == "invalid_openbot_database"

