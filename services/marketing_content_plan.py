"""Planear contenido: el asistente de Contenido (objetivo → modo → formato → instrucciones → propuesta).

El tenant trabaja con CONTENIDO. La preparación con IA usa por dentro los trabajos de generación (lease, worker,
idempotencia, reservas, costos, auditoría), pero nunca se exponen: la API devuelve estados comerciales.
Nada se publica automáticamente: la propuesta llega como "Listo para revisar" y sigue la aprobación de siempre.
"""
import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from services.marketing_domain import CHANNELS, DomainError
from services.marketing_reel_plan import OBJECTIVES, STYLES
from services.member_portal import PortalError

KEY = re.compile(r"^[A-Za-z0-9_-]{16,120}$")
PLAN_ERRORS = {"idempotency_conflict": 409, "forbidden": 403}
MODES = ("library_ai", "fully_ai")
FORMATS = ("image", "carousel", "reel", "video")
DURATION = {"image": 15, "carousel": 15, "reel": 30, "video": 60}
# Estado del contenido → estado comercial (lo único que ve el tenant).
DISPLAY = {"idea": "draft", "draft": "draft", "generating": "preparing", "review": "ready_for_review",
           "approved": "approved", "rejected": "rejected", "scheduled": "scheduled", "publishing": "scheduled",
           "published": "published", "failed": "could_not_prepare", "archived": "archived"}
# Avance de la preparación según la etapa interna (solo el porcentaje, nunca la etapa).
PROGRESS = {"draft": 10, "awaiting_generation_approval": 20, "queued": 30, "processing": 60, "succeeded": 100}
PUBLIC_PUBLICATION = ("id", "channel", "status", "scheduled_at", "published_at")
PUBLICATION_STATUS = {"pending": "scheduled", "queued": "scheduled", "publishing": "scheduled", "published": "published",
                      "cancelled": "cancelled", "failed": "not_published"}
PUBLIC_STATUS = {"generating": "preparing"}           # el resto de estados del contenido ya son comerciales


def public_status(s: Optional[str]) -> Optional[str]:
    return PUBLIC_STATUS.get(s, s) if s else s


def public_publication(p: Dict[str, Any]) -> Dict[str, Any]:
    """Cola de publicación sin detalles de envío: solo canal, fecha y estado comercial."""
    out = {k: p.get(k) for k in PUBLIC_PUBLICATION}
    out["status"] = PUBLICATION_STATUS.get(p.get("status"), "scheduled")
    return out


def public_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Historial inmutable tal cual, con los estados internos de preparación en su nombre comercial."""
    return [{**h, "from_status": public_status(h.get("from_status")), "to_status": public_status(h.get("to_status"))}
            for h in history]
TERMINAL_FAIL = ("failed", "cancelled")


def _err(code: str, status: int = 400):
    raise PortalError(code, status)


def _text(v: Any, n: int) -> str:
    s = str(v or "").strip()
    if len(s) > n:
        _err("text_too_long")
    return s


def validate(body: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que el owner/manager decide en el asistente. La IA puede proponer, pero esto manda."""
    if body.get("objective") not in OBJECTIVES:
        _err("invalid_objective")
    mode = body.get("mode")
    if mode not in MODES:
        _err("invalid_mode")
    fmt = body.get("format")
    if fmt not in FORMATS:
        _err("invalid_format")
    media = [str(m) for m in (body.get("media_ids") or [])]
    if mode == "fully_ai" and media:
        _err("fully_ai_uses_no_media")                       # completamente con IA: ningún archivo real
    if mode == "library_ai" and not media:
        _err("real_media_required")
    ins = body.get("instructions") or {}
    platforms = [p for p in (ins.get("platforms") or []) if p in CHANNELS]
    if not platforms:
        _err("channels_required")
    tone = ins.get("tone") or "professional"
    if tone not in STYLES:
        _err("invalid_tone")
    return {"objective": body["objective"], "mode": mode, "format": fmt, "media_ids": media,
            "topic": _text(ins.get("topic"), 300), "audience": _text(ins.get("audience"), 300),
            "offer": _text(ins.get("offer"), 300), "tone": tone, "cta": _text(ins.get("cta"), 200),
            "platforms": platforms, "preferred_date": ins.get("preferred_date") or None,
            "language": "en" if body.get("language") == "en" else "es"}


def display_status(row: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None) -> str:
    status = row.get("status")
    if status in ("draft", "idea") and history:
        last = history[-1]
        if last.get("from_status") == "review" and last.get("to_status") == "draft" and last.get("comment"):
            return "changes_requested"
    return DISPLAY.get(status, "draft")


class ContentPlanner:
    """Orquesta el asistente con el servicio de Marketing (contenido) y el de Estudio (trabajos internos)."""

    def __init__(self, svc, studio_factory):
        self.svc, self.db, self.studio_factory = svc, svc.db, studio_factory

    # ------------------------------------------------------------------ planear
    def plan(self, jwt: Optional[str], tenant_id: str, body: Dict[str, Any], idempotency_key: Any,
             actor_role: Optional[str] = None) -> Dict[str, Any]:
        """Crea el contenido UNA vez por clave (tenant + usuario), también con peticiones simultáneas: la base
        reserva la clave y crea el contenido en la misma transacción. Misma clave y otro cuerpo → 409."""
        c = self.svc.ctx(jwt, tenant_id)                        # rol, tenant y módulo antes que nada
        self.svc._enabled(c)
        key = str(idempotency_key or "")
        if not KEY.fullmatch(key):
            _err("idempotency_key_required")
        v = validate(body)
        label = str((body.get("labels") or {}).get("objective") or v["objective"])[:300]
        try:
            values = self.svc._content_values(c, {
                "title": (v["topic"] or label)[:160], "format": v["format"], "channels": v["platforms"],
                "language": v["language"], "topic": v["topic"] or None, "objective": label, "cta": v["cta"] or None,
                "assigned_to": c.user["id"], **({"planned_at": v["preferred_date"]} if v["preferred_date"] else {})})
        except DomainError as e:
            raise PortalError(e.code, e.status)
        digest = hashlib.sha256(json.dumps(v, sort_keys=True, default=str).encode()).hexdigest()
        res = self.db.rpc("marketing_plan_content", {
            "p_tenant": c.tenant_id, "p_user": c.user["id"], "p_role": actor_role or c.role, "p_key": key,
            "p_hash": digest, "p_content": values}) or {}
        if res.get("error"):
            raise PortalError(res["error"], PLAN_ERRORS.get(res["error"], 409))
        row = self.svc._content(c, res["content_id"])
        if res.get("result") != "created":                         # repetición: el mismo contenido, nada nuevo
            return {"content": self.present_rows(c, [row])[0], "preparation": "existing"}
        job_key = "plan-" + hashlib.sha256(f"{c.tenant_id}:{c.user['id']}:{key}".encode()).hexdigest()[:40]
        state = self._prepare(jwt, c, row, v, key=job_key)
        return {"content": self.present(c, self.svc._content(c, row["id"])), "preparation": state}

    def _brief(self, v: Dict[str, Any]) -> Dict[str, Any]:
        return {"objective": v["objective"], "audience": "custom" if v["audience"] else "general",
                "audience_notes": v["audience"], "style": v["tone"], "duration_seconds": DURATION[v["format"]],
                "subtitles": True, "targets": [], "language": v["language"],
                "script": {"hook": v["topic"], "body": v["offer"], "cta": v["cta"]}}

    def _prepare(self, jwt: str, c, content: Dict[str, Any], v: Dict[str, Any], key: str = "",
                 regeneration_of: Optional[str] = None) -> str:
        """Prepara la propuesta con IA si el plan lo permite. Si no, el contenido queda como borrador con las
        instrucciones del usuario (nada se pierde, nada se cobra)."""
        studio = self.studio_factory()
        try:
            job = studio.create_job(jwt, c.tenant_id, {
                "brief": self._brief(v), "creation_mode": v["mode"], "media_ids": v["media_ids"],
                "quality_tier": "draft", "scenes": 4 if v["format"] in ("image", "carousel") else 6,
                "idempotency_key": key or None,
                **({"regeneration_of": regeneration_of} if regeneration_of else {})})
        except (PortalError, DomainError) as e:
            if e.code in ("fully_ai_uses_no_media", "real_media_required", "media_expired", "consent_required",
                          "media_not_usable", "forbidden_claim"):
                raise PortalError(e.code, e.status)
            return "unavailable"
        if job.get("status") != "draft" or (job.get("content_id") and job["content_id"] != content["id"]):
            return "preparing"                       # la misma petición ya lo está preparando: no se toca
        self.db.update("marketing_generation_jobs", {"id": f"eq.{job['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"content_id": content["id"]})
        try:
            studio.estimate_job(jwt, c.tenant_id, job["id"])
            studio.approve_job(jwt, c.tenant_id, job["id"], True)    # la aprobación humana es la del contenido
        except (PortalError, DomainError):
            try:
                studio.cancel_job(jwt, c.tenant_id, job["id"])
            except (PortalError, DomainError):
                pass
            return "unavailable"
        if not self._system_move(c, content["id"], ("draft", "failed"), "generating", "status"):
            return "unavailable"
        try:                    # simulado: se prepara al momento; con proveedores reales sigue en segundo plano
            studio.process_job(jwt, c.tenant_id, job["id"])
        except (PortalError, DomainError):
            pass
        self.sync(c, [self.svc._content(c, content["id"])])
        return "preparing"

    def _system_move(self, c, content_id: str, frm, to: str, action: str, values: Optional[Dict] = None) -> bool:
        cur = self.svc._content(c, content_id)
        if cur["status"] not in frm:
            return False
        if cur["status"] == "failed" and to == "generating":     # failed → draft → generating
            self._system_move(c, content_id, ("failed",), "draft", "reopen")
            cur = self.svc._content(c, content_id)
        rows = self.db.update("marketing_content", {"id": f"eq.{content_id}", "tenant_id": f"eq.{c.tenant_id}",
                                                    "status": f"eq.{cur['status']}"},
                              {**(values or {}), "status": to, "updated_by": c.user["id"]})
        if rows:                                   # cambio del sistema: nunca como acción personal del usuario
            self.db.insert("marketing_approval_events", {
                "tenant_id": c.tenant_id, "content_id": content_id, "action": action, "from_status": cur["status"],
                "to_status": to, "comment": None, "actor_id": c.user["id"], "actor_role": "system"})
        return bool(rows)

    # ------------------------------------------------------------------ sincronizar y presentar
    def _jobs(self, c, ids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not ids:
            return {}
        rows = self.db.select("marketing_generation_jobs", {
            "tenant_id": f"eq.{c.tenant_id}", "content_id": f"in.({','.join(ids)})",
            "select": "id,content_id,status,regeneration_of,request_metadata,result_metadata,created_at",
            "order": "created_at.desc", "limit": "1000"}) or []
        replaced = {r.get("regeneration_of") for r in rows if r.get("regeneration_of")}
        latest: Dict[str, Dict[str, Any]] = {}
        for r in rows:                                   # el último intento: el que ningún reintento reemplazó
            if r["id"] not in replaced:
                latest.setdefault(r["content_id"], r)
        return latest

    def sync(self, c, rows: List[Dict[str, Any]]) -> None:
        """Contenido en preparación: si su trabajo terminó, la propuesta pasa a revisión; si falló, a
        "No se pudo preparar". Idempotente (cambios condicionados al estado)."""
        pending = [r for r in rows if r.get("status") == "generating"]
        jobs = self._jobs(c, [r["id"] for r in pending])
        for r in pending:
            j = jobs.get(r["id"])
            if not j:
                continue
            if j["status"] == "succeeded":
                brief = ((j.get("request_metadata") or {}).get("brief") or {})
                script = brief.get("script") or {}
                proposal = {"caption": r.get("caption") or "\n\n".join(x for x in (script.get("hook"), script.get("body")) if x)
                            or None, "cta": r.get("cta") or script.get("cta") or None}
                self._system_move(c, r["id"], ("generating",), "review", "submit", proposal)
            elif j["status"] in TERMINAL_FAIL:
                self._system_move(c, r["id"], ("generating",), "failed", "status")

    def present(self, c, row: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None,
                job: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out = {k: v for k, v in row.items() if k != "notes" or not str(v or "").startswith(("generation_job:", "mock_generation_job:"))}
        out["display_status"] = display_status(row, history)
        out["status"] = public_status(row.get("status"))
        if row.get("status") == "generating":
            out["progress"] = PROGRESS.get((job or {}).get("status"), 30)
        return out

    def present_rows(self, c, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self.sync(c, rows)
        ids = [r["id"] for r in rows]
        fresh = {r["id"]: r for r in (self.db.select("marketing_content", {
            "tenant_id": f"eq.{c.tenant_id}", "id": f"in.({','.join(ids)})", "select": "*", "limit": "1000"}) or [])} if ids else {}
        rows = [{**r, **{k: fresh[r["id"]][k] for k in ("status", "caption", "cta") if r["id"] in fresh and k in fresh[r["id"]]}}
                for r in rows]
        drafts = [r["id"] for r in rows if r.get("status") in ("draft", "idea")]
        events: Dict[str, List[Dict[str, Any]]] = {}
        if drafts:
            for e in self.db.select("marketing_approval_events", {
                    "tenant_id": f"eq.{c.tenant_id}", "content_id": f"in.({','.join(drafts)})",
                    "select": "content_id,action,from_status,to_status,comment,created_at", "order": "created_at.asc",
                    "limit": "5000"}) or []:
                events.setdefault(e["content_id"], []).append(e)
        jobs = self._jobs(c, [r["id"] for r in rows if r.get("status") == "generating"])
        return [self.present(c, r, events.get(r["id"]), jobs.get(r["id"])) for r in rows]

    # ------------------------------------------------------------------ reintentar
    def retry(self, jwt: str, tenant_id: str, content_id: str) -> Dict[str, Any]:
        c = self.svc.ctx(jwt, tenant_id)
        self.svc._enabled(c)
        cur = self.svc._content(c, content_id)                  # la reserva condicionada de abajo exige «failed»
        last = self._jobs(c, [cur["id"]]).get(cur["id"])
        if not last:
            _err("not_retryable", 409)
        brief = ((last.get("request_metadata") or {}).get("brief") or {})
        script = brief.get("script") or {}
        media = [i["media_id"] for i in (self.db.select("marketing_generation_inputs", {
            "tenant_id": f"eq.{c.tenant_id}", "job_id": f"eq.{last['id']}", "select": "media_id", "limit": "100"}) or [])
            if i.get("media_id")]
        v = {"objective": brief.get("objective"), "mode": "library_ai" if media else "fully_ai", "format": cur["format"]
             if cur["format"] in FORMATS else "reel", "media_ids": media, "topic": script.get("hook") or "",
             "audience": brief.get("audience_notes") or "", "offer": script.get("body") or "",
             "tone": brief.get("style") or "professional", "cta": script.get("cta") or "",
             "language": brief.get("language") or "es"}
        # El reintento se reserva con un cambio condicionado (No se pudo preparar → Borrador): de dos reintentos
        # simultáneos solo uno sigue; la clave derivada del intento anterior evita además un segundo trabajo.
        if not self._system_move(c, cur["id"], ("failed",), "draft", "reopen"):
            _err("not_retryable", 409)
        state = self._prepare(jwt, c, self.svc._content(c, cur["id"]), v, key=f"retry-{last['id']}",
                              regeneration_of=last["id"])
        return {"content": self.present(c, self.svc._content(c, cur["id"])), "preparation": state}
