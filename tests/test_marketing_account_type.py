"""Tipo de cuenta del Brand Kit: negocio local, canal de contenido y canal médico educativo (p. ej. Dr. Redden).
Cambia las reglas del redactor con IA y, en el canal médico, exige confirmar que cada archivo está anonimizado."""
import pytest

from services import marketing_copywriter as cw
from services.marketing import MarketingService
from services.marketing_library import LibraryService
from services.marketing_plan import MarketingPlanService
from services.marketing_privacy import MockFaceDetector
from tests.test_marketing import NOW, OWNER, T1, T2, OTHER, err as brand_err, jwt
from tests.test_marketing_text_ai import FakeClaude, TextDB
import test_marketing_studio as st                       # la Biblioteca usa sus propios ids de prueba


def test_default_is_local_business_and_is_validated():
    db = TextDB()
    svc = MarketingService(db, now=NOW)
    assert svc.brand(jwt(OWNER), T1)["profile"]["account_type"] == "local_business"
    assert svc.save_brand(jwt(OWNER), T1, {"business_name": "X"})["profile"]["account_type"] == "local_business"
    out = svc.save_brand(jwt(OTHER), T2, {"business_name": "Dr. Redden", "account_type": "medical_education"})
    assert out["profile"]["account_type"] == "medical_education"
    assert svc.brand(jwt(OWNER), T1)["profile"]["account_type"] == "local_business"      # cada tenant el suyo
    assert brand_err(svc.save_brand, jwt(OWNER), T1, {"account_type": "hospital"}) == ("invalid_account_type", 400)


def test_system_prompt_follows_account_type():
    assert cw.system({}) == cw.SYSTEM
    assert "seguir la cuenta" in cw.system({"account_type": "content_channel"})
    medical = cw.system({"account_type": "medical_education"})
    assert "nunca des consejo médico personal" in medical and "no sustituye la consulta médica" in medical
    assert "seguir la cuenta" in medical
    assert "consejo médico" not in cw.system({"account_type": "local_business"})


def _medical_svc(ai):
    s = MarketingPlanService(TextDB(), now=NOW)
    s.text_client = ai
    s.save_brand(jwt(OWNER), T1, {"business_name": "Dr. Redden", "account_type": "medical_education"})
    c = s.create_content(jwt(OWNER), T1, {"title": "Historia de la anestesia", "format": "reel",
                                          "channels": ["instagram", "tiktok"], "language": "es"})
    return s, c


def test_medical_channel_sends_medical_rules():
    ai = FakeClaude()
    s, c = _medical_svc(ai)
    s.write_copy(jwt(OWNER), T1, c["id"], {})
    assert "canal médico de divulgación" in ai.calls[0]["system"]


@pytest.mark.parametrize("bad", ["Deja tu tratamiento y prueba esto", "La cura definitiva para la migraña",
                                 "Stop taking your medication today", "Sin necesidad de ir al médico"])
def test_medical_channel_rejects_unsafe_advice(bad):
    ai = FakeClaude()
    ai.data = {**ai.data, "caption": bad}
    s, c = _medical_svc(ai)
    assert brand_err(s.write_copy, jwt(OWNER), T1, c["id"], {}) == ("ai_output_rejected", 422)


def test_local_business_is_not_affected_by_medical_filter():
    ai = FakeClaude()
    ai.data = {**ai.data, "caption": "Sin necesidad de ir al médico para empezar a moverte"}
    s = MarketingPlanService(TextDB(), now=NOW)
    s.text_client = ai
    c = s.create_content(jwt(OWNER), T1, {"title": "x", "format": "image", "channels": ["instagram"], "language": "es"})
    assert s.write_copy(jwt(OWNER), T1, c["id"], {})["content"]["caption"].startswith("Sin necesidad")


def _medical_lib():
    db = st.StudioDB()
    MarketingService(db, now=NOW).save_brand(st.jwt(st.OWNER), st.T1, {"business_name": "Dr. Redden",
                                                               "account_type": "medical_education"})
    return db, LibraryService(db, now=st.NOW, detector=MockFaceDetector(0.95))


def test_medical_library_requires_deidentification():
    db, lib = _medical_lib()
    assert lib.library(st.jwt(st.OWNER), st.T1)["medical"] is True
    m = st.up(lib)
    body = {"contains_people": False, "contains_minors": False, "people_policy": "no_people"}
    assert st.err(lib.classify, st.jwt(st.OWNER), st.T1, m["id"], body) == "deidentification_required"
    assert st.err(lib.classify, st.jwt(st.OWNER), st.T1, m["id"], {**body, "deidentified": False}) == "deidentification_required"
    r = lib.classify(st.jwt(st.OWNER), st.T1, m["id"], {**body, "deidentified": True})
    assert r["privacy_class"] == "business_media_no_people" and r["metadata"]["deidentified"] is True
    # excluir siempre se puede, sin confirmar nada
    assert lib.classify(st.jwt(st.OWNER), st.T1, m["id"], {"people_policy": "exclude"})["privacy_class"] == "restricted"


def test_local_business_library_unchanged():
    db = st.StudioDB()
    lib = LibraryService(db, now=st.NOW, detector=MockFaceDetector(0.95))
    assert lib.library(st.jwt(st.OWNER), st.T1)["medical"] is False
    m = st.up(lib)
    r = lib.classify(st.jwt(st.OWNER), st.T1, m["id"], {"contains_people": False, "contains_minors": False,
                                                  "people_policy": "no_people"})
    assert r["privacy_class"] == "business_media_no_people"
