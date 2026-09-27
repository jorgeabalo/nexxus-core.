"""
Puente Claudia -> Supabase -> Manager Panel.

Principio: CLAUDIA TIENE PRIORIDAD.
  * Todo se ejecuta en un único hilo de fondo (cola FIFO), así que ninguna
    escritura retrasa la respuesta TwiML a Twilio.
  * Cualquier error (Supabase caído, timeout, datos raros) se captura y se
    registra en logs; nunca se propaga a la llamada.
  * Las operaciones de una misma llamada se procesan en orden
    (start -> lead -> end) gracias al hilo único.
  * No se inventa nada: solo se guardan datos que Twilio o el cliente
    dieron de verdad. No se guarda el transcript.

Trazabilidad: calls.lead_id <-> leads.call_id, alerts.call_id/lead_id.
Cuando Claudia agende citas: appointments.call_id / lead_id (ver docs).
"""
import logging
import os
import queue
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from services.supabase_admin import SupabaseAdmin

logger = logging.getLogger(__name__)

# Twilio CallStatus -> calls.status
_STATUS_MAP = {
    "completed": "completed",
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "canceled",
}


def _e164(numero: Optional[str]) -> Optional[str]:
    if not numero:
        return None
    digitos = re.sub(r"\D", "", numero)
    if not digitos:
        return None
    if len(digitos) == 10:
        digitos = "1" + digitos
    return f"+{digitos}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CallLogger:
    def __init__(self, client: Optional[SupabaseAdmin] = None):
        self.db = client or SupabaseAdmin()
        self.enabled = self.db.enabled and os.getenv("CALL_LOGGING_ENABLED", "true").lower() not in ("false", "0", "no")
        self._q: "queue.Queue[tuple]" = queue.Queue(maxsize=1000)
        self._tenant_cache: Dict[str, str] = {}
        self._call_cache: Dict[str, str] = {}   # call_sid -> calls.id
        self._worker: Optional[threading.Thread] = None
        if self.enabled:
            self._worker = threading.Thread(target=self._run, name="call-logger", daemon=True)
            self._worker.start()

    # ------------------------------------------------------------------
    # API pública (no bloqueante). Devuelven inmediatamente.
    # ------------------------------------------------------------------
    def call_started(self, call_data: Dict[str, Any], tenant_slug: Optional[str]) -> None:
        self._submit(self._do_call_started, dict(call_data), tenant_slug)

    def lead_captured(self, call_sid: str, lead: Dict[str, Any], called_phone: Optional[str]) -> None:
        self._submit(self._do_lead_captured, call_sid, dict(lead), called_phone)

    def call_ended(self, call_data: Dict[str, Any], summarize: Optional[Callable[[], Optional[Dict[str, str]]]] = None) -> None:
        self._submit(self._do_call_ended, dict(call_data), summarize)

    def flush(self, timeout: float = 5.0) -> None:
        """Solo para tests: espera a que la cola se vacíe."""
        if not self.enabled:
            return
        done = threading.Event()
        self._submit(done.set)
        done.wait(timeout)

    # ------------------------------------------------------------------
    def _submit(self, fn: Callable, *args) -> None:
        if not self.enabled:
            return
        try:
            self._q.put_nowait((fn, args))
        except queue.Full:
            logger.error("CALL_LOGGER cola llena; evento descartado (la llamada no se afecta)")
        except Exception as e:  # pragma: no cover - defensivo
            logger.error(f"CALL_LOGGER no pudo encolar: {e}")

    def _run(self) -> None:
        while True:
            fn, args = self._q.get()
            try:
                fn(*args)
            except Exception as e:
                # Nunca sale de aquí: Claudia no se entera.
                logger.error(f"CALL_LOGGER_ERROR {getattr(fn, '__name__', fn)}: {e}")
                print(f"CALL_LOGGER_ERROR {getattr(fn, '__name__', fn)}: {e}", flush=True)
            finally:
                self._q.task_done()

    # ------------------------------------------------------------------
    def _tenant_id(self, slug: Optional[str] = None, phone: Optional[str] = None) -> Optional[str]:
        """El tenant se resuelve por el número de Twilio llamado (tenants.twilio_phone);
        el slug del mapeo local es respaldo."""
        phone = _e164(phone)
        for key, params in ((f"phone:{phone}", {"twilio_phone": f"eq.{phone}"} if phone else None),
                            (f"slug:{slug}", {"slug": f"eq.{slug}"} if slug else None)):
            if not params:
                continue
            if key in self._tenant_cache:
                return self._tenant_cache[key]
            rows = self.db.select("tenants", {**params, "select": "id", "limit": "1"}) or []
            if rows:
                self._tenant_cache[key] = rows[0]["id"]
                return rows[0]["id"]
        return None

    def _call_id(self, call_sid: str) -> Optional[str]:
        if call_sid in self._call_cache:
            return self._call_cache[call_sid]
        rows = self.db.select("calls", {"call_sid": f"eq.{call_sid}", "select": "id", "limit": "1"}) or []
        if rows:
            self._call_cache[call_sid] = rows[0]["id"]
            return rows[0]["id"]
        return None

    def _do_call_started(self, call_data: Dict[str, Any], tenant_slug: Optional[str]) -> None:
        call_sid = call_data.get("CallSid")
        tenant_id = self._tenant_id(tenant_slug, call_data.get("To"))
        if not call_sid or not tenant_id:
            logger.warning(f"CALL_LOGGER sin call_sid/tenant (sid={call_sid}, tenant={tenant_slug})")
            return
        row = self.db.upsert("calls", {
            "tenant_id": tenant_id,
            "call_sid": call_sid,
            "direction": "inbound",
            "handled_by": "claudia",
            "caller_phone": _e164(call_data.get("From")),
            "called_phone": _e164(call_data.get("To")),
            "status": "in_progress",
        }, on_conflict="call_sid")
        if row:
            self._call_cache[call_sid] = row["id"]
        # Llamadas que se quedaron "en curso" (no llegó el status callback)
        limite = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        self.db.update("calls", {"tenant_id": f"eq.{tenant_id}", "status": "eq.in_progress",
                                 "started_at": f"lt.{limite}"}, {"status": "abandoned"})

    def _do_lead_captured(self, call_sid: str, lead: Dict[str, Any], called_phone: Optional[str]) -> None:
        call_id = self._call_id(call_sid)
        tenant_id = None
        if call_id:
            rows = self.db.select("calls", {"id": f"eq.{call_id}", "select": "tenant_id"}) or []
            tenant_id = rows[0]["tenant_id"] if rows else None
        tenant_id = tenant_id or self._tenant_id(phone=called_phone)
        if not tenant_id:
            logger.warning(f"CALL_LOGGER lead sin tenant (sid={call_sid})")
            return

        nombre = (lead.get("nombre") or "").strip() or None
        telefono = _e164(lead.get("telefono") or lead.get("telefono_llamante"))
        email = (lead.get("email") or "").strip().lower() or None
        motivo = (lead.get("motivo") or "").strip() or None

        lead_row = self.db.insert("leads", {
            "tenant_id": tenant_id,
            "call_id": call_id,
            "name": nombre,
            "phone": telefono,
            "email": email,
            "reason": motivo,
            "source": "claudia",
            "status": "new",
        })
        if not lead_row:
            return
        if call_id:
            self.db.update("calls", {"id": f"eq.{call_id}"},
                           {"lead_id": lead_row["id"], "follow_up_required": True, "caller_name": nombre})
        self.db.insert("alerts", {
            "tenant_id": tenant_id,
            "type": "follow_up",
            "severity": "warning",
            "title": f"Call back: {nombre or telefono or 'new lead'}",
            "detail": " · ".join(x for x in [telefono, email, motivo] if x) or None,
            "call_id": call_id,
            "lead_id": lead_row["id"],
        })
        print(f"CALL_LOGGER lead guardado lead_id={lead_row['id']} call_id={call_id}", flush=True)

    def _do_call_ended(self, call_data: Dict[str, Any], summarize) -> None:
        call_sid = call_data.get("CallSid")
        if not call_sid:
            return
        values: Dict[str, Any] = {
            "status": _STATUS_MAP.get(call_data.get("CallStatus", ""), "completed"),
            "ended_at": _now_iso(),
        }
        try:
            dur = call_data.get("CallDuration")
            if dur not in (None, ""):
                values["duration_seconds"] = max(0, int(float(dur)))
        except (TypeError, ValueError):
            pass

        if summarize:
            try:
                resumen = summarize() or {}
                for campo in ("summary", "intent", "outcome"):
                    if resumen.get(campo):
                        values[campo] = str(resumen[campo])[:2000]
            except Exception as e:
                logger.error(f"CALL_LOGGER resumen falló: {e}")

        call_id = self._call_id(call_sid)
        if call_id:
            self.db.update("calls", {"id": f"eq.{call_id}"}, values)
        else:
            # No se registró el inicio (p.ej. Supabase caído en ese momento)
            tenant_id = self._tenant_id(phone=call_data.get("To"))
            if tenant_id:
                self.db.upsert("calls", {
                    "tenant_id": tenant_id, "call_sid": call_sid,
                    "caller_phone": _e164(call_data.get("From")),
                    "called_phone": _e164(call_data.get("To")),
                    **values,
                }, on_conflict="call_sid")
        self._call_cache.pop(call_sid, None)
