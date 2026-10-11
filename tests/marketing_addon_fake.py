"""Doble de 20261013120000_marketing_publication_addons.sql para las pruebas de Python.

Reproduce las funciones atómicas (marketing_submit/decide/cancel_publication_addon), el importe y la línea de
factura generados por la base, una sola ampliación vigente por ciclo (un cambio empieza el ciclo siguiente),
quién puede solicitar y autorizar, el historial inmutable y que nada se borra. La hora la fija la prueba
(`db.addon_now`). Las mismas reglas se prueban en SQL real (PGlite y PostgreSQL 17).
"""
import uuid
from datetime import date, timezone
from zoneinfo import ZoneInfo

EFFECTIVE = ("authorized_uninvoiced", "cancelled", "replaced")


def generated(n):
    dollars = (n // 8) * 35 + ((n % 8) // 4) * 20 + (n % 4) * 6
    return {"blocks_8": n // 8, "blocks_4": (n % 8) // 4, "blocks_1": n % 4, "amount_cents": dollars * 100,
            "billing_line": f"Publicaciones adicionales de Marketing: {n} por mes — ${dollars}"}


def _next(cycle):
    d = date.fromisoformat(cycle)
    return (date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)).isoformat()


def install(db, now=None):
    from test_marketing import NOW
    db.addon_now = now or NOW
    for t in ("marketing_publication_addons", "marketing_publication_addon_events", "marketing_purchase_permissions"):
        db.tables.setdefault(t, [])
    A, EV = db.tables["marketing_publication_addons"], db.tables["marketing_publication_addon_events"]
    real_insert, real_update, real_delete = db.insert, db.update, getattr(db, "delete", None)
    real_rpc = getattr(db, "rpc", None)

    def role(tenant, user):
        r = next((u for u in db.tables["tenant_users"] if u["tenant_id"] == tenant and u["user_id"] == user and u.get("active")), None)
        return r and r["role"]

    def approver(tenant, user):
        perm = next((p for p in db.tables["marketing_purchase_permissions"] if p["tenant_id"] == tenant), None)
        return role(tenant, user) == "owner" or (role(tenant, user) == "manager" and bool(perm and perm["managers_can_authorize"]))

    def cycle(tenant):
        tz = next((t.get("timezone") for t in db.tables["tenants"] if t["id"] == tenant), None) or "UTC"
        return db.addon_now.astimezone(ZoneInfo(tz)).date().replace(day=1).isoformat()

    def covers(r, cyc):
        return r["status"] in EFFECTIVE and r["starts_cycle"] <= cyc and (not r["ends_cycle"] or r["ends_cycle"] > cyc)

    def log(r, actor):
        EV.append({"addon_id": r["id"], "tenant_id": r["tenant_id"], "actor_id": actor, "status": r["status"],
                   "event": "authorized" if r["status"] == "authorized_uninvoiced" else r["status"],
                   "additional_publications": r["additional_publications"], "amount_cents": r["amount_cents"],
                   "billing_line": r["billing_line"], "starts_cycle": r["starts_cycle"], "ends_cycle": r["ends_cycle"]})

    def apply(tenant, new_id):
        cur = cycle(tenant)
        start = _next(cur) if any(r["tenant_id"] == tenant and r["id"] != new_id and covers(r, cur) for r in A) else cur
        for r in A:
            if r["tenant_id"] == tenant and r["id"] != new_id and r["status"] == "authorized_uninvoiced":
                r.update({"status": "replaced", "replaced_by": new_id, "ends_cycle": max(r["starts_cycle"], start)})
                log(r, None)
        return start

    def ts():
        return db.addon_now.astimezone(timezone.utc).isoformat()

    def submit(a):
        t, key = a["p_tenant"], a["p_key"]
        found = next((r for r in A if r["tenant_id"] == t and r["idempotency_key"] == key), None)
        if found:
            same = (found["additional_publications"] == a["p_additional"] and found["cadence"] == a["p_cadence"]
                    and found["per_week"] == a["p_per_week"])
            return {"result": "existing", "addon": dict(found)} if same else {"error": "idempotency_conflict"}
        if role(t, a["p_actor"]) not in ("owner", "manager"):
            return {"error": "forbidden"}
        if a["p_authorize"] and not approver(t, a["p_actor"]):
            return {"error": "owner_authorization_required"}
        row = {"id": str(uuid.uuid4()), "tenant_id": t, "cadence": a["p_cadence"], "per_week": a["p_per_week"],
               "additional_publications": a["p_additional"], "status": "requested", "starts_cycle": None,
               "ends_cycle": None, "requested_by": a["p_actor"], "requested_at": ts(), "authorized_by": None,
               "authorized_at": None, "declined_by": None, "cancelled_by": None, "cancelled_at": None,
               "replaced_by": None, "idempotency_key": key, "currency": "USD", **generated(a["p_additional"])}
        if a["p_authorize"]:
            cur = cycle(t)
            same = next((r for r in A if r["tenant_id"] == t and r["status"] == "authorized_uninvoiced"
                         and r["starts_cycle"] <= cur and r["additional_publications"] == a["p_additional"]
                         and r["cadence"] == a["p_cadence"] and r["per_week"] == a["p_per_week"]), None)
            future = any(r["tenant_id"] == t and r["status"] == "authorized_uninvoiced" and r["starts_cycle"] > cur for r in A)
            if same and not future:
                return {"result": "unchanged", "addon": dict(same)}
            row.update({"status": "authorized_uninvoiced", "starts_cycle": apply(t, row["id"]),
                        "authorized_by": a["p_actor"], "authorized_at": ts()})
        A.append(row)
        log(row, a["p_actor"])
        return {"result": "authorized" if a["p_authorize"] else "requested", "addon": dict(row)}

    def find(a):
        return next((r for r in A if r["id"] == a["p_addon"] and r["tenant_id"] == a["p_tenant"]), None)

    def decide(a):
        r = find(a)
        if not r:
            return {"error": "not_found"}
        if not approver(a["p_tenant"], a["p_actor"]):
            return {"error": "owner_authorization_required"}
        target = "authorized_uninvoiced" if a["p_approve"] else "declined"
        if r["status"] == target:
            return {"result": "existing", "addon": dict(r)}
        if r["status"] != "requested":
            return {"error": "addon_not_pending"}
        if a["p_approve"]:
            r.update({"starts_cycle": apply(a["p_tenant"], r["id"]), "status": target, "authorized_by": a["p_actor"],
                      "authorized_at": ts()})
        else:
            r.update({"status": target, "declined_by": a["p_actor"]})
        log(r, a["p_actor"])
        return {"result": "authorized" if a["p_approve"] else "declined", "addon": dict(r)}

    def cancel(a):
        r = find(a)
        if not r:
            return {"error": "not_found"}
        if not approver(a["p_tenant"], a["p_actor"]):
            return {"error": "owner_authorization_required"}
        if r["status"] == "cancelled":
            return {"result": "existing", "addon": dict(r)}
        if r["status"] != "authorized_uninvoiced":
            return {"error": "addon_not_active"}
        r.update({"status": "cancelled", "cancelled_by": a["p_actor"], "cancelled_at": ts(),
                  "ends_cycle": max(r["starts_cycle"], _next(cycle(a["p_tenant"])))})
        log(r, a["p_actor"])
        return {"result": "cancelled", "addon": dict(r)}

    RPCS = {"marketing_submit_publication_addon": submit, "marketing_decide_publication_addon": decide,
            "marketing_cancel_publication_addon": cancel}

    def rpc(fn, args, *rest, **kw):
        if fn in RPCS:
            return RPCS[fn](args)
        return real_rpc(fn, args, *rest, **kw)

    def insert(table, row):
        if table in ("marketing_publication_addons", "marketing_publication_addon_events"):
            raise RuntimeError("42501 permission denied: solo las funciones de la base escriben")
        if table == "marketing_purchase_permissions" and role(row["tenant_id"], row.get("updated_by")) != "owner":
            raise RuntimeError("42501 marketing_purchase_permission_owner_only")
        return real_insert(table, row)

    def update(table, filters, values):
        if table in ("marketing_publication_addons", "marketing_publication_addon_events"):
            raise RuntimeError("42501 permission denied: solo las funciones de la base escriben")
        if table == "marketing_purchase_permissions":
            for r in [r for r in db.tables[table] if db._match(r, filters)]:
                if role(r["tenant_id"], values.get("updated_by")) != "owner":
                    raise RuntimeError("42501 marketing_purchase_permission_owner_only")
        return real_update(table, filters, values)

    def delete(table, filters):
        if table in ("marketing_publication_addons", "marketing_publication_addon_events", "marketing_purchase_permissions"):
            raise RuntimeError("42501 immutable")
        return real_delete(table, filters)

    db.insert, db.update, db.delete, db.rpc = insert, update, delete, rpc
    return db
