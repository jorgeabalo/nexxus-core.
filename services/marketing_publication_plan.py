"""Plan público de publicaciones y ampliación mensual recurrente (hasta cancelación).

El tenant ve publicaciones (incluidas, adicionales, utilizadas, disponibles) y precios comerciales de +1, +4 y +8.
Nunca presupuestos técnicos, proveedores, costos internos ni el estado interno de facturación.

* Una sola ampliación vigente por tenant y ciclo. Un cambio reemplaza la cantidad del PRÓXIMO ciclo (nunca se
  acumula con la vigente); sin ampliación vigente, empieza en el ciclo actual.
* Límite efectivo de un ciclo = publicaciones incluidas + ampliación vigente en ese ciclo. Se aplica al programar,
  según el mes (zona del tenant) de la fecha de publicación; la base lo exige también con concurrencia.
* No se cobra en este PR: la autorización del owner deja el registro en `authorized_uninvoiced` (autorizado, sin
  factura ni pago) para que AITA lo añada como línea recurrente a la factura mensual. Autorizar ≠ pago capturado.
* Las operaciones son funciones atómicas de la base (marketing_submit/decide/cancel_publication_addon).
"""
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from services.member_portal import PortalError

BLOCKS = ((8, 3500), (4, 2000), (1, 600))          # +8 = $35, +4 = $20, +1 = $6 (precio comercial)
MAX_ADDITIONAL = 500
MAX_PER_WEEK = MAX_ADDITIONAL // 4
WEEKS_PER_MONTH = 4      # facturación y cuota mensuales; "por semana" solo reparte: N/semana = 4N por ciclo
KEY = re.compile(r"^[A-Za-z0-9_-]{16,120}$")
UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
EFFECTIVE = ("authorized_uninvoiced", "cancelled", "replaced")      # cuentan en los ciclos [inicio, fin)
PUBLIC_COLS = ("id,cadence,per_week,additional_publications,status,starts_cycle,ends_cycle,requested_by,"
               "requested_at,authorized_at,cancelled_at")
ERRORS = {"idempotency_conflict": 409, "forbidden": 403, "owner_authorization_required": 403, "not_found": 404,
          "addon_not_pending": 409, "addon_not_active": 409}


def _int(v: Any, lo: int, hi: int) -> int:
    if isinstance(v, bool):
        raise PortalError("invalid_publication_addon", 400)
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise PortalError("invalid_publication_addon", 400)
    if str(v).strip() != str(n) or not (lo <= n <= hi):
        raise PortalError("invalid_publication_addon", 400)
    return n


def quote(additional: Any) -> Dict[str, Any]:
    """Combinación exacta más económica (los bloques grandes siempre cuestan menos por publicación)."""
    count = _int(additional, 1, MAX_ADDITIONAL)
    left, cents, blocks = count, 0, []
    for size, price in BLOCKS:
        qty, left = divmod(left, size)
        if qty:
            blocks.append({"size": size, "quantity": qty, "subtotal": round(qty * price / 100, 2)})
            cents += qty * price
    return {"additional_publications": count, "amount": round(cents / 100, 2), "currency": "USD", "blocks": blocks}


def spec(body: Dict[str, Any]) -> Dict[str, Any]:
    cadence = body.get("cadence") or "monthly"
    if cadence == "weekly":
        per_week = _int(body.get("per_week"), 1, MAX_PER_WEEK)
        return {"cadence": "weekly", "per_week": per_week, **quote(per_week * WEEKS_PER_MONTH)}
    if cadence != "monthly":
        raise PortalError("invalid_publication_addon", 400)
    return {"cadence": "monthly", "per_week": None, **quote(body.get("additional_publications"))}


def cycle_of(c, at: Optional[datetime] = None) -> str:
    return (at or c.now).astimezone(c.zone).date().replace(day=1).isoformat()


def shift(cycle: str, months: int = 1) -> str:
    d = date.fromisoformat(cycle)
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1).isoformat()


def can_authorize(db, c) -> bool:
    if c.role == "owner":
        return True
    if c.role != "manager":
        return False
    p = db.select("marketing_purchase_permissions", {"tenant_id": f"eq.{c.tenant_id}",
                                                     "select": "managers_can_authorize", "limit": "1"}) or []
    return bool(p and p[0].get("managers_can_authorize") is True)


def _rows(db, c) -> List[Dict[str, Any]]:
    return db.select("marketing_publication_addons", {"tenant_id": f"eq.{c.tenant_id}", "select": PUBLIC_COLS,
                                                      "order": "requested_at.desc", "limit": "500"}) or []


def _covers(row: Dict[str, Any], cycle: str) -> bool:
    return (row.get("status") in EFFECTIVE and row.get("starts_cycle") is not None
            and str(row["starts_cycle"])[:10] <= cycle
            and (not row.get("ends_cycle") or str(row["ends_cycle"])[:10] > cycle))


def addon_for_cycle(rows: List[Dict[str, Any]], cycle: str) -> int:
    return sum(int(r["additional_publications"]) for r in rows if _covers(r, cycle))


def effective_limit(db, c, cycle: str) -> Optional[int]:
    """Límite efectivo del ciclo (None = sin límite). Lo mismo calcula private.marketing_post_limit."""
    base = c.settings.get("monthly_post_limit")
    return None if base is None else max(int(base), 0) + addon_for_cycle(_rows(db, c), cycle)


def public_addon(row: Dict[str, Any], cycle: str, me: Optional[str] = None) -> Dict[str, Any]:
    """Sin estado interno de facturación: solo lo que el cliente entiende."""
    q = quote(row["additional_publications"])
    st, start, end = row["status"], row.get("starts_cycle"), row.get("ends_cycle")
    start, end = (str(start)[:10] if start else None), (str(end)[:10] if end else None)
    state = {"requested": "pending", "declined": "declined"}.get(st)
    if state is None:
        state = ("upcoming" if start > cycle else "ended" if (end and end <= cycle)
                 else "ending" if end else "active")
    return {"id": row["id"], "state": state, "cadence": row.get("cadence") or "monthly", "per_week": row.get("per_week"),
            "additional_publications": q["additional_publications"], "amount": q["amount"], "currency": "USD",
            "blocks": q["blocks"], "starts_cycle": start, "ends_cycle": end, "requested_at": row.get("requested_at"),
            "requested_by_me": bool(me) and row.get("requested_by") == me}


def summary(db, c, used: int) -> Dict[str, Any]:
    offers = [{"size": size, "price": price / 100, "currency": "USD"} for size, price in reversed(BLOCKS)]
    base = c.settings.get("monthly_post_limit")
    if base is None:
        return {"included": None, "added": 0, "total": None, "used": int(used), "available": None, "offers": [],
                "current": None, "upcoming": None, "next_total": None, "pending": [], "can_authorize": False,
                "managers_can_authorize": False}
    cyc, me = cycle_of(c), (getattr(c, "user", None) or {}).get("id")
    nxt = shift(cyc)
    rows = _rows(db, c)
    included = max(int(base), 0)
    added = addon_for_cycle(rows, cyc)
    current = next((r for r in rows if _covers(r, cyc)), None)
    upcoming = next((r for r in rows if r.get("status") == "authorized_uninvoiced"
                     and str(r.get("starts_cycle"))[:10] > cyc), None)
    perm = db.select("marketing_purchase_permissions", {"tenant_id": f"eq.{c.tenant_id}",
                                                        "select": "managers_can_authorize", "limit": "1"}) or []
    return {"included": included, "added": added, "total": included + added, "used": int(used),
            "available": max(included + added - int(used), 0), "offers": offers, "cycle_start": cyc, "next_cycle": nxt,
            "current": public_addon(current, cyc, me) if current else None,
            "upcoming": public_addon(upcoming, cyc, me) if upcoming else None,
            "next_total": included + addon_for_cycle(rows, nxt),
            "pending": [public_addon(r, cyc, me) for r in rows if r.get("status") == "requested"],
            "can_authorize": can_authorize(db, c),
            "managers_can_authorize": bool(perm and perm[0].get("managers_can_authorize") is True)}


def _call(db, c, fn: str, args: Dict[str, Any]) -> Dict[str, Any]:
    res = db.rpc(fn, args) or {}
    if res.get("error"):
        raise PortalError(res["error"], ERRORS.get(res["error"], 409))
    return {**public_addon(res["addon"], cycle_of(c), c.user["id"]), "result": res.get("result")}


def submit(db, c, body: Dict[str, Any], authorize: bool) -> Dict[str, Any]:
    """authorize=False: solicitud (owner o manager). authorize=True: autorización (owner o manager delegado)."""
    if body.get("confirm") is not True:
        raise PortalError("confirmation_required", 400)
    key = str(body.get("idempotency_key") or "")
    if not KEY.fullmatch(key):
        raise PortalError("invalid_idempotency_key", 400)
    s = spec(body)
    if authorize and not can_authorize(db, c):
        raise PortalError("owner_authorization_required", 403)
    return _call(db, c, "marketing_submit_publication_addon", {
        "p_tenant": c.tenant_id, "p_actor": c.user["id"], "p_cadence": s["cadence"], "p_per_week": s["per_week"],
        "p_additional": s["additional_publications"], "p_authorize": authorize, "p_key": key})


def _id(addon_id: Any) -> str:
    if not UUID.fullmatch(str(addon_id or "")):
        raise PortalError("not_found", 404)
    return str(addon_id)


def decide(db, c, addon_id: Any, approve: bool, confirm: bool) -> Dict[str, Any]:
    if confirm is not True:
        raise PortalError("confirmation_required", 400)
    if not can_authorize(db, c):
        raise PortalError("owner_authorization_required", 403)
    return _call(db, c, "marketing_decide_publication_addon", {"p_tenant": c.tenant_id, "p_addon": _id(addon_id),
                                                                "p_actor": c.user["id"], "p_approve": bool(approve)})


def cancel(db, c, addon_id: Any, confirm: bool) -> Dict[str, Any]:
    """Termina la ampliación al cierre del ciclo en curso (lo de este mes ya está en uso)."""
    if confirm is not True:
        raise PortalError("confirmation_required", 400)
    if not can_authorize(db, c):
        raise PortalError("owner_authorization_required", 403)
    return _call(db, c, "marketing_cancel_publication_addon", {"p_tenant": c.tenant_id, "p_addon": _id(addon_id),
                                                                "p_actor": c.user["id"]})


def set_permission(db, c, managers_can_authorize: Any) -> Dict[str, Any]:
    """Solo el owner delega (o retira) el permiso de compras a los managers."""
    if c.role != "owner":
        raise PortalError("owner_only", 403)
    if not isinstance(managers_can_authorize, bool):
        raise PortalError("invalid_permission", 400)
    values = {"managers_can_authorize": managers_can_authorize, "updated_by": c.user["id"], "updated_at": c.now.isoformat()}
    if db.select("marketing_purchase_permissions", {"tenant_id": f"eq.{c.tenant_id}", "select": "tenant_id", "limit": "1"}):
        db.update("marketing_purchase_permissions", {"tenant_id": f"eq.{c.tenant_id}"}, values)
    else:
        db.insert("marketing_purchase_permissions", {"tenant_id": c.tenant_id, **values})
    return {"managers_can_authorize": managers_can_authorize}
