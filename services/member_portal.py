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
                "portal_token_version,portal_invited_at,portal_activated_at,portal_last_used_at,"
                "next_evaluation_due,joined_as,gender")


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


def _safe_err(text: Optional[str]) -> Optional[str]:
    """Mensaje de error sin enlaces ni tokens (pueden contener el acceso del socio)."""
    if not text:
        return None
    text = re.sub(r"https?://\S+", "[url]", str(text))
    text = re.sub(r"[A-Za-z0-9_\-]{24,}", "[redacted]", text)
    return text[:200]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def e164(numero: Optional[str]) -> Optional[str]:
    """Normaliza a E.164. Números de 10 dígitos se asumen de EE. UU. (NANP).
    Devuelve None si no es un número válido (mejor no enviar que enviar mal)."""
    raw = (numero or "").strip()
    d = re.sub(r"\D", "", raw)
    if not raw.startswith("+") and len(d) == 10:
        d = "1" + d
    if d.startswith("1"):
        # NANP: 1 + área (2-9) + central (2-9) + 4 dígitos
        return f"+{d}" if re.fullmatch(r"1[2-9]\d{2}[2-9]\d{6}", d) else None
    return f"+{d}" if raw.startswith("+") and 8 <= len(d) <= 15 else None


def mask_phone(e: str) -> str:
    return (e[:2] + "•" * max(0, len(e) - 6) + e[-4:]) if e else ""


# Estados de Twilio -> estados que ve el manager
_SMS_STATUS = {
    "accepted": "queued", "scheduled": "queued", "queued": "queued",
    "sending": "sent", "sent": "sent",
    "delivered": "delivered", "read": "delivered",
    "failed": "failed", "undelivered": "failed", "canceled": "failed",
}


def sms_state(twilio_status: Optional[str]) -> str:
    return _SMS_STATUS.get((twilio_status or "").lower(), "queued")


def public_base_url(fallback: Optional[str] = None) -> str:
    """URL pública para enlaces y callbacks. Debe ser https y no localhost."""
    url = (os.getenv("PUBLIC_BASE_URL") or fallback or "").strip().rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?", url) or re.search(r"localhost|127\.0\.0\.1|\.local$", url):
        raise PortalError("invalid_public_url", 500)
    return url


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

    def _brand(self, tenant: Dict[str, Any]) -> str:
        brand = (tenant.get("branding") or {}).get("display_name") or tenant.get("name") or "Tu gimnasio"
        return brand.title() if brand.isupper() else brand

    def send_access(self, member: Dict[str, Any], base_url: str, sms: bool, email: bool,
                    purpose: str = "portal_access", next_view: Optional[str] = None) -> Dict[str, Any]:
        """Envía el enlace personal (SMS) y/o un enlace mágico (email).
        purpose: portal_access | evaluation_invite. El enlace NUNCA se registra en logs ni en la base."""
        base = public_base_url(base_url)
        tenant = self._tenant(member["tenant_id"])
        brand = self._brand(tenant)
        link = self.link_for(member, base) + (f"?next={next_view}" if next_view else "")
        result: Dict[str, Any] = {"link": link, "sms": None, "email": None}
        name = (member.get("first_name") or "").strip()

        if sms:
            phone = e164(member.get("phone"))
            if not phone:
                result["sms"] = {"ok": False, "status": "failed", "detail": "invalid_phone" if member.get("phone") else "no_phone"}
            else:
                if purpose == "evaluation_invite":
                    body = (f"{brand}: hola {name}, es momento de tu evaluación de progreso. "
                            f"Complétala aquí: {link} Enlace personal, no lo compartas.")
                else:
                    body = (f"{brand}: hola {name}, este es tu acceso personal al portal de socio: {link} "
                            f"Es tu llave personal, no la compartas.")
                result["sms"] = self._send_sms(phone, tenant.get("twilio_phone"), " ".join(body.split()),
                                               tenant_id=member["tenant_id"], member_id=member["id"],
                                               purpose=purpose, base_url=base)

        if email:
            addr = (member.get("email") or "").strip().lower()
            if not addr:
                result["email"] = {"ok": False, "status": "failed", "detail": "no_email"}
            else:
                self.ensure_auth_user(member)  # el usuario debe existir con ese email
                redirect = f"{base}/m/" + (f"?next={next_view}" if next_view else "")
                status, _ = self.db.auth("POST", "/otp", anon=True, params={"redirect_to": redirect},
                                         json={"email": addr, "create_user": False})
                ok = status < 300
                result["email"] = {"ok": ok, "status": "sent" if ok else "failed",
                                   "detail": None if ok else ("rate_limited" if status == 429 else f"status_{status}")}

        if purpose == "portal_access" and ((result["sms"] or {}).get("ok") or (result["email"] or {}).get("ok")):
            self.db.update("members", {"id": f"eq.{member['id']}"}, {"portal_invited_at": _now()})
        return result

    def send_evaluation_invite(self, member: Dict[str, Any], base_url: str, sms: bool, email: bool,
                               created_by: Optional[str] = None, auto: bool = False) -> Dict[str, Any]:
        kind = "reevaluation" if self._has_initial(member["id"]) else "initial"
        result = self.send_access(member, base_url, sms, email, purpose="evaluation_invite", next_view="evaluation")
        channel = "+".join([c for c, on in (("sms", sms), ("email", email)) if on]) or "sms"
        try:
            self.db.insert("evaluation_invitations", {
                "tenant_id": member["tenant_id"], "member_id": member["id"], "kind": kind,
                "due_date": member.get("next_evaluation_due") or datetime.now(timezone.utc).date().isoformat(),
                "channel": channel, "auto": auto, "created_by": created_by,
                "sms_status": (result.get("sms") or {}).get("status"),
                "email_status": (result.get("email") or {}).get("status"),
            })
        except Exception as e:  # el registro no debe impedir el envío
            logger.error(f"EVAL_INVITE_LOG_ERROR {type(e).__name__}")
        result["kind"] = kind
        return result

    def _has_initial(self, member_id: str) -> bool:
        rows = self.db.select("member_evaluations", {"member_id": f"eq.{member_id}", "kind": "eq.initial",
                                                     "status": "eq.submitted", "select": "id", "limit": "1"}) or []
        return bool(rows)

    # ---------------- SMS con seguimiento de entrega ----------------
    def _twilio(self):
        sid, token = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
        if not (sid and token):
            return None
        from twilio.rest import Client
        return Client(sid, token)

    def _send_sms(self, to: str, from_: Optional[str], body: str, *, tenant_id: str, member_id: Optional[str],
                  purpose: str, base_url: str) -> Dict[str, Any]:
        """Devuelve {ok, status, sid, error_code, detail}. ok=True solo significa que Twilio ACEPTÓ el
        mensaje (queued/accepted); la entrega real llega después por el callback."""
        if self._sms_sender:
            res = self._sms_sender(to, from_, body)
        else:
            client = self._twilio()
            if not (client and from_):
                res = {"ok": False, "status": "failed", "detail": "twilio_not_configured"}
            else:
                try:
                    msg = client.messages.create(to=to, from_=from_, body=body,
                                                 status_callback=f"{base_url}/webhooks/twilio/sms-status")
                    res = {"ok": True, "sid": msg.sid, "twilio_status": msg.status,
                           "error_code": msg.error_code, "detail": msg.error_message}
                except Exception as e:
                    code = getattr(e, "code", None)
                    res = {"ok": False, "twilio_status": "failed", "error_code": code,
                           "detail": "twilio_error", "error_message": _safe_err(getattr(e, "msg", "") or str(e))}
        tw = res.get("twilio_status") or ("queued" if res.get("ok") else "failed")
        res["status"] = sms_state(tw) if res.get("ok") else "failed"
        print(f"PORTAL_SMS purpose={purpose} to={mask_phone(to)} sid={res.get('sid')} "
              f"status={tw} error={res.get('error_code')}", flush=True)
        try:
            self.db.insert("sms_messages", {
                "tenant_id": tenant_id, "member_id": member_id, "purpose": purpose, "to_masked": mask_phone(to),
                "message_sid": res.get("sid"), "status": res["status"], "twilio_status": tw,
                "error_code": res.get("error_code"), "error_message": res.get("error_message")})
        except Exception as e:
            logger.error(f"SMS_LOG_ERROR {type(e).__name__}")
        return {k: res.get(k) for k in ("ok", "status", "sid", "error_code", "detail")}

    def update_sms_status(self, sid: str, twilio_status: str, error_code: Optional[int] = None,
                          error_message: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if not sid:
            return None
        state = sms_state(twilio_status)
        values: Dict[str, Any] = {"status": state, "twilio_status": twilio_status, "updated_at": _now()}
        if error_code:
            values["error_code"] = int(error_code)
            values["error_message"] = _safe_err(error_message)
        if state == "delivered":
            values["delivered_at"] = _now()
        rows = self.db.update("sms_messages", {"message_sid": f"eq.{sid}"}, values) or []
        return rows[0] if rows else None

    def refresh_sms(self, sid: str) -> Optional[Dict[str, Any]]:
        """Consulta a Twilio el estado real de un mensaje (por si no llegó el callback)."""
        client = self._twilio()
        if not client:
            raise PortalError("twilio_not_configured", 503)
        msg = client.messages(sid).fetch()
        return self.update_sms_status(sid, msg.status, msg.error_code, msg.error_message)

    def last_sms(self, member_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.select("sms_messages", {"member_id": f"eq.{member_id}", "order": "created_at.desc", "limit": "1",
                                               "select": "message_sid,purpose,to_masked,status,twilio_status,error_code,"
                                                         "error_message,created_at,updated_at,delivered_at"}) or []
        if not rows:
            return None
        r = dict(rows[0])
        sid = r.pop("message_sid", None) or ""
        r["sid_tail"] = sid[-6:] if sid else None
        r["has_sid"] = bool(sid)
        return r

    def sms_diagnostics(self, tenant_id: str) -> Dict[str, Any]:
        """Datos reales de Twilio para diagnosticar (sin credenciales ni cuerpos de mensaje)."""
        tenant = self._tenant(tenant_id)
        sender = tenant.get("twilio_phone")
        out: Dict[str, Any] = {"sender": sender, "credentials": bool(os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN")),
                               "public_base_url": None, "account": None, "number": None, "recent": [], "errors": []}
        try:
            out["public_base_url"] = public_base_url()
        except PortalError:
            out["errors"].append("public_base_url_missing_or_invalid")
        client = self._twilio()
        if not client:
            out["errors"].append("twilio_not_configured")
            return out
        try:
            acc = client.api.v2010.accounts(os.getenv("TWILIO_ACCOUNT_SID")).fetch()
            out["account"] = {"type": acc.type, "status": acc.status}
        except Exception as e:
            out["errors"].append(f"account:{type(e).__name__}")
        try:
            nums = client.incoming_phone_numbers.list(phone_number=sender, limit=1) if sender else []
            if nums:
                caps = nums[0].capabilities or {}
                out["number"] = {"sms": bool(caps.get("sms")), "voice": bool(caps.get("voice")),
                                 "messaging_service": bool(getattr(nums[0], "sms_application_sid", None))}
            else:
                out["errors"].append("sender_not_in_account")
        except Exception as e:
            out["errors"].append(f"number:{type(e).__name__}")
        try:
            for m in client.messages.list(from_=sender, limit=10) if sender else []:
                out["recent"].append({"date": m.date_created.isoformat() if m.date_created else None,
                                      "to": mask_phone(m.to or ""), "status": m.status,
                                      "state": sms_state(m.status), "error_code": m.error_code})
        except Exception as e:
            out["errors"].append(f"messages:{type(e).__name__}")
        return out

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


    # ------------------------------------------------------------------
    # 5) Evaluaciones: lectura autorizada (para el PDF)
    # ------------------------------------------------------------------
    def _evaluation_bundle(self, evaluation_id: str) -> Dict[str, Any]:
        try:
            uuid.UUID(str(evaluation_id))
        except ValueError:
            raise PortalError("not_found", 404)
        rows = self.db.select("member_evaluations", {"id": f"eq.{evaluation_id}", "status": "eq.submitted",
                                                     "select": "*", "limit": "1"}) or []
        if not rows:
            raise PortalError("not_found", 404)
        ev = rows[0]
        member = self._member(ev["member_id"])
        q = None
        if ev.get("questionnaire_id"):
            qs = self.db.select("questionnaires", {"id": f"eq.{ev['questionnaire_id']}", "select": "version,title,definition",
                                                   "limit": "1"}) or []
            q = qs[0] if qs else None
        meas = None
        if ev.get("measurement_id"):
            ms = self.db.select("measurements", {"id": f"eq.{ev['measurement_id']}", "select": "*", "limit": "1"}) or []
            meas = ms[0] if ms else None
        progress = self.db.select("progress_entries", {"evaluation_id": f"eq.{evaluation_id}", "select": "*",
                                                       "order": "created_at.asc"}) or []
        baseline_meas = self.db.select("measurements", {"member_id": f"eq.{member['id']}", "is_baseline": "eq.true",
                                                        "select": "*", "limit": "1"}) or []
        return {"evaluation": ev, "member": member, "tenant": self._tenant(member["tenant_id"]),
                "questionnaire": q, "measurement": meas, "progress": progress,
                "baseline_measurement": baseline_meas[0] if baseline_meas else None}

    def evaluation_for_member(self, jwt: str, evaluation_id: str) -> Dict[str, Any]:
        me = self.member_from_jwt(jwt)
        bundle = self._evaluation_bundle(evaluation_id)
        if bundle["evaluation"]["member_id"] != me["id"]:
            raise PortalError("not_found", 404)   # no revelar que existe
        return bundle

    def evaluation_for_staff(self, jwt: str, evaluation_id: str) -> Dict[str, Any]:
        bundle = self._evaluation_bundle(evaluation_id)
        self.staff_member(jwt, bundle["evaluation"]["member_id"])   # owner/manager del mismo gym
        return bundle

    # ------------------------------------------------------------------
    # 6) Invitaciones automáticas cada 90 días
    # ------------------------------------------------------------------
    def run_due_invitations(self, today: Optional[str] = None) -> Dict[str, int]:
        """Envía (una sola vez por fecha) la invitación a los socios con evaluación vencida.
        Requiere PUBLIC_BASE_URL. Idempotente: la fila de invitación se reserva antes de enviar."""
        base = public_base_url()
        today = today or datetime.now(timezone.utc).date().isoformat()
        due = self.db.select("members", {"next_evaluation_due": f"lte.{today}", "select": _MEMBER_COLS,
                                         "limit": "500"}) or []
        sent = skipped = 0
        for m in due:
            if (m.get("membership_status") or "active").lower() != "active":
                skipped += 1
                continue
            kind = "reevaluation" if self._has_initial(m["id"]) else "initial"
            try:
                claim = self.db.insert("evaluation_invitations", {
                    "tenant_id": m["tenant_id"], "member_id": m["id"], "kind": kind,
                    "due_date": m["next_evaluation_due"], "channel": "sms+email", "auto": True})
            except Exception:
                skipped += 1           # ya se invitó para esta fecha (índice único)
                continue
            res = self.send_access(m, base, bool(m.get("phone")), bool(m.get("email")),
                                   purpose="evaluation_invite", next_view="evaluation")
            try:
                self.db.update("evaluation_invitations", {"id": f"eq.{claim['id']}"}, {
                    "sms_status": (res.get("sms") or {}).get("status"),
                    "email_status": (res.get("email") or {}).get("status")})
            except Exception:
                pass
            sent += 1
        return {"sent": sent, "skipped": skipped}
