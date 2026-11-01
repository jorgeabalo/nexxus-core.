"""Escribir con IA (Claude) y versiones por plataforma. Sin red: el cliente de Claude es un doble."""
import json
import threading
import time
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


class TextDB(FakeDB):
    """FakeDB con marketing_reserve_text_generation: cuenta y reserva bajo un candado, como la función SQL
    (que usa pg_advisory_xact_lock por tenant). La función real está en 20261101120000_marketing_text_quota.sql."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self.rpc_fail = False

    def rpc(self, fn, a, timeout=None):
        assert fn == "marketing_reserve_text_generation"
        if self.rpc_fail:
            raise RuntimeError("PGRST connection refused")
        with self._lock:
            if not a["p_limit"] or a["p_limit"] <= 0:
                return {"status": "rejected", "reason": "limit_text_ai"}
            used = [r for r in self.tables.get("marketing_text_generations", []) if r["tenant_id"] == a["p_tenant"]]
            if len(used) >= a["p_limit"]:
                return {"status": "rejected", "reason": "limit_text_ai"}
            row = self.insert("marketing_text_generations", {
                "id": len(self.tables.get("marketing_text_generations", [])) + 1, "tenant_id": a["p_tenant"],
                "content_id": a["p_content"], "actor_id": a["p_actor"], "model": a["p_model"], "status": "pending",
                "input_tokens": 0, "output_tokens": 0})
            return {"status": "reserved", "id": row["id"]}


@pytest.fixture
def db():
    return TextDB()


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


def test_concurrent_requests_cannot_exceed_the_limit(svc, db, ai, monkeypatch):
    """Antes se contaba, se llamaba a Claude y luego se registraba: dos peticiones a la vez pasaban las dos."""
    monkeypatch.setenv("MARKETING_TEXT_DAILY_LIMIT", "1")
    real = ai.create
    ai.create = lambda **kw: (time.sleep(0.05), real(**kw))[1]       # la llamada tarda: la ventana de carrera se abre
    c = draft(svc)
    start, results = threading.Barrier(2), []

    def go():
        start.wait()
        try:
            svc.write_copy(jwt(OWNER), T1, c["id"], {})
            results.append("ok")
        except Exception as e:
            results.append(getattr(e, "code", type(e).__name__))

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok", "text_ai_daily_limit"]
    assert len(ai.calls) == 1                                          # Claude se llamó una sola vez
    assert [r["status"] for r in db.tables["marketing_text_generations"]] == ["succeeded"]


def test_no_reservation_no_claude(svc, db, ai):
    db.rpc_fail = True
    assert err(svc.write_copy, jwt(OWNER), T1, draft(svc)["id"], {}) == ("text_ai_unavailable", 503)
    assert ai.calls == []


def test_unsettled_reservation_keeps_counting(svc, db, monkeypatch):
    monkeypatch.setenv("MARKETING_TEXT_DAILY_LIMIT", "1")
    c = draft(svc)
    real_update = db.update
    db.update = lambda t, f, v: (_ for _ in ()).throw(RuntimeError("timeout")) if t == "marketing_text_generations" \
        else real_update(t, f, v)
    svc.write_copy(jwt(OWNER), T1, c["id"], {})                       # el texto se guarda aunque falle la liquidación
    assert db.tables["marketing_text_generations"][0]["status"] == "pending"
    assert err(svc.write_copy, jwt(OWNER), T1, c["id"], {}) == ("text_ai_daily_limit", 429)


def test_unexpected_error_settles_as_failed(svc, db, ai):
    ai.create = lambda **kw: (_ for _ in ()).throw(KeyError("raro"))
    with pytest.raises(Exception):
        svc.write_copy(jwt(OWNER), T1, draft(svc)["id"], {})
    assert db.tables["marketing_text_generations"][0]["status"] == "failed"


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
