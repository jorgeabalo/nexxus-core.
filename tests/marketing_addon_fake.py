"""Doble de las reglas de 20261013120000_marketing_publication_addons.sql para las pruebas de Python.

Reproduce: importe y bloques generados por la base, clave única por tenant, quién puede solicitar y autorizar
(owner, o manager con permiso delegado), transiciones de un solo sentido y que nada se borra ni se altera.
Las mismas reglas se prueban en SQL real (tests/sql/marketing_publication_addons.mjs y PostgreSQL 17).
"""
import uuid

MUTABLE = {"status", "decided_by", "decided_at", "cancelled_by", "cancelled_at", "ends_cycle"}
TRANSITIONS = {("requested", "authorized"), ("requested", "declined"), ("authorized", "cancelled")}


def _role(db, tenant, user):
    r = next((u for u in db.tables["tenant_users"] if u["tenant_id"] == tenant and u["user_id"] == user and u.get("active")), None)
    return r and r["role"]


def approver(db, tenant, user):
    role = _role(db, tenant, user)
    perm = next((p for p in db.tables["marketing_purchase_permissions"] if p["tenant_id"] == tenant), None)
    return role == "owner" or (role == "manager" and bool(perm and perm["managers_can_authorize"]))


def generated(n):
    return {"blocks_8": n // 8, "blocks_4": (n % 8) // 4, "blocks_1": n % 4,
            "amount_cents": (n // 8) * 3500 + ((n % 8) // 4) * 2000 + (n % 4) * 600}


def install(db):
    db.tables.setdefault("marketing_publication_addons", [])
    db.tables.setdefault("marketing_purchase_permissions", [])
    real_insert, real_update, real_delete = db.insert, db.update, getattr(db, "delete", None)

    def insert(table, row):
        if table == "marketing_publication_addons":
            if set(row) & {"amount_cents", "blocks_8", "blocks_4", "blocks_1"}:
                raise RuntimeError("428C9 cannot insert a non-DEFAULT value into a generated column")
            if any(r["tenant_id"] == row["tenant_id"] and r["idempotency_key"] == row["idempotency_key"]
                   for r in db.tables[table]):
                raise RuntimeError("Supabase POST -> 409: duplicate key value violates unique constraint")
            if _role(db, row["tenant_id"], row["requested_by"]) not in ("owner", "manager"):
                raise RuntimeError("42501 marketing_addon_requester_not_allowed")
            if row.get("status") not in ("requested", "authorized"):
                raise RuntimeError("23514 marketing_addon_invalid_status")
            if row["status"] == "authorized" and not approver(db, row["tenant_id"], row.get("decided_by")):
                raise RuntimeError("42501 marketing_addon_owner_authorization_required")
            row = {"id": str(uuid.uuid4()), "currency": "USD", "requested_at": "2026-10-15T15:00:00+00:00",
                   "ends_cycle": None, "cancelled_by": None, "cancelled_at": None, "decided_by": None,
                   "decided_at": None, **row, **generated(int(row["additional_publications"]))}
        if table == "marketing_purchase_permissions" and _role(db, row["tenant_id"], row.get("updated_by")) != "owner":
            raise RuntimeError("42501 marketing_purchase_permission_owner_only")
        return real_insert(table, row)

    def update(table, filters, values):
        if table == "marketing_publication_addons":
            if set(values) - MUTABLE:
                raise RuntimeError("42501 marketing_publication_addons_immutable")
            for r in [r for r in db.tables[table] if db._match(r, filters)]:
                new = {**r, **values}
                if (r["status"], new["status"]) not in TRANSITIONS:
                    raise RuntimeError("42501 marketing_publication_addons_immutable")
                who = new["cancelled_by"] if new["status"] == "cancelled" else new.get("decided_by")
                if not approver(db, r["tenant_id"], who):
                    raise RuntimeError("42501 marketing_addon_owner_authorization_required")
        if table == "marketing_purchase_permissions":
            for r in [r for r in db.tables[table] if db._match(r, filters)]:
                if _role(db, r["tenant_id"], values.get("updated_by")) != "owner":
                    raise RuntimeError("42501 marketing_purchase_permission_owner_only")
        return real_update(table, filters, values)

    def delete(table, filters):
        if table in ("marketing_publication_addons", "marketing_purchase_permissions"):
            raise RuntimeError("42501 immutable")
        return real_delete(table, filters)

    db.insert, db.update, db.delete = insert, update, delete
    return db
