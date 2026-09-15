import json
import pytest
import billing_service
from fastapi.testclient import TestClient
from main import app
from conftest import crear_negocio_gym
from models import Suscripcion


@pytest.mark.parametrize("key", ["", "synthetic-stripe-key"])
def test_missing_webhook_secret_rejects_even_without_stripe_key(db, monkeypatch, key):
    monkeypatch.setattr(billing_service, "STRIPE_SECRET_KEY", key)
    monkeypatch.setattr(billing_service, "STRIPE_WEBHOOK_SECRET", "")
    n = crear_negocio_gym(db)
    # An unsigned event must not change any local state either.
    payload = json.dumps({"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_test"}}}).encode()
    response = TestClient(app).post("/webhooks/stripe", content=payload)
    assert response.status_code == 400
    db.refresh(n)
    assert n.activo is True


def test_invalid_signature_and_modified_payload_are_rejected(signed_webhook):
    payload = json.dumps({"type": "unknown", "data": {"object": {"id": "test"}}}).encode()
    client = TestClient(app)
    for body, signature in [(payload, "invalid"), (payload + b" ", signed_webhook(payload))]:
        assert client.post("/webhooks/stripe", content=body, headers={"stripe-signature": signature}).status_code == 400


def test_valid_signature_at_http_boundary_updates_local_state(db, signed_webhook):
    n = crear_negocio_gym(db)
    billing_service.crear_suscripcion(n.id, "starter", "test@example.invalid")
    sub = db.query(Suscripcion).filter_by(negocio_id=n.id).one()
    sub.stripe_subscription_id = "sub_signed_test"
    db.commit()
    payload = json.dumps({"type": "customer.subscription.deleted", "data": {"object": {"id": sub.stripe_subscription_id}}}).encode()
    response = TestClient(app).post("/webhooks/stripe", content=payload, headers={"stripe-signature": signed_webhook(payload)})
    assert response.status_code == 200
    db.refresh(n)
    assert n.activo is False
