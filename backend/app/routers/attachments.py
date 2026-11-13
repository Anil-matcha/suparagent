"""Files attached to messages (docs/EVERYDAY.md)."""

import asyncio
import hashlib
import logging
import contextlib
import threading
from pathlib import Path

from fastapi import Request, APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel

from app.routers.threads import _thread_or_404, get_runtime
from app.services import attachments, audio_ingestion
from app.services.local_access_policy import require_local_access
from app.services import thread_store as store
from app.services.thread_runtime import ThreadRuntime

router = APIRouter(prefix="/threads", tags=["Attachments"], dependencies=[Depends(require_local_access)])

_READ_LIMIT = max(attachments.IMAGE_MAX_BYTES, audio_ingestion.MAX_BYTES, attachments.DOCUMENT_MAX_BYTES, attachments.VIDEO_MAX_BYTES) + 1


class AttachmentResponse(BaseModel):
    id: str
    name: str
    mime: str
    bytes: int
    # image | text | audio | document | video
    kind: str
    transcript: dict | None = None
    extracted_text: str | None = None


def _refuse(error: attachments.AttachmentError) -> HTTPException:
    return HTTPException(status_code=error.status, detail={"error_code": error.code, "message": error.message})


@router.post("/{thread_id}/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    thread_id: int, request: Request, file: UploadFile = File(...), consent_provider: str | None = Form(None), upload_id: str | None = Form(None), rt: ThreadRuntime = Depends(get_runtime)
):
    """Store one file for this conversation's next message. Send its id in
    `attachment_ids` with the turn."""
    await _thread_or_404(rt, thread_id)
    data = await file.read(_READ_LIMIT)
    if len(data) >= _READ_LIMIT:
        raise _refuse(
            attachments.AttachmentError(
                413, "too_large", f"{attachments.display_name(file.filename)} is over the upload limit."
            )
        )
    try:
        transcript = None
        if (file.content_type and file.content_type.startswith("audio/")) or Path(file.filename or "").suffix.lower() in audio_ingestion.TYPES:
            if upload_id is not None:
                audio_ingestion.require_consent(consent_provider)
                if not attachments._ID.fullmatch(upload_id):
                    raise attachments.AttachmentError(400, "invalid_upload_id", "Invalid upload identifier.")
                try:
                    existing, _ = attachments.load(thread_id, upload_id)
                except attachments.AttachmentError as error:
                    if error.code != "attachment_not_found":
                        raise
                else:
                    if existing.kind != "audio" or existing.name != attachments.display_name(file.filename) or existing.sha256 != hashlib.sha256(data).hexdigest():
                        raise attachments.AttachmentError(409, "upload_id_conflict", "This upload identifier belongs to a different file.")
                    return AttachmentResponse(**existing.public())
            transcript = await _while_connected(request, audio_ingestion.transcribe(attachments.display_name(file.filename), data, consent_provider))
        await _thread_or_404(rt, thread_id)
        async with rt.session() as db:
            thread = await store._locked_thread(db, thread_id)
            if thread.owner_id != rt.owner_id:
                raise HTTPException(status_code=404, detail="Thread not found")
            saved = await run_in_threadpool(attachments.save, thread_id, file.filename, file.content_type, data, transcript=transcript, upload_id=upload_id)
            await db.commit()
    except store.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except attachments.AttachmentError as error:
        raise _refuse(error) from None
    return AttachmentResponse(**saved.public())


@router.get("/{thread_id}/attachments/{attachment_id}")
async def get_attachment(thread_id: int, attachment_id: str, rt: ThreadRuntime = Depends(get_runtime)):
    """The attached file's bytes. Images are shown inline; text is served as
    plain text. Never interpreted by the browser as a page."""
    await _thread_or_404(rt, thread_id)
    try:
        attachment, data = await run_in_threadpool(attachments.read_bytes, thread_id, attachment_id)
    except attachments.AttachmentError as error:
        raise _refuse(error) from None
    media = attachment.mime if attachment.kind in ("image", "audio", "video", "document") else "text/plain; charset=utf-8"
    return Response(
        content=data,
        media_type=media,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/{thread_id}/attachments/{attachment_id}", status_code=204)
async def discard_attachment(thread_id: int, attachment_id: str, rt: ThreadRuntime = Depends(get_runtime)):
    """Discard an unused upload, without removing an attachment in a turn."""
    if not attachments._ID.fullmatch(attachment_id):
        raise HTTPException(status_code=404, detail="No such attachment")
    async with rt.session() as db:
        try:
            thread = await store._locked_thread(db, thread_id)
        except store.ThreadNotFound:
            raise HTTPException(status_code=404, detail="Thread not found") from None
        if thread.owner_id != rt.owner_id:
            raise HTTPException(status_code=404, detail="Thread not found")
        events = await store.list_all_events(db, thread_id)
        if any(item.get("id") == attachment_id for event in events if event.type == "turn.started" for item in event.data.get("attachments", [])):
            raise HTTPException(status_code=409, detail={"error_code": "attachment_in_use", "message": "This attachment is part of a conversation."})
        try:
            _, path = attachments._paths(thread_id, attachment_id)
        except attachments.AttachmentError as error:
            if error.code != "attachment_not_found":
                raise _refuse(error) from None
            path = None
        folder = attachments.thread_dir(thread_id)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        import os
        os.chmod(folder.parent, 0o700)
        os.chmod(folder, 0o700)
        (folder / f"{attachment_id}.discarded").touch(mode=0o600, exist_ok=True)
        if path is not None:
            path.unlink(missing_ok=True)
            (path.parent / f"{attachment_id}.json").unlink(missing_ok=True)
        await db.commit()
    return Response(status_code=204)


@router.get("/audio/capabilities")
async def audio_capabilities():
    return await asyncio.to_thread(audio_ingestion.capabilities)


class DictationResponse(BaseModel):
    text: str
    duration_seconds: float = 0.0
    device: str = "cpu"


@router.post("/audio/dictate", response_model=DictationResponse)
async def dictate_audio(file: UploadFile = File(...)):
    """Transcribe a microphone recording using local Whisper on CPU/GPU."""
    from app.services import local_whisper
    data = await file.read(audio_ingestion.MAX_BYTES + 1)
    if len(data) > audio_ingestion.MAX_BYTES:
        raise _refuse(
            attachments.AttachmentError(413, "too_large", "Voice recording is over the upload limit.")
        )
    if not data:
        raise _refuse(
            attachments.AttachmentError(400, "empty_file", "The voice recording is empty.")
        )
    filename = file.filename or "recording.webm"
    try:
        result = await asyncio.to_thread(local_whisper.transcribe_bytes, data, filename)
        return DictationResponse(
            text=result.get("text", "").strip(),
            duration_seconds=result.get("duration_seconds", 0.0),
            device=result.get("device", "cpu"),
        )
    except ImportError:
        raise HTTPException(status_code=503, detail={"error_code": "dictation_unavailable", "message": "Local dictation is not available on this machine."})
    except Exception:
        logging.getLogger(__name__).exception("dictation failed")
        raise HTTPException(status_code=500, detail={"error_code": "dictation_failed", "message": "Dictation failed. Try again."})


class AudioURLRequest(BaseModel):
    url: str
    consent_provider: str
    upload_id: str | None = None


@router.post("/{thread_id}/attachments/audio-url", response_model=AttachmentResponse, status_code=201)
async def import_audio_url(thread_id: int, body: AudioURLRequest, request: Request, rt: ThreadRuntime = Depends(get_runtime)):
    await _thread_or_404(rt, thread_id)
    try:
        audio_ingestion.require_consent(body.consent_provider)
        if body.upload_id is not None:
            if not attachments._ID.fullmatch(body.upload_id):
                raise attachments.AttachmentError(400, "invalid_upload_id", "Invalid upload identifier.")
            try:
                existing, _ = attachments.load(thread_id, body.upload_id)
            except attachments.AttachmentError as error:
                if error.code != "attachment_not_found":
                    raise
            else:
                if existing.kind != "audio" or not existing.transcript or existing.transcript.get("source_url") != body.url:
                    raise attachments.AttachmentError(409, "upload_id_conflict", "This upload identifier belongs to a different file.")
                return AttachmentResponse(**existing.public())
        cancelled = threading.Event()
        try:
            name, data = await _while_connected(request, asyncio.to_thread(audio_ingestion.fetch_url, body.url, cancelled))
        finally:
            cancelled.set()
        name = attachments.display_name(name)
        transcript = await _while_connected(request, audio_ingestion.transcribe(name, data, body.consent_provider))
        transcript["source_url"] = body.url
        await _thread_or_404(rt, thread_id)
        async with rt.session() as db:
            thread = await store._locked_thread(db, thread_id)
            if thread.owner_id != rt.owner_id:
                raise HTTPException(status_code=404, detail="Thread not found")
            if body.upload_id is not None:
                try:
                    current, _ = attachments.load(thread_id, body.upload_id)
                except attachments.AttachmentError as error:
                    if error.code != "attachment_not_found":
                        raise
                else:
                    if current.kind != "audio" or not current.transcript or current.transcript.get("source_url") != body.url:
                        raise attachments.AttachmentError(409, "upload_id_conflict", "This upload identifier belongs to a different file.")
            saved = attachments.save(thread_id, name, None, data, transcript=transcript, upload_id=body.upload_id)
            await db.commit()
    except store.ThreadNotFound:
        raise HTTPException(status_code=404, detail="Thread not found") from None
    except attachments.AttachmentError as error:
        raise _refuse(error) from None
    return AttachmentResponse(**saved.public())


async def _while_connected(request: Request, operation):
    """Cancel outbound provider requests when the user disconnects; never save partial output."""
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.1)
            if await request.is_disconnected():
                raise attachments.AttachmentError(499, "audio_cancelled", "Audio processing was cancelled. No attachment was stored.")
        return await task
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
