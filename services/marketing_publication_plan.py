"""Plan público de publicaciones y autorizaciones de ampliación.

El tenant ve publicaciones, no presupuestos ni costos internos de IA. Cada
ampliación es mensual, inmutable e idempotente; el cobro real queda fuera de
este PR, pero la autorización queda lista para incorporarla a la factura.
"""
import re
from typing import Any, Dict

from services.member_portal import PortalError

BLOCKS = ((8, 3500), (4, 2000), (1, 600))
MAX_ADDITIONAL = 500
KEY = re.compile(r"^[A-Za-z0-9_-]{16,120}$")


def quote(additional: Any) -> Dict[str, Any]:
    try:
        count = int(additional)
    except (TypeError, ValueError):
        raise PortalError("invalid_publication_addon", 400)
    if isinstance(additional, bool) or str(additional).strip() != str(count) or not (1 <= count <= MAX_ADDITIONAL):
        raise PortalError("invalid_publication_addon", 400)
    left, cents, blocks = count, 0, []
    for size, price in BLOCKS:
        qty, left = divmod(left, size)
        if qty:
            blocks.append({"size": size, "quantity": qty, "subtotal": round(qty * price / 100, 2)})
            cents += qty * price
    return {"additional_publications": count, "amount": round(cents / 100, 2), "currency": "USD",
            "blocks": blocks}


def cycle_start(c) -> str:
    return c.now.astimezone(c.zone).date().replace(day=1).isoformat()


def authorized_rows(db, c):
    return db.select("marketing_publication_addons", {
        "tenant_id": f"eq.{c.tenant_id}", "cycle_start": f"eq.{cycle_start(c)}",
        "select": "id,additional_publications,amount_cents,authorized_at", "limit": "1000"}) or []


def summary(db, c, used: int) -> Dict[str, Any]:
    base = c.settings.get("monthly_post_limit")
    if base is None:
        return {"included": None, "added": 0, "total": None, "used": int(used), "available": None, "offers": []}
    included = max(int(base), 0)
    added = sum(max(int(r.get("additional_publications") or 0), 0) for r in authorized_rows(db, c))
    total = included + added
    return {"included": included, "added": added, "total": total, "used": int(used),
            "available": max(total - int(used), 0),
            "offers": [{"size": size, "price": price / 100, "currency": "USD"} for size, price in reversed(BLOCKS)]}


def authorize(db, c, additional: Any, confirm: bool, idempotency_key: Any) -> Dict[str, Any]:
    if confirm is not True:
        raise PortalError("confirmation_required", 400)
    key = str(idempotency_key or "")
    if not KEY.fullmatch(key):
        raise PortalError("invalid_idempotency_key", 400)
    q = quote(additional)
    existing = db.select("marketing_publication_addons", {
        "tenant_id": f"eq.{c.tenant_id}", "idempotency_key": f"eq.{key}", "select": "*", "limit": "1"}) or []
    if existing:
        row = existing[0]
        saved = quote(row.get("additional_publications"))
        if saved["additional_publications"] != q["additional_publications"]:
            raise PortalError("idempotency_conflict", 409)
        return {**saved, "id": row["id"], "cycle_start": row["cycle_start"],
                "authorized_at": row.get("authorized_at")}
    try:
        row = db.insert("marketing_publication_addons", {
            "tenant_id": c.tenant_id, "cycle_start": cycle_start(c), "additional_publications": q["additional_publications"],
            "requested_by": c.user["id"], "authorized_by": c.user["id"], "idempotency_key": key})
    except Exception:
        # Doble clic simultáneo: la otra petición ganó el índice único (tenant, clave). Se devuelve la suya
        # (o conflicto si era otra cantidad); nunca hay dos autorizaciones ni un error 500.
        again = db.select("marketing_publication_addons", {
            "tenant_id": f"eq.{c.tenant_id}", "idempotency_key": f"eq.{key}", "select": "*", "limit": "1"}) or []
        if not again:
            raise
        if int(again[0].get("additional_publications") or 0) != q["additional_publications"]:
            raise PortalError("idempotency_conflict", 409)
        return {**q, "id": again[0]["id"], "cycle_start": again[0]["cycle_start"],
                "authorized_at": again[0].get("authorized_at")}
    # PostgreSQL calcula amount_cents: el cliente nunca puede enviar ni modificar el precio.
    if row.get("amount_cents") is not None and int(row["amount_cents"]) != round(q["amount"] * 100):
        raise PortalError("publication_addon_price_mismatch", 500)
    return {**q, "id": row["id"], "cycle_start": row["cycle_start"],
            "authorized_at": row.get("authorized_at") or row.get("created_at")}
