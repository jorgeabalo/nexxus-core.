"""
AITA Marketing — servicio del Manager Panel (Fase 1).

Seguridad (se comprueba aquí con el JWT de quien llama; RLS además impide
leer datos ajenos y prohíbe que los usuarios escriban directamente):
  * owner / manager del tenant → todo el módulo.
  * staff, socios, otros tenants → 403 (staff podrá recibir permiso más adelante).
  * Si marketing_settings.marketing_enabled es false, solo se puede consultar.

Reglas de estado: services/marketing_domain.py (y el trigger de la migración).
Cada cambio de estado queda en marketing_approval_events (quién, cuándo, de qué
estado a cuál y comentario).

Fase 1 no publica nada: programar crea filas en marketing_publications con una
idempotency_key y se las entrega al PublisherProvider, que hoy está desactivado.
"""
import calendar
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from services import marketing_domain as d
from services.marketing_domain import DomainError
from services.marketing_providers import PublishRequest, default_providers, redact
from services.member_portal import PortalError

logger = logging.getLogger(__name__)

ROLES = ("owner", "manager")
CONTENT_COLS = ("id,tenant_id,campaign_id,title,objective,topic,format,channels,language,caption,script,cta,hashtags,"
                "planned_at,scheduled_at,notes,status,assigned_to,submitted_at,approved_by,approved_at,created_by,"
                "updated_by,created_at,updated_at")
CAMPAIGN_COLS = "id,tenant_id,name,objective,description,start_date,end_date,budget,status,created_at,updated_at"
BRAND_FIELDS = ("business_name", "description", "target_audience", "tone", "languages", "priority_services", "cta",
                "phone", "website", "color_primary", "color_accent", "color_secondary", "logo_url", "banned_topics",
                "compliance_notes", "ai_instructions")
ACTIONS = {"review": "submit", "approved": "approve", "rejected": "reject", "draft": "reopen",
           "scheduled": "schedule", "archived": "archive"}


def _uuid(v: Any, code: str = "not_found", status: int = 404) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError, AttributeError):
        raise PortalError(code, status)


def _guard(fn):
    """Convierte DomainError en PortalError (mismo formato de error que el resto del panel)."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except DomainError as e:
            raise PortalError(e.code, e.status)
    wrapper.__name__ = fn.__name__
    return wrapper


class MarketingService:
    def __init__(self, db, now: Optional[datetime] = None, providers: Optional[Dict[str, Any]] = None):
        self.db = db
        self._now = now
        self.providers = providers or default_providers()

    # ------------------------------------------------------------------ contexto
    def ctx(self, jwt: str, tenant_id: str) -> SimpleNamespace:
        if not getattr(self.db, "enabled", True):
            raise PortalError("portal_not_configured", 503)
        user = self.db.user_from_jwt(jwt)
        if not user:
            raise PortalError("unauthorized", 401)
        tenant_id = _uuid(tenant_id, "forbidden", 403)
        rows = self.db.select("tenant_users", {
            "user_id": f"eq.{user['id']}", "tenant_id": f"eq.{tenant_id}", "active": "eq.true",
            "role": f"in.({','.join(ROLES)})", "select": "role", "limit": "1"})
        if not rows:
            raise PortalError("forbidden", 403)
        t = (self.db.select("tenants", {"id": f"eq.{tenant_id}", "select": "id,name,timezone,branding,settings",
                                        "limit": "1"}) or [{}])[0]
        try:
            zone = ZoneInfo(t.get("timezone") or "America/Chicago")
        except Exception:
            zone = ZoneInfo("America/Chicago")
        now = self._now or datetime.now(timezone.utc)
        return SimpleNamespace(user=user, role=rows[0]["role"], tenant_id=tenant_id, tenant=t, zone=zone, now=now,
                               settings=self._settings(tenant_id))

    def _settings(self, tenant_id: str) -> Dict[str, Any]:
        row = (self.db.select("marketing_settings", {"tenant_id": f"eq.{tenant_id}", "select": "*", "limit": "1"}) or [None])[0]
        out = dict(d.DEFAULT_SETTINGS)
        if row:
            out.update({k: row.get(k) for k in d.DEFAULT_SETTINGS if k in row})
        return out

    def _enabled(self, c) -> None:
        if not c.settings["marketing_enabled"]:
            raise PortalError("marketing_disabled", 403)

    def _month(self, c, ref: Optional[datetime] = None):
        local = (ref or c.now).astimezone(c.zone)
        first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last = calendar.monthrange(first.year, first.month)[1]
        nxt = first + timedelta(days=last)
        return first.astimezone(timezone.utc), nxt.astimezone(timezone.utc)

    def _usage(self, c, ref: Optional[datetime] = None) -> Dict[str, int]:
        start, end = self._month(c, ref)
        rows = self.db.select("marketing_content", {
            "tenant_id": f"eq.{c.tenant_id}", "status": f"in.({','.join(d.COUNTED)})",
            "and": f"(scheduled_at.gte.{_iso(start)},scheduled_at.lt.{_iso(end)})",
            "select": "id,status,format", "limit": "10000"}) or []
        return d.usage_for(rows)

    def _when(self, c, v: Any, code: str = "invalid_date") -> Optional[str]:
        """'YYYY-MM-DDTHH:MM' en la zona del tenant (o ISO con zona) → ISO UTC."""
        if v in (None, ""):
            return None
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            raise PortalError(code, 400)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=c.zone)
        if not (2000 <= dt.year <= 2100):
            raise PortalError(code, 400)
        return _iso(dt)

    # ------------------------------------------------------------------ resumen
    def dashboard(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        rows = self.db.select("marketing_content", {"tenant_id": f"eq.{c.tenant_id}", "status": "neq.archived",
                                                    "select": "id,status", "limit": "10000"}) or []
        counts = {s: 0 for s in d.STATUSES}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        upcoming = self.db.select("marketing_content", {
            "tenant_id": f"eq.{c.tenant_id}", "status": "in.(approved,scheduled)",
            "or": f"(scheduled_at.gte.{_iso(c.now)},planned_at.gte.{_iso(c.now)})",
            "select": "id,title,status,format,channels,planned_at,scheduled_at,campaign_id",
            "order": "scheduled_at.asc.nullslast,planned_at.asc", "limit": "10"}) or []
        return {
            "role": c.role, "settings": c.settings,
            "counts": {"drafts": counts["idea"] + counts["draft"] + counts["generating"] + counts["rejected"],
                       "review": counts["review"], "approved": counts["approved"], "scheduled": counts["scheduled"],
                       "published": counts["published"], "failed": counts["failed"], "by_status": counts},
            "usage": self._usage(c),
            "limits": {k: c.settings[k] for k in d.LIMIT_KEYS},
            "upcoming": upcoming,
        }

    # ------------------------------------------------------------------ Brand Kit
    def brand(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        row = (self.db.select("marketing_brand_profiles", {"tenant_id": f"eq.{c.tenant_id}", "select": "*",
                                                           "limit": "1"}) or [None])[0]
        tenant_logo = d.safe_logo_url((c.tenant.get("branding") or {}).get("logo_url"))
        if row:
            profile = {k: row.get(k) for k in BRAND_FIELDS}
        else:
            profile = d.tenant_brand_defaults(c.tenant)
        return {"profile": profile, "inherited": row is None, "tenant_logo_url": tenant_logo,
                "updated_at": row.get("updated_at") if row else None}

    @_guard
    def save_brand(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        v: Dict[str, Any] = {
            "business_name": d.text(body.get("business_name"), 120, "invalid_business_name"),
            "description": d.text(body.get("description"), 2000, "invalid_description"),
            "target_audience": d.text(body.get("target_audience"), 1000, "invalid_target_audience"),
            "tone": d.text(body.get("tone"), 500, "invalid_tone"),
            "languages": d.text_list(body.get("languages"), d.LANGUAGES, "invalid_languages"),
            "priority_services": d.text_list(body.get("priority_services"), None, "invalid_services"),
            "cta": d.text(body.get("cta"), 200, "invalid_cta"),
            "phone": d.text(body.get("phone"), 25, "invalid_phone"),
            "website": d.text(body.get("website"), 300, "invalid_website"),
            "banned_topics": d.text_list(body.get("banned_topics"), None, "invalid_banned_topics", max_items=50),
            "compliance_notes": d.text(body.get("compliance_notes"), 2000, "invalid_compliance_notes"),
            "ai_instructions": d.text(body.get("ai_instructions"), 4000, "invalid_ai_instructions"),
        }
        if v["phone"] and not re.fullmatch(r"[0-9+()\-. ]{7,25}", v["phone"]):
            raise PortalError("invalid_phone", 400)
        if v["website"] and not d.WEBSITE.match(v["website"]):
            raise PortalError("invalid_website", 400)
        for k in ("color_primary", "color_accent", "color_secondary"):
            raw = body.get(k)
            v[k] = d.color(raw) if raw not in (None, "") else None
            if raw not in (None, "") and v[k] is None:
                raise PortalError("invalid_color", 400)
        logo = body.get("logo_url")
        if logo in (None, ""):
            v["logo_url"] = None
        elif not (isinstance(logo, str) and d.LOCAL_PATH.match(logo) and len(logo) <= 500 and d.safe_logo_url(logo)):
            # Misma política que el panel (sin //host, sin SVG, sin URLs externas) y,
            # además, sin base64: los binarios no se guardan en tablas.
            raise PortalError("invalid_logo", 400)
        else:
            v["logo_url"] = logo
        row = self.db.upsert("marketing_brand_profiles", {**v, "tenant_id": c.tenant_id, "updated_by": c.user["id"]},
                             "tenant_id")
        return {"profile": {k: (row or v).get(k) for k in BRAND_FIELDS}, "inherited": False}

    # ------------------------------------------------------------------ campañas
    def campaigns(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        rows = self.db.select("marketing_campaigns", {"tenant_id": f"eq.{c.tenant_id}", "select": CAMPAIGN_COLS,
                                                      "order": "created_at.desc", "limit": "500"}) or []
        content = self.db.select("marketing_content", {"tenant_id": f"eq.{c.tenant_id}", "campaign_id": "not.is.null",
                                                       "select": "id,campaign_id", "limit": "10000"}) or []
        for r in rows:
            r["content_count"] = sum(1 for x in content if x.get("campaign_id") == r["id"])
        return {"campaigns": rows}

    @_guard
    def save_campaign(self, jwt: str, tenant_id: str, body: Dict[str, Any], campaign_id: Optional[str] = None):
        c = self.ctx(jwt, tenant_id)
        self._enabled(c)
        v = {"name": d.text(body.get("name"), 120, "invalid_name", True),
             "objective": d.text(body.get("objective"), 300, "invalid_objective"),
             "description": d.text(body.get("description"), 2000, "invalid_description"),
             "start_date": _date(body.get("start_date")), "end_date": _date(body.get("end_date")),
             "status": body.get("status") or "planned"}
        if v["status"] not in d.CAMPAIGN_STATUSES:
            raise PortalError("invalid_status", 400)
        if v["start_date"] and v["end_date"] and v["end_date"] < v["start_date"]:
            raise PortalError("invalid_dates", 400)
        budget = body.get("budget")
        if budget in (None, ""):
            v["budget"] = None
        else:
            try:
                b = float(budget)
            except (TypeError, ValueError):
                raise PortalError("invalid_budget", 400)
            if not (0 <= b < 100_000_000):
                raise PortalError("invalid_budget", 400)
            v["budget"] = round(b, 2)
        if campaign_id:
            self._campaign(c, campaign_id)
            return (self.db.update("marketing_campaigns", {"id": f"eq.{_uuid(campaign_id)}",
                                                           "tenant_id": f"eq.{c.tenant_id}"}, v) or [{}])[0]
        return self.db.insert("marketing_campaigns", {**v, "tenant_id": c.tenant_id, "created_by": c.user["id"]})

    def _campaign(self, c, campaign_id: Any, code: str = "not_found", status: int = 404) -> Dict[str, Any]:
        rows = self.db.select("marketing_campaigns", {"id": f"eq.{_uuid(campaign_id, code, status)}",
                                                      "tenant_id": f"eq.{c.tenant_id}", "select": "id,name", "limit": "1"})
        if not rows:
            raise PortalError(code, status)
        return rows[0]

    # ------------------------------------------------------------------ contenido
    def content_list(self, jwt: str, tenant_id: str, status: str = "", campaign_id: str = "") -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        q = {"tenant_id": f"eq.{c.tenant_id}", "select": CONTENT_COLS, "order": "updated_at.desc", "limit": "500"}
        if status:
            if status not in d.STATUSES:
                raise PortalError("invalid_status", 400)
            q["status"] = f"eq.{status}"
        else:
            q["status"] = "neq.archived"
        if campaign_id:
            q["campaign_id"] = f"eq.{_uuid(campaign_id, 'invalid_campaign', 400)}"
        return {"content": self.db.select("marketing_content", q) or []}

    def _content(self, c, content_id: Any) -> Dict[str, Any]:
        rows = self.db.select("marketing_content", {"id": f"eq.{_uuid(content_id)}", "tenant_id": f"eq.{c.tenant_id}",
                                                    "select": CONTENT_COLS, "limit": "1"})
        if not rows:
            raise PortalError("not_found", 404)
        return rows[0]

    def content(self, jwt: str, tenant_id: str, content_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        item = self._content(c, content_id)
        history = self.db.select("marketing_approval_events", {
            "tenant_id": f"eq.{c.tenant_id}", "content_id": f"eq.{item['id']}",
            "select": "id,action,from_status,to_status,comment,actor_id,actor_role,created_at",
            "order": "created_at.asc", "limit": "500"}) or []
        pubs = self.db.select("marketing_publications", {
            "tenant_id": f"eq.{c.tenant_id}", "content_id": f"eq.{item['id']}",
            "select": "id,channel,provider,status,scheduled_at,published_at,attempts,last_error",
            "order": "created_at.desc", "limit": "50"}) or []
        return {"content": item, "history": history, "publications": pubs,
                "allowed": [s for s in d.TRANSITIONS.get(item["status"], ())
                            if d.can_transition(item["status"], s, approval_required=c.settings["approval_required"])
                            and s not in ("publishing", "published", "failed", "generating")]}

    def _content_values(self, c, body: Dict[str, Any], current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cur = current or {}
        get = lambda k: body[k] if k in body else cur.get(k)   # noqa: E731
        fmt = get("format")
        if fmt not in d.FORMATS:
            raise DomainError("invalid_format", 400)
        lang = get("language") or "en"
        if lang not in d.LANGUAGES:
            raise DomainError("invalid_language", 400)
        v = {"title": d.text(get("title"), 160, "invalid_title", True),
             "objective": d.text(get("objective"), 300, "invalid_objective"),
             "topic": d.text(get("topic"), 300, "invalid_topic"),
             "format": fmt, "language": lang,
             "channels": d.text_list(get("channels"), d.CHANNELS, "invalid_channels"),
             "caption": d.text(get("caption"), 5000, "invalid_caption"),
             "script": d.text(get("script"), 10000, "invalid_script"),
             "cta": d.text(get("cta"), 200, "invalid_cta"),
             "hashtags": d.hashtags(get("hashtags")),
             "notes": d.text(get("notes"), 2000, "invalid_notes"),
             "planned_at": self._when(c, body["planned_at"]) if "planned_at" in body else cur.get("planned_at")}
        camp = get("campaign_id")
        v["campaign_id"] = self._campaign(c, camp, "invalid_campaign", 400)["id"] if camp else None
        who = get("assigned_to")
        if who:
            who = _uuid(who, "invalid_assignee", 400)
            if not self.db.select("tenant_users", {"tenant_id": f"eq.{c.tenant_id}", "user_id": f"eq.{who}",
                                                   "active": "eq.true", "select": "user_id", "limit": "1"}):
                raise DomainError("invalid_assignee", 400)
        v["assigned_to"] = who or None
        return v

    @_guard
    def create_content(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._enabled(c)
        status = body.get("status") or "draft"
        if status not in ("idea", "draft"):
            raise PortalError("invalid_status", 400)
        body = {**body}
        body.setdefault("assigned_to", c.user["id"])
        v = self._content_values(c, body)
        row = self.db.insert("marketing_content", {**v, "status": status, "tenant_id": c.tenant_id,
                                                   "created_by": c.user["id"], "updated_by": c.user["id"]})
        self._event(c, row["id"], "create", None, status, None)
        return row

    @_guard
    def update_content(self, jwt: str, tenant_id: str, content_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._enabled(c)
        cur = self._content(c, content_id)
        if cur["status"] not in d.EDITABLE:
            raise PortalError("not_editable", 409)
        v = self._content_values(c, body, cur)
        rows = self.db.update("marketing_content", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                                    "status": f"eq.{cur['status']}"}, {**v, "updated_by": c.user["id"]})
        if not rows:
            raise PortalError("conflict", 409)
        return rows[0]

    # ------------------------------------------------------------------ estados
    @_guard
    def transition(self, jwt: str, tenant_id: str, content_id: str, target: str, comment: Any = None,
                   scheduled_at: Any = None) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        self._enabled(c)
        cur = self._content(c, content_id)
        note = d.text(comment, 2000, "invalid_comment")
        if target == "rejected" and not note:
            raise PortalError("comment_required", 400)
        if target in ("publishing", "published", "failed", "generating"):
            # Solo el sistema (proveedores, Fase 2+) mueve estos estados.
            raise PortalError("invalid_transition", 409)
        d.check_transition(cur["status"], target, approval_required=c.settings["approval_required"])
        values: Dict[str, Any] = {"status": target, "updated_by": c.user["id"]}
        if target == "review":
            values["submitted_at"] = _iso(c.now)
        if target == "approved" and cur["status"] != "scheduled":
            values.update(approved_by=c.user["id"], approved_at=_iso(c.now))
        if target in ("draft", "rejected"):
            values.update(approved_by=None, approved_at=None)
        if target == "scheduled":
            values["scheduled_at"] = self._schedule_checks(c, cur, scheduled_at)
        if cur["status"] == "scheduled" and target == "approved":
            values["scheduled_at"] = None
        rows = self.db.update("marketing_content", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}",
                                                    "status": f"eq.{cur['status']}"}, values)
        if not rows:
            raise PortalError("conflict", 409)
        action = "unschedule" if cur["status"] == "scheduled" else (
            "restore" if cur["status"] == "archived" else ACTIONS.get(target, "status"))
        self._event(c, cur["id"], action, cur["status"], target, note)
        if target == "scheduled":
            self._queue(c, rows[0])
        elif cur["status"] == "scheduled":
            self._cancel_queue(c, cur["id"])
        return rows[0]

    def _schedule_checks(self, c, cur: Dict[str, Any], when: Any) -> str:
        at = self._when(c, when) or cur.get("planned_at")
        if not at:
            raise PortalError("scheduled_at_required", 400)
        at_dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if at_dt <= c.now:
            raise PortalError("scheduled_in_past", 400)
        if not cur.get("channels"):
            raise PortalError("channels_required", 400)
        hit = d.limit_reached(c.settings, self._usage(c, at_dt), cur["format"])
        if hit:
            raise PortalError(f"limit_{hit}", 409)
        return at

    def _queue(self, c, item: Dict[str, Any]) -> None:
        """Una fila por canal con idempotency_key estable; el proveedor (hoy desactivado) decide."""
        publisher = self.providers["publisher"]
        for ch in item.get("channels") or []:
            key = f"{item['id']}:{ch}:{item['scheduled_at']}"
            req = PublishRequest(tenant_id=c.tenant_id, content_id=item["id"], channels=[ch],
                                 scheduled_at=item["scheduled_at"], caption=item.get("caption"), idempotency_key=key)
            try:
                res = publisher.schedule(req)
                status, err = ("queued" if res.status == "queued" else "pending"), None
            except Exception as e:
                logger.error(f"MARKETING_PUBLISHER_ERROR {type(e).__name__}")
                status, err = "failed", redact(e)
                res = None
            self.db.upsert("marketing_publications", {
                "tenant_id": c.tenant_id, "content_id": item["id"], "channel": ch, "provider": publisher.name,
                "status": status, "scheduled_at": item["scheduled_at"], "idempotency_key": key,
                "external_id": getattr(res, "external_id", None), "last_error": err}, "tenant_id,idempotency_key")

    def _cancel_queue(self, c, content_id: str) -> None:
        self.db.update("marketing_publications", {"tenant_id": f"eq.{c.tenant_id}", "content_id": f"eq.{content_id}",
                                                  "status": "in.(pending,queued)"}, {"status": "cancelled"})

    def _event(self, c, content_id: str, action: str, frm: Optional[str], to: str, comment: Optional[str]) -> None:
        self.db.insert("marketing_approval_events", {
            "tenant_id": c.tenant_id, "content_id": content_id, "action": action, "from_status": frm,
            "to_status": to, "comment": comment, "actor_id": c.user["id"], "actor_role": c.role})

    # ------------------------------------------------------------------ calendario
    def calendar(self, jwt: str, tenant_id: str, start: str, end: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        s, e = _date(start), _date(end)
        if not s or not e or e < s or (e - s).days > 62:
            raise PortalError("invalid_range", 400)
        lo = _iso(datetime.combine(s, datetime.min.time(), c.zone))
        hi = _iso(datetime.combine(e + timedelta(days=1), datetime.min.time(), c.zone))
        rows = self.db.select("marketing_content", {
            "tenant_id": f"eq.{c.tenant_id}", "status": "neq.archived",
            "or": f"(and(scheduled_at.gte.{lo},scheduled_at.lt.{hi}),and(planned_at.gte.{lo},planned_at.lt.{hi}))",
            "select": "id,title,status,format,channels,planned_at,scheduled_at,campaign_id,assigned_to",
            "order": "planned_at.asc", "limit": "1000"}) or []
        camps = {x["id"]: x["name"] for x in self.db.select("marketing_campaigns", {
            "tenant_id": f"eq.{c.tenant_id}", "select": "id,name", "limit": "1000"}) or []}
        people = self._people(c, {r["assigned_to"] for r in rows if r.get("assigned_to")})
        for r in rows:
            r["date"] = r.get("scheduled_at") or r.get("planned_at")
            r["campaign_name"] = camps.get(r.get("campaign_id"))
            r["assignee_name"] = people.get(r.get("assigned_to"))
        return {"items": rows, "timezone": str(c.zone)}

    def _people(self, c, ids) -> Dict[str, str]:
        if not ids:
            return {}
        links = self.db.select("tenant_users", {"tenant_id": f"eq.{c.tenant_id}", "user_id": f"in.({','.join(ids)})",
                                                "select": "user_id,staff_id", "limit": "500"}) or []
        staff_ids = [x["staff_id"] for x in links if x.get("staff_id")]
        staff = {s["id"]: " ".join(filter(None, [s.get("first_name"), s.get("last_name")])) for s in (
            self.db.select("staff", {"tenant_id": f"eq.{c.tenant_id}", "id": f"in.({','.join(staff_ids)})",
                                     "select": "id,first_name,last_name", "limit": "500"}) or [] if staff_ids else [])}
        return {x["user_id"]: staff.get(x.get("staff_id")) for x in links if staff.get(x.get("staff_id"))}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(v: Any) -> Optional[date]:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise PortalError("invalid_date", 400)
