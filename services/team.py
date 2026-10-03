"""
Equipo del Manager Panel: invitar gerentes y quitarles el acceso.

El alta sigue el diseño de la migración de seguridad: se registra el email en
tenant_invites y Supabase Auth envía la invitación; el trigger
link_invites_for_user crea el vínculo tenant_users (tenant + rol) cuando la
persona acepta, o al instante si ya tenía cuenta.

Permisos (se comprueban aquí, con el JWT del usuario que llama):
  * owner   → invita owner / manager / staff y puede quitar accesos;
  * manager → invita manager / staff;
  * staff   → nada.
Nunca se puede quitar el acceso a uno mismo ni al último owner.
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from services.member_portal import PortalError, _now

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CAN_INVITE = {"owner": {"owner", "manager", "staff"}, "manager": {"manager", "staff"}}


class TeamService:
    def __init__(self, db):
        self.db = db

    # -- quién llama -------------------------------------------------------
    def caller(self, jwt: str, tenant_id: str) -> Tuple[Dict[str, Any], str]:
        """Devuelve (usuario, rol) si es owner/manager activo de ese tenant."""
        user = self.db.user_from_jwt(jwt)
        if not user:
            raise PortalError("unauthorized", 401)
        if not tenant_id:
            raise PortalError("forbidden", 403)
        rows = self.db.select("tenant_users", {
            "user_id": f"eq.{user['id']}", "tenant_id": f"eq.{tenant_id}", "active": "eq.true",
            "role": "in.(owner,manager)", "select": "role", "limit": "1"})
        if not rows:
            raise PortalError("forbidden", 403)
        return user, rows[0]["role"]

    def _email_of(self, user_id: str) -> Optional[str]:
        status, data = self.db.auth("GET", f"/admin/users/{user_id}")
        return (data or {}).get("email") if status == 200 else None

    # -- lista -------------------------------------------------------------
    def list(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        user, role = self.caller(jwt, tenant_id)
        rows = self.db.select("tenant_users", {
            "tenant_id": f"eq.{tenant_id}", "active": "eq.true",
            "select": "user_id,role,created_at", "order": "created_at.asc"}) or []
        people = [{"user_id": r["user_id"], "role": r["role"], "since": r.get("created_at"),
                   "email": self._email_of(r["user_id"]), "is_me": r["user_id"] == user["id"]} for r in rows]
        pending = self.db.select("tenant_invites", {
            "tenant_id": f"eq.{tenant_id}", "accepted_at": "is.null",
            "select": "id,email,role,created_at", "order": "created_at.desc"}) or []
        return {"me": {"role": role}, "members": people, "pending": pending}

    # -- invitar -----------------------------------------------------------
    def invite(self, jwt: str, tenant_id: str, email: str, role: str, base_url: str) -> Dict[str, Any]:
        _, my_role = self.caller(jwt, tenant_id)
        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise PortalError("invalid_email", 400)
        if role not in CAN_INVITE.get(my_role, set()):
            raise PortalError("role_not_allowed", 403)
        redirect = {"redirect_to": f"{base_url}/manager"}

        # Invitación nueva: se borra la anterior para que el trigger (AFTER
        # INSERT) vuelva a enlazar si la persona ya tiene cuenta.
        self.db.delete("tenant_invites", {"tenant_id": f"eq.{tenant_id}", "email": f"eq.{email}"})
        self.db.insert("tenant_invites", {"tenant_id": tenant_id, "email": email, "role": role})

        status, _ = self.db.auth("POST", "/invite", json={"email": email}, params=redirect)
        if status < 300:
            return {"status": "invited", "email": email, "role": role}

        # Ya tenía cuenta (p. ej. otro gimnasio o un socio): se le da acceso
        # directo con ese rol y se le envía un email para crear/recuperar contraseña.
        s2, data = self.db.auth("POST", "/admin/generate_link", json={"type": "magiclink", "email": email})
        user_id = ((data or {}).get("id") or ((data or {}).get("user") or {}).get("id")) if s2 < 300 else None
        if not user_id:
            logger.error(f"TEAM invite falló status={status}")
            raise PortalError("rate_limited" if status == 429 else "invite_failed", 429 if status == 429 else 502)
        self.db.upsert("tenant_users", {"tenant_id": tenant_id, "user_id": user_id, "role": role, "active": True},
                       on_conflict="tenant_id,user_id")
        self.db.update("tenant_invites", {"tenant_id": f"eq.{tenant_id}", "email": f"eq.{email}"},
                       {"accepted_at": _now()})
        s3, _ = self.db.auth("POST", "/recover", anon=True, json={"email": email}, params=redirect)
        return {"status": "existing_account", "email": email, "role": role, "email_sent": s3 < 300}

    # -- quitar acceso -----------------------------------------------------
    def revoke(self, jwt: str, tenant_id: str, user_id: str) -> Dict[str, Any]:
        me, my_role = self.caller(jwt, tenant_id)
        if my_role != "owner":
            raise PortalError("forbidden", 403)
        if user_id == me["id"]:
            raise PortalError("cannot_remove_self", 400)
        rows = self.db.select("tenant_users", {"tenant_id": f"eq.{tenant_id}", "user_id": f"eq.{user_id}",
                                               "active": "eq.true", "select": "role", "limit": "1"})
        if not rows:
            raise PortalError("not_found", 404)
        self.db.update("tenant_users", {"tenant_id": f"eq.{tenant_id}", "user_id": f"eq.{user_id}"}, {"active": False})
        return {"ok": True}

    def cancel_invite(self, jwt: str, tenant_id: str, invite_id: str) -> Dict[str, Any]:
        self.caller(jwt, tenant_id)
        rows: List[Dict[str, Any]] = self.db.select("tenant_invites", {
            "id": f"eq.{invite_id}", "tenant_id": f"eq.{tenant_id}", "accepted_at": "is.null",
            "select": "id", "limit": "1"}) or []
        if not rows:
            raise PortalError("not_found", 404)
        self.db.delete("tenant_invites", {"id": f"eq.{invite_id}", "tenant_id": f"eq.{tenant_id}"})
        return {"ok": True}
