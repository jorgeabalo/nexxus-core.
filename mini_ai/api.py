"""Opt-in, authenticated text API. No business tools or persistent chat history."""
import json
import os
from functools import lru_cache
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from services.supabase_admin import SupabaseAdmin

router = APIRouter(prefix="/api/mini-ai", tags=["Nexxus Mini AI"])
MAX_BODY = 24000
SYSTEM = """Eres Nexxus Mini AI, un asistente personal. Responde en el idioma del
usuario de forma clara y breve. No eres Claudia ni la recepcionista de Golden Age.
No tienes acceso a datos de negocios, otros usuarios, archivos, dispositivos,
internet ni herramientas. No afirmes haber realizado acciones o consultado datos
actuales. No solicites claves ni contraseñas. No tienes memoria fuera del contexto
que acompaña esta consulta."""


@router.post('/health')
async def health():
    # Transport-only check: no credentials, provider, database, or feature activation.
    return JSONResponse({'status': 'ok'}, headers={'Cache-Control': 'no-store'})


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=2000)


class Chat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    messages: list[Message] = Field(min_length=1, max_length=10)

    def validate_turns(self):
        roles = [m.role for m in self.messages]
        if roles[-1] != "user" or any(r != ("user" if i % 2 == 0 else "assistant")
                                      for i, r in enumerate(roles)):
            raise HTTPException(422, "invalid_turns")
        if any(not m.content.strip() for m in self.messages):
            raise HTTPException(422, "empty_message")


@lru_cache(maxsize=1)
def database():
    return SupabaseAdmin()


async def answer(messages):
    from anthropic import AsyncAnthropic
    model = (os.getenv("MINI_AI_MODEL") or os.getenv("DOMUS_AI_MODEL")
             or os.getenv("ANTHROPIC_MODEL") or os.getenv("MODELO_CLAUDE")
             or "claude-haiku-4-5-20251001")
    async with AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"],
                              base_url="https://api.anthropic.com", timeout=25.0,
                              max_retries=0) as client:
        result = await client.messages.create(model=model, max_tokens=500,
                                              system=SYSTEM, messages=messages)
    text = "\n".join(b.text for b in result.content if b.type == "text").strip()
    if not text or result.stop_reason == "tool_use":
        raise ValueError("empty_answer")
    return text[:2000]


@router.post("/chat")
async def chat(request: Request):
    # All configuration gates fail closed. Existing services are unaffected.
    if os.getenv("MINI_AI_ENABLED", "").lower() != "true":
        raise HTTPException(503, "mini_ai_disabled")
    if not os.getenv("ANTHROPIC_API_KEY", "").strip():
        raise HTTPException(503, "provider_unconfigured")
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or not 1 <= len(auth[7:]) <= 8192:
        raise HTTPException(401, "invalid_session")
    db = database()
    try:
        user = await run_in_threadpool(db.user_from_jwt, auth[7:])
    except Exception:
        raise HTTPException(503, "auth_unavailable") from None
    if not user:
        raise HTTPException(401, "invalid_session")
    allowed = {v.strip() for v in os.getenv("MINI_AI_USER_IDS", "").split(",") if v.strip()}
    if user["id"] not in allowed:
        raise HTTPException(403, "mini_ai_access_denied")
    try:
        user_id = str(UUID(user["id"]))
    except (ValueError, TypeError):
        raise HTTPException(403, "mini_ai_access_denied") from None
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise HTTPException(415, "json_required")
    # Bound streamed bodies too; Content-Length alone is untrusted.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise HTTPException(413, "request_too_large")
    try:
        payload = Chat.model_validate(json.loads(body))
    except (ValueError, ValidationError, UnicodeError):
        # Do not echo user content, tokens, or validation input in errors.
        raise HTTPException(422, "invalid_request") from None
    payload.validate_turns()
    try:
        quota = await run_in_threadpool(db.rpc, "mini_ai_take_quota", {"p_user": user_id})
    except Exception:
        raise HTTPException(503, "quota_unavailable") from None
    if not quota or not isinstance(quota.get("allowed"), bool):
        raise HTTPException(503, "quota_unavailable")
    if not quota["allowed"]:
        raise HTTPException(429, "usage_limit", headers={"Retry-After": "60"})
    try:
        response = await answer([m.model_dump() for m in payload.messages])
    except Exception:
        # Provider diagnostics can contain secrets or user content; never expose them.
        raise HTTPException(502, "provider_unavailable") from None
    return JSONResponse({"assistant": "Nexxus Mini AI", "reply": response},
                        headers={"Cache-Control": "no-store"})
