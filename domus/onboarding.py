"""Public shell; household data uses Supabase user JWT and RLS directly."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()
ROOT = Path(__file__).parent / "panel"


def enabled():
    return os.getenv("DOMUS_ONBOARDING_ENABLED", "").lower() == "true"


def headers():
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    parsed = urlsplit(url)
    origin = f"https://{parsed.netloc}" if parsed.scheme == "https" and parsed.netloc and not parsed.username else ""
    return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' {origin}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"}


@router.get("/api/domus/onboarding/config")
async def config():
    if not enabled():
        raise HTTPException(404, "domus_onboarding_disabled")
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_ANON_KEY", "")
    if not url or not key:
        return JSONResponse({"error": "domus_onboarding_not_configured"}, status_code=503, headers=headers())
    return JSONResponse({"supabaseUrl": url.rstrip("/"), "supabaseAnonKey": key}, headers=headers())


@router.get("/domus")
@router.get("/domus/")
async def index():
    if not enabled():
        raise HTTPException(404, "domus_onboarding_disabled")
    return FileResponse(ROOT / "index.html", headers=headers())


@router.get("/domus/assets/{filename}")
async def asset(filename: str):
    if not enabled() or filename not in {"app.js", "style.css", "supabase.js"}:
        raise HTTPException(404)
    path = ROOT / filename if filename != "supabase.js" else ROOT.parent.parent / "manager/assets/vendor/supabase.js"
    return FileResponse(path, headers=headers())

# Panel questions are scoped to the authenticated Supabase user's visible home.
from uuid import UUID
from types import SimpleNamespace
import httpx
from pydantic import BaseModel, Field
from fastapi import Header
from domus.intelligence import NexxusIntelligence


class PanelQuestion(BaseModel):
    home_id: UUID
    text: str = Field(min_length=1, max_length=1500)
    consent: bool = False


@router.post('/api/domus/panel/ask')
async def panel_question(question: PanelQuestion, authorization: str = Header(default='')):
    if not enabled():
        raise HTTPException(404)
    if not authorization.startswith('Bearer ') or len(authorization) > 8192:
        raise HTTPException(401, 'authentication_required')
    if not question.text.strip():
        raise HTTPException(422, 'question_required')
    url = os.getenv('SUPABASE_URL', '').rstrip('/')
    key = os.getenv('SUPABASE_ANON_KEY', '')
    if not url.startswith('https://') or not key:
        raise HTTPException(503, 'not_configured')
    request_headers = {'apikey': key, 'Authorization': authorization}
    try:
        async with httpx.AsyncClient(timeout=3.0, follow_redirects=False) as api:
            # Auth verifies the JWT remotely; it is never decoded and trusted locally.
            identity = await api.get(url + '/auth/v1/user', headers=request_headers)
            if identity.status_code != 200 or not identity.json().get('id'):
                raise HTTPException(401, 'invalid_session')
            # Use the SAME user JWT and public key so RLS decides household access.
            visible = await api.get(url + '/rest/v1/domus_homes', headers=request_headers,
                                    params={'select': 'id', 'id': 'eq.' + str(question.home_id), 'limit': '1'})
            if visible.status_code != 200:
                raise HTTPException(503, 'home_unavailable')
            if not visible.json():
                raise HTTPException(403, 'home_not_authorized')
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, 'authentication_unavailable') from None
    if not question.consent:
        raise HTTPException(400, 'question_consent_required')
    # No household records, account identifiers or calendar data enter the prompt.
    command = SimpleNamespace(text=question.text.strip(), panel=True)
    answer = await NexxusIntelligence().execute(command)
    return JSONResponse({'answer': answer}, headers=headers())
