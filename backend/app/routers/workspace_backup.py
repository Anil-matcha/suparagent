"""Encrypted workspace backup export, preview, and staged restore API."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.desktop import profile
from app.server import backup
from app.services.local_access_policy import require_local_access

router = APIRouter(prefix="/workspace-backup", tags=["Workspace Backup"], dependencies=[Depends(require_local_access)])

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB safety limit
_MIN_PASSPHRASE = 12


@dataclass
class _ExportJob:
    id: str
    status: str = "running"  # running | done | error
    created_at: float = field(default_factory=time.time)
    archive_path: Path | None = None
    archive_name: str | None = None
    summary: dict | None = None
    error: str | None = None


@dataclass
class _PreviewEntry:
    id: str
    archive_path: Path
    temp_dir: Path
    summary: dict
    created_at: float = field(default_factory=time.time)


_EXPORT_JOBS: dict[str, _ExportJob] = {}
_PREVIEWS: dict[str, _PreviewEntry] = {}


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passphrase: str = Field(min_length=_MIN_PASSPHRASE, max_length=1024)
    skip_workspaces: bool = False


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=8, max_length=64)
    passphrase: str = Field(min_length=1, max_length=1024)
    confirm: str = Field(min_length=1, max_length=32)


def _prune_stale() -> None:
    cutoff = time.time() - 3600
    for key in [k for k, v in _EXPORT_JOBS.items() if v.created_at < cutoff]:
        _EXPORT_JOBS.pop(key, None)
    for key in [k for k, v in _PREVIEWS.items() if v.created_at < cutoff]:
        entry = _PREVIEWS.pop(key, None)
        if entry is not None:
            shutil.rmtree(entry.temp_dir, ignore_errors=True)


def _backup_target_dir() -> Path:
    if profile.active():
        return profile.current().backup_dir
    where = backup.locations()
    return backup.default_output_dir(where["database"])


def _data_dir() -> Path:
    if profile.active():
        return profile.current().data_dir
    return backup.locations()["database"].parent


@router.get("/status")
async def workspace_backup_status():
    """Return live size estimate and supported restore mode."""
    try:
        info = await asyncio.to_thread(backup.estimate_backup)
    except backup.BackupError as exc:
        raise HTTPException(400, detail={"error_code": "backup_unavailable", "message": str(exc)}) from None
    return {**info, "staged_restore_supported": profile.active()}


@router.post("/export", status_code=202)
async def start_workspace_export(payload: ExportRequest):
    """Create an encrypted backup archive asynchronously."""
    _prune_stale()
    job_id = uuid4().hex
    job = _ExportJob(id=job_id)
    _EXPORT_JOBS[job_id] = job
    out_dir = _backup_target_dir()

    async def _run():
        try:
            report = await asyncio.to_thread(
                backup.create_backup,
                out_dir,
                skip_workspaces=payload.skip_workspaces,
                passphrase=payload.passphrase,
            )
            total_files = sum(r.files for r in report.parts.values())
            total_bytes = sum(r.bytes for r in report.parts.values())
            job.archive_path = report.archive
            job.archive_name = report.archive.name
            job.summary = {
                "files": total_files,
                "bytes": report.archive.stat().st_size if report.archive.exists() else total_bytes,
                "parts": {name: {"files": r.files, "bytes": r.bytes} for name, r in report.parts.items()},
                "skipped": report.skipped,
            }
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)
            job.status = "error"

    asyncio.create_task(_run())
    return {"job_id": job_id, "status": job.status}


@router.get("/export/{job_id}")
async def get_export_status(job_id: str):
    job = _EXPORT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, detail={"error_code": "job_not_found", "message": "Export job not found."})
    return {
        "job_id": job.id,
        "status": job.status,
        "archive_name": job.archive_name,
        "summary": job.summary,
        "error": job.error,
    }


@router.get("/download/{job_id}")
async def download_export(job_id: str):
    job = _EXPORT_JOBS.get(job_id)
    if job is None or job.status != "done" or job.archive_path is None or not job.archive_path.is_file():
        raise HTTPException(404, detail={"error_code": "archive_missing", "message": "The backup archive is not ready or no longer exists."})
    return FileResponse(
        path=job.archive_path,
        filename=job.archive_name or job.archive_path.name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/preview")
async def preview_uploaded_backup(
    file: UploadFile = File(...),
    passphrase: str = Form(...),
):
    """Decrypt metadata and inspect an uploaded backup archive without modifying disk state."""
    _prune_stale()
    temp_dir = Path(tempfile.mkdtemp(prefix=".upload-preview-"))
    os.chmod(temp_dir, 0o700)
    dest = temp_dir / "uploaded.tar.gz.enc"
    total = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, detail={"error_code": "archive_too_large", "message": "Backup archive exceeds upload limit."})
                out.write(chunk)
        summary = await asyncio.to_thread(backup.preview_backup, dest, passphrase)
        preview_id = uuid4().hex
        _PREVIEWS[preview_id] = _PreviewEntry(id=preview_id, archive_path=dest, temp_dir=temp_dir, summary=summary)
        return {"preview_id": preview_id, "summary": summary}
    except backup.BackupError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(422, detail={"error_code": "invalid_backup", "message": str(exc)}) from None
    except HTTPException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


@router.post("/restore")
async def stage_workspace_restore(payload: RestoreRequest):
    """Stage a verified backup archive so the next clean startup applies it safely."""
    if payload.confirm.strip().upper() != "RESTORE":
        raise HTTPException(422, detail={"error_code": "confirmation_required", "message": 'Type "RESTORE" to confirm replacing local workspace data.'})
    entry = _PREVIEWS.get(payload.preview_id)
    if entry is None or not entry.archive_path.is_file():
        raise HTTPException(404, detail={"error_code": "preview_expired", "message": "Preview expired or missing. Upload the backup again."})
    try:
        summary = await asyncio.to_thread(backup.stage_pending_restore, entry.archive_path, _data_dir(), payload.passphrase)
    except backup.BackupError as exc:
        raise HTTPException(422, detail={"error_code": "restore_failed", "message": str(exc)}) from None
    finally:
        _PREVIEWS.pop(payload.preview_id, None)
        shutil.rmtree(entry.temp_dir, ignore_errors=True)
    return {"staged": True, "restart_required": True, "summary": summary}
