"""Environment identity endpoint for verifying remote server targets."""

from fastapi import APIRouter

from app.services import environment_identity

well_known_router = APIRouter(tags=["Environment"])
router = APIRouter(tags=["Environment"])


@well_known_router.get("/.well-known/suparagent/environment")
def well_known_environment():
    """Return this server's stable identity and supported capabilities."""
    return environment_identity.descriptor()


@router.get("/environment")
def api_environment():
    """Alias under /api/v1 for environment discovery."""
    return environment_identity.descriptor()
