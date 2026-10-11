"""Plan público de publicaciones y ampliaciones mensuales (recurrentes hasta cancelación).

El tenant ve publicaciones (incluidas, adicionales, utilizadas, disponibles) y precios comerciales de +1, +4 y
+8. Nunca presupuestos técnicos de IA, proveedores ni costos internos. No se cobra en este PR: se registra la
solicitud (manager) y la autorización (owner, o manager con permiso delegado) para la factura mensual.
La base de datos calcula el importe y protege las transiciones (requested → authorized | declined;
authorized → cancelled); este módulo aplica las mismas reglas antes, para responder con errores claros.
"""
import re
from datetime import date
from typing import Any, Dict, List, Optional

from services.member_portal import PortalError

BLOCKS = ((8, 3500), (4, 2000), (1, 600))          # +8 = USD 35, +4 = USD 20, +1 = USD 6 (precio comercial)
MAX_ADDITIONAL = 500
MAX_PER_WEEK = MAX_ADDITIONAL // 4
WEEKS_PER_MONTH = 4                                 # 1 por semana = 4 al mes (así se presenta al cliente)
KEY = re.compile(r"^[A-Za-z0-9_-]{16,120}$")
ACTIVE = ("authorized", "cancelled")                # cancelada = sigue vigente hasta ends_cycle
PUBLIC_COLS = ("id,cadence,per_week,additional_publications,amount_cents,starts_cycle,ends_cycle,status,"
               "requested_by,requested_at,decided_at,cancelled_at")


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
    """Cantidad mensual o frecuencia semanal (una forma de repartir la cuota: N por semana = 4N al mes)."""
    cadence = body.get("cadence") or "monthly"
    if cadence == "weekly":
        per_week = _int(body.get("per_week"), 1, MAX_PER_WEEK)
        return {"cadence": "weekly", "per_week": per_week, **quote(per_week * WEEKS_PER_MONTH)}
    if cadence != "monthly":
        raise PortalError("invalid_publication_addon", 400)
    return {"cadence": "monthly", "per_week": None, **quote(body.get("additional_publications"))}


def cycle_start(c) -> str:
    return c.now.astimezone(c.zone).date().replace(day=1).isoformat()


def next_cycle(c) -> str:
    d = date.fromisoformat(cycle_start(c))
    return (date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)).isoformat()


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


def _active(row: Dict[str, Any], cycle: str) -> bool:
    return (row.get("status") in ACTIVE and str(row.get("starts_cycle")) <= cycle
            and (not row.get("ends_cycle") or str(row["ends_cycle"]) > cycle))


def public_addon(row: Dict[str, Any], me: Optional[str] = None) -> Dict[str, Any]:
    q = quote(row["additional_publications"])
    return {"id": row["id"], "status": row["status"], "cadence": row.get("cadence") or "monthly",
            "per_week": row.get("per_week"), "additional_publications": q["additional_publications"],
            "amount": q["amount"], "currency": "USD", "blocks": q["blocks"],
            "starts_cycle": str(row.get("starts_cycle")), "ends_cycle": row.get("ends_cycle"),
            "requested_at": row.get("requested_at"), "decided_at": row.get("decided_at"),
            "requested_by_me": bool(me) and row.get("requested_by") == me}


def summary(db, c, used: int) -> Dict[str, Any]:
    offers = [{"size": size, "price": price / 100, "currency": "USD"} for size, price in reversed(BLOCKS)]
    base = c.settings.get("monthly_post_limit")
    if base is None:
        return {"included": None, "added": 0, "total": None, "used": int(used), "available": None, "offers": [],
                "active": [], "pending": [], "can_authorize": False, "managers_can_authorize": False}
    cycle, me = cycle_start(c), (getattr(c, "user", None) or {}).get("id")
    rows = _rows(db, c)
    active = [r for r in rows if _active(r, cycle)]
    included = max(int(base), 0)
    added = sum(int(r["additional_publications"]) for r in active)
    perm = db.select("marketing_purchase_permissions", {"tenant_id": f"eq.{c.tenant_id}",
                                                        "select": "managers_can_authorize", "limit": "1"}) or []
    return {"included": included, "added": added, "total": included + added, "used": int(used),
            "available": max(included + added - int(used), 0), "offers": offers, "cycle_start": cycle,
            "next_cycle": next_cycle(c),
            "active": [public_addon(r, me) for r in active],
            "pending": [public_addon(r, me) for r in rows if r.get("status") == "requested"],
            "can_authorize": can_authorize(db, c),
            "managers_can_authorize": bool(perm and perm[0].get("managers_can_authorize") is True)}


def _existing(db, c, key: str) -> Optional[Dict[str, Any]]:
    rows = db.select("marketing_publication_addons", {"tenant_id": f"eq.{c.tenant_id}", "idempotency_key": f"eq.{key}",
                                                      "select": "*", "limit": "1"}) or []
    return rows[0] if rows else None


def _same(row: Dict[str, Any], s: Dict[str, Any]) -> bool:
    return (int(row.get("additional_publications") or 0) == s["additional_publications"]
            and (row.get("cadence") or "monthly") == s["cadence"] and row.get("per_week") == s["per_week"])


def _replay(row: Dict[str, Any], s: Dict[str, Any], me: str) -> Dict[str, Any]:
    if not _same(row, s):
        raise PortalError("idempotency_conflict", 409)          # misma clave con otra cantidad o frecuencia
    return public_addon(row, me)


def submit(db, c, body: Dict[str, Any], authorize: bool) -> Dict[str, Any]:
    """authorize=False: solicitud (owner o manager). authorize=True: autorización directa (owner o delegado)."""
    if body.get("confirm") is not True:
        raise PortalError("confirmation_required", 400)
    key = str(body.get("idempotency_key") or "")
    if not KEY.fullmatch(key):
        raise PortalError("invalid_idempotency_key", 400)
    s = spec(body)
    if authorize and not can_authorize(db, c):
        raise PortalError("owner_authorization_required", 403)
    me = c.user["id"]
    found = _existing(db, c, key)
    if found:
        return _replay(found, s, me)
    row = {"tenant_id": c.tenant_id, "cadence": s["cadence"], "per_week": s["per_week"],
           "additional_publications": s["additional_publications"], "starts_cycle": cycle_start(c),
           "status": "authorized" if authorize else "requested", "requested_by": me, "idempotency_key": key}
    if authorize:
        row.update({"decided_by": me, "decided_at": c.now.isoformat()})
    try:
        saved = db.insert("marketing_publication_addons", row)
    except Exception:
        # Doble clic o reintento simultáneo: la otra petición ganó el índice único (tenant, clave).
        found = _existing(db, c, key)
        if not found:
            raise
        return _replay(found, s, me)
    # PostgreSQL calcula amount_cents: el cliente nunca envía ni modifica el precio.
    if saved.get("amount_cents") is not None and int(saved["amount_cents"]) != round(s["amount"] * 100):
        raise PortalError("publication_addon_price_mismatch", 500)
    return public_addon(saved, me)


UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _one(db, c, addon_id: Any) -> Dict[str, Any]:
    if not UUID.fullmatch(str(addon_id or "")):
        raise PortalError("not_found", 404)
    rows = db.select("marketing_publication_addons", {"tenant_id": f"eq.{c.tenant_id}", "id": f"eq.{addon_id}",
                                                      "select": "*", "limit": "1"})
    if not rows:
        raise PortalError("not_found", 404)
    return rows[0]


def decide(db, c, addon_id: Any, approve: bool, confirm: bool) -> Dict[str, Any]:
    """El owner (o un manager delegado) aprueba o rechaza una solicitud. Una sola vez, aunque haya doble clic."""
    if confirm is not True:
        raise PortalError("confirmation_required", 400)
    if not can_authorize(db, c):
        raise PortalError("owner_authorization_required", 403)
    row = _one(db, c, addon_id)
    target = "authorized" if approve else "declined"
    if row["status"] == target:
        return public_addon(row, c.user["id"])                 # repetición: mismo resultado
    if row["status"] != "requested":
        raise PortalError("addon_not_pending", 409)
    done = db.update("marketing_publication_addons", {"tenant_id": f"eq.{c.tenant_id}", "id": f"eq.{row['id']}",
                                                      "status": "eq.requested"},
                     {"status": target, "decided_by": c.user["id"], "decided_at": c.now.isoformat()})
    if not done:
        row = _one(db, c, addon_id)                             # otra petición la decidió a la vez
        if row["status"] != target:
            raise PortalError("addon_not_pending", 409)
        return public_addon(row, c.user["id"])
    return public_addon(done[0], c.user["id"])


def cancel(db, c, addon_id: Any, confirm: bool) -> Dict[str, Any]:
    """Cancela una ampliación autorizada desde el mes siguiente (lo del mes en curso ya está en uso)."""
    if confirm is not True:
        raise PortalError("confirmation_required", 400)
    if not can_authorize(db, c):
        raise PortalError("owner_authorization_required", 403)
    row = _one(db, c, addon_id)
    if row["status"] == "cancelled":
        return public_addon(row, c.user["id"])
    if row["status"] != "authorized":
        raise PortalError("addon_not_active", 409)
    # starts_cycle siempre es un mes ya iniciado, así que el mes siguiente es posterior a él.
    done = db.update("marketing_publication_addons", {"tenant_id": f"eq.{c.tenant_id}", "id": f"eq.{row['id']}",
                                                      "status": "eq.authorized"},
                     {"status": "cancelled", "cancelled_by": c.user["id"], "cancelled_at": c.now.isoformat(),
                      "ends_cycle": next_cycle(c)})
    return public_addon(done[0] if done else _one(db, c, addon_id), c.user["id"])


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
