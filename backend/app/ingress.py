"""The webhook ingress: a separate small app that serves only `GET /health`,
`POST /hooks/{routine_id}` (docs/ROUTINES.md) and Composio's signed trigger events
(`POST /hooks/composio/{route_id}`), for exposing webhooks
through a tunnel or reverse proxy without exposing the rest of the API.

    uv run uvicorn app.ingress:app --host 127.0.0.1 --port 8788

It shares the database with the backend and only queues runs; it never
starts one (it has no scheduler), so the backend's next poll starts them.
"""

from fastapi import FastAPI

from app.core.logging import configure_logging
from app.routers.composio_hooks import router as composio_hooks_router
from app.routers.hooks import router as hooks_router

configure_logging()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    return {"ok": True, "app": "suparagent-webhooks"}


app.include_router(hooks_router)
app.include_router(composio_hooks_router)
