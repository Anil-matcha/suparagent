"""Agent settings overview summary and live system prompt preview."""

from __future__ import annotations

import math
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import app_settings
from app.db.models import Agent, AgentRevision, Routine
from app.services import agent_memory, agent_skills, local_claude_runner, workspaces
from app.services.composio_bridge import apps_for


def _approx_tokens(byte_count: int) -> int:
    return math.ceil(byte_count / 4) if byte_count > 0 else 0


async def build_overview(db: AsyncSession, rt, agent: Agent) -> dict:
    """Return agent setup progress, workspace path, capability summary, and activity counts."""
    routine_count = int(
        (await db.execute(select(func.count()).select_from(Routine).where(Routine.agent_id == agent.id))).scalar()
        or 0
    )
    revision_count = int(
        (
            await db.execute(
                select(func.count()).select_from(AgentRevision).where(AgentRevision.agent_id == agent.id)
            )
        ).scalar()
        or 0
    )
    engines = await rt.engines.describe(only=agent.provider) if agent.provider in rt.engines.adapters else []
    engine_entry = engines[0] if engines else None
    skills = agent_skills.list_skills(agent.id)
    apps = apps_for(agent.connector_scopes)

    setup = [
        {"id": "instructions", "label": "Add standing instructions", "done": bool((agent.instructions or "").strip()), "section": "basics"},
        {"id": "model", "label": "Verify engine & model readiness", "done": bool(engine_entry and engine_entry.get("available")), "section": "model"},
        {"id": "memory", "label": "Review persistent notes & memory", "done": bool(agent.memory_enabled), "section": "memory"},
        {"id": "apps", "label": "Connect external apps or skills", "done": bool(apps or skills), "section": "apps"},
    ]

    return {
        "agent_id": agent.id,
        "who": {
            "name": agent.name,
            "title": agent.title,
            "description": agent.description,
            "instructions_lead": (agent.instructions or "").strip().splitlines()[0][:240]
            if (agent.instructions or "").strip()
            else None,
        },
        "setup": setup,
        "summary": {
            "provider": agent.provider,
            "provider_label": engine_entry.get("label", agent.provider) if engine_entry else agent.provider,
            "model": agent.model,
            "effort": agent.effort,
            "approval_mode": agent.approval_mode,
            "workspace_path": agent.workspace_path,
            "memory_enabled": bool(agent.memory_enabled),
            "skills_count": len(skills),
            "apps_count": len(apps),
            "routines_count": routine_count,
            "revisions_count": revision_count,
        },
    }


async def build_prompt_preview(db: AsyncSession, rt, agent: Agent) -> dict:
    """Assemble the real prompt sections this agent injects into a turn, with byte & token estimates."""
    sections: list[dict] = []

    def add_section(sec_id: str, label: str, text: str | None) -> None:
        clean = (text or "").strip()
        if not clean:
            return
        redacted, _ = agent_memory.redact_secrets(clean)
        size = len(redacted.encode("utf-8"))
        sections.append({"id": sec_id, "label": label, "text": redacted, "bytes": size})

    add_section("instructions", "Standing instructions", agent.instructions)

    reply_style = getattr(agent, "reply_style", "default") or "default"
    if reply_style == "conversational":
        add_section(
            "reply_style",
            "Reply style (Conversational)",
            "Respond in a concise, natural conversational style. Prefer direct, human-paced phrasing over heavy boilerplate headings unless asked.",
        )

    if agent.memory_enabled:
        mem_doc = agent_memory.read_memory(agent.id)
        if mem_doc.text.strip():
            loaded, truncated = agent_memory.loaded_part(mem_doc.text)
            label = "Memory notes (MEMORY.md · truncated)" if truncated else "Memory notes (MEMORY.md)"
            add_section("memory", label, loaded)

    if app_settings.resolve("LOCAL_TASK_WORKSPACE_ISOLATION").effective_value:
        cwd = Path(agent.workspace_path) if agent.workspace_path else (local_claude_runner.workspace_dir() / "agents" / str(agent.id))
        ws_note = workspaces.workspace_locations_prompt(
            agent.id,
            cwd,
            agent.workspace_path,
            base=local_claude_runner.workspace_dir(),
        )
        add_section("workspace", "Workspace folder layout", ws_note)

    enabled_skills = [s for s in agent_skills.list_skills(agent.id) if s.enabled]
    if enabled_skills:
        add_section("skills", f"Enabled skills ({len(enabled_skills)})", agent_skills.prompt_block(enabled_skills))

    tool_scope = getattr(agent, "tool_scope", None)
    if isinstance(tool_scope, dict) and (tool_scope.get("allow") or tool_scope.get("deny")):
        parts = []
        if tool_scope.get("allow"):
            parts.append("Allow: " + ", ".join(tool_scope["allow"]))
        if tool_scope.get("deny"):
            parts.append("Deny: " + ", ".join(tool_scope["deny"]))
        add_section("tool_scope", "Tool scope constraints", "\n".join(parts))

    total_bytes = sum(s["bytes"] for s in sections)
    return {
        "sections": sections,
        "total_bytes": total_bytes,
        "approx_tokens": _approx_tokens(total_bytes),
        "note": "Shows the standing instructions, memory, skills, and context loaded on every new turn (secrets redacted).",
    }
