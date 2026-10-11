"""Doble de 20261014120000_marketing_autopilot.sql para las pruebas de Python.

Reproduce las funciones atómicas marketing_autopilot_set/claim/approve y marketing_plan_content: quién puede
activar (owner o manager con el permiso delegado propio), quién puede pausar, el historial inmutable, la vuelta a
revisión de lo futuro al pausar, un hueco por día, la aprobación automática atribuida al sistema con versión de
reglas, la cuota del ciclo y la idempotencia por tenant + usuario. Las mismas reglas se prueban en SQL real
(PGlite y PostgreSQL 17).
"""
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NOTE_HOLD = "Piloto automático en pausa: queda esperando revisión."
NOTE_AUTO = "Aprobado automáticamente bajo la autorización del owner."


def install(db):
    for t in ("marketing_autopilot_settings", "marketing_autopilot_events", "marketing_autopilot_items",
              "marketing_plan_requests"):
        db.tables.setdefault(t, [])
    S, EV, IT, PR = (db.tables[t] for t in ("marketing_autopilot_settings", "marketing_autopilot_events",
                                            "marketing_autopilot_items", "marketing_plan_requests"))
    real_rpc, real_insert, real_update, real_delete = db.rpc, db.insert, db.update, getattr(db, "delete", None)

    def now():
        return getattr(db, "addon_now", None) or datetime.now(timezone.utc)

    def iso(dt):
        return dt.astimezone(timezone.utc).isoformat()

    def role(tenant, user):
        r = next((u for u in db.tables["tenant_users"] if u["tenant_id"] == tenant and u["user_id"] == user
                  and u.get("active") and u["role"] in ("owner", "manager")), None)
        return r and r["role"]

    def setting(tenant):
        return next((s for s in S if s["tenant_id"] == tenant), None)

    def manager_ok(tenant, user):
        s = setting(tenant)
        return role(tenant, user) == "owner" or (role(tenant, user) == "manager" and bool(s and s["managers_can_manage"]))

    def content(cid, tenant=None):
        return next((c for c in db.tables["marketing_content"] if c["id"] == cid
                     and (tenant is None or c["tenant_id"] == tenant)), None)

    def event(tenant, cid, action, frm, to, comment, actor):
        real_insert("marketing_approval_events", {"tenant_id": tenant, "content_id": cid, "action": action,
                                                  "from_status": frm, "to_status": to, "comment": comment,
                                                  "actor_id": actor, "actor_role": "system"})

    def hold(tenant, actor):
        n = 0
        for i in [i for i in IT if i["tenant_id"] == tenant]:
            c = content(i["content_id"], tenant)
            when = c and (c.get("scheduled_at") or i["slot_at"])
            if not c or c["status"] not in ("approved", "scheduled") or str(when) <= iso(now()):
                continue
            if c["status"] == "scheduled":
                c.update(status="approved", scheduled_at=None)
                event(tenant, c["id"], "unschedule", "scheduled", "approved", NOTE_HOLD, actor)
                for p in db.tables.get("marketing_publications", []):
                    if p["content_id"] == c["id"] and p["status"] in ("pending", "queued"):
                        p["status"] = "cancelled"
            c.update(status="draft", approved_by=None, approved_at=None)
            event(tenant, c["id"], "reopen", "approved", "draft", NOTE_HOLD, actor)
            c.update(status="review", submitted_at=iso(now()))
            event(tenant, c["id"], "submit", "draft", "review", NOTE_HOLD, actor)
            n += 1
        return n

    def ap_set(a):
        t, actor, action = a["p_tenant"], a["p_actor"], a["p_action"]
        r = role(t, actor)
        if not r:
            return {"error": "forbidden"}
        s = setting(t)
        if not s:
            s = {"tenant_id": t, "mode": "manual", "rules": None, "rules_version": 0, "authorized_by": None,
                 "authorized_at": None, "starts_on": None, "paused_at": None, "paused_by": None,
                 "managers_can_manage": False, "updated_by": actor, "updated_at": iso(now())}
            S.append(s)
        held = 0
        if action in ("grant", "revoke"):
            if r != "owner":
                return {"error": "owner_only"}
            if s["managers_can_manage"] == (action == "grant"):
                return {"result": "unchanged", "settings": dict(s)}
            s.update(managers_can_manage=action == "grant", updated_by=actor)
            ev = "delegation_granted" if action == "grant" else "delegation_revoked"
        elif action == "pause":
            if s["mode"] == "manual":
                return {"result": "unchanged", "settings": dict(s)}
            s.update(mode="manual", paused_at=iso(now()), paused_by=actor, updated_by=actor)
            held, ev = hold(t, actor), "paused"
        elif action == "activate":
            if not manager_ok(t, actor):
                return {"error": "autopilot_authorization_required"}
            rules, start = a.get("p_rules"), a.get("p_starts_on")
            if not isinstance(rules, dict) or not start:
                return {"error": "invalid_autopilot_rules"}
            if s["mode"] == "autopilot" and s["rules"] == rules and s["starts_on"] == start:
                return {"result": "unchanged", "settings": dict(s)}
            ev = "rules_changed" if s["mode"] == "autopilot" else ("reactivated" if s["rules_version"] > 0 else "activated")
            changed = s["rules"] != rules or s["starts_on"] != start
            s.update(mode="autopilot", rules=rules, rules_version=s["rules_version"] + (1 if changed else 0),
                     authorized_by=actor, authorized_at=iso(now()), starts_on=start, paused_at=None, paused_by=None,
                     updated_by=actor)
        else:
            return {"error": "invalid_action"}
        EV.append({"id": len(EV) + 1, "tenant_id": t, "event": ev, "actor_id": actor, "actor_role": r,
                   "rules": s["rules"], "rules_version": s["rules_version"], "starts_on": s["starts_on"],
                   "created_at": iso(now())})
        return {"result": ev, "settings": dict(s), "held": held}

    def ap_claim(a):
        t, cid = a["p_tenant"], a["p_content"]
        s = setting(t)
        if not s or s["mode"] != "autopilot" or s["rules_version"] != a["p_version"]:
            return {"error": "autopilot_paused"}
        i = next((i for i in IT if i["tenant_id"] == t and i["slot_day"] == a["p_slot_day"]), None)
        if i:
            return {"result": "existing", "item": dict(i)} if i["content_id"] == cid else {"error": "slot_taken"}
        if not content(cid, t):
            return {"error": "not_found"}
        i = {"content_id": cid, "tenant_id": t, "slot_at": a["p_slot_at"], "slot_day": a["p_slot_day"],
             "rules_version": a["p_version"], "auto_approved_at": None, "approved_rules_version": None,
             "authorized_by": None, "authorized_at": None}
        IT.append(i)
        return {"result": "claimed", "item": dict(i)}

    def cycle(t, at):
        tz = next((x.get("timezone") for x in db.tables["tenants"] if x["id"] == t), None) or "UTC"
        dt = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(tz)).date().replace(day=1)

    def ap_approve(a):
        t, cid = a["p_tenant"], a["p_content"]
        s = setting(t)
        if not s or s["mode"] != "autopilot" or s["paused_at"]:
            return {"error": "autopilot_paused"}
        if not manager_ok(t, s["authorized_by"]):
            return {"error": "authorization_withdrawn"}
        if s["rules_version"] != a["p_version"]:
            return {"error": "rules_changed"}
        i = next((i for i in IT if i["content_id"] == cid), None)
        c = content(cid, t)
        if not i or i["tenant_id"] != t or not c:
            return {"error": "tenant_mismatch"}
        if c["status"] != "review":
            return {"error": "invalid_transition"}
        rules = s["rules"] or {}
        if c["format"] not in (rules.get("formats") or []):
            return {"error": "format_not_authorized"}
        if not c.get("channels") or not set(c["channels"]) <= set(rules.get("platforms") or []):
            return {"error": "platform_not_authorized"}
        base = next((x.get("monthly_post_limit") for x in db.tables["marketing_settings"] if x["tenant_id"] == t), None)
        if base is not None:
            cyc = cycle(t, i["slot_at"])
            from marketing_addon_fake import EFFECTIVE  # misma regla de ampliaciones vigentes
            addon = sum(int(x["additional_publications"]) for x in db.tables.get("marketing_publication_addons", [])
                        if x["tenant_id"] == t and x["status"] in EFFECTIVE and x["starts_cycle"] <= cyc.isoformat()
                        and (not x["ends_cycle"] or x["ends_cycle"] > cyc.isoformat()))
            used = sum(1 for m in db.tables["marketing_content"] if m["tenant_id"] == t and m["id"] != cid
                       and m["status"] in ("scheduled", "publishing", "published") and m.get("scheduled_at")
                       and cycle(t, m["scheduled_at"]) == cyc)
            used += sum(1 for x in IT if x["tenant_id"] == t and x["content_id"] != cid
                        and (content(x["content_id"], t) or {}).get("status") == "approved" and cycle(t, x["slot_at"]) == cyc)
            if used + 1 > int(base) + addon:
                return {"error": "quota_exhausted"}
        c.update(status="approved", approved_by=None, approved_at=iso(now()), updated_by=s["authorized_by"])
        event(t, cid, "approve", "review", "approved", NOTE_AUTO, s["authorized_by"])
        i.update(auto_approved_at=iso(now()), approved_rules_version=s["rules_version"],
                 authorized_by=s["authorized_by"], authorized_at=s["authorized_at"])
        return {"result": "approved", "item": dict(i)}

    def plan_content(a):
        t, user, key, h, v = a["p_tenant"], a["p_user"], a["p_key"], a["p_hash"], a["p_content"]
        if not role(t, user):
            return {"error": "forbidden"}
        r = next((r for r in PR if r["tenant_id"] == t and r["user_id"] == user and r["idempotency_key"] == key), None)
        if r:
            return {"error": "idempotency_conflict"} if r["request_hash"] != h else \
                {"result": "existing", "content_id": r["content_id"]}
        r = {"tenant_id": t, "user_id": user, "idempotency_key": key, "request_hash": h, "content_id": None}
        PR.append(r)
        row = real_insert("marketing_content", {
            "id": str(uuid.uuid4()), "tenant_id": t, "title": v["title"], "objective": v.get("objective"),
            "topic": v.get("topic"), "format": v["format"], "channels": list(v.get("channels") or []),
            "language": v.get("language") or "es", "cta": v.get("cta"), "planned_at": v.get("planned_at"),
            "notes": v.get("notes"), "status": "draft", "assigned_to": v.get("assigned_to") or user,
            "created_by": user, "updated_by": user, "caption": None, "script": None, "hashtags": [],
            "campaign_id": None, "scheduled_at": None})
        real_insert("marketing_approval_events", {"tenant_id": t, "content_id": row["id"], "action": "create",
                                                  "from_status": None, "to_status": "draft", "comment": None,
                                                  "actor_id": user, "actor_role": a.get("p_role") or "system"})
        r["content_id"] = row["id"]
        return {"result": "created", "content_id": row["id"]}

    RPCS = {"marketing_autopilot_set": ap_set, "marketing_autopilot_claim": ap_claim,
            "marketing_autopilot_approve": ap_approve, "marketing_plan_content": plan_content}

    def rpc(fn, args, *rest, **kw):
        if fn in RPCS:
            return RPCS[fn](args)
        return real_rpc(fn, args, *rest, **kw)

    PROTECTED = ("marketing_autopilot_settings", "marketing_autopilot_events", "marketing_autopilot_items",
                 "marketing_plan_requests")

    def insert(table, row):
        if table in PROTECTED:
            raise RuntimeError("42501 permission denied: solo las funciones de la base escriben")
        return real_insert(table, row)

    def update(table, filters, values):
        if table in PROTECTED:
            raise RuntimeError("42501 permission denied: solo las funciones de la base escriben")
        return real_update(table, filters, values)

    def delete(table, filters):
        if table in PROTECTED:
            raise RuntimeError("42501 immutable")
        return real_delete(table, filters)

    db.rpc, db.insert, db.update, db.delete = rpc, insert, update, delete
    return db
