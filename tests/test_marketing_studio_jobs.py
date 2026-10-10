"""
AITA Marketing (Fase 2): Estudio de Reels — mezcla, flujo mock, worker, idempotencia y límites.
Sin red ni gasto: proveedores reales apagados.
"""
import socket
import uuid

import pytest

from services import marketing_jobs_domain as jd
from services.marketing_ai_router import MarketingAIRouter
from services.marketing_domain import DomainError
from services.marketing_library import LibraryService
from services.marketing_studio import StudioService
from services.marketing_ai_router import MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_privacy import MockFaceDetector
from services.marketing_worker import JobRunner
from test_marketing import NOW, T1, T2, OWNER, MANAGER, STAFF, OTHER
from test_marketing_studio import StudioDB, err, up, no_people, new_job, approved_job, jwt, BRIEF


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def lib(db):
    return LibraryService(db, now=NOW, detector=MockFaceDetector(0.95))


@pytest.fixture
def studio(db):
    return StudioService(db, now=NOW, router=MarketingAIRouter(env={}),
                         adapters={"mock": MockProvider(), "omniroute": OmniRouteAdapter(env={}),
                                   "local_ffmpeg": DisabledLocalTools()})


@pytest.mark.parametrize("real,ai,code", [(60, 30, "mix_must_total_100"), (101, -1, "mix_must_total_100"),
                                          (33, 67, "mix_step"), ("x", 50, "invalid_mix"), (True, 0, "invalid_mix")])
def test_mix_validation(lib, studio, real, ai, code):
    assert err(new_job, studio, [], real=real, ai=ai) == code


def test_full_mock_flow(lib, studio, db):
    m = up(lib)
    no_people(lib, m)
    j = new_job(studio, [m["id"]], real=50, ai=50)
    assert j["status"] == "draft" and j.get("approved_at") is None
    assert err(studio.process_job, jwt(OWNER), T1, j["id"]) == "generation_approval_required"
    j = studio.estimate_job(jwt(OWNER), T1, j["id"])
    assert j["status"] == "awaiting_generation_approval" and j["estimated_cost"] == 0
    est = j["request_metadata"]["estimate"]
    assert est["external_calls"] == 0 and {s["provider"] for s in est["subtasks"]} == {"mock"}
    assert est["plan"]["by_origin"]["client_original"]["scenes"] == 3
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], False) == "confirmation_required"
    assert err(studio.approve_job, jwt(STAFF), T1, j["id"], True) == "forbidden"
    j = studio.approve_job(jwt(MANAGER), T1, j["id"], True)
    assert j["status"] == "queued" and j["approved_by"] == MANAGER
    j = studio.process_job(jwt(OWNER), T1, j["id"])
    assert j["status"] == "succeeded" and j["actual_cost"] == 0 and j["selected_provider"] == "mock"
    assert j["result_metadata"]["mock"] is True
    full = studio.job(jwt(OWNER), T1, j["id"])
    scenes = [o for o in full["outputs"] if o["kind"] == "scene"]
    assert len(scenes) == 6 and sum(o["duration_ms"] for o in scenes) == 30000
    assert all(o["metadata"]["mock"] for o in full["outputs"])
    assert [e["to_status"] for e in full["events"]] == ["draft", "awaiting_generation_approval", "queued",
                                                       "processing", "succeeded"]
    assert [e["actor_role"] for e in full["events"]][-2:] == ["system", "system"]       # lo ejecuta el runner
    out = studio.send_to_approval(jwt(OWNER), T1, j["id"], "Reel octubre")
    assert out["content"]["status"] == "review" and out["content"]["format"] == "reel"
    assert out["content"]["notes"].startswith("mock_generation_job:")
    assert db.tables["marketing_publications"] == []                          # nunca publica
    assert err(studio.send_to_approval, jwt(OWNER), T1, j["id"], "x") == "already_sent"
    # un resultado simulado no se puede programar aunque se apruebe y se le quite la marca
    cid = out["content"]["id"]
    studio.transition(jwt(OWNER), T1, cid, "approved")
    db.tables["marketing_content"][-1].update({"channels": ["instagram"], "notes": "editado"})
    assert err(studio.transition, jwt(OWNER), T1, cid, "scheduled", None, "2026-10-30T10:00") == "mock_content_not_publishable"
    assert db.tables["marketing_publications"] == []


def test_real_provider_jobs_never_run_inside_request(lib, studio, db):
    j = approved_job(studio)
    est = j["request_metadata"]["estimate"]
    est["subtasks"][0]["provider"] = "omniroute"
    db.tables["marketing_generation_jobs"][0]["request_metadata"] = {**j["request_metadata"], "estimate": est}
    assert err(studio.process_job, jwt(OWNER), T1, j["id"]) == "requires_worker"
    assert db.tables["marketing_generation_jobs"][0]["status"] == "queued"


def test_worker_claims_once(lib, studio, db):
    j = approved_job(studio)
    runner = JobRunner(db, studio.router, studio.adapters, now=NOW)
    assert runner.run_pending()[0]["status"] == "succeeded"
    assert runner.run_pending() == []
    assert err(studio.process_job, jwt(OWNER), T1, j["id"]) == "invalid_transition"


def test_retry_never_double_charges(lib, studio, db):
    j = approved_job(studio)
    mock = studio.adapters["mock"]
    studio.process_job(jwt(OWNER), T1, j["id"])
    n = mock.submissions
    assert err(studio.process_job, jwt(OWNER), T1, j["id"]) == "invalid_transition"
    assert mock.submissions == n
    keys = [u["idempotency_key"] for u in db.tables["marketing_model_usage"]]
    assert len(keys) == len(set(keys))
    k = "retry-key-0123456789"
    assert new_job(studio, [], 0, 100, idempotency_key=k)["id"] == new_job(studio, [], 0, 100, idempotency_key=k)["id"]


def test_cancel_and_fail_keep_evidence(lib, studio, db):
    j = studio.cancel_job(jwt(OWNER), T1, new_job(studio, [], real=0, ai=100)["id"])
    assert j["status"] == "cancelled" and j["error_code"] == "cancelled_by_user"
    assert err(studio.reopen_job, jwt(OWNER), T1, j["id"]) == "invalid_transition"


@pytest.mark.parametrize("change", [{"ai_generation_enabled": False}, {"monthly_generation_job_limit": 0},
                                    {"monthly_generated_video_seconds_limit": 0}, {"budget": 0}])
def test_zero_limits_block_all_generation_including_mock(lib, studio, db, change):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    if "budget" in change:
        db.tables["aita_cost_budgets"][0]["monthly_marketing_ai_cost_limit_cents"] = 0
    else:
        db.tables["marketing_settings"][0].update(change)
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "generation_disabled"
    assert db.tables["marketing_model_usage"] == [] and studio.adapters["mock"].submissions == 0


def test_golden_age_defaults_block_generation_but_library_works(db):
    """Valores por defecto de la migración para un tenant sin configurar (como Golden Age)."""
    s = db.tables["marketing_settings"][0]
    s.update(jd.GEN_DEFAULTS)
    db.tables["aita_cost_budgets"][0]["monthly_marketing_ai_cost_limit_cents"] = 0     # subpresupuestos cerrados
    lib = LibraryService(db, now=NOW)
    studio = StudioService(db, now=NOW, router=MarketingAIRouter(env={}))
    assert err(up, lib) == "library_disabled"                  # ni siquiera la Biblioteca está habilitada
    s["library_storage_limit_bytes"] = 1073741824              # el operador autoriza 1 GiB (solo Biblioteca)
    m = up(lib)                                             # subir y clasificar no consume generación
    no_people(lib, m)
    assert studio.overview(jwt(OWNER), T1)["generation_enabled"] is False
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [m["id"]])["id"])
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "generation_disabled"


@pytest.mark.parametrize("key", ["ai_generation_enabled", "monthly_generation_job_limit",
                                 "monthly_marketing_ai_cost_limit_cents"])
def test_each_switch_alone_disables_generation(key):
    on = {"ai_generation_enabled": True, "monthly_generation_job_limit": 5, "monthly_marketing_ai_cost_limit_cents": 2000}
    assert jd.generation_enabled(on) is True
    assert jd.generation_enabled({**on, key: False if key == "ai_generation_enabled" else 0}) is False
    assert jd.generation_enabled({**on, "monthly_generation_job_limit": None}) is True      # null = sin límite


def test_limits_checked_before_any_spend(lib, studio, db):
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    db.tables["marketing_settings"][0]["monthly_generated_video_seconds_limit"] = 10
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "limit_monthly_generated_video_seconds_limit"
    assert db.tables["marketing_model_usage"] == []


def test_tenant_cannot_raise_own_limits():
    import inspect
    from services import marketing_library, marketing_studio, marketing_studio_routes, marketing, marketing_routes
    for mod in (marketing_library, marketing_studio, marketing_studio_routes, marketing, marketing_routes):
        src = inspect.getsource(mod)
        for verb in ("insert", "update", "upsert", "delete"):
            assert f'{verb}("marketing_settings"' not in src, (mod.__name__, verb)


def test_regeneration_changing_mix_requires_reconfirmation(lib, studio):
    j = new_job(studio, [], 0, 100)
    studio.cancel_job(jwt(OWNER), T1, j["id"])
    m = up(lib)
    no_people(lib, m)
    assert err(new_job, studio, [m["id"]], 50, 50, regeneration_of=j["id"]) == "mix_change_requires_confirmation"
    r = new_job(studio, [m["id"]], 50, 50, regeneration_of=j["id"], mix_reconfirmed=True)
    assert r["regeneration_of"] == j["id"]


def test_forbidden_claims_in_script(studio):
    bad = {**BRIEF, "script": {"hook": "Antes y después en 30 días", "body": "", "cta": ""}}
    assert err(studio.create_job, jwt(OWNER), T1, {"brief": bad, "real_media_percent": 0, "ai_media_percent": 100,
                                                   "quality_tier": "draft", "maximum_cost": 1}) == "forbidden_claim"


def test_no_network_with_providers_disabled(lib, studio, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    j = approved_job(studio)
    assert studio.process_job(jwt(OWNER), T1, j["id"])["status"] == "succeeded"


def test_job_status_revalidates_permissions(studio, db):
    j = new_job(studio, [], 0, 100)
    db.tables["tenant_users"][0]["active"] = False                  # el owner pierde acceso
    assert err(studio.job, jwt(OWNER), T1, j["id"]) == "forbidden"
    assert err(studio.job, jwt(OTHER), T2, j["id"]) == "not_found"


def test_queued_always_requires_approval():
    with pytest.raises(DomainError) as e:
        jd.check_job_transition("awaiting_generation_approval", "queued")
    assert e.value.code == "generation_approval_required"
    jd.check_job_transition("awaiting_generation_approval", "queued", approved=True)
    for frm in ("draft", "processing", "succeeded", "failed", "cancelled"):
        with pytest.raises(DomainError):
            jd.check_job_transition(frm, "queued", approved=True)


def test_job_and_media_domain_lists():
    assert jd.JOB_STATUSES == ("draft", "awaiting_generation_approval", "queued", "processing", "succeeded",
                               "failed", "cancelled")
    assert set(jd.TERMINAL) == {s for s, t in jd.JOB_TRANSITIONS.items() if not t}
    assert jd.public_error("SECRET_TRACE token=abc") == "internal_error"
    assert uuid.UUID(T1)


def test_approval_rechecks_inputs_even_without_automatic_cancellation(lib, studio, db):
    from test_marketing_studio import consented
    m = up(lib)
    consented(lib, m)
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [m["id"]])["id"])
    db.tables["marketing_media"][0].update({"consent_status": "revoked", "people_policy": "exclude"})   # sin pasar por revoke
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "consent_revoked"
    assert db.tables["aita_cost_ledger"] == []
