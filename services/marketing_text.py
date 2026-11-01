"""
AITA Marketing — «Escribir con IA» sobre un contenido en borrador.

Toma el contenido y el Brand Kit, llama al redactor (marketing_copywriter), guarda
el texto y una versión por red (platform_variants) y deja el contenido como
borrador para que una persona lo revise. Cada intento queda registrado (sin
texto) en marketing_text_generations, que también sirve de límite diario: el cupo se
reserva en la base (marketing_reserve_text_generation) antes de llamar a Claude y la
fila se liquida después. Si no se puede reservar, no se llama a Claude.
"""
import logging
import os
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


def _reserve(svc, c, content_id: str) -> int:
    """Cuenta y reserva el cupo en una transacción, antes de gastar nada. Sin reserva no hay redacción."""
    try:
        res = svc.db.rpc("marketing_reserve_text_generation", {
            "p_tenant": c.tenant_id, "p_content": content_id, "p_actor": c.user["id"], "p_model": cw.model()[:100],
            "p_limit": daily_limit()}) or {}
    except Exception as e:
        logger.error(f"MARKETING_TEXT_RESERVE {type(e).__name__}")
        raise PortalError("text_ai_unavailable", 503)
    if res.get("status") != "reserved":
        raise PortalError("text_ai_daily_limit", 429)
    return res["id"]


def _settle(svc, c, rid: int, status: str, model: str, usage: Optional[Dict[str, int]] = None) -> None:
    try:
        svc.db.update("marketing_text_generations", {"id": f"eq.{rid}", "tenant_id": f"eq.{c.tenant_id}",
                                                     "status": "eq.pending"},
                      {"status": status, "model": model[:100], **(usage or {"input_tokens": 0, "output_tokens": 0})})
    except Exception as e:                              # la reserva queda 'pending' y sigue contando: falla cerrado
        logger.error(f"MARKETING_TEXT_SETTLE {type(e).__name__}")


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
    brand = svc.brand(jwt, tenant_id)["profile"]
    rid = _reserve(svc, c, cur["id"])
    try:
        out = cw.write(cur, brand, request or "", client=client)
    except DomainError as e:
        _settle(svc, c, rid, "rejected" if e.code == "ai_output_rejected" else "failed", cw.model())
        raise PortalError(e.code, e.status)
    except Exception:
        _settle(svc, c, rid, "failed", cw.model())
        raise
    values = {"caption": out["caption"] or None, "cta": out["cta"] or None, "hashtags": _tags(out["hashtags"])}
    if out["script"]:
        values["script"] = out["script"]
    values["platform_variants"] = platforms.variants({**cur, **values}, out["per_platform"])
    rows = svc.db.update("marketing_content", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                               "status": f"eq.{cur['status']}"}, {**values, "updated_by": c.user["id"]})
    _settle(svc, c, rid, "succeeded", out["model"], out["usage"])
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
