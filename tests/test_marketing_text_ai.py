"""Escribir con IA (Claude) y versiones por plataforma. Sin red: el cliente de Claude es un doble."""
import json
from types import SimpleNamespace

import pytest

from services import marketing_copywriter as cw
from services import marketing_platforms as platforms
from services.marketing_plan import MarketingPlanService
from tests.test_marketing import NOW, OWNER, STAFF, T1, T2, FakeDB, err, jwt


class FakeClaude:
    def __init__(self, data=None, stop="end_turn", fail=False):
        self.calls, self.stop, self.fail = [], stop, fail
        self.data = data or {
            "caption": "Fuerza y equilibrio para seguir haciendo lo que te gusta.",
            "cta": "Reserva tu clase de prueba",
            "hashtags": ["#fuerza", "longevidad", "#salud fuerte", "#GoldenAge"],
            "script": "1. (3s) Gancho: ¿Te cuesta levantarte del suelo?\n2. (5s) Ejercicio guiado.",
            "per_platform": [{"channel": "instagram", "text": "Versión para Instagram 💪"},
                             {"channel": "x", "text": "Versión corta para X"},
                             {"channel": "tiktok", "text": "no pedida: se ignora"}],
        }
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("network down")
        return SimpleNamespace(stop_reason=self.stop, usage=SimpleNamespace(input_tokens=900, output_tokens=400),
                               content=[SimpleNamespace(type="text", text=json.dumps(self.data))])


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def ai():
    return FakeClaude()


@pytest.fixture
def svc(db, ai):
    s = MarketingPlanService(db, now=NOW)
    s.text_client = ai
    return s


def draft(svc, **kw):
    body = {"title": "Movilidad después de los 60", "format": "reel", "channels": ["instagram", "x"],
            "language": "es", "caption": "Clase de movilidad los martes"}
    body.update(kw)
    return svc.create_content(jwt(OWNER), T1, body)


def test_write_fills_draft_and_platform_versions(svc, db, ai):
    svc.save_brand(jwt(OWNER), T1, {"business_name": "Golden Age", "phone": "+1 305 555 0100",
                                    "banned_topics": ["dietas milagro"]})
    c = draft(svc)
    out = svc.write_copy(jwt(OWNER), T1, c["id"], {"request": "más cercano"})
    item = out["content"]
    assert item["status"] == "draft"                                   # nunca se publica ni se aprueba sola
    assert item["caption"].startswith("Fuerza") and item["cta"] == "Reserva tu clase de prueba"
    assert item["hashtags"] == ["#fuerza", "#longevidad", "#GoldenAge"]          # el hashtag inválido se descarta
    assert item["script"].startswith("1. (3s)")
    v = out["variants"]
    assert set(v) == {"instagram", "x"} and v["instagram"]["text"] == "Versión para Instagram 💪"
    assert v["instagram"]["aspect"] == "9:16" and v["x"]["limit"] == 280
    call = ai.calls[0]
    assert call["model"] == cw.DEFAULT_MODEL and call["output_config"]["format"]["type"] == "json_schema"
    sent = call["messages"][0]["content"]
    assert isinstance(sent, str) and "Golden Age" in sent and "más cercano" in sent and "280" in sent
    log = db.tables["marketing_text_generations"]
    assert [(r["status"], r["input_tokens"], r["output_tokens"]) for r in log] == [("succeeded", 900, 400)]
    assert "caption" not in log[0] and "prompt" not in log[0]          # el registro no guarda textos


def test_image_post_has_no_script(svc):
    c = draft(svc, format="image")
    assert svc.write_copy(jwt(OWNER), T1, c["id"], {})["content"]["script"] is None


def test_forbidden_claims_from_ai_are_rejected(svc, db, ai):
    ai.data = {**ai.data, "caption": "Resultados garantizados en 30 días"}
    c = draft(svc)
    assert err(svc.write_copy, jwt(OWNER), T1, c["id"], {}) == ("ai_output_rejected", 422)
    assert db.tables["marketing_content"][0]["caption"] == "Clase de movilidad los martes"   # nada se pisa
    assert db.tables["marketing_text_generations"][0]["status"] == "rejected"


def test_banned_topic_from_brand_kit_is_rejected(svc, ai):
    svc.save_brand(jwt(OWNER), T1, {"banned_topics": ["dietas milagro"]})
    ai.data = {**ai.data, "cta": "Pregunta por nuestras Dietas Milagro"}
    assert err(svc.write_copy, jwt(OWNER), T1, draft(svc)["id"], {}) == ("ai_output_rejected", 422)


def test_refusal_and_network_errors_are_clean(svc, ai):
    c = draft(svc)
    ai.stop = "refusal"
    assert err(svc.write_copy, jwt(OWNER), T1, c["id"], {}) == ("ai_output_rejected", 422)
    ai.stop, ai.fail = "end_turn", True
    assert err(svc.write_copy, jwt(OWNER), T1, c["id"], {}) == ("text_ai_unavailable", 503)


def test_off_without_api_key(db, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = MarketingPlanService(db, now=NOW)
    c = draft(s)
    assert err(s.write_copy, jwt(OWNER), T1, c["id"], {}) == ("text_ai_unavailable", 503)
    assert s.dashboard(jwt(OWNER), T1)["text_ai"] is False
    assert "marketing_text_generations" not in db.tables                 # ni se llama ni se registra


def test_only_editable_drafts_with_channels(svc):
    c = draft(svc)
    svc.transition(jwt(OWNER), T1, c["id"], "review")
    assert err(svc.write_copy, jwt(OWNER), T1, c["id"], {}) == ("not_editable", 409)
    assert err(svc.write_copy, jwt(OWNER), T1, draft(svc, channels=[])["id"], {}) == ("channels_required", 400)


def test_roles_and_tenants(svc):
    c = draft(svc)
    assert err(svc.write_copy, jwt(STAFF), T1, c["id"], {})[1] == 403
    assert err(svc.write_copy, jwt(OWNER), T2, c["id"], {}) == ("not_found", 404)


def test_daily_limit(svc, monkeypatch):
    monkeypatch.setenv("MARKETING_TEXT_DAILY_LIMIT", "2")
    c = draft(svc)
    svc.write_copy(jwt(OWNER), T1, c["id"], {})
    svc.write_copy(jwt(OWNER), T1, c["id"], {})
    assert err(svc.write_copy, jwt(OWNER), T1, c["id"], {}) == ("text_ai_daily_limit", 429)


def test_manual_edit_rebuilds_versions(svc, db):
    c = draft(svc)
    svc.write_copy(jwt(OWNER), T1, c["id"], {})
    svc.update_content(jwt(OWNER), T1, c["id"], {"title": "Otro título"})
    assert db.tables["marketing_content"][0]["platform_variants"]["instagram"]["text"] == "Versión para Instagram 💪"
    row = svc.update_content(jwt(OWNER), T1, c["id"], {"caption": "Texto nuevo escrito a mano"})
    assert row["platform_variants"]["instagram"]["text"].startswith("Texto nuevo escrito a mano")


def test_compose_respects_each_platform_limit():
    long = ("Entrenamos fuerza y equilibrio con calma. " * 30).strip()
    v = platforms.variants({"format": "video", "channels": list(platforms.RULES), "caption": long,
                            "cta": "Llámanos", "hashtags": ["#a", "#b", "#c", "#d", "#e", "#f"]})
    for ch, x in v.items():
        assert len(x["text"]) <= platforms.RULES[ch]["text_max"] and "Llámanos" in x["text"], ch
    assert "#" not in v["google_business"]["text"] and v["youtube"]["aspect"] == "16:9"
    assert v["tiktok"]["aspect"] == "9:16"
