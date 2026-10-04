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
