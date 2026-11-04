"""Matrix API for an existing tenant; no provisioning or write operations."""
import logging
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from aita_orchestrator import AITAOrchestrator
from services.member_portal import PortalError

logger = logging.getLogger(__name__)


class ReadCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: str = Field(min_length=1, max_length=36)
    agent: str = Field(min_length=1, max_length=40)
    capability: str = Field(min_length=1, max_length=40)


def build_router(get_db: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/manager/aita")

    def bearer(request):
        auth = request.headers.get("authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else ""

    async def call(fn):
        try:
            return await run_in_threadpool(fn)
        except PortalError as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status)
        except Exception as exc:
            logger.error("AITA_MATRIX_ERROR %s", type(exc).__name__)
            return JSONResponse({"error": "server_error"}, status_code=500)

    @router.get("/agents")
    async def agents(request: Request, tenant_id: str = ""):
        return await call(lambda: AITAOrchestrator(get_db()).list_agents(bearer(request), tenant_id))

    @router.post("/read")
    async def read(request: Request, command: ReadCommand):
        return await call(lambda: AITAOrchestrator(get_db()).run(
            bearer(request), command.tenant_id, command.agent, command.capability))

    return router
