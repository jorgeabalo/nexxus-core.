"""Modo de control de las publicaciones: «Revisar antes de publicar» (predeterminado) o «Piloto automático de AITA».

* Activar o cambiar las reglas exige al owner, o a un manager con el permiso delegado de administrar el piloto
  (distinto del permiso de compras). Pausar puede cualquier owner o manager, al instante: vuelve al modo manual y
  lo futuro queda esperando revisión (lo publicado y los borradores se conservan).
* Distribuye las publicaciones disponibles del ciclo en cuatro semanas (plan base: 2 por semana hasta usar las 8),
  como máximo una por día, sin acumularlas al final. Usa una ampliación ya autorizada; nunca compra ampliaciones.
  Al agotarse la cuota se detiene.
* La aprobación automática respeta la máquina de estados, se atribuye al sistema bajo la autorización del owner y
  guarda la versión de reglas. Falla si el piloto está en pausa, la autorización ya no vale, cambiaron las reglas o
  el tenant, se agotó la cuota, el material venció, falta consentimiento, hay menores o está restringido, el formato
  o la plataforma no están autorizados, no hay presupuesto interno válido o los conectores siguen desactivados.
* Hoy no hay canales conectados: se puede configurar y ver el calendario preliminar, pero el ciclo del sistema no
  crea ni envía nada («El piloto comenzará cuando conectes y autorices tus canales»).
Las escrituras pasan por funciones atómicas de la base (20261014120000_marketing_autopilot.sql).
"""
import copy
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from services import marketing_jobs_domain as jd
from services import marketing_publication_plan as pp
from services.marketing import _iso
from services.marketing_content_plan import FORMATS, MODES, ContentPlanner
from services.marketing_domain import CHANNELS
from services.marketing_reel_plan import OBJECTIVES
from services.marketing_worker import recheck_inputs
from services.member_portal import PortalError

WEEKS = 4
UNLIMITED_DEFAULT = 8                      # plan sin límite: el ritmo del plan base (2 por semana)
HOUR = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ERRORS = {"forbidden": 403, "owner_only": 403, "autopilot_authorization_required": 403,
          "invalid_autopilot_rules": 400, "invalid_action": 400}
SETTING_COLS = ("tenant_id,mode,rules,rules_version,authorized_by,authorized_at,starts_on,paused_at,"
                "managers_can_manage")


def _err(code: str, status: int = 400):
    raise PortalError(code, status)


def channels_connected(svc) -> bool:
    """Hasta conectar y autorizar canales reales (Postiz, redes) el publicador está desactivado."""
    return getattr(svc.providers.get("publisher"), "name", "disabled") != "disabled"


def validate_rules(body: Dict[str, Any], today: date) -> Dict[str, Any]:
    """Reglas canónicas (listas ordenadas) para que el mismo contenido sea siempre la misma versión."""
    if body.get("objective") not in OBJECTIVES:
        _err("invalid_objective")
    platforms = sorted({p for p in (body.get("platforms") or []) if p in CHANNELS})
    if not platforms or len(platforms) != len(set(body.get("platforms") or [])):
        _err("channels_required" if not platforms else "invalid_channels")
    days = body.get("days") or []
    if not days or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in days):
        _err("invalid_days")
    hours = body.get("hours") or {}
    h_from, h_to = str(hours.get("from") or ""), str(hours.get("to") or "")
    if not HOUR.fullmatch(h_from) or not HOUR.fullmatch(h_to) or h_from >= h_to:
        _err("invalid_hours")
    formats = sorted({f for f in (body.get("formats") or []) if f in FORMATS})
    if not formats or len(formats) != len(set(body.get("formats") or [])):
        _err("invalid_format")
    if body.get("mode") not in MODES:
        _err("invalid_mode")
    cta = str(body.get("cta") or "").strip()
    banned = [str(x).strip() for x in (body.get("banned_topics") or []) if str(x).strip()]
    if len(cta) > 200 or len(banned) > 20 or any(len(x) > 100 for x in banned):
        _err("text_too_long")
    try:
        start = date.fromisoformat(str(body.get("starts_on") or ""))
    except ValueError:
        _err("invalid_date")
    if not today <= start <= today + timedelta(days=90):
        _err("invalid_date")
    return {"rules": {"objective": body["objective"], "platforms": platforms, "days": sorted(set(days)),
                      "hours": {"from": h_from, "to": h_to}, "formats": formats, "mode": body["mode"], "cta": cta,
                      "banned_topics": banned}, "starts_on": start.isoformat()}


def distribute(n: int, start: date, days: List[int], hour: str, zone, today: Optional[date] = None,
               weeks: int = WEEKS) -> List[datetime]:
    """n publicaciones repartidas en `weeks` semanas desde `start`: el mismo número por semana (el resto en las
    primeras, nunca al final), días permitidos espaciados, máximo una por día, a la hora de inicio permitida."""
    if n <= 0:
        return []
    base, extra = divmod(n, weeks)
    hh, mm = (int(x) for x in hour.split(":"))
    out: List[datetime] = []
    for w in range(weeks):
        want = base + (1 if w < extra else 0)
        cands = [start + timedelta(days=7 * w + i) for i in range(7)]
        cands = [d for d in cands if d.weekday() in days and (today is None or d >= today)]
        k = min(want, len(cands))
        for j in range(k):
            d = cands[int((j + 0.5) * len(cands) / k)]
            out.append(datetime.combine(d, time(hh, mm), zone).astimezone(timezone.utc))
    return sorted(out)


class AutopilotService:
    def __init__(self, svc):
        self.svc, self.db = svc, svc.db

    # ------------------------------------------------------------------ lectura
    def _row(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.select("marketing_autopilot_settings", {"tenant_id": f"eq.{tenant_id}", "select": SETTING_COLS,
                                                               "limit": "1"}) or []
        return rows[0] if rows else None

    def _role_of(self, tenant_id: str, user_id: Optional[str]) -> Optional[str]:
        if not user_id:
            return None
        rows = self.db.select("tenant_users", {"tenant_id": f"eq.{tenant_id}", "user_id": f"eq.{user_id}",
                                               "active": "eq.true", "select": "role", "limit": "1"}) or []
        return rows[0]["role"] if rows else None

    def _remaining(self, c, cycle: str, exclude_autopilot: bool = False) -> Optional[int]:
        """Publicaciones disponibles del ciclo (None = sin límite). Cuenta lo programado y lo aprobado del piloto."""
        limit = pp.effective_limit(self.db, c, cycle)
        if limit is None:
            return None
        start = datetime.combine(date.fromisoformat(cycle), time(0), c.zone)
        used = self.svc._usage(c, start)["posts"]
        items = self.db.select("marketing_autopilot_items", {"tenant_id": f"eq.{c.tenant_id}",
                                                             "select": "content_id,slot_at", "limit": "1000"}) or []
        mine = [i for i in items if pp.cycle_of(c, datetime.fromisoformat(str(i["slot_at"]).replace("Z", "+00:00"))) == cycle]
        if mine:
            st = {r["id"]: r["status"] for r in (self.db.select("marketing_content", {
                "tenant_id": f"eq.{c.tenant_id}", "id": f"in.({','.join(i['content_id'] for i in mine)})",
                "select": "id,status", "limit": "1000"}) or [])}
            if exclude_autopilot:                     # base estable del reparto: solo lo programado a mano
                used -= sum(1 for i in mine if st.get(i["content_id"]) in ("scheduled", "publishing", "published"))
            else:
                used += sum(1 for i in mine if st.get(i["content_id"]) in ("approved", "review", "generating", "draft"))
        return max(limit - used, 0)

    def _active_cycle(self, c, row: Optional[Dict[str, Any]]) -> str:
        """Ciclo en el que trabaja el piloto: el de su fecha de inicio si aún no llegó; si no, el actual."""
        today = c.now.astimezone(c.zone).date()
        start = date.fromisoformat(str((row or {}).get("starts_on") or today)[:10])
        return pp.cycle_of(c, datetime.combine(max(start, today), time(12), c.zone))

    def public(self, c, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        row = row or {}
        mode = row.get("mode") or "manual"
        role = c.role
        delegated = row.get("managers_can_manage") is True
        connected = channels_connected(self.svc)
        remaining = self._remaining(c, self._active_cycle(c, row))
        message = None
        if mode == "autopilot":
            message = "quota_exhausted" if remaining == 0 else (None if connected else "waiting_for_channels")
        return {"mode": mode, "rules": row.get("rules"), "rules_version": row.get("rules_version") or 0,
                "starts_on": row.get("starts_on"), "authorized_at": row.get("authorized_at"),
                "authorized_role": self._role_of(c.tenant_id, row.get("authorized_by")) if row.get("authorized_by") else None,
                "paused_at": row.get("paused_at"), "managers_can_manage": delegated,
                "can_manage": role == "owner" or (role == "manager" and delegated), "can_pause": role in ("owner", "manager"),
                "can_delegate": role == "owner", "channels_connected": connected, "available": remaining,
                "message": message, "timezone": str(c.zone)}

    def status(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        c = self.svc.ctx(jwt, tenant_id)
        return self.public(c, self._row(c.tenant_id))

    # ------------------------------------------------------------------ calendario preliminar
    def calendar(self, c, rules: Dict[str, Any], starts_on: str) -> Dict[str, Any]:
        start = date.fromisoformat(starts_on)
        cycle = pp.cycle_of(c, datetime.combine(start, time(12), c.zone))
        base = self._remaining(c, cycle, exclude_autopilot=True)
        n = UNLIMITED_DEFAULT if base is None else base
        today = c.now.astimezone(c.zone).date()
        slots = distribute(n, start, rules["days"], rules["hours"]["from"], c.zone, today=today)
        left: Dict[str, Optional[int]] = {}
        out = []
        for i, at in enumerate(slots):
            cyc = pp.cycle_of(c, at)
            if cyc != cycle:                          # otro ciclo: solo con su propia cuota disponible
                if cyc not in left:
                    left[cyc] = self._remaining(c, cyc)
                if left[cyc] is not None and left[cyc] <= 0:
                    continue
                if left[cyc] is not None:
                    left[cyc] -= 1
            out.append({"at": _iso(at), "day": at.astimezone(c.zone).date().isoformat(),
                        "format": rules["formats"][i % len(rules["formats"])], "platforms": rules["platforms"]})
        return {"slots": out, "planned": len(out), "available": n, "unplaced": max(n - len(out), 0),
                "timezone": str(c.zone)}

    def preview(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.svc.ctx(jwt, tenant_id)
        self.svc._enabled(c)
        v = validate_rules(body, c.now.astimezone(c.zone).date())
        return {**self.calendar(c, v["rules"], v["starts_on"]), "channels_connected": channels_connected(self.svc)}

    # ------------------------------------------------------------------ autorización, pausa y permiso
    def _set(self, c, action: str, rules=None, starts_on=None) -> Dict[str, Any]:
        res = self.db.rpc("marketing_autopilot_set", {"p_tenant": c.tenant_id, "p_actor": c.user["id"],
                                                      "p_action": action, "p_rules": rules, "p_starts_on": starts_on}) or {}
        if res.get("error"):
            raise PortalError(res["error"], ERRORS.get(res["error"], 409))
        return {**self.public(c, res["settings"]), "result": res.get("result")}

    def activate(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.svc.ctx(jwt, tenant_id)
        self.svc._enabled(c)
        if body.get("confirm") is not True:
            _err("confirmation_required")
        v = validate_rules(body.get("rules") or {}, c.now.astimezone(c.zone).date())
        return self._set(c, "activate", v["rules"], v["starts_on"])

    def pause(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        return self._set(self.svc.ctx(jwt, tenant_id), "pause")       # pausar nunca exige nada más

    def set_permission(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.svc.ctx(jwt, tenant_id)
        self.svc._enabled(c)
        value = body.get("managers_can_manage")
        if not isinstance(value, bool):
            _err("invalid_permission")
        return self._set(c, "grant" if value else "revoke")

    # ------------------------------------------------------------------ ciclo del sistema (worker, más adelante)
    def _system(self, user_id: str):
        """Servicios que actúan con el contexto del usuario que autorizó (rol revalidado en cada llamada)."""
        svc = copy.copy(self.svc)
        svc.ctx = lambda jwt, tenant_id: svc._ctx_for({"id": user_id}, tenant_id)
        base = self.svc.planner()

        def studio():
            st = base.studio_factory()
            st.ctx = lambda jwt, tenant_id: st._ctx_for({"id": user_id}, tenant_id)
            return st
        return svc, ContentPlanner(svc, studio)

    def tick(self, tenant_id: str) -> Dict[str, Any]:
        row = self._row(tenant_id)
        if not row or row.get("mode") != "autopilot":
            return {"state": "manual"}
        svc, planner = self._system(row["authorized_by"])
        try:
            c = svc.ctx(None, tenant_id)
            svc._enabled(c)
        except PortalError:
            return {"state": "authorization_withdrawn"}
        if not channels_connected(self.svc):
            return {"state": "waiting_for_channels"}         # nada se crea ni se envía
        rules, version = row["rules"], int(row["rules_version"])
        cal = self.calendar(c, rules, str(row["starts_on"])[:10])
        taken = {str(i["slot_day"])[:10] for i in (self.db.select("marketing_autopilot_items", {
            "tenant_id": f"eq.{c.tenant_id}", "select": "slot_day", "limit": "1000"}) or [])}
        out = {"state": "running", "created": 0, "approved": 0, "scheduled": 0, "failures": []}
        for slot in cal["slots"]:
            if slot["day"] in taken:
                continue
            if self._remaining(c, pp.cycle_of(c, datetime.fromisoformat(slot["at"].replace("Z", "+00:00")))) == 0:
                out["state"] = "quota_exhausted"
                break
            body = {"objective": rules["objective"], "mode": rules["mode"], "format": slot["format"],
                    "media_ids": self._library(c) if rules["mode"] == "library_ai" else [],
                    "instructions": {"topic": "", "audience": "", "offer": "", "tone": "professional",
                                     "cta": rules.get("cta") or "", "platforms": rules["platforms"],
                                     "preferred_date": slot["at"]},
                    "language": c.settings.get("language") or "es"}
            key = f"autopilot-{c.tenant_id}-{slot['day'].replace('-', '')}-v{version}"
            try:
                made = planner.plan(None, c.tenant_id, body, key, actor_role="system")
            except PortalError as e:
                out["failures"].append(e.code)
                continue
            res = self.db.rpc("marketing_autopilot_claim", {"p_tenant": c.tenant_id, "p_content": made["content"]["id"],
                                                            "p_slot_at": slot["at"], "p_slot_day": slot["day"],
                                                            "p_version": version}) or {}
            if res.get("error"):
                out["failures"].append(res["error"])
                continue
            out["created"] += res.get("result") == "claimed"
        for item in self.db.select("marketing_autopilot_items", {"tenant_id": f"eq.{c.tenant_id}",
                                                                 "auto_approved_at": "is.null", "select": "*",
                                                                 "limit": "1000"}) or []:
            planner.sync(c, [svc._content(c, item["content_id"])])
            why = self.auto_approve(c, item)
            if why:
                out["failures"].append(why)
                continue
            out["approved"] += 1
            out["scheduled"] += self._schedule(c, item["content_id"], item["slot_at"], row["authorized_by"])
        if out["state"] == "running" and self._remaining(c, self._active_cycle(c, row)) == 0:
            out["state"] = "quota_exhausted"
        return out

    def _library(self, c) -> List[str]:
        from services import marketing_privacy as pv
        from services.marketing_retention import is_expired
        rows = self.db.select("marketing_media", {"tenant_id": f"eq.{c.tenant_id}", "processing_status": "eq.ready",
                                                  "select": "*", "order": "created_at.desc", "limit": "50"}) or []
        ok = [m["id"] for m in rows if pv.usable_media(m) is None and pv.original_class(m) in pv.EXTERNAL_OK
              and not is_expired(m, c.now)]
        return ok[:3]

    def auto_approve(self, c, item: Dict[str, Any]) -> Optional[str]:
        """None si quedó aprobado automáticamente; si no, el motivo (el contenido sigue esperando revisión)."""
        if not channels_connected(self.svc):
            return "channels_not_connected"
        content = self.svc._content(c, item["content_id"])
        if content["status"] != "review":
            return "not_ready"
        studio = self.svc.planner().studio_factory()
        if jd.ai_budget(studio._settings(c.tenant_id)) <= 0:
            return "budget_unavailable"
        jobs = self.db.select("marketing_generation_jobs", {"tenant_id": f"eq.{c.tenant_id}",
                                                            "content_id": f"eq.{content['id']}", "select": "id",
                                                            "order": "created_at.desc", "limit": "20"}) or []
        for j in jobs:
            why = recheck_inputs(self.db, c.tenant_id, j["id"], c.now)   # vencido, consentimiento, menores, restringido
            if why:
                return why
        rules = (self._row(c.tenant_id) or {}).get("rules") or {}
        text = " ".join(str(content.get(k) or "") for k in ("title", "topic", "caption", "script", "cta")).lower()
        if any(b.lower() in text for b in rules.get("banned_topics") or []):
            return "banned_topic"
        res = self.db.rpc("marketing_autopilot_approve", {"p_tenant": c.tenant_id, "p_content": content["id"],
                                                          "p_version": int(item["rules_version"])}) or {}
        return res.get("error")

    def _schedule(self, c, content_id: str, slot_at: Any, actor: str) -> int:
        cur = self.svc._content(c, content_id)
        at = datetime.fromisoformat(str(slot_at).replace("Z", "+00:00"))
        if cur["status"] != "approved" or at <= c.now:
            return 0
        try:
            when = self.svc._schedule_checks(c, {**cur, "planned_at": _iso(at)}, _iso(at))
        except PortalError:
            return 0                                              # cuota, simulado o canales: queda aprobado
        rows = self.db.update("marketing_content", {"id": f"eq.{content_id}", "tenant_id": f"eq.{c.tenant_id}",
                                                    "status": "eq.approved"},
                              {"status": "scheduled", "scheduled_at": when, "updated_by": actor})
        if not rows:
            return 0
        self.db.insert("marketing_approval_events", {
            "tenant_id": c.tenant_id, "content_id": content_id, "action": "schedule", "from_status": "approved",
            "to_status": "scheduled", "comment": "Programado automáticamente bajo la autorización del owner.",
            "actor_id": actor, "actor_role": "system"})
        self.svc._queue(c, rows[0])
        return 1
