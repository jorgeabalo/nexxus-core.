"""
Portal del socio (Member Panel): enlace / QR personal y envío de acceso.

Enlace personal:  {BASE}/m/q/<token>
  token = <member_id sin guiones>.<firma>
  firma = HMAC-SHA256(PORTAL_TOKEN_SECRET, "<member_id>:<portal_token_version>")

  * El token NO se guarda en la base: se recalcula cuando hace falta.
  * "Regenerar QR" = subir members.portal_token_version => el enlace
    anterior deja de funcionar al instante.
  * Sin PORTAL_TOKEN_SECRET el portal por QR queda deshabilitado.

Al abrir el enlace, el servidor asegura que el socio tenga usuario en
Supabase Auth (members.user_id) y genera un enlace mágico de un solo uso;
el navegador lo canjea (verifyOtp) y queda con una sesión real de Supabase,
así que todo lo que ve el socio pasa por RLS.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from services.supabase_admin import SupabaseAdmin

logger = logging.getLogger(__name__)

_MEMBER_COLS = ("id,tenant_id,first_name,last_name,email,phone,user_id,membership_status,"
                "portal_token_version,portal_invited_at,portal_activated_at,portal_last_used_at")


class PortalError(Exception):
    """Error esperado (enlace inválido, sin permiso, etc.). Mensaje seguro para el cliente."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def _secret() -> bytes:
    s = re.sub(r"\s+", "", os.getenv("PORTAL_TOKEN_SECRET") or "")
    if len(s) < 32:
        raise PortalError("portal_not_configured", 503)
    return s.encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def make_token(member_id: str, version: int) -> str:
    mid = uuid.UUID(str(member_id))
    sig = hmac.new(_secret(), f"{mid}:{int(version)}".encode(), hashlib.sha256).digest()
    return f"{mid.hex}.{_b64(sig)[:32]}"


def parse_token(token: str) -> Tuple[str, str]:
    m = re.fullmatch(r"([0-9a-f]{32})\.([A-Za-z0-9_\-]{32})", token or "")
    if not m:
        raise PortalError("invalid_link", 404)
    return str(uuid.UUID(m.group(1))), m.group(2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def e164(numero: Optional[str]) -> Optional[str]:
    d = re.sub(r"\D", "", numero or "")
    if len(d) == 10:
        d = "1" + d
    return f"+{d}" if len(d) >= 11 else None


class MemberPortal:
    def __init__(self, db: Optional[SupabaseAdmin] = None, sms_sender=None):
        self.db = db or SupabaseAdmin()
        self._sms_sender = sms_sender  # inyectable en tests

    # ------------------------------------------------------------------
    def _member(self, member_id: str) -> Dict[str, Any]:
        if not self.db.enabled:
            raise PortalError("portal_not_configured", 503)
        rows = self.db.select("members", {"id": f"eq.{member_id}", "select": _MEMBER_COLS, "limit": "1"}) or []
        if not rows:
            raise PortalError("invalid_link", 404)
        return rows[0]

    def _tenant(self, tenant_id: str) -> Dict[str, Any]:
        rows = self.db.select("tenants", {"id": f"eq.{tenant_id}", "select": "id,name,twilio_phone,branding,settings",
                                          "limit": "1"}) or []
        return rows[0] if rows else {}

    def link_for(self, member: Dict[str, Any], base_url: str) -> str:
        return f"{base_url.rstrip('/')}/m/q/{make_token(member['id'], member.get('portal_token_version') or 1)}"

    # ------------------------------------------------------------------
    # 1) Abrir el enlace / QR
    # ------------------------------------------------------------------
    def verify_token(self, token: str) -> Dict[str, Any]:
        _secret()  # sin secreto configurado el portal por QR está deshabilitado
        member_id, sig = parse_token(token)
        member = self._member(member_id)
        expected = make_token(member_id, member.get("portal_token_version") or 1).split(".", 1)[1]
        if not hmac.compare_digest(expected, sig):
            raise PortalError("invalid_link", 404)
        if (member.get("membership_status") or "active").lower() in ("cancelled", "canceled"):
            raise PortalError("membership_inactive", 403)
        return member

    def login_token_for(self, token: str) -> str:
        """Valida el enlace y devuelve un token_hash de un solo uso para verifyOtp."""
        member = self.verify_token(token)
        user_id, email = self.ensure_auth_user(member)
        status, data = self.db.auth("POST", "/admin/generate_link", json={"type": "magiclink", "email": email})
        props = (data or {}).get("properties") or {}
        hashed = props.get("hashed_token") or (data or {}).get("hashed_token")
        if status >= 300 or not hashed:
            logger.error(f"PORTAL generate_link falló status={status}")
            raise PortalError("login_unavailable", 502)
        values = {"portal_last_used_at": _now()}
        if not member.get("portal_activated_at"):
            values["portal_activated_at"] = _now()
        self.db.update("members", {"id": f"eq.{member['id']}"}, values)
        return hashed

    # ------------------------------------------------------------------
    # 2) Usuario de Supabase Auth del socio
    # ------------------------------------------------------------------
    def _alias_email(self, member_id: str) -> str:
        return f"m-{uuid.UUID(member_id).hex}@members.aita-nexxus.app"

    def ensure_auth_user(self, member: Dict[str, Any]) -> Tuple[str, str]:
        """Devuelve (user_id, email) del usuario de Auth del socio, creándolo si falta.
        Si el socio tiene email real se usa ese; si no, un alias técnico (nunca recibe correo)."""
        real_email = (member.get("email") or "").strip().lower() or None
        if member.get("user_id"):
            status, user = self.db.auth("GET", f"/admin/users/{member['user_id']}")
            if status == 200 and user:
                email = (user.get("email") or "").lower()
                if real_email and email != real_email and email.endswith("@members.aita-nexxus.app"):
                    # el socio añadió su email real: se actualiza para que pueda entrar por email
                    s2, _ = self.db.auth("PUT", f"/admin/users/{member['user_id']}",
                                         json={"email": real_email, "email_confirm": True})
                    if s2 < 300:
                        email = real_email
                return member["user_id"], email
        email = real_email or self._alias_email(member["id"])
        status, created = self.db.auth("POST", "/admin/users", json={
            "email": email, "email_confirm": True,
            "app_metadata": {"aita_member_id": member["id"], "aita_tenant_id": member["tenant_id"]},
        })
        user_id = (created or {}).get("id") if status < 300 else None
        if not user_id:
            # ya existía un usuario con ese email (p.ej. también es manager): se localiza con generate_link
            s2, data = self.db.auth("POST", "/admin/generate_link", json={"type": "magiclink", "email": email})
            user_id = (data or {}).get("id") or ((data or {}).get("user") or {}).get("id")
            if s2 >= 300 or not user_id:
                logger.error(f"PORTAL no se pudo crear/ubicar el usuario del socio status={status}/{s2}")
                raise PortalError("login_unavailable", 502)
        linked = self.db.select("members", {"user_id": f"eq.{user_id}", "select": "id", "limit": "1"}) or []
        if linked and linked[0]["id"] != member["id"]:
            raise PortalError("account_conflict", 409)
        self.db.update("members", {"id": f"eq.{member['id']}"}, {"user_id": user_id})
        return user_id, email

    # ------------------------------------------------------------------
    # 3) Acciones del staff (owner / manager)
    # ------------------------------------------------------------------
    def staff_member(self, jwt: str, member_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Verifica que el JWT sea de un owner/manager del tenant del socio."""
        user = self.db.user_from_jwt(jwt)
        if not user:
            raise PortalError("unauthorized", 401)
        try:
            uuid.UUID(str(member_id))
        except ValueError:
            raise PortalError("not_found", 404)
        member = self._member(member_id)
        rows = self.db.select("tenant_users", {
            "user_id": f"eq.{user['id']}", "tenant_id": f"eq.{member['tenant_id']}",
            "active": "eq.true", "role": "in.(owner,manager)", "select": "role", "limit": "1"}) or []
        if not rows:
            raise PortalError("forbidden", 403)
        return user, member

    def regenerate(self, member: Dict[str, Any]) -> Dict[str, Any]:
        version = int(member.get("portal_token_version") or 1) + 1
        self.db.update("members", {"id": f"eq.{member['id']}"}, {"portal_token_version": version})
        member = dict(member, portal_token_version=version)
        return member

    def send_access(self, member: Dict[str, Any], base_url: str, sms: bool, email: bool) -> Dict[str, Any]:
        tenant = self._tenant(member["tenant_id"])
        brand = (tenant.get("branding") or {}).get("display_name") or tenant.get("name") or "Tu gimnasio"
        link = self.link_for(member, base_url)
        result: Dict[str, Any] = {"link": link, "sms": None, "email": None}

        if sms:
            phone = e164(member.get("phone"))
            if not phone:
                result["sms"] = {"ok": False, "detail": "no_phone"}
            else:
                body = (f"{brand.title() if brand.isupper() else brand}: hola {member.get('first_name') or ''}, "
                        f"este es tu acceso personal al portal de socio: {link} "
                        f"Es tu llave personal, no la compartas.").replace("  ", " ")
                result["sms"] = self._send_sms(phone, tenant.get("twilio_phone"), body)

        if email:
            addr = (member.get("email") or "").strip().lower()
            if not addr:
                result["email"] = {"ok": False, "detail": "no_email"}
            else:
                self.ensure_auth_user(member)  # el usuario debe existir con ese email
                status, _ = self.db.auth("POST", "/otp", anon=True,
                                         params={"redirect_to": f"{base_url.rstrip('/')}/m"},
                                         json={"email": addr, "create_user": False})
                result["email"] = {"ok": status < 300, "detail": None if status < 300 else f"status_{status}"}

        if (result["sms"] or {}).get("ok") or (result["email"] or {}).get("ok"):
            self.db.update("members", {"id": f"eq.{member['id']}"}, {"portal_invited_at": _now()})
        return result

    def _send_sms(self, to: str, from_: Optional[str], body: str) -> Dict[str, Any]:
        if self._sms_sender:
            return self._sms_sender(to, from_, body)
        sid, token = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
        if not (sid and token and from_):
            return {"ok": False, "detail": "twilio_not_configured"}
        try:
            from twilio.rest import Client
            msg = Client(sid, token).messages.create(to=to, from_=from_, body=body)
            print(f"PORTAL_SMS_OK sid={msg.sid} estado={msg.status}", flush=True)
            return {"ok": True, "detail": msg.status}
        except Exception as e:
            print(f"PORTAL_SMS_ERROR {type(e).__name__}", flush=True)
            return {"ok": False, "detail": "twilio_error"}

    # ------------------------------------------------------------------
    # 4) El propio socio
    # ------------------------------------------------------------------
    def member_from_jwt(self, jwt: str) -> Dict[str, Any]:
        user = self.db.user_from_jwt(jwt)
        if not user:
            raise PortalError("unauthorized", 401)
        rows = self.db.select("members", {"user_id": f"eq.{user['id']}", "select": _MEMBER_COLS, "limit": "1"}) or []
        if not rows:
            raise PortalError("not_a_member", 403)
        return rows[0]
