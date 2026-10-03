"""
Tests de la Fase 1 del Manager Panel.

Objetivo principal: demostrar que el registro en Supabase NUNCA afecta a
Claudia/Twilio (Supabase caído o lento => la llamada sigue igual), que la
trazabilidad call -> lead -> alert se guarda con IDs reales, y que la
service_role key jamás sale hacia el navegador.
"""
import os
import time
import types

import pytest
from fastapi.testclient import TestClient

import main
from recepcionista_service import RecepcionistaIAService
from services.call_logger import CallLogger

GA_PHONE = "+13462457940"


# ---------------------------------------------------------------------------
# Dobles de prueba
# ---------------------------------------------------------------------------
class FailingSupabase:
    """Supabase caído y lento: cada operación tarda y luego falla."""
    enabled = True

    def __init__(self, delay=0.5):
        self.delay = delay
        self.calls = 0

    def _boom(self, *a, **k):
        self.calls += 1
        time.sleep(self.delay)
        raise RuntimeError("Supabase down (simulated)")

    select = insert = upsert = update = _boom


class FakeSupabase:
    """PostgREST en memoria, suficiente para verificar lo que escribe el logger."""
    enabled = True

    def __init__(self):
        self.tables = {"tenants": [{"id": "t-ga", "slug": "golden_age", "twilio_phone": GA_PHONE}],
                       "calls": [], "leads": [], "alerts": []}
        self._n = 0

    def _id(self, prefix):
        self._n += 1
        return f"{prefix}-{self._n}"

    @staticmethod
    def _match(row, filters):
        for k, v in filters.items():
            if k in ("select", "limit", "on_conflict"):
                continue
            op, _, val = v.partition(".")
            if op == "eq" and str(row.get(k)) != val:
                return False
            if op == "lt" and not (str(row.get(k, "")) < val):
                return False
        return True

    def select(self, table, params):
        return [dict(r) for r in self.tables[table] if self._match(r, params)]

    def insert(self, table, row):
        row = {"id": self._id(table), **row}
        self.tables[table].append(row)
        return dict(row)

    def upsert(self, table, row, on_conflict):
        for r in self.tables[table]:
            if r.get(on_conflict) == row.get(on_conflict):
                r.update(row)
                return dict(r)
        return self.insert(table, {"started_at": "2026-09-27T00:00:00+00:00", **row})

    def update(self, table, filters, values):
        out = []
        for r in self.tables[table]:
            if self._match(r, filters):
                r.update(values)
                out.append(dict(r))
        return out


class FakeAnthropic:
    """Cliente LLM falso: devuelve respuestas fijas sin tocar la red."""

    def __init__(self, reply):
        self.reply = reply
        self.models_used = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, model, **kwargs):
        self.models_used.append(model)
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=self.reply)])


def make_recepcionista(reply):
    r = RecepcionistaIAService.__new__(RecepcionistaIAService)
    r.client = FakeAnthropic(reply)
    r.sesiones = {}
    r.on_lead = None
    r.cargar_config()
    r._avisar_por_sms = lambda *a, **k: None       # sin SMS reales en tests
    r.enviar_sms_despedida = lambda *a, **k: False
    return r


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)  # sin validación de firma en tests
    main.twilio_handler = main.TwilioWebhookHandler()
    main._llamadas_cerradas.clear()
    main._sms_despedida_enviados.clear()
    return TestClient(main.app)


def use(monkeypatch, logger, recep=None):
    monkeypatch.setattr(main, "call_logger", logger)
    if recep is not None:
        monkeypatch.setattr(main, "recepcionista", recep)
        recep.on_lead = lambda sid, lead, num: logger.lead_captured(sid, lead, num)


def voice(client, sid="CA_test_1", frm="+12815550101"):
    return client.post("/webhooks/twilio/voice", data={"CallSid": sid, "From": frm, "To": GA_PHONE, "CallStatus": "ringing"})


# ---------------------------------------------------------------------------
# 1. Claudia tiene prioridad: Supabase caído/lento no afecta a la llamada
# ---------------------------------------------------------------------------
def test_voice_webhook_unaffected_when_supabase_down(client, monkeypatch):
    failing = FailingSupabase(delay=1.0)
    use(monkeypatch, CallLogger(client=failing))
    t0 = time.monotonic()
    r = voice(client)
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert "<Gather" in r.text and "Golden Age Gym" in r.text
    assert elapsed < 0.5, f"la respuesta a Twilio no debe esperar a Supabase ({elapsed:.2f}s)"
    main.call_logger.flush(timeout=5)
    assert failing.calls >= 1  # sí intentó registrar, falló, y la llamada siguió


def test_gather_and_lead_unaffected_when_supabase_down(client, monkeypatch):
    recep = make_recepcionista("Perfecto, Ana, el equipo te contactará.\n[CONTACTO: nombre=Ana; telefono=+13465550000; email=; motivo=precios]")
    use(monkeypatch, CallLogger(client=FailingSupabase(delay=1.0)), recep)
    voice(client, sid="CA_test_2")
    t0 = time.monotonic()
    r = client.post("/api/twilio/mensaje", data={"CallSid": "CA_test_2", "SpeechResult": "Quiero información", "From": "+13465550000", "To": GA_PHONE})
    assert r.status_code == 200
    assert "Perfecto, Ana" in r.text and "[CONTACTO" not in r.text
    assert time.monotonic() - t0 < 0.5


def test_status_callback_unaffected_when_supabase_down(client, monkeypatch):
    recep = make_recepcionista("Claro.")
    use(monkeypatch, CallLogger(client=FailingSupabase(delay=1.0)), recep)
    t0 = time.monotonic()
    r = client.post("/webhooks/twilio/status", data={"CallSid": "CA_test_3", "CallStatus": "completed", "CallDuration": "42", "From": "+13465550000", "To": GA_PHONE})
    assert r.status_code == 204
    assert time.monotonic() - t0 < 0.5


def test_logger_disabled_without_supabase_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    lg = CallLogger()
    assert lg.enabled is False
    lg.call_started({"CallSid": "x", "To": GA_PHONE}, "golden_age")  # no-op, no lanza


# ---------------------------------------------------------------------------
# 2. Trazabilidad real call -> lead -> alert (sin datos inventados)
# ---------------------------------------------------------------------------
def test_call_lead_alert_are_linked_by_ids(client, monkeypatch):
    db = FakeSupabase()
    recep = make_recepcionista("Listo, te contactamos.\n[CONTACTO: nombre=Ana López; telefono=+13465550000; email=Ana@Mail.com; motivo=inscripción]")
    use(monkeypatch, CallLogger(client=db), recep)

    voice(client, sid="CA_trace", frm="+13465550000")
    client.post("/api/twilio/mensaje", data={"CallSid": "CA_trace", "SpeechResult": "Me quiero inscribir", "From": "+13465550000", "To": GA_PHONE})
    recep.client.reply = '{"summary": "Quiere inscribirse; dejó sus datos.", "intent": "membership", "outcome": "lead_captured"}'
    client.post("/webhooks/twilio/status", data={"CallSid": "CA_trace", "CallStatus": "completed", "CallDuration": "95", "From": "+13465550000", "To": GA_PHONE})
    main.call_logger.flush(timeout=5)

    [call] = db.tables["calls"]
    [lead] = db.tables["leads"]
    [alert] = db.tables["alerts"]
    assert call["tenant_id"] == "t-ga" and call["call_sid"] == "CA_trace"
    assert call["caller_phone"] == "+13465550000"
    assert lead["call_id"] == call["id"] and call["lead_id"] == lead["id"]
    assert alert["call_id"] == call["id"] and alert["lead_id"] == lead["id"] and alert["type"] == "follow_up"
    assert lead["name"] == "Ana López" and lead["email"] == "ana@mail.com" and lead["source"] == "claudia"
    assert call["follow_up_required"] is True
    assert call["status"] == "completed" and call["duration_seconds"] == 95
    assert call["intent"] == "membership" and call["outcome"] == "lead_captured"
    assert "inscribirse" in call["summary"]
    # No se guarda el transcript
    assert "Me quiero inscribir" not in str(db.tables)
    # Solo existen las filas que produjo la llamada real
    assert len(db.tables["calls"]) == 1 and len(db.tables["leads"]) == 1 and len(db.tables["alerts"]) == 1


def test_call_without_lead_creates_no_lead_or_alert(client, monkeypatch):
    db = FakeSupabase()
    recep = make_recepcionista("Abrimos a las siete.")
    use(monkeypatch, CallLogger(client=db), recep)
    voice(client, sid="CA_nolead")
    client.post("/api/twilio/mensaje", data={"CallSid": "CA_nolead", "SpeechResult": "¿A qué hora abren?", "To": GA_PHONE})
    client.post("/webhooks/twilio/status", data={"CallSid": "CA_nolead", "CallStatus": "completed", "CallDuration": "30", "To": GA_PHONE})
    main.call_logger.flush(timeout=5)
    assert len(db.tables["calls"]) == 1
    assert db.tables["leads"] == [] and db.tables["alerts"] == []
    assert not db.tables["calls"][0].get("follow_up_required")


def test_status_callback_is_idempotent(client, monkeypatch):
    db = FakeSupabase()
    use(monkeypatch, CallLogger(client=db), make_recepcionista("ok"))
    voice(client, sid="CA_dup")
    for _ in range(3):
        client.post("/webhooks/twilio/status", data={"CallSid": "CA_dup", "CallStatus": "completed", "CallDuration": "10", "To": GA_PHONE})
    main.call_logger.flush(timeout=5)
    assert len(db.tables["calls"]) == 1


# ---------------------------------------------------------------------------
# 3. Modelo LLM configurable por entorno
# ---------------------------------------------------------------------------
def test_llm_model_is_configurable(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "modelo-a")
    monkeypatch.delenv("SUMMARY_MODEL", raising=False)
    assert RecepcionistaIAService.modelo() == "modelo-a"
    assert RecepcionistaIAService.modelo("SUMMARY_MODEL") == "modelo-a"
    monkeypatch.setenv("SUMMARY_MODEL", "modelo-b")
    assert RecepcionistaIAService.modelo("SUMMARY_MODEL") == "modelo-b"
    r = make_recepcionista('{"summary":"x","intent":"other","outcome":"unresolved"}')
    r.generar_resumen([{"role": "user", "content": "hola"}])
    r.procesar_mensaje("s1", "hola")
    assert r.client.models_used == ["modelo-b", "modelo-a"]


# ---------------------------------------------------------------------------
# 4. /manager: sin secretos en el navegador y cabeceras de seguridad
# ---------------------------------------------------------------------------
def test_manager_config_never_exposes_service_role(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "TOP-SECRET-SERVICE-ROLE")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "TOP-SECRET-ANTHROPIC")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN_X", "TOP-SECRET-TWILIO")
    r = client.get("/api/manager/config")
    assert r.status_code == 200
    assert r.json() == {"supabaseUrl": "https://example.supabase.co", "supabaseAnonKey": "public-anon-key"}
    assert "TOP-SECRET" not in r.text
    assert r.headers["cache-control"] == "no-store"
    # Ni la página ni los assets contienen secretos
    for path in ["/manager", "/manager/assets/js/app.js", "/manager/assets/js/api.js"]:
        page = client.get(path)
        assert page.status_code == 200, path
        assert "TOP-SECRET" not in page.text


def test_manager_config_503_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    assert client.get("/api/manager/config").status_code == 503


def test_manager_page_security_headers(client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    r = client.get("/manager")
    assert r.status_code == 200
    assert "Manager Dashboard" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "https://example.supabase.co" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]


def test_manager_shell_contains_no_business_data(client):
    html = client.get("/manager").text
    for private in ["Roberto", "Edgar", "832-388", "281-352", "Gessner"]:
        assert private not in html


# ---------------------------------------------------------------------------
# 5. Endpoints existentes siguen respondiendo
# ---------------------------------------------------------------------------
def test_existing_endpoints_still_respond(client, monkeypatch):
    recep = make_recepcionista("Hola, ¿en qué te ayudo?")
    use(monkeypatch, CallLogger(client=FakeSupabase()), recep)
    assert client.get("/").json() == {"status": "ok"}
    assert client.post("/api/iniciar", json={"sesion_id": "web1"}).status_code == 200
    r = client.post("/api/mensaje", json={"sesion_id": "web1", "mensaje": "hola"})
    assert r.status_code == 200 and r.json()["respuesta"] == "Hola, ¿en qué te ayudo?"
    assert client.post("/api/finalizar", json={"sesion_id": "web1"}).status_code == 200


# ---------------------------------------------------------------------------
# 6. Clave pegada con salto de línea: se limpia y nunca aparece en errores
# ---------------------------------------------------------------------------
def test_supabase_key_whitespace_is_stripped_and_never_logged(monkeypatch):
    from services.supabase_admin import SupabaseAdmin
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co\n")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "sb_secret_ABCDEFGH\nIJKLMNOP_123")
    a = SupabaseAdmin()
    assert a.url == "https://example.supabase.co"
    assert a._key == "sb_secret_ABCDEFGHIJKLMNOP_123"
    msg = a._scrub("Illegal header value b'sb_secret_ABCDEFGH\\nIJKLMNOP_123' sb_secret_ABCDEFGHIJKLMNOP_123")
    assert "ABCDEFGH" not in msg and "IJKLMNOP" not in msg

    class Boom:
        def request(self, *a, **k):
            raise ValueError("Illegal header value b'sb_secret_ABCDEFGHIJKLMNOP_123'")
    a._client = Boom()
    with pytest.raises(RuntimeError) as exc:
        a.select("tenants", {"select": "id"})
    assert "ABCDEFGH" not in str(exc.value)
