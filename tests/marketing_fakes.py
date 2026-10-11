"""
Doble de pruebas de las funciones atómicas de Marketing (aprobación con reserva de costo y de bytes,
reserva/liberación de almacenamiento y uso total), bajo un candado como SELECT … FOR UPDATE.
Las funciones SQL reales se prueban en tests/sql/marketing_generation_budget.mjs y marketing_retention.mjs.
"""
import copy
import threading
import time
from datetime import datetime, timedelta

TERMINAL_JOB = ("succeeded", "failed", "cancelled")


def _later(iso, **kw):
    return (datetime.fromisoformat(iso) + timedelta(**kw)).isoformat()


class MarketingRpcMixin:
    """Requiere self.tables (FakeDB) y self.rpc_now (fecha ISO de "ahora")."""
    rpc_delay = 0.0                                   # agranda la ventana de carrera en las pruebas de concurrencia

    def _rpc_init(self, now_iso):
        self._rpc_lock = threading.Lock()
        self.rpc_now = now_iso
        self.rpc_calls = []
        self.tables.setdefault("marketing_storage_reservations", [])
        self.tables.setdefault("marketing_stream_tokens", [])
        self.tables.setdefault("marketing_runtime_leases", [])

    def _live(self, r):
        """Reserva abierta y no caducada (expires_at > now())."""
        return r["status"] == "reserved" and (not r.get("expires_at") or r["expires_at"] > self.rpc_now)

    def _owns_live_lease(self, job_id, worker):
        j = next((x for x in self.tables["marketing_generation_jobs"] if x["id"] == job_id), None)
        return bool(j and j["status"] == "processing" and j.get("lease_expires_at")
                    and j["lease_expires_at"] > self.rpc_now and worker and j.get("lease_owner") == worker)

    def insert(self, table, row):
        """Emula marketing_output_guard y marketing_usage_guard: resultados y costos solo del dueño del
        lease vigente de un trabajo en proceso."""
        if table in ("marketing_generation_outputs", "marketing_model_usage"):
            if not self._owns_live_lease(row["job_id"], row.get("worker_id")):
                raise RuntimeError(f"{table} requires the worker holding a live lease")
        return super().insert(table, row)

    def update(self, table, filters, values):
        """Emula marketing_job_storage_release, el guardián de reservas cerradas y el de leases."""
        if table == "marketing_storage_reservations":
            for r in self.tables.get(table, []):
                if self._match(r, filters) and r["status"] != "reserved":
                    raise RuntimeError("storage reservation already closed")
        if table == "marketing_generation_jobs":
            for j in self.tables.get(table, []):
                if not self._match(j, filters) or j["status"] != "processing":
                    continue
                if values.get("status") in TERMINAL_JOB:      # solo por finalize/fail/cancel/timeout
                    raise RuntimeError("processing jobs end only through the marketing functions")
                live = bool(j.get("lease_expires_at")) and j["lease_expires_at"] > self.rpc_now
                owner = values.get("lease_owner", j.get("lease_owner"))
                if values.get("status") == "succeeded" and (not j.get("lease_owner") or not live or owner != j.get("lease_owner")):
                    raise RuntimeError("generation job lease expired or not held")
                if owner != j.get("lease_owner") and j.get("lease_owner") and live:
                    raise RuntimeError("generation job lease is held by another worker")
        rows = super().update(table, filters, values)
        if table == "marketing_generation_jobs" and values.get("status") in TERMINAL_JOB:
            for j in rows:
                for r in self.tables["marketing_storage_reservations"]:
                    if (r["tenant_id"] == j["tenant_id"] and r["reservation_key"] == f"job:{j['id']}"
                            and r["status"] == "reserved"):
                        r["status"] = "consumed" if j["status"] == "succeeded" else "released"
        return rows

    def rpc(self, fn, a, timeout=None):
        self.rpc_calls.append(fn)
        with self._rpc_lock:
            return getattr(self, f"_rpc_{fn}")(a)

    def _rpc_marketing_approve_generation(self, a):
        reserved = a["p_reserved"]
        if reserved is None or reserved < 0:
            return {"status": "rejected", "reason": "cost_not_estimable"}
        s = next((x for x in self.tables["marketing_settings"] if x["tenant_id"] == a["p_tenant"]), None)
        limit = (s or {}).get("monthly_ai_cost_limit")
        if not s or s.get("ai_generation_enabled") is not True or not limit:      # 0 o desconocido: cerrado
            return {"status": "rejected", "reason": "generation_disabled"}
        job = next((j for j in self.tables["marketing_generation_jobs"]
                    if j["id"] == a["p_job"] and j["tenant_id"] == a["p_tenant"]
                    and j["status"] == "awaiting_generation_approval"), None)
        if not job:
            return {"status": "rejected", "reason": "conflict"}
        used = sum(float(j.get("reserved_cost") or 0) if j["status"] in ("queued", "processing")
                   else float(j.get("actual_cost") or 0)
                   for j in self.tables["marketing_generation_jobs"]
                   if j["tenant_id"] == a["p_tenant"] and j.get("approved_at"))
        if self.rpc_delay:
            time.sleep(self.rpc_delay)
        if limit is not None and used + reserved > float(limit):
            return {"status": "rejected", "reason": "budget_exceeded", "available": max(float(limit) - used, 0)}
        sb = a.get("p_storage_bytes")
        if sb is None or sb < 0:
            return {"status": "rejected", "reason": "storage_not_estimable"}
        if sb > 0:
            lim = s.get("library_storage_limit_bytes", 0)
            if lim == 0:
                return {"status": "rejected", "reason": "library_disabled"}
            if lim is not None and self._rpc_marketing_storage_used({"p_tenant": a["p_tenant"]}) + sb > lim:
                return {"status": "rejected", "reason": "limit_library_storage"}
        job.update({"status": "queued", "approved_at": self.rpc_now, "approved_by": a["p_user"], "reserved_cost": reserved,
                    "reserved_storage_bytes": sb})
        if sb > 0:
            self.tables["marketing_storage_reservations"].append({
                "tenant_id": a["p_tenant"], "reservation_key": f"job:{job['id']}", "kind": "generation", "bytes": sb,
                "status": "reserved", "expires_at": _later(self.rpc_now, hours=24)})
        self.tables["marketing_generation_job_events"].append({
            "tenant_id": a["p_tenant"], "job_id": job["id"], "action": "approve", "from_status": "awaiting_generation_approval",
            "to_status": "queued", "detail": {"reserved_cost": reserved}, "actor_id": a["p_user"], "actor_role": "owner",
            "created_at": self.rpc_now})
        return {"status": "approved", "job": dict(job)}

    def _rpc_marketing_storage_used(self, a):
        t = a["p_tenant"]
        T = self.tables
        return int(sum(int(m.get("byte_size") or 0) for m in T["marketing_media"]
                       if m["tenant_id"] == t and m.get("processing_status") != "deleted")
                   + sum(int(d.get("byte_size") or 0) for d in T["marketing_media_derivatives"]
                         if d["tenant_id"] == t and d.get("status") != "deleted")
                   + sum(int(o.get("byte_size") or 0) for o in T["marketing_generation_outputs"]
                         if o["tenant_id"] == t and not o.get("purged_at"))
                   + sum(int(r["bytes"]) for r in T["marketing_storage_reservations"]
                         if r["tenant_id"] == t and self._live(r)
                         and not any(r["reservation_key"] == f"upload:{m['id']}" for m in T["marketing_media"]
                                     if m["tenant_id"] == t)))

    def _rpc_marketing_reserve_storage(self, a):
        if not a["p_bytes"] or a["p_bytes"] <= 0:
            return {"status": "rejected", "reason": "invalid_size"}
        s = next((x for x in self.tables["marketing_settings"] if x["tenant_id"] == a["p_tenant"]), None)
        lim = (s or {}).get("library_storage_limit_bytes", 0)
        if not s or lim == 0:
            return {"status": "rejected", "reason": "library_disabled"}
        if any(r["tenant_id"] == a["p_tenant"] and r["reservation_key"] == a["p_key"]
               for r in self.tables["marketing_storage_reservations"]):
            return {"status": "duplicate"}
        used = self._rpc_marketing_storage_used({"p_tenant": a["p_tenant"]})
        if self.rpc_delay:
            time.sleep(self.rpc_delay)
        if lim is not None and used + a["p_bytes"] > lim:
            return {"status": "rejected", "reason": "limit_library_storage", "available": max(lim - used, 0)}
        self.tables["marketing_storage_reservations"].append({"tenant_id": a["p_tenant"], "reservation_key": a["p_key"],
                                                               "kind": a["p_kind"], "bytes": a["p_bytes"], "status": "reserved",
                                                               "expires_at": _later(self.rpc_now, minutes=15)})
        return {"status": "reserved"}

    def _rpc_marketing_release_storage(self, a):
        for r in self.tables["marketing_storage_reservations"]:
            if (r["tenant_id"] == a["p_tenant"] and r["reservation_key"] == a["p_key"] and self._live(r)
                    and not r["reservation_key"].startswith("job:")):        # las de trabajos: solo la base
                r["status"] = "consumed" if a["p_consumed"] else "released"
                return {"status": r["status"]}
        return None

    # ---------------------------------------------------------------- worker: lease, heartbeat, timeout
    def _inputs_allowed(self, j):
        for i in self.tables["marketing_generation_inputs"]:
            if i["job_id"] != j["id"] or not i.get("media_id"):
                continue
            m = next((x for x in self.tables["marketing_media"] if x["id"] == i["media_id"]), None)
            if (not m or m.get("processing_status") != "ready" or (m.get("expires_at") or "") <= self.rpc_now
                    or m.get("retention_status") in ("purge_pending", "purged", "purge_failed")
                    or (m.get("people_policy") == "exclude" and m.get("contains_people") is not False)
                    or (m.get("people_policy") == "consented" and m.get("consent_status") != "granted")
                    or (m.get("contains_people") is not False and m.get("contains_minors") is not False)):
                return False
        return True

    def _job_event_sys(self, j, action, frm, to, detail):
        self.tables["marketing_generation_job_events"].append({
            "tenant_id": j["tenant_id"], "job_id": j["id"], "action": action, "from_status": frm, "to_status": to,
            "detail": detail, "actor_id": None, "actor_role": "system", "created_at": self.rpc_now})

    def _rpc_marketing_claim_job(self, a):
        if not a.get("p_worker"):
            return {"status": "rejected", "reason": "invalid_worker"}
        lease = min(max(int(a.get("p_lease_seconds") or 120), 30), 900)
        maxa = min(max(int(a.get("p_max_attempts") or 3), 1), 10)
        oldest = _later(self.rpc_now, hours=-24)
        jobs = self.tables["marketing_generation_jobs"]
        want = a.get("p_job")
        cand = sorted((j for j in jobs if j["status"] == "queued" and j.get("approved_at") and j.get("approved_by")
                       and j.get("reserved_cost") is not None and j["approved_at"] > oldest
                       and want in (None, j["id"])), key=lambda j: (j["approved_at"], j["id"]))
        retry = False
        if not cand:
            cand = sorted((j for j in jobs if j["status"] == "processing" and j.get("lease_expires_at")
                           and j["lease_expires_at"] <= self.rpc_now and int(j.get("attempts") or 0) < maxa
                           and (j.get("approved_at") or "") > oldest and want in (None, j["id"])),
                          key=lambda j: (j["lease_expires_at"], j["id"]))
            if not cand:
                return {"status": "empty"}
            retry = True
        if self.rpc_delay:
            time.sleep(self.rpc_delay)
        j = cand[0]
        frm = j["status"]
        if not self._inputs_allowed(j):
            j.update({"status": "failed", "error_code": "media_not_ready", "completed_at": self.rpc_now,
                      "lease_owner": None, "lease_expires_at": None})
            self._job_event_sys(j, "fail", frm, "failed", {"error_code": "media_not_ready"})
            return {"status": "skipped", "job_id": j["id"]}
        j.update({"status": "processing", "lease_owner": a["p_worker"], "lease_expires_at": _later(self.rpc_now, seconds=lease),
                  "heartbeat_at": self.rpc_now, "attempts": int(j.get("attempts") or 0) + 1})
        self._job_event_sys(j, "start", frm, "processing", {"attempt": j["attempts"], "retry": retry})
        return {"status": "claimed", "retry": retry, "job": dict(j)}

    def _rpc_marketing_job_heartbeat(self, a):
        lease = min(max(int(a.get("p_lease_seconds") or 120), 30), 900)
        for j in self.tables["marketing_generation_jobs"]:
            if (j["id"] == a["p_job"] and j["status"] == "processing" and j.get("lease_owner") == a["p_worker"]
                    and (j.get("lease_expires_at") or "") > self.rpc_now):
                j.update({"heartbeat_at": self.rpc_now, "lease_expires_at": _later(self.rpc_now, seconds=lease)})
                return True
        return None

    def _rpc_marketing_timeout_jobs(self, a):
        maxa = min(max(int(a.get("p_max_attempts") or 3), 1), 10)
        oldest = _later(self.rpc_now, hours=-24)
        n = 0
        for j in self.tables["marketing_generation_jobs"]:
            stuck = (j["status"] == "processing" and (not j.get("lease_expires_at") or j["lease_expires_at"] <= self.rpc_now)
                     and (int(j.get("attempts") or 0) >= maxa or (j.get("approved_at") or "") <= oldest))
            stale = j["status"] == "queued" and (j.get("approved_at") or "") <= oldest and j.get("approved_at")
            if not (stuck or stale):
                continue
            frm = j["status"]
            j.update({"status": "failed", "error_code": "timeout", "completed_at": self.rpc_now,
                      "lease_owner": None, "lease_expires_at": None})
            for o in self.tables["marketing_generation_outputs"]:
                if o["job_id"] == j["id"]:
                    o.update({"review_status": "rejected", "metadata": {**(o.get("metadata") or {}), "blocked": "timeout"}})
            for r in self.tables["marketing_storage_reservations"]:
                if r["reservation_key"] == f"job:{j['id']}" and r["status"] == "reserved":
                    r["status"] = "released"                       # disparador marketing_job_storage_release
            self._job_event_sys(j, "fail", frm, "failed", {"error_code": "timeout"})
            n += 1
        return n

    # ---------------------------------------------------------------- finalización atómica (emulada)
    finalize_hook = None                              # pruebas: inyectar un fallo en un paso intermedio

    def _atomic(self, fn):
        """Como una transacción: si algo falla a mitad, la base queda exactamente como antes."""
        snap = copy.deepcopy(self.tables)
        try:
            return fn()
        except Exception:
            self.tables.clear()
            self.tables.update(snap)
            raise

    def _close_job_reservation(self, j):
        for r in self.tables["marketing_storage_reservations"]:
            if r["tenant_id"] == j["tenant_id"] and r["reservation_key"] == f"job:{j['id']}" and r["status"] == "reserved":
                r["status"] = "consumed" if j["status"] == "succeeded" else "released"

    def _insert_usage(self, tenant, job_id, worker, usage):
        keys = {u["idempotency_key"] for u in self.tables["marketing_model_usage"] if u["tenant_id"] == tenant}
        for u in usage or []:
            if u["idempotency_key"] not in keys:
                keys.add(u["idempotency_key"])
                super().insert("marketing_model_usage", {**u, "tenant_id": tenant, "job_id": job_id, "worker_id": worker})

    def _rpc_marketing_finalize_generation(self, a):
        return self._atomic(lambda: self._finalize(a))

    def _hook(self, step):
        if self.finalize_hook:
            self.finalize_hook(step)

    def _finalize(self, a):
        t, jid, w = a["p_tenant"], a["p_job"], a["p_worker"]
        if not self._owns_live_lease(jid, w):
            return {"status": "rejected", "reason": "lease_lost"}
        j = next(x for x in self.tables["marketing_generation_jobs"] if x["id"] == jid)
        s = next((x for x in self.tables["marketing_settings"] if x["tenant_id"] == t), None)
        if not s or s.get("ai_generation_enabled") is not True or not s.get("monthly_ai_cost_limit"):
            return {"status": "rejected", "reason": "budget_exceeded"}
        mine = [u for u in self.tables["marketing_model_usage"] if u["tenant_id"] == t]
        old = sum(float(u.get("actual_cost") or 0) for u in mine if u["job_id"] == jid)
        keys = {u["idempotency_key"] for u in mine}
        new = sum(float(u.get("actual_cost") or 0) for u in a.get("p_usage") or [] if u["idempotency_key"] not in keys)
        if old + new > min(float(j["maximum_cost"]), float(j.get("reserved_cost") or 0)):
            return {"status": "rejected", "reason": "budget_exceeded"}
        outs = a.get("p_outputs") or []
        for o in outs:
            p = o.get("storage_path")
            if p and (p.split("/")[:3] != [t, "derivatives", jid] or len(p.split("/")) != 4
                      or not p.split("/")[3].startswith(f"{w}-") or int(o.get("byte_size") or 0) <= 0):
                return {"status": "rejected", "reason": "invalid_output"}
        size = sum(int(o.get("byte_size") or 0) for o in outs if o.get("storage_path"))
        if size > 0:
            res = self._rpc_marketing_confirm_output_storage({"p_tenant": t, "p_job": jid, "p_bytes": size})
            if res.get("status") != "ok":
                return {"status": "rejected", "reason": res.get("reason")}
        self._hook("after_storage")
        self._insert_usage(t, jid, w, a.get("p_usage"))
        seen = set()
        for i, o in enumerate(outs):
            key = (o["kind"], o.get("scene_index", -1), o.get("storage_path") or "")
            if key in seen or any((x["kind"], x.get("scene_index", -1), x.get("storage_path") or "") == key
                                  for x in self.tables["marketing_generation_outputs"] if x["job_id"] == jid):
                raise RuntimeError("duplicate output")                      # índice único
            seen.add(key)
            super().insert("marketing_generation_outputs", {**o, "tenant_id": t, "job_id": jid, "worker_id": w,
                                                            "review_status": "generated"})
            if i == 0:
                self._hook("after_first_output")
        self._hook("before_succeeded")
        sel = a.get("p_selected") or {}
        j.update({"status": "succeeded", "completed_at": self.rpc_now, "result_metadata": a.get("p_result") or {},
                  "actual_cost": round(sum(float(u.get("actual_cost") or 0) for u in self.tables["marketing_model_usage"]
                                           if u["job_id"] == jid), 4),
                  "selected_provider": sel.get("provider") or j.get("selected_provider"),
                  "selected_model": sel.get("model") or j.get("selected_model"),
                  "provider_job_id": sel.get("provider_job_id") or j.get("provider_job_id")})
        self._close_job_reservation(j)
        self._job_event_sys(j, "succeed", "processing", "succeeded", {"worker": w})
        return {"status": "succeeded", "job": dict(j)}

    def _rpc_marketing_fail_generation(self, a):
        def go():
            t, jid, w = a["p_tenant"], a["p_job"], a["p_worker"]
            if not self._owns_live_lease(jid, w):
                return {"status": "rejected", "reason": "lease_lost"}
            j = next(x for x in self.tables["marketing_generation_jobs"] if x["id"] == jid)
            self._insert_usage(t, jid, w, a.get("p_usage"))
            j.update({"status": "failed", "error_code": a["p_error_code"], "completed_at": self.rpc_now,
                      "actual_cost": round(sum(float(u.get("actual_cost") or 0) for u in self.tables["marketing_model_usage"]
                                               if u["job_id"] == jid), 4)})
            self._close_job_reservation(j)
            self._job_event_sys(j, "fail", "processing", "failed", {"error_code": a["p_error_code"]})
            return {"status": "failed", "job": dict(j)}
        return self._atomic(go)

    def _rpc_marketing_cancel_generation(self, a):
        if a["p_to"] not in ("failed", "cancelled"):
            return {"status": "rejected", "reason": "invalid_transition"}
        j = next((x for x in self.tables["marketing_generation_jobs"]
                  if x["id"] == a["p_job"] and x["tenant_id"] == a["p_tenant"]), None)
        if not j:
            return {"status": "rejected", "reason": "not_found"}
        if j["status"] in TERMINAL_JOB:
            return {"status": "unchanged", "job": dict(j)}
        frm = j["status"]
        j.update({"status": a["p_to"], "error_code": a["p_error_code"], "completed_at": self.rpc_now})
        self._close_job_reservation(j)
        for o in self.tables["marketing_generation_outputs"]:
            if o["job_id"] == j["id"]:
                o.update({"review_status": "rejected", "metadata": {**(o.get("metadata") or {}), "blocked": a["p_error_code"]}})
        self.tables["marketing_generation_job_events"].append({
            "tenant_id": j["tenant_id"], "job_id": j["id"], "action": "fail" if a["p_to"] == "failed" else "cancel",
            "from_status": frm, "to_status": a["p_to"], "detail": {"error_code": a["p_error_code"]},
            "actor_id": a.get("p_actor"), "actor_role": a.get("p_role") if a.get("p_role") in ("owner", "manager") else "system",
            "created_at": self.rpc_now})
        return {"status": a["p_to"], "job": dict(j)}

    def _rpc_marketing_close_purge(self, a):
        if a.get("p_holder"):
            lease = next((x for x in self.tables["marketing_runtime_leases"] if x["name"] == "retention_runner"), None)
            if not lease or lease["holder"] != a["p_holder"] or lease["expires_at"] <= self.rpc_now:
                return {"status": "lease_lost"}
        elif not a.get("p_actor") or a.get("p_role") not in ("owner", "manager") or a.get("p_reason") not in (
                "user_deleted", "consent_revoked"):
            return {"status": "rejected", "reason": "lease_required"}
        m = next((x for x in self.tables["marketing_media"] if x["id"] == a["p_media"] and x["tenant_id"] == a["p_tenant"]), None)
        if not m or m.get("processing_status") == "deleted" or m.get("retention_status") != "purge_pending":
            return {"status": "already_closed"}
        for d in self.tables["marketing_media_derivatives"]:
            if d.get("media_id") == m["id"] and d.get("status") != "deleted":
                d.update({"status": "deleted", "purged_at": self.rpc_now, "metadata": {}})
        m.update({"processing_status": "deleted", "retention_status": "purged", "purged_at": self.rpc_now,
                  "deleted_at": self.rpc_now, "deleted_by": a.get("p_actor"),
                  "purge_reason": m.get("purge_reason") or a.get("p_reason"), "original_filename": None, "metadata": {},
                  "storage_path": f"{m['tenant_id']}/originals/{m['id']}/purged", "last_purge_error": None})
        self.tables["marketing_media_events"].append({
            "tenant_id": m["tenant_id"], "media_id": m["id"], "action": "purged", "actor_id": a.get("p_actor"),
            "actor_role": a.get("p_role") if a.get("p_role") in ("owner", "manager") else "system",
            "detail": {"reason": m["purge_reason"]}, "created_at": self.rpc_now})
        return {"status": "closed"}

    def _rpc_marketing_acquire_runtime_lease(self, a):
        ttl = min(max(int(a.get("p_ttl_seconds") or 300), 30), 3600)
        if self.rpc_delay:
            time.sleep(self.rpc_delay)
        L = self.tables["marketing_runtime_leases"]
        cur = next((x for x in L if x["name"] == a["p_name"]), None)
        if cur and cur["expires_at"] > self.rpc_now and cur["holder"] != a["p_holder"]:
            return None
        row = {"name": a["p_name"], "holder": a["p_holder"], "acquired_at": self.rpc_now,
               "expires_at": _later(self.rpc_now, seconds=ttl)}
        if cur:
            cur.update(row)
        else:
            L.append(row)
        return True

    def _rpc_marketing_release_runtime_lease(self, a):
        L = self.tables["marketing_runtime_leases"]
        for x in list(L):
            if x["name"] == a["p_name"] and x["holder"] == a["p_holder"]:
                L.remove(x)
                return True
        return None

    def _rpc_marketing_expire_storage_reservations(self, a):
        n = 0
        for r in self.tables["marketing_storage_reservations"]:
            if r["status"] == "reserved" and r.get("expires_at") and r["expires_at"] <= self.rpc_now:
                r["status"] = "expired"
                n += 1
        return n

    def _rpc_marketing_confirm_output_storage(self, a):
        if a["p_bytes"] is None or a["p_bytes"] <= 0:
            return {"status": "rejected", "reason": "invalid_size"}
        if "p_worker" in a and not self._owns_live_lease(a["p_job"], a["p_worker"]):
            return {"status": "rejected", "reason": "lease_lost"}
        s = next((x for x in self.tables["marketing_settings"] if x["tenant_id"] == a["p_tenant"]), None)
        if not s:
            return {"status": "rejected", "reason": "library_disabled"}
        res = next((r for r in self.tables["marketing_storage_reservations"] if r["tenant_id"] == a["p_tenant"]
                    and r["reservation_key"] == f"job:{a['p_job']}" and self._live(r)), None)
        if not res:
            return {"status": "rejected", "reason": "reservation_expired"}
        used, lim = self._rpc_marketing_storage_used({"p_tenant": a["p_tenant"]}), s.get("library_storage_limit_bytes")
        if lim is not None and used - int(res["bytes"]) + a["p_bytes"] > lim:
            return {"status": "rejected", "reason": "storage_quota_exceeded"}
        held, res["bytes"] = res["bytes"], a["p_bytes"]
        return {"status": "ok", "reserved": held, "bytes": a["p_bytes"]}
