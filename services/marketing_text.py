"""
AITA Marketing — «Escribir con IA» sobre un contenido en borrador.

Toma el contenido y el Brand Kit, llama al redactor (marketing_copywriter), guarda
el texto y una versión por red (platform_variants) y deja el contenido como
borrador para que una persona lo revise. Cada intento queda registrado (sin
texto) en marketing_text_generations, que también sirve de límite diario.
"""
import logging
import os
from datetime import timedelta
from typing import Any, Dict, Optional

from services import marketing_copywriter as cw
from services import marketing_domain as d
from services import marketing_platforms as platforms
from services.marketing_domain import DomainError
from services.member_portal import PortalError

logger = logging.getLogger(__name__)
VARIANT_SOURCES = ("caption", "cta", "hashtags", "channels", "format")


def daily_limit(env: Optional[Dict[str, str]] = None) -> int:
    env = os.environ if env is None else env
    try:
        return max(int(env.get("MARKETING_TEXT_DAILY_LIMIT", "30")), 0)
    except ValueError:
        return 30


def _log(svc, c, content_id: str, status: str, model: str, usage: Optional[Dict[str, int]] = None) -> None:
    try:
        svc.db.insert("marketing_text_generations", {
            "tenant_id": c.tenant_id, "content_id": content_id, "actor_id": c.user["id"], "model": model[:100],
            "status": status, **(usage or {"input_tokens": 0, "output_tokens": 0})})
    except Exception as e:                              # el registro nunca rompe la redacción
        logger.error(f"MARKETING_TEXT_LOG {type(e).__name__}")


def _tags(raw) -> list:
    out = []
    for t in raw or []:
        try:
            out += d.hashtags([t])
        except DomainError:
            continue                                     # un hashtag raro de la IA se descarta, no rompe el resto
    return list(dict.fromkeys(out))[:30]


def write_copy(svc, jwt: str, tenant_id: str, content_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    c = svc.ctx(jwt, tenant_id)
    svc._enabled(c)
    cur = svc._content(c, content_id)
    if cur["status"] not in d.EDITABLE:
        raise PortalError("not_editable", 409)
    if not cur.get("channels"):
        raise PortalError("channels_required", 400)
    try:
        request = d.text(body.get("request"), 1000, "invalid_request")
    except DomainError as e:
        raise PortalError(e.code, e.status)
    client = getattr(svc, "text_client", None)
    if client is None and not cw.enabled():
        raise PortalError("text_ai_unavailable", 503)
    since = (c.now - timedelta(days=1)).isoformat()
    used = svc.db.select("marketing_text_generations", {
        "tenant_id": f"eq.{c.tenant_id}", "created_at": f"gte.{since}", "select": "id", "limit": "1000"}) or []
    if len(used) >= daily_limit():
        raise PortalError("text_ai_daily_limit", 429)
    brand = svc.brand(jwt, tenant_id)["profile"]
    try:
        out = cw.write(cur, brand, request or "", client=client)
    except DomainError as e:
        _log(svc, c, cur["id"], "rejected" if e.code == "ai_output_rejected" else "failed", cw.model())
        raise PortalError(e.code, e.status)
    values = {"caption": out["caption"] or None, "cta": out["cta"] or None, "hashtags": _tags(out["hashtags"])}
    if out["script"]:
        values["script"] = out["script"]
    values["platform_variants"] = platforms.variants({**cur, **values}, out["per_platform"])
    rows = svc.db.update("marketing_content", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                               "status": f"eq.{cur['status']}"}, {**values, "updated_by": c.user["id"]})
    _log(svc, c, cur["id"], "succeeded", out["model"], out["usage"])
    if not rows:
        raise PortalError("conflict", 409)
    return {"content": svc.planner().present(c, rows[0]), "variants": values["platform_variants"]}


def refresh_variants(svc, c, before: Dict[str, Any], body: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Tras editar a mano el texto, las redes o el formato, las versiones por red se rehacen a partir del texto
    nuevo (las propias de la IA ya no corresponden). Editar solo el título o la fecha no las toca."""
    if not any(k in body and body[k] != before.get(k) for k in VARIANT_SOURCES):
        return row
    variants = platforms.variants(row)
    rows = svc.db.update("marketing_content", {"id": f"eq.{row['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                         {"platform_variants": variants})
    return rows[0] if rows else {**row, "platform_variants": variants}
