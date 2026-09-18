"""Pruebas de seguridad para los webhooks de voz de Twilio."""

import re

from twilio.request_validator import RequestValidator


def _firma(token, url, datos):
    return RequestValidator(token).compute_signature(url, datos)


def test_twilio_rechaza_firma_invalida(client, db, monkeypatch):
    from conftest import crear_negocio_gym

    crear_negocio_gym(db, slug="gym-voz", nombre="Gym Voz")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token-prueba")

    r = client.post(
        "/webhooks/twilio/voice/gym-voz",
        data={"From": "+17135550100", "CallSid": "CA123"},
        headers={"X-Twilio-Signature": "firma-falsa"},
    )
    assert r.status_code == 403


def test_twilio_inicia_y_continua_llamada_firmada(client, db, monkeypatch):
    from conftest import crear_negocio_gym

    crear_negocio_gym(db, slug="gym-voz", nombre="Gym Voz")
    token = "token-prueba"
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", token)

    ruta_voz = "/webhooks/twilio/voice/gym-voz"
    datos_voz = {"From": "+17135550100", "CallSid": "CA123"}
    firma_voz = _firma(token, f"http://testserver{ruta_voz}", datos_voz)
    inicio = client.post(
        ruta_voz,
        data=datos_voz,
        headers={"X-Twilio-Signature": firma_voz},
    )
    assert inicio.status_code == 200
    assert inicio.headers["content-type"].startswith("application/xml")
    assert "<Gather" in inicio.text
    assert "Gym Voz" in inicio.text

    match = re.search(r"llamada_id=([0-9a-f-]{36})", inicio.text)
    assert match
    llamada_id = match.group(1)

    ruta_speech = f"/webhooks/twilio/speech/gym-voz?llamada_id={llamada_id}"
    datos_speech = {"SpeechResult": "hola", "CallSid": "CA123"}
    firma_speech = _firma(token, f"http://testserver{ruta_speech}", datos_speech)
    respuesta = client.post(
        ruta_speech,
        data=datos_speech,
        headers={"X-Twilio-Signature": firma_speech},
    )
    assert respuesta.status_code == 200
    assert "<Say" in respuesta.text
    assert "<Gather" in respuesta.text
