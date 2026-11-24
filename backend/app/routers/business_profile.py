"""The business profile over HTTP: read by the person's screens, written only by the person."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.services import business_profile, business_profile_proposals
from app.services.local_access_policy import require_local_access

router = APIRouter(prefix="/business-profile", tags=["Business profile"], dependencies=[Depends(require_local_access)])


class ProfileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, str]
    expected_hash: str | None = None


@router.get("")
async def read_profile():
    return business_profile.read()


@router.put("")
async def write_profile(body: ProfileWrite):
    try:
        return business_profile.write(body.fields, expected_hash=body.expected_hash)
    except business_profile.UnknownField as exc:
        raise HTTPException(
            status_code=422, detail={"error_code": "unknown_field", "message": f"No such field: {exc}."}
        ) from None
    except business_profile.ProfileTooLarge as exc:
        raise HTTPException(
            status_code=413, detail={"error_code": "profile_too_large", "message": str(exc)}
        ) from None
    except business_profile.ProfileLooksSecret as exc:
        label = business_profile.FIELDS[exc.field][0]
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "profile_looks_secret",
                "field": exc.field,
                "message": f"{label} looks like it contains a key, token or password. "
                "Remove it: every agent can read this profile.",
            },
        ) from None
    except business_profile.ProfileConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "profile_changed",
                "message": "The business profile changed since it was opened.",
                "current": exc.current["fields"],
                "current_hash": exc.current["hash"],
            },
        ) from None


class ProposalAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overwrite: bool = False


@router.get("/proposals")
async def list_proposals():
    """Changes workers suggested. Only the person can accept or dismiss them."""
    return {"items": business_profile_proposals.list_pending(), "max_pending": business_profile_proposals.MAX_PENDING}


@router.post("/proposals/{proposal_id}/accept")
async def accept_proposal(proposal_id: str, body: ProposalAccept | None = None):
    overwrite = bool(body and body.overwrite)
    try:
        return business_profile_proposals.accept(proposal_id, overwrite=overwrite)
    except business_profile_proposals.ProposalNotFound:
        raise HTTPException(
            status_code=404, detail={"error_code": "proposal_not_found", "message": "That suggestion is gone."}
        ) from None
    except business_profile_proposals.ProposalStale as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "proposal_stale",
                "message": "That field changed after the suggestion was made. Review it, or accept to overwrite.",
                "current": exc.current,
            },
        ) from None
    except business_profile.ProfileConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error_code": "profile_changed",
                "message": "The business profile changed just now. Try again.",
                "current_hash": exc.current["hash"],
            },
        ) from None
    except (business_profile.ProfileTooLarge, business_profile.ProfileLooksSecret):
        raise HTTPException(
            status_code=422,
            detail={"error_code": "proposal_invalid", "message": "That suggestion cannot be saved to the profile."},
        ) from None


@router.post("/proposals/{proposal_id}/dismiss")
async def dismiss_proposal(proposal_id: str):
    try:
        business_profile_proposals.dismiss(proposal_id)
    except business_profile_proposals.ProposalNotFound:
        raise HTTPException(
            status_code=404, detail={"error_code": "proposal_not_found", "message": "That suggestion is gone."}
        ) from None
    return {"ok": True}
