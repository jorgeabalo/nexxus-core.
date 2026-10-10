"""
Seguridad de los webhooks de Twilio: fallan CERRADOS.

Se rechaza (403) cualquier petición a /webhooks/twilio/voice, /webhooks/twilio/status
o /api/twilio/mensaje si TWILIO_AUTH_TOKEN falta, está vacío (o solo espacios) o la
firma X-Twilio-Signature es inválida o no viene. Solo una firma válida pasa.
Una petición rechazada no llega a Claude ni registra nada.
Además: GET /health para el healthcheck de Railway, sin secretos.
"""
import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

import main
from services.call_logger import CallLogger
from services.twilio_service import TwilioSignatureValidator
from test_manager_and_claudia import GA_PHONE, FakeSupabase, make_recepcionista

TOKEN = "unit-test-twilio-token"
BASE = "https://testserver"
VOICE = {"CallSid": "CA_sec_1", "From": "+12815550101", "To": GA_PHONE, "CallStatus": "ringing"}
GATHER = {"CallSid": "CA_sec_1", "SpeechResult": "¿A qué hora abren?", "From": "+12815550101", "To": GA_PHONE}
STATUS = {"CallSid": "CA_sec_1", "CallStatus": "completed", "CallDuration": "12", "From": "+12815550101", "To": GA_PHONE}
ENDPOINTS = [("/webhooks/twilio/voice", VOICE, 200), ("/api/twilio/mensaje", GATHER, 200),
             ("/webhooks/twilio/status", STATUS, 204)]


def sign(path, data, token=TOKEN):
    return RequestValidator(token).compute_signature(f"{BASE}{path}", data)


# ---------------------------------------------------------------- validador
@pytest.mark.parametrize("token", [None, "", "   ", "\n"])
def test_validator_rejects_without_token(monkeypatch, token):
    if token is None:
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
    v = TwilioSignatureValidator()
    # ni siquiera una firma "correcta" calculada con un token vacío es aceptada
    assert v.validate(f"{BASE}/webhooks/twilio/voice", VOICE, sign("/webhooks/twilio/voice", VOICE, token or "x")) is False
    assert v.validate(f"{BASE}/webhooks/twilio/voice", VOICE, "") is False


def test_validator_rejects_invalid_or_missing_signature(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    v = TwilioSignatureValidator()
    url = f"{BASE}/webhooks/twilio/voice"
    assert v.validate(url, VOICE, "") is False
    assert v.validate(url, VOICE, "firma-falsa") is False
    assert v.validate(url, VOICE, sign("/webhooks/twilio/voice", VOICE, "otro-token")) is False
    assert v.validate(url, {**VOICE, "From": "+19999999999"}, sign("/webhooks/twilio/voice", VOICE)) is False  # datos alterados


def test_validator_accepts_valid_signature_and_strips_token(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", f"  {TOKEN}\n")      # espacios al pegar la variable
    v = TwilioSignatureValidator()
    assert v.validate(f"{BASE}/webhooks/twilio/voice", VOICE, sign("/webhooks/twilio/voice", VOICE)) is True


# ---------------------------------------------------------------- endpoints
@pytest.fixture
def app_with(monkeypatch):
    """Devuelve (client, recepcionista, db) con el token indicado (None = sin variable)."""
    def build(token, public_base=None):
        if public_base is None:
            monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
        else:
            monkeypatch.setenv("PUBLIC_BASE_URL", public_base)
        if token is None:
            monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        else:
            monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)
        monkeypatch.setattr(main, "twilio_handler", main.TwilioWebhookHandler())
        db = FakeSupabase()
        monkeypatch.setattr(main, "call_logger", CallLogger(client=db))
        recep = make_recepcionista("Abrimos a las siete.")
        monkeypatch.setattr(main, "recepcionista", recep)
        main._llamadas_cerradas.clear()
        main._sms_despedida_enviados.clear()
        return TestClient(main.app), recep, db
    return build


def _assert_untouched(recep, db):
    main.call_logger.flush(timeout=5)
    assert recep.client.models_used == [], "una petición rechazada no debe llamar a Claude"
    assert db.tables.get("calls", []) == [] and db.tables.get("leads", []) == []


@pytest.mark.parametrize("path,data,_", ENDPOINTS)
@pytest.mark.parametrize("token", [None, "", "   "])
def test_endpoints_reject_without_token(app_with, path, data, _, token):
    client, recep, db = app_with(token)
    r = client.post(path, data=data, headers={"X-Twilio-Signature": sign(path, data, "cualquiera")})
    assert r.status_code == 403
    _assert_untouched(recep, db)


@pytest.mark.parametrize("path,data,_", ENDPOINTS)
@pytest.mark.parametrize("signature", [None, "", "firma-falsa", "otro-token"])
def test_endpoints_reject_invalid_signature(app_with, path, data, _, signature):
    client, recep, db = app_with(TOKEN)
    if signature == "otro-token":
        signature = sign(path, data, "otro-token")
    headers = {} if signature is None else {"X-Twilio-Signature": signature}
    r = client.post(path, data=data, headers=headers)
    assert r.status_code == 403
    _assert_untouched(recep, db)


def test_endpoints_reject_tampered_body(app_with):
    client, recep, db = app_with(TOKEN)
    path = "/api/twilio/mensaje"
    r = client.post(path, data={**GATHER, "SpeechResult": "otra cosa"}, headers={"X-Twilio-Signature": sign(path, GATHER)})
    assert r.status_code == 403
    _assert_untouched(recep, db)


@pytest.mark.parametrize("path,data,expected", ENDPOINTS)
def test_endpoints_accept_valid_signature(app_with, path, data, expected):
    client, recep, _ = app_with(TOKEN)
    r = client.post(path, data=data, headers={"X-Twilio-Signature": sign(path, data)})
    assert r.status_code == expected
    if path == "/api/twilio/mensaje":
        assert "Abrimos a las siete" in r.text and recep.client.models_used    # sí llegó a Claude
    if path == "/webhooks/twilio/voice":
        assert "<Gather" in r.text


def test_signature_covers_query_string(app_with):
    client, _, _ = app_with(TOKEN)
    path = "/webhooks/twilio/voice?tenant=golden_age"
    good = client.post(path, data=VOICE, headers={"X-Twilio-Signature": sign(path, VOICE)})
    assert good.status_code == 200
    bad = client.post(path, data=VOICE, headers={"X-Twilio-Signature": sign("/webhooks/twilio/voice", VOICE)})
    assert bad.status_code == 403


@pytest.mark.parametrize("path,data,_", [e for e in ENDPOINTS if e[0] != "/webhooks/twilio/voice"])
def test_rejects_when_handler_missing(app_with, monkeypatch, path, data, _):
    client, recep, db = app_with(TOKEN)
    monkeypatch.setattr(main, "twilio_handler", None)
    r = client.post(path, data=data, headers={"X-Twilio-Signature": sign(path, data)})
    assert r.status_code == 403
    _assert_untouched(recep, db)


# ---------------------------------------------------------------- URL pública (PUBLIC_BASE_URL)
PUBLIC = "https://nexxus-core-production.up.railway.app"


@pytest.mark.parametrize("path,data,expected", ENDPOINTS)
def test_signature_uses_public_base_url(app_with, path, data, expected):
    client, _, _ = app_with(TOKEN, public_base=PUBLIC + "/")          # la barra final se ignora
    good = RequestValidator(TOKEN).compute_signature(f"{PUBLIC}{path}", data)
    assert client.post(path, data=data, headers={"X-Twilio-Signature": good}).status_code == expected


def test_forwarded_headers_cannot_change_validated_url(app_with):
    client, recep, db = app_with(TOKEN, public_base=PUBLIC)
    path = "/api/twilio/mensaje"
    evil = "https://evil.example"
    forged = {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https",
              "X-Twilio-Signature": RequestValidator(TOKEN).compute_signature(f"{evil}{path}", GATHER)}
    assert client.post(path, data=GATHER, headers=forged).status_code == 403
    # firma sobre la URL interna/http tampoco vale
    internal = {"X-Twilio-Signature": RequestValidator(TOKEN).compute_signature(f"http://testserver{path}", GATHER)}
    assert client.post(path, data=GATHER, headers=internal).status_code == 403
    _assert_untouched(recep, db)
    # la firma correcta sigue valiendo aunque el proxy mande cabeceras raras
    ok = {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http",
          "X-Twilio-Signature": RequestValidator(TOKEN).compute_signature(f"{PUBLIC}{path}", GATHER)}
    assert client.post(path, data=GATHER, headers=ok).status_code == 200


@pytest.mark.parametrize("bad", ["http://nexxus-core-production.up.railway.app", "https://x.example/sub", "nexxus.example", "https://"])
def test_invalid_public_base_url_fails_closed(app_with, bad):
    client, recep, db = app_with(TOKEN, public_base=bad)
    path = "/webhooks/twilio/voice"
    sig = RequestValidator(TOKEN).compute_signature(f"{bad.rstrip('/')}{path}", VOICE)
    assert client.post(path, data=VOICE, headers={"X-Twilio-Signature": sig}).status_code == 403
    _assert_untouched(recep, db)


def test_query_string_with_public_base_url(app_with):
    client, _, _ = app_with(TOKEN, public_base=PUBLIC)
    path = "/webhooks/twilio/voice?tenant=golden_age"
    sig = RequestValidator(TOKEN).compute_signature(f"{PUBLIC}{path}", VOICE)
    assert client.post(path, data=VOICE, headers={"X-Twilio-Signature": sig}).status_code == 200


# ---------------------------------------------------------------- healthcheck
def test_health_is_minimal_and_has_no_secrets(monkeypatch):
    for k, v in {"SUPABASE_SERVICE_ROLE_KEY": "srv-secret", "ANTHROPIC_API_KEY": "sk-secret",
                 "TWILIO_AUTH_TOKEN": "tw-secret", "STRIPE_SECRET_KEY": "stripe-secret"}.items():
        monkeypatch.setenv(k, v)
    r = TestClient(main.app).get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert not any(s in r.text for s in ("secret", "sk-", "supabase", "twilio"))


def test_health_does_not_depend_on_external_services(monkeypatch):
    monkeypatch.setattr(main, "member_portal", None)
    monkeypatch.setattr(main, "recepcionista", None)
    monkeypatch.setattr(main, "twilio_handler", None)
    assert TestClient(main.app).get("/health").json() == {"status": "ok"}
