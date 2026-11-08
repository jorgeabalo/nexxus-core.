"""Carrusel: láminas validadas en script, imágenes del tenant con privacidad apta, reglas médicas y redactor."""
import json
import uuid

import pytest

from services import marketing_carousel as mc
from services.marketing_domain import DomainError
from services.marketing_plan import MarketingPlanService
from tests.test_marketing import NOW, OWNER, OTHER, STAFF, T1, T2, FakeDB, err, jwt

IMG_OK, IMG_PEOPLE, IMG_OTHER, VIDEO = (str(uuid.uuid4()) for _ in range(4))


def media(mid, tenant=T1, **kw):
    return {"id": mid, "tenant_id": tenant, "media_type": "image", "deleted_at": None, "consent_status": "not_required",
            "retention_status": "active", "processing_status": "ready", "validation_status": "passed",
            "contains_people": False, "contains_minors": False, "people_policy": "no_people", **kw}


@pytest.fixture
def db():
    db = FakeDB()
    db.tables["marketing_media"] = [
        media(IMG_OK), media(VIDEO, media_type="video"), media(IMG_OTHER, tenant=T2),
        media(IMG_PEOPLE, contains_people=True, people_policy="consented", consent_status="granted")]
    return db


@pytest.fixture
def svc(db):
    return MarketingPlanService(db, now=NOW)


def slides(n=8, **first):
    out = [{"title": f"Lámina {i + 1}: ¿qué es la presión arterial?", "body": "Texto breve y claro, con eñes y tildes."}
           for i in range(n)]
    out[0].update(first)
    return out


def deck(**kw):
    return {"audience": "Público general", "tone": "Claro", "theme": "light",
            "slides": slides(), "references": [{"label": "AHA 2017 Guideline", "url": "https://www.ahajournals.org/x"}],
            **kw}


def create(svc, who=OWNER, tenant=T1, **kw):
    body = {"title": "Presión arterial", "format": "carousel", "channels": ["instagram"], "language": "es",
            "caption": "Lo básico de la presión arterial.", "hashtags": "#salud #corazon", "carousel": deck()}
    body.update(kw)
    return svc.create_content(jwt(who), tenant, body)


def brand(db, account_type, tenant=T1):
    db.tables["marketing_brand_profiles"] = [{"tenant_id": tenant, "account_type": account_type}]


def test_save_and_reopen_roundtrip(svc):
    row = create(svc)
    doc = mc.parse(row["script"])
    assert doc["kind"] == "carousel" and doc["size"] == "1080x1350" and len(doc["slides"]) == 8
    assert doc["slides"][0]["role"] == "cover" and doc["slides"][-1]["role"] == "close"
    assert "eñes" in doc["slides"][0]["body"] and doc["references"][0]["label"] == "AHA 2017 Guideline"
    again = svc.content(jwt(OWNER), T1, row["id"])["content"]
    assert mc.parse(again["script"]) == doc
    s = deck(slides=slides(10, title="Portada nueva", media_id=IMG_OK, refs=[0], review=True))
    upd = svc.update_content(jwt(OWNER), T1, row["id"], {"carousel": s})
    out = mc.parse(upd["script"])
    assert len(out["slides"]) == 10 and out["slides"][0] == {
        "role": "cover", "title": "Portada nueva", "body": slides()[0]["body"], "media_id": IMG_OK, "refs": [0], "review": True}


@pytest.mark.parametrize("bad, code", [
    ({"slides": slides(1)}, "invalid_carousel_slides"), ({"slides": slides(11)}, "invalid_carousel_slides"),
    ({"slides": [{"title": "", "body": ""}] * 3}, "invalid_carousel_slides"),
    ({"slides": slides(3, title="x" * 91)}, "invalid_carousel_title"),
    ({"slides": slides(3, body="x" * 321)}, "invalid_carousel_body"),
    ({"slides": slides(3, refs=[5])}, "invalid_carousel_reference"),
    ({"references": [{"label": "x", "url": "javascript:alert(1)"}]}, "invalid_carousel_reference"),
    ({"references": [{"label": "", "url": "https://a.org"}]}, "invalid_carousel_reference"),
    ({"slides": slides(3, media_id="../../etc")}, "invalid_carousel_media"),
    ({"slides": slides(3, role="hero")}, "invalid_carousel_slides"),
    ({"theme": "neon"}, "invalid_carousel")])
def test_validation(svc, bad, code):
    assert err(create, svc, carousel=deck(**bad))[0] == code


def test_carousel_only_for_carousel_format(svc):
    assert err(create, svc, format="reel")[0] == "invalid_carousel"


@pytest.mark.parametrize("mid", [IMG_OTHER, VIDEO, str(uuid.uuid4())])
def test_media_must_be_own_ready_image(svc, mid):
    assert err(create, svc, carousel=deck(slides=slides(3, media_id=mid))) == ("carousel_media_not_allowed", 409)


def test_people_images_never_on_medical_channel(svc, db):
    assert create(svc, carousel=deck(slides=slides(3, media_id=IMG_PEOPLE)))["id"]   # negocio local con consentimiento
    brand(db, "medical_education")
    assert err(create, svc, carousel=deck(slides=slides(3, media_id=IMG_PEOPLE)))[0] == "carousel_media_not_allowed"
    assert create(svc, carousel=deck(slides=slides(3, media_id=IMG_OK)))["id"]


def test_revoked_consent_blocks(svc, db):
    db.tables["marketing_media"][3]["consent_status"] = "revoked"
    assert err(create, svc, carousel=deck(slides=slides(3, media_id=IMG_PEOPLE)))[0] == "carousel_media_not_allowed"


@pytest.mark.parametrize("text", ["Cura definitiva para la hipertensión", "Deja tu medicación y mejora",
                                  "Resultados garantizados"])
def test_medical_claims_blocked(svc, db, text):
    brand(db, "medical_education")
    assert err(create, svc, carousel=deck(slides=slides(3, title=text))) == ("carousel_claim_blocked", 422)


def test_medical_claim_in_caption_blocked(svc, db):
    brand(db, "medical_education")
    assert err(create, svc, caption="Esta es la cura milagrosa")[0] == "carousel_claim_blocked"


def test_tenant_isolation_and_roles(svc):
    row = create(svc)
    assert err(svc.content, jwt(OTHER), T2, row["id"])[0] == "not_found"
    assert err(create, svc, who=STAFF)[1] == 403
    assert err(svc.update_content, jwt(OTHER), T1, row["id"], {"carousel": deck()})[1] == 403


def test_locked_after_review(svc):
    row = create(svc)
    svc.transition(jwt(OWNER), T1, row["id"], "review")
    assert err(svc.update_content, jwt(OWNER), T1, row["id"], {"carousel": deck()}) == ("not_editable", 409)


def test_plain_script_still_works(svc):
    row = svc.create_content(jwt(OWNER), T1, {"title": "x", "format": "carousel", "script": "1. Uno\n2. Dos"})
    assert row["script"] == "1. Uno\n2. Dos" and mc.parse(row["script"]) is None


def test_size_limit():
    big = deck(slides=[{"title": "t" * 90, "body": "b" * 320} for _ in range(10)],
               references=[{"label": "l" * 240, "url": "https://a.org/" + "p" * 480} for _ in range(15)])
    with pytest.raises(DomainError) as e:
        mc.dump(mc.validate(big))
    assert e.value.code == "carousel_too_long"


def test_from_text_keeps_media_and_flags_review():
    cur = mc.validate(deck(slides=slides(3, media_id=IMG_OK, refs=[0])))
    text = "1. ¿Qué es? | Una explicación.\n2. Cifras | Más de 120/80.\nsigue aquí\n3. Cierre | Guarda esto."
    out = mc.from_text(text, cur)
    assert [s["title"] for s in out["slides"]] == ["¿Qué es?", "Cifras", "Cierre"]
    assert out["slides"][1]["body"] == "Más de 120/80. sigue aquí"
    assert out["slides"][0]["media_id"] == IMG_OK and out["slides"][0]["refs"] == [0]
    assert all(s["review"] for s in out["slides"]) and out["medical_review"] is False
    assert out["references"] == cur["references"]
    assert mc.from_text("sin láminas", cur) is None


class Msg:
    stop_reason = "end_turn"

    def __init__(self, data):
        self.content = [type("B", (), {"type": "text", "text": json.dumps(data)})()]
        self.usage = type("U", (), {"input_tokens": 1, "output_tokens": 2})()


class FakeClaude:
    def __init__(self, data):
        self.messages, self.data, self.prompts = self, data, []

    def create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        return Msg(self.data)


def test_ai_writer_fills_slides(svc, db):
    db.rpc = lambda name, args, timeout=None: {"status": "reserved", "id": 1}
    db.tables["marketing_text_generations"] = [{"id": 1, "tenant_id": T1, "status": "pending"}]
    svc.text_client = FakeClaude({"caption": "Caption nuevo", "cta": "Guarda este post", "hashtags": ["#salud"],
                                  "script": "1. Portada | Gancho\n2. Dato | Explicación\n3. Cierre | Síguenos",
                                  "per_platform": [{"channel": "instagram", "text": "Caption nuevo"}]})
    row = create(svc, carousel=deck(slides=slides(3, media_id=IMG_OK)))
    out = svc.write_copy(jwt(OWNER), T1, row["id"], {})
    doc = mc.parse(out["content"]["script"])
    assert [s["title"] for s in doc["slides"]] == ["Portada", "Dato", "Cierre"]
    assert doc["slides"][0]["media_id"] == IMG_OK and all(s["review"] for s in doc["slides"])
    assert '"laminas": 3' in svc.text_client.prompts[0] and "Público general" in svc.text_client.prompts[0]
