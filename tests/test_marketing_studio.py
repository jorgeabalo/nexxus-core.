"""
AITA Marketing (Fase 2): Biblioteca privada, privacidad, mezcla real/IA, trabajos y flujo mock.
Sin red: Supabase y Storage en memoria; proveedores reales apagados.
"""
import socket
import struct
import uuid

import pytest

from services import marketing_jobs_domain as jd
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from services.marketing_privacy import MockFaceDetector
from services.marketing_studio import StudioService
from services.member_portal import PortalError
from test_marketing import FakeDB, NOW, T1, T2, OWNER, MANAGER, STAFF, OTHER, MEMBER

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1080, 1920) + b"\x08\x02\x00\x00\x00" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + struct.pack("<HH", 10, 20) + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 10 + struct.pack("<HH", 300, 200) + b"\x00" * 16
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 20 + b"webm" + b"\x00" * 40


class StudioDB(FakeDB):
    def __init__(self):
        super().__init__()
        for t in ("marketing_media", "marketing_media_derivatives", "marketing_generation_jobs",
                  "marketing_generation_job_events", "marketing_generation_inputs", "marketing_generation_outputs",
                  "marketing_model_usage"):
            self.tables[t] = []
        for s in self.tables["marketing_settings"]:
            s.update({"ai_generation_enabled": True, "max_upload_bytes": 10_000_000,
                      "library_storage_limit_bytes": 50_000_000, "monthly_generation_job_limit": 5,
                      "monthly_regeneration_limit": 2, "monthly_generated_image_limit": 10,
                      "monthly_generated_video_seconds_limit": 600, "monthly_ai_cost_limit": 0})
        self.storage, self.signed = {}, []

    def storage_upload(self, bucket, key, data, mime):
        if (bucket, key) in self.storage:
            raise RuntimeError("Supabase storage POST -> 409: Duplicate")     # x-upsert=false
        self.storage[(bucket, key)] = (data, mime)

    def storage_sign(self, bucket, key, ttl):
        assert (bucket, key) in self.storage
        self.signed.append((key, ttl))
        return f"https://x.supabase.co/storage/v1/object/sign/{bucket}/{key}?token=secret-token"

    def update(self, table, filters, values):
        if table in ("marketing_generation_job_events", "marketing_generation_inputs", "marketing_model_usage"):
            raise RuntimeError("append-only")
        return super().update(table, filters, values)


def jwt(u):
    return f"jwt-{u}"


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


def err(fn, *a, **kw):
    with pytest.raises(PortalError) as e:
        fn(*a, **kw)
    return e.value.code


def up(lib, data=PNG, name="foto.png", mime="image/png", who=OWNER, tenant=T1):
    return lib.upload(jwt(who), tenant, name, mime, data)


def no_people(lib, m, who=OWNER):
    return lib.classify(jwt(who), T1, m["id"], {"contains_people": False, "people_policy": "no_people",
                                                "consent_status": "not_required"})


BRIEF = {"objective": "new_members", "audience": "seniors_60_plus", "style": "energetic", "duration_seconds": 30,
         "script": {"hook": "Muévete con confianza", "body": "Clases guiadas", "cta": "Reserva tu clase"},
         "targets": ["instagram", "tiktok"]}


def new_job(studio, media_ids, real=50, ai=50, who=OWNER, **kw):
    body = {"brief": BRIEF, "real_media_percent": real, "ai_media_percent": ai, "quality_tier": "draft",
            "maximum_cost": 5, "media_ids": media_ids, "scenes": 6, **kw}
    return studio.create_job(jwt(who), T1, body)


# ================================================================== permisos
@pytest.mark.parametrize("who", [STAFF, MEMBER, OTHER])
def test_only_owner_manager_of_tenant(lib, studio, who):
    assert err(lib.library, jwt(who), T1) == "forbidden"
    assert err(up, lib, who=who) == "forbidden"
    assert err(studio.jobs, jwt(who), T1) == "forbidden"
    assert err(studio.create_job, jwt(who), T1, {}) == "forbidden"


def test_anonymous_and_disabled_module(lib, db):
    assert err(lib.library, "", T1) == "unauthorized"
    db.tables["tenants"][0]["modules"] = {"marketing": False}
    assert err(lib.library, jwt(OWNER), T1) == "marketing_disabled"


def test_manager_can_use_library(lib):
    assert up(lib, who=MANAGER)["processing_status"] == "ready"
    assert lib.library(jwt(MANAGER), T1)["items"]


# ================================================================== archivos
@pytest.mark.parametrize("data,name,mime", [(PNG, "a.png", "image/png"), (JPEG, "a.jpg", "image/jpeg"),
                                            (GIF, "a.gif", "image/gif"), (WEBP, "a.webp", "image/webp"),
                                            (MP4, "a.mp4", "video/mp4"), (MOV, "a.MOV", "video/quicktime"),
                                            (WEBM, "a.webm", "video/webm")])
def test_valid_formats(lib, db, data, name, mime):
    m = up(lib, data, name, mime)
    assert m["mime_type"] == mime and m["people_policy"] == "exclude" and m["contains_people"] is None
    assert m["storage_path"].startswith(f"{T1}/originals/{m['id']}/")
    assert len(m["checksum"]) == 64 and m["uploaded_by"] == OWNER


def test_dimensions_parsed(lib):
    m = up(lib)
    assert (m["width"], m["height"]) == (1080, 1920)


@pytest.mark.parametrize("data,name,mime,code", [
    (PNG, "a.gif", "image/gif", "extension_mismatch"),            # extensión falsa
    (PNG, "a.png", "image/jpeg", "mime_mismatch"),                # MIME falso
    (b"hello world" * 10, "a.png", "image/png", "unrecognized_content"),   # firma falsa
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>', "a.svg", "image/svg+xml", "forbidden_file_type"),
    (b"<!DOCTYPE html><html><script>alert(1)</script>", "a.png", "image/png", "forbidden_file_type"),
    (b"MZ\x90\x00" + b"\x00" * 60, "a.png", "image/png", "forbidden_file_type"),
    (b"#!/bin/sh\nrm -rf /", "a.png", "image/png", "forbidden_file_type"),
    (PNG, "a.exe", "image/png", "unsupported_extension"),
    (PNG, "sinextension", "image/png", "unsupported_extension"),
    (b"", "a.png", "image/png", "empty_file"),
])
def test_rejected_files(lib, db, data, name, mime, code):
    assert err(up, lib, data, name, mime) == code
    assert db.storage == {} and db.tables["marketing_media"] == []


def test_too_large_and_storage_limit(lib, db):
    db.tables["marketing_settings"][0]["max_upload_bytes"] = 50
    assert err(up, lib) == "file_too_large"
    db.tables["marketing_settings"][0].update({"max_upload_bytes": 10_000, "library_storage_limit_bytes": 50})
    assert err(up, lib) == "limit_library_storage"
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = 0        # por defecto: cerrado
    assert err(up, lib) == "limit_library_storage"


@pytest.mark.parametrize("name", ["../../etc/passwd.png", "..\\..\\x.png", "/abs/olute.png", "a/../../b.png",
                                  "%2e%2e%2fx.png", "con:x.png", "ñandú foto (1).png"])
def test_path_traversal_and_names_sanitized(lib, name):
    m = up(lib, PNG + name.encode(), name)
    parts = m["storage_path"].split("/")
    assert len(parts) == 4 and parts[0] == T1 and parts[1] == "originals" and ".." not in m["storage_path"]
    assert all(ch.isalnum() or ch in "._-" for ch in parts[3])


def test_duplicate_upload_reuses_original(lib, db):
    a, b = up(lib), up(lib)
    assert b["duplicate"] and a["id"] == b["id"] and len(db.storage) == 1


def test_short_lived_signed_url_only_for_own_tenant(lib, db, monkeypatch):
    m = up(lib)
    p = lib.preview(jwt(OWNER), T1, m["id"])
    assert p["expires_in"] == 300 and "token=" in p["url"]
    monkeypatch.setenv("MARKETING_SIGNED_URL_TTL", "99999")
    assert lib.preview(jwt(OWNER), T1, m["id"])["expires_in"] == 900
    # el owner de T1 es manager de T2: aun así no puede ver el archivo de T1 pasando T2
    assert err(lib.preview, jwt(OWNER), T2, m["id"]) == "not_found"
    assert err(lib.preview, jwt(OTHER), T2, m["id"]) == "not_found"


def test_original_never_overwritten(lib, db):
    m = up(lib)
    with pytest.raises(RuntimeError):
        db.storage_upload("marketing-assets", m["storage_path"], b"otra cosa", "image/png")
    assert db.storage[("marketing-assets", m["storage_path"])][0] == PNG


def test_archive_never_deletes(lib, db):
    m = up(lib)
    assert lib.set_archived(jwt(OWNER), T1, m["id"], True)["processing_status"] == "archived"
    assert lib.set_archived(jwt(OWNER), T1, m["id"], False)["processing_status"] == "ready"
    assert len(db.tables["marketing_media"]) == 1 and len(db.storage) == 1


# ================================================================== privacidad
def test_unknown_people_defaults_to_exclude_and_blocks_use(lib, studio):
    m = up(lib)
    assert err(new_job, studio, [m["id"]]) == "media_excluded"


@pytest.mark.parametrize("body,code", [
    ({"contains_people": None, "people_policy": "no_people"}, "no_people_requires_confirmation"),
    ({"contains_people": True, "people_policy": "consented", "consent_status": "pending"}, "consent_required"),
    ({"contains_people": True, "people_policy": "magic"}, "invalid_people_policy"),
])
def test_classification_rules(lib, body, code):
    m = up(lib)
    assert err(lib.classify, jwt(OWNER), T1, m["id"], body) == code


def test_anonymize_creates_derivative_requiring_human_review(lib, studio, db):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "people_policy": "anonymize"})
    assert err(new_job, studio, [m["id"]]) == "anonymization_required"
    der = lib.anonymize(jwt(OWNER), T1, m["id"], "pixelate_faces")
    assert der["status"] == "needs_review" and der["storage_path"].startswith(f"{T1}/derivatives/{m['id']}/")
    assert "pixelation_not_guaranteed" in der["warnings"]
    assert db.storage[("marketing-assets", m["storage_path"])][0] == PNG          # original intacto
    assert err(lib.review_derivative, jwt(OWNER), T1, der["id"], True) == "human_review_required"
    assert lib.review_derivative(jwt(OWNER), T1, der["id"], True, True)["status"] == "ready"
    j = new_job(studio, [m["id"]])
    assert j["people_policy"] == "anonymize"
    assert db.tables["marketing_generation_inputs"][0]["privacy_class"] == "anonymized_people"


def test_low_confidence_stops_for_human_review(db):
    lib = LibraryService(db, now=NOW, detector=MockFaceDetector(0.4))
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "people_policy": "anonymize"})
    der = lib.anonymize(jwt(OWNER), T1, m["id"], "blur_faces")
    assert der["metadata"]["low_confidence"] and der["storage_path"] is None
    assert err(lib.review_derivative, jwt(OWNER), T1, der["id"], True, True) == "human_review_required"


def test_production_detector_unavailable_never_claims_anonymity(db):
    lib = LibraryService(db, now=NOW)                         # detector por defecto: no disponible
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "people_policy": "anonymize"})
    der = lib.anonymize(jwt(OWNER), T1, m["id"], "silhouette")
    assert der["metadata"]["guarantee"] == "not_full_anonymity" and der["metadata"]["stop_reason"]


def test_invalid_anonymization_method(lib):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "people_policy": "anonymize"})
    assert err(lib.anonymize, jwt(OWNER), T1, m["id"], "face_swap") == "invalid_anonymization_method"


def test_consented_people_allowed(lib, studio):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "people_policy": "consented",
                                           "consent_status": "granted", "consent_note": "Formulario 12/10"})
    assert new_job(studio, [m["id"]])["people_policy"] == "consented"


def test_other_tenant_media_cannot_be_used(lib, studio, db):
    m = up(lib)
    no_people(lib, m)
    s2 = StudioService(db, now=NOW, router=MarketingAIRouter(env={}))
    assert err(s2.create_job, jwt(OTHER), T2, {"brief": BRIEF, "real_media_percent": 50, "ai_media_percent": 50,
                                               "quality_tier": "draft", "maximum_cost": 1,
                                               "media_ids": [m["id"]]}) == "not_found"


# ================================================================== mezcla y flujo completo
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
    full = studio.job(jwt(OWNER), T1, j["id"])
    scenes = [o for o in full["outputs"] if o["kind"] == "scene"]
    assert len(scenes) == 6 and sum(o["duration_ms"] for o in scenes) == 30000
    assert [e["to_status"] for e in full["events"]] == ["draft", "awaiting_generation_approval", "queued",
                                                       "processing", "succeeded"]
    out = studio.send_to_approval(jwt(OWNER), T1, j["id"], "Reel octubre")
    assert out["content"]["status"] == "review" and out["content"]["format"] == "reel"
    assert db.tables["marketing_publications"] == []                          # nunca publica
    assert err(studio.send_to_approval, jwt(OWNER), T1, j["id"], "x") == "already_sent"


def test_retry_never_double_charges(lib, studio, db):
    j = new_job(studio, [], real=0, ai=100)
    j = studio.estimate_job(jwt(OWNER), T1, j["id"])
    j = studio.approve_job(jwt(OWNER), T1, j["id"], True)
    mock = studio.adapters["mock"]
    studio.process_job(jwt(OWNER), T1, j["id"])
    n = mock.submissions
    assert err(studio.process_job, jwt(OWNER), T1, j["id"]) == "invalid_transition"
    assert mock.submissions == n
    keys = [u["idempotency_key"] for u in db.tables["marketing_model_usage"]]
    assert len(keys) == len(set(keys))
    # misma idempotency_key en el alta → mismo trabajo
    k = "retry-key-0123456789"
    assert new_job(studio, [], 0, 100, idempotency_key=k)["id"] == new_job(studio, [], 0, 100, idempotency_key=k)["id"]


def test_cancel_and_fail_keep_evidence(lib, studio, db):
    j = new_job(studio, [], real=0, ai=100)
    j = studio.cancel_job(jwt(OWNER), T1, j["id"])
    assert j["status"] == "cancelled" and j["error_code"] == "cancelled_by_user"
    assert err(studio.reopen_job, jwt(OWNER), T1, j["id"]) == "invalid_transition"


def test_limits_checked_before_any_spend(lib, studio, db):
    db.tables["marketing_settings"][0]["ai_generation_enabled"] = False
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [], 0, 100)["id"])
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "generation_disabled"
    db.tables["marketing_settings"][0].update({"ai_generation_enabled": True, "monthly_generation_job_limit": 0})
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "limit_monthly_generation_job_limit"
    db.tables["marketing_settings"][0].update({"monthly_generation_job_limit": 5,
                                               "monthly_generated_video_seconds_limit": 10})
    assert err(studio.approve_job, jwt(OWNER), T1, j["id"], True) == "limit_monthly_generated_video_seconds_limit"
    assert db.tables["marketing_model_usage"] == []


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
    j = new_job(studio, [], 0, 100)
    j = studio.estimate_job(jwt(OWNER), T1, j["id"])
    j = studio.approve_job(jwt(OWNER), T1, j["id"], True)
    assert studio.process_job(jwt(OWNER), T1, j["id"])["status"] == "succeeded"


def test_job_status_revalidates_permissions(studio, db):
    j = new_job(studio, [], 0, 100)
    db.tables["tenant_users"][0]["active"] = False                  # el owner pierde acceso
    assert err(studio.job, jwt(OWNER), T1, j["id"]) == "forbidden"
    assert err(studio.job, jwt(OTHER), T2, j["id"]) == "not_found"


def test_queued_always_requires_approval():
    from services.marketing_domain import DomainError
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


def test_supabase_storage_sign_builds_short_lived_url_without_leaking(monkeypatch):
    import httpx
    from services.supabase_admin import SupabaseAdmin
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return R(200, {"signedURL": "/object/sign/marketing-assets/a/b.png?token=t"})
    monkeypatch.setattr(httpx, "post", fake_post)
    sa = SupabaseAdmin(url="https://proj.supabase.co", service_key="service-key-123")
    url = sa.storage_sign("marketing-assets", "a/b.png", 300)
    assert url == "https://proj.supabase.co/storage/v1/object/sign/marketing-assets/a/b.png?token=t"
    assert calls == [("https://proj.supabase.co/storage/v1/object/sign/marketing-assets/a/b.png", {"expiresIn": 300})]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: R(400, {"message": "service-key-123"}))
    with pytest.raises(RuntimeError) as e:
        sa.storage_sign("marketing-assets", "a/b.png", 300)
    assert "service-key-123" not in str(e.value) and "a/b.png" not in str(e.value)
