"""
AITA Marketing (Fase 2): MarketingAIRouter, catálogo, mezcla real/IA, sincronía Python ↔ SQL
y endpoints HTTP de Biblioteca / Estudio / Trabajos.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services import marketing_jobs_domain as jd
from services.marketing_ai_catalog import CatalogError, load, parse
from services.marketing_ai_router import (MarketingAIRouter, MockProvider, OmniRouteAdapter, RouteRequest,
                                          RouterError, provider_enabled)
from services.marketing_domain import DomainError
from services.marketing_mix import PRESETS, confirm_mix, plan_scenes, suggest_mix, validate_mix
from test_marketing import T1, OWNER, STAFF, MEMBER
from test_marketing_gate import ProdLikeDB

ROOT = Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase/migrations/20261011120000_marketing_reel_studio.sql").read_text()
RAW = json.loads((ROOT / "services/marketing_ai_catalog.json").read_text())


def entry(**kw):
    base = {"provider": "omniroute", "model_id": "x", "supported_tasks": ["image_generation"], "quality_score": 70,
            "estimated_cost": 0.04, "billing_unit": "image", "latency_class": "medium", "reliability_score": 0.9,
            "supports_image_input": True, "supports_video_input": False, "supports_reference_image": True,
            "supports_commercial_use": True, "data_retention_policy": "zero_retention", "real_people_allowed": False,
            "external": True, "price_verified": True, "enabled": True}
    return {**base, **kw}


def catalog(*models):
    return parse({**RAW, "models": list(models)})


def req(**kw):
    base = dict(tenant_id=T1, task_type="image_generation", quality_tier="standard", maximum_cost=1.0,
                privacy_class="synthetic_only", idempotency_key="k" * 20)
    return RouteRequest(**{**base, **kw})


ON = {"OMNIROUTE_ENABLED": "true"}


# ================================================================== catálogo
def test_default_catalog_is_valid_and_has_no_secrets():
    cat = load()
    assert cat.version and cat.currency == "USD" and cat.price_source
    text = (ROOT / "services/marketing_ai_catalog.json").read_text()
    assert not re.search(r"(?i)(api[_-]?key|secret|bearer|sk-[a-z0-9])", text)
    ext = [m for m in cat.models if m.external]
    assert ext and all(not m.enabled and not m.price_verified and m.estimated_cost is None for m in ext)


@pytest.mark.parametrize("bad", [{"api_key": "x"}, {"supported_tasks": ["mind_reading"]}, {"estimated_cost": -1},
                                 {"reliability_score": 0}, {"billing_unit": "vibes"}, {"provider": "Bad Name"}])
def test_catalog_validation(bad):
    with pytest.raises(CatalogError):
        catalog(entry(**bad))


# ================================================================== política de selección
def test_disabled_by_default_no_external_model():
    r = MarketingAIRouter(env={}).route(req(task_type="text_to_video", quality_tier="draft", units=10))
    assert r.model.provider == "mock" and not r.model.external


def test_omniroute_off_unless_env():
    assert provider_enabled("omniroute", {}) is False
    assert provider_enabled("omniroute", {"OMNIROUTE_ENABLED": "TRUE"}) is True
    assert provider_enabled("unknown", {"X": "true"}) is False


def test_cheapest_expected_cost_and_tiebreaks():
    cat = catalog(entry(model_id="a", estimated_cost=0.05, reliability_score=0.9),
                  entry(model_id="b", estimated_cost=0.04, reliability_score=0.5),     # 0.08 esperado
                  entry(model_id="c", estimated_cost=0.04, reliability_score=0.95, latency_class="slow"),
                  entry(model_id="d", estimated_cost=0.04, reliability_score=0.95, latency_class="fast"))
    r = MarketingAIRouter(cat, env=ON).route(req())
    assert r.model.model_id == "d" and [m.model_id for m in r.fallbacks] == ["c", "a", "b"]


def test_excludes_over_budget_never_upgrades():
    cat = catalog(entry(model_id="cheap", quality_score=50), entry(model_id="pricey", estimated_cost=5, quality_score=95))
    with pytest.raises(RouterError) as e:
        MarketingAIRouter(cat, env=ON).route(req(quality_tier="premium", maximum_cost=1))
    assert e.value.code == "no_eligible_model"
    r = MarketingAIRouter(cat, env=ON).route(req(quality_tier="draft", maximum_cost=1))
    assert r.model.model_id == "cheap" and r.fallbacks == ()            # el caro nunca es fallback


def test_excludes_low_quality_and_slow_and_unsupported():
    cat = catalog(entry(model_id="low", quality_score=20), entry(model_id="slow", latency_class="slow"),
                  entry(model_id="noimg", supports_image_input=False), entry(model_id="ok"))
    r = MarketingAIRouter(cat, env=ON).route(req(maximum_latency="medium", input_media_types=("image",)))
    assert r.model.model_id == "ok"
    assert r.excluded["quality"] == 1 and r.excluded["latency"] == 1 and r.excluded["unsupported"] == 1


def test_unverified_price_is_excluded():
    cat = catalog(entry(model_id="unpriced", price_verified=False), entry(model_id="nocost", estimated_cost=None))
    with pytest.raises(RouterError):
        MarketingAIRouter(cat, env=ON).route(req())


@pytest.mark.parametrize("cls,kw,ok", [
    ("restricted", {}, False),                                                     # nunca sale
    ("consented_people", {}, False),                                               # modelo no admite personas
    ("consented_people", {"real_people_allowed": True}, True),
    ("consented_people", {"real_people_allowed": True, "_unscanned": True}, False),   # sin antivirus: no sale
    ("anonymized_people", {"real_people_allowed": True, "data_retention_policy": "unknown"}, False),
    ("business_media_no_people", {"supports_commercial_use": False}, False),
    ("synthetic_only", {}, True),
])
def test_privacy_rules_for_external(cls, kw, ok):
    kw = dict(kw)
    clean = not kw.pop("_unscanned", False)
    cat = catalog(entry(**kw))
    r = req(privacy_class=cls, inputs_malware_clean=clean)
    if ok:
        assert MarketingAIRouter(cat, env=ON).route(r).model.external
    else:
        with pytest.raises(RouterError):
            MarketingAIRouter(cat, env=ON).route(r)


def test_local_tools_may_process_restricted():
    r = MarketingAIRouter(env={}).route(req(task_type="face_anonymization", privacy_class="restricted",
                                            quality_tier="standard"))
    assert not r.model.external


@pytest.mark.parametrize("kw,code", [({"task_type": "x"}, "invalid_task_type"), ({"quality_tier": "ultra"}, "invalid_quality_tier"),
                                     ({"privacy_class": "public"}, "invalid_privacy_class"), ({"maximum_cost": -1}, "invalid_maximum_cost"),
                                     ({"idempotency_key": "short"}, "invalid_idempotency_key")])
def test_request_validation(kw, code):
    with pytest.raises(RouterError) as e:
        MarketingAIRouter(env={}).route(req(**kw))
    assert e.value.code == code


def test_mock_is_deterministic_and_idempotent():
    m = MockProvider()
    model = load().models[1]
    a, b = m.submit(req(), model), m.submit(req(), model)
    assert a == b and m.submissions == 1
    assert MockProvider().submit(req(), model).provider_job_id == a.provider_job_id


def test_omniroute_adapter_has_no_transport():
    ad = OmniRouteAdapter(env={"OMNIROUTE_ENABLED": "true", "OMNIROUTE_BASE_URL": "https://example.invalid"})
    assert ad.enabled()
    assert ad.submit(req(), load().models[4]).error_code == "provider_disabled"
    payload = ad.build_payload(req(), load().models[4])
    assert T1 not in json.dumps(payload) and "url" not in json.dumps(payload).lower()


# ================================================================== mezcla
def test_presets_and_plan_totals():
    assert PRESETS == ((100, 0), (75, 25), (50, 50), (25, 75), (0, 100))
    for real, ai in PRESETS:
        for scenes, secs in ((1, 15), (4, 30), (6, 30), (7, 45), (20, 90)):
            p = plan_scenes(real, ai, scenes, secs)
            assert sum(x["seconds"] for x in p["plan"]) == secs and len(p["plan"]) == scenes
            real_scenes = p["by_origin"]["client_original"]["scenes"]
            assert abs(real_scenes - scenes * real / 100) <= 1


def test_custom_percentages_step_5():
    assert validate_mix(35, 65) == {"real_media_percent": 35, "ai_media_percent": 65}
    with pytest.raises(DomainError):
        validate_mix(37, 63)


def test_adapted_real_scenes_are_labeled():
    p = plan_scenes(50, 50, 4, 20, adapt_real=True)
    assert p["by_origin"]["client_ai_adapted"]["scenes"] == 2 and p["by_origin"]["client_original"]["scenes"] == 0


def test_suggestion_is_only_a_suggestion():
    s = suggest_mix(10, 7)
    assert s["is_suggestion"] and s["requires_confirmation"]
    with pytest.raises(DomainError):
        confirm_mix("staff", 50, 50)


# ================================================================== sincronía Python ↔ SQL
def _sql_cases(fn):
    body = MIG[MIG.index(f"create or replace function private.{fn}()"):]
    body = body[:body.index("end $$;")]
    return {k: tuple(v.replace("'", "").split(",")) if v else ()
            for k, v in re.findall(r"when '(\w+)'\s+then array\[([^\]]*)\]", body)}


def test_job_transitions_match_sql():
    sql = _sql_cases("marketing_job_transition")
    py = {k: v for k, v in jd.JOB_TRANSITIONS.items() if v}
    assert sql == py


def test_media_transitions_match_sql():
    assert _sql_cases("marketing_media_guard") == jd.MEDIA_TRANSITIONS


def test_sql_enumerations_match_python():
    st = re.search(r"status\s+text not null default 'draft' check \(status in \(([^)]*)\)\)", MIG.replace("\n", " "))
    sts = tuple(x.strip(" '") for x in re.sub(r"\s+", " ", st.group(1)).split(","))
    assert sts == jd.JOB_STATUSES
    for p in ("exclude", "anonymize", "consented", "no_people", "synthetic_only", "restricted"):
        assert f"'{p}'" in MIG


def test_applied_migration_unchanged_and_new_one_is_additive():
    import hashlib
    applied = (ROOT / "supabase/migrations/20261009120000_aita_marketing.sql").read_bytes()
    assert hashlib.sha256(applied).hexdigest() == "4e7378b14b1861a76ccf1e9d8b0d1f0f70f4d5f797b5617db5ad5ae9b90f3184"
    low = MIG.lower()
    assert "drop table" not in low and "truncate" not in low and "drop column" not in low
    assert "delete from" not in low and "tenants set" not in low          # no toca datos ni módulos


# ================================================================== HTTP
JOB = "00000000-0000-0000-0000-000000000001"
ENDPOINTS = [
    ("GET", f"/api/manager/marketing/library?tenant_id={T1}", None),
    ("GET", f"/api/manager/marketing/library/{JOB}/content?tenant_id={T1}", None),
    ("POST", f"/api/manager/marketing/library/{JOB}/revoke-consent", {"tenant_id": T1}),
    ("POST", f"/api/manager/marketing/library/{JOB}/delete", {"tenant_id": T1, "confirm": True}),
    ("PATCH", f"/api/manager/marketing/library/{JOB}/privacy", {"tenant_id": T1, "people_policy": "exclude"}),
    ("POST", f"/api/manager/marketing/library/{JOB}/archive", {"tenant_id": T1}),
    ("POST", f"/api/manager/marketing/library/{JOB}/anonymize", {"tenant_id": T1, "method": "blur_faces"}),
    ("POST", f"/api/manager/marketing/library/derivatives/{JOB}/review", {"tenant_id": T1}),
    ("GET", f"/api/manager/marketing/studio?tenant_id={T1}", None),
    ("POST", "/api/manager/marketing/studio/mix-preview", {"tenant_id": T1, "real_media_percent": 50, "ai_media_percent": 50}),
    ("GET", f"/api/manager/marketing/jobs?tenant_id={T1}", None),
    ("POST", "/api/manager/marketing/jobs", {"tenant_id": T1}),
    ("GET", f"/api/manager/marketing/jobs/{JOB}?tenant_id={T1}", None),
] + [("POST", f"/api/manager/marketing/jobs/{JOB}/{a}", {"tenant_id": T1})
     for a in ("estimate", "approve", "reopen", "cancel", "process", "send-to-approval")]


def client_for(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return TestClient(main.app)


def call(client, method, path, body, user=OWNER):
    return client.request(method, path, json=body, headers={"Authorization": f"Bearer jwt-{user}"})


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_disabled_tenant_never_touches_tables(monkeypatch, method, path, body):
    db = ProdLikeDB(modules={"marketing": False}, tables_exist=False)
    r = call(client_for(monkeypatch, db), method, path, body)
    assert r.status_code == 403 and r.json() == {"error": "marketing_disabled"} and db.marketing_calls == []


@pytest.mark.parametrize("user", [STAFF, MEMBER])
def test_staff_and_member_blocked_everywhere(monkeypatch, user):
    db = ProdLikeDB(modules={"marketing": True})
    client = client_for(monkeypatch, db)
    for method, path, body in ENDPOINTS:
        r = call(client, method, path, body, user)
        assert r.status_code == 403 and r.json() == {"error": "forbidden"}, path
    up = client.post(f"/api/manager/marketing/library?tenant_id={T1}", content=b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,
                     headers={"Authorization": f"Bearer jwt-{user}", "Content-Type": "image/png", "X-File-Name": "a.png"})
    assert up.status_code == 403
    assert db.marketing_calls == []


def test_enabled_without_new_tables_is_controlled_503(monkeypatch):
    db = ProdLikeDB(modules={"marketing": True}, missing_only="marketing_generation_jobs")
    r = call(client_for(monkeypatch, db), "GET", f"/api/manager/marketing/jobs?tenant_id={T1}", None)
    assert r.status_code == 503 and r.json() == {"error": "marketing_unavailable"}


def test_unknown_job_action_is_404(monkeypatch):
    r = call(client_for(monkeypatch, ProdLikeDB(modules={"marketing": True})), "POST",
             f"/api/manager/marketing/jobs/{JOB}/publish", {"tenant_id": T1})
    assert r.status_code == 404


def test_every_studio_method_goes_through_the_gate():
    import inspect
    from services.marketing_library import LibraryService
    from services.marketing_studio import StudioService
    for cls in (LibraryService, StudioService):
        own = [n for n, f in vars(cls).items() if inspect.isfunction(f) and not n.startswith("_")]
        assert own
        for name in own:
            src = inspect.getsource(inspect.unwrap(getattr(cls, name)))
            assert "self.ctx(jwt, tenant_id)" in src, name


def test_logs_never_include_urls_paths_or_prompts():
    src = "\n".join(p.read_text() for p in (ROOT / "services").glob("marketing_*.py"))
    lines = [ln for ln in src.splitlines() if "logger." in ln and "getLogger" not in ln]
    assert lines
    for ln in lines:
        assert not re.search(r"\{(url|path|data|prompt|key|signed|script|body)", ln), ln
