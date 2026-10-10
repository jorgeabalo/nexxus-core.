"""
AITA Marketing (Fase 2): Biblioteca privada — permisos, archivos, antivirus, borrado y privacidad.
Sin red: Supabase y Storage en memoria; proveedores reales apagados. Los trabajos: test_marketing_studio_jobs.py.
"""
import struct
import zlib

import pytest

from services import marketing_media_files as mf
from services.marketing_ai_router import MarketingAIRouter, MockProvider, OmniRouteAdapter, DisabledLocalTools
from services.marketing_library import LibraryService
from services.marketing_privacy import MockFaceDetector
from services.marketing_studio import StudioService
from services.member_portal import PortalError
from marketing_fakes import MarketingRpcMixin
from test_marketing import FakeDB, NOW, T1, T2, OWNER, MANAGER, STAFF, OTHER, MEMBER


def png(w=1080, h=1920, extra=b""):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + extra
            + chunk(b"IDAT", zlib.compress(b"\x00\x80")) + chunk(b"IEND", b""))


def mp4_with_duration(seconds, timescale=1000, pad=0):
    mvhd = struct.pack(">I4sB3xIIII", 32, b"mvhd", 0, 0, 0, timescale, int(seconds * timescale))
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 0)
    mdat = struct.pack(">I4s", 8 + pad, b"mdat") + b"\x00" * pad
    return ftyp + moov + mdat


PNG = png()
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
GIF = b"GIF89a" + struct.pack("<HH", 10, 20) + b"\x00" * 32 + b"\x3b"
_WEBP_BODY = b"WEBPVP8 " + b"\x00" * 10 + struct.pack("<HH", 300, 200) + b"\x00" * 16
WEBP = b"RIFF" + struct.pack("<I", len(_WEBP_BODY)) + _WEBP_BODY
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 20 + b"webm" + b"\x00" * 40


class StudioDB(MarketingRpcMixin, FakeDB):
    def __init__(self):
        super().__init__()
        self._rpc_init(NOW.isoformat())
        for t in ("marketing_media", "marketing_media_derivatives", "marketing_media_events", "marketing_generation_jobs",
                  "marketing_generation_job_events", "marketing_generation_inputs", "marketing_generation_outputs",
                  "marketing_model_usage"):
            self.tables[t] = []
        for s in self.tables["marketing_settings"]:
            s.update({"ai_generation_enabled": True, "max_upload_bytes": 10_000_000,
                      "library_storage_limit_bytes": 1073741824, "monthly_generation_job_limit": 5,   # 1 GiB explícito
                      "monthly_regeneration_limit": 2, "monthly_generated_image_limit": 10,
                      "monthly_generated_video_seconds_limit": 600, "monthly_ai_cost_limit": 20})
        self.storage, self.streamed, self.removed = {}, [], []

    def storage_upload(self, bucket, key, data, mime):
        if (bucket, key) in self.storage:
            raise RuntimeError("Supabase storage POST -> 409: Duplicate")     # x-upsert=false
        self.storage[(bucket, key)] = (data, mime)

    def storage_stream(self, bucket, key, chunk_size=65536, byte_range=None):
        data = self.storage[(bucket, key)][0]
        self.streamed.append((key, byte_range))
        if byte_range:
            data = data[byte_range[0]:byte_range[1] + 1]
        return iter([data[i:i + chunk_size] for i in range(0, len(data), chunk_size)])

    def storage_remove(self, bucket, key):
        self.removed.append(key)
        self.storage.pop((bucket, key), None)

    def update(self, table, filters, values):
        if table in ("marketing_generation_job_events", "marketing_generation_inputs", "marketing_model_usage",
                     "marketing_media_events"):
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


def consented(lib, m):
    return lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "contains_minors": False,
                                                  "people_policy": "consented", "consent_status": "granted"})


BRIEF = {"objective": "new_members", "audience": "seniors_60_plus", "style": "energetic", "duration_seconds": 30,
         "script": {"hook": "Muévete con confianza", "body": "Clases guiadas", "cta": "Reserva tu clase"},
         "targets": ["instagram", "tiktok"]}


def new_job(studio, media_ids, real=50, ai=50, who=OWNER, **kw):
    body = {"brief": BRIEF, "real_media_percent": real, "ai_media_percent": ai, "quality_tier": "draft",
            "maximum_cost": 5, "media_ids": media_ids, "scenes": 6, **kw}
    return studio.create_job(jwt(who), T1, body)


def approved_job(studio, media_ids=(), real=0, ai=100):
    j = new_job(studio, list(media_ids), real, ai)
    studio.estimate_job(jwt(OWNER), T1, j["id"])
    return studio.approve_job(jwt(OWNER), T1, j["id"], True)


# ================================================================== permisos
@pytest.mark.parametrize("who", [STAFF, MEMBER, OTHER])
def test_only_owner_manager_of_tenant(lib, studio, who):
    assert err(lib.library, jwt(who), T1) == "forbidden"
    assert err(up, lib, who=who) == "forbidden"
    assert err(lib.upload_limit, jwt(who), T1) == "forbidden"
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
    assert m["contains_minors"] is None and m["validation_status"] == "passed"
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
    # truncados
    (PNG[:40], "a.png", "image/png", "truncated_file"),
    (PNG[:-12], "a.png", "image/png", "truncated_file"),
    (JPEG[:-2], "a.jpg", "image/jpeg", "truncated_file"),
    (GIF[:-1], "a.gif", "image/gif", "truncated_file"),
    (WEBP[:-10], "a.webp", "image/webp", "truncated_file"),
    (b"\x00\x00\x00\x18ftypisom" + b"\x00" * 12 + b"\x00\x00\x10\x00mdat" + b"\x00" * 20, "a.mp4", "video/mp4", "truncated_file"),
    # polyglot: cabecera válida con contenido activo o un ZIP escondido
    (png(extra=b"\x00\x00\x00\x20tEXt<script>alert(document.cookie)</script>"), "a.png", "image/png", "forbidden_file_type"),
    (GIF[:-1] + b"<html><body onload=x()>" + b"\x3b", "a.gif", "image/gif", "forbidden_file_type"),
    (JPEG[:-2] + b"<?php system($_GET[1]); ?>" + b"\xff\xd9", "a.jpg", "image/jpeg", "forbidden_file_type"),
    (PNG + b"PK\x03\x04payload" + b"PK\x05\x06" + b"\x00" * 18, "a.png", "image/png", "forbidden_file_type"),
    (JPEG[:-2] + b"javascript:alert(1)" + b"\xff\xd9", "a.jpg", "image/jpeg", "forbidden_file_type"),
    # metadatos falsos o excesivos
    (png(w=0, h=100), "a.png", "image/png", "invalid_media_metadata"),
    (png(w=70000, h=70000), "a.png", "image/png", "invalid_media_metadata"),
    (mp4_with_duration(36000), "a.mp4", "video/mp4", "invalid_media_metadata"),     # 10 h en 80 bytes
    (mp4_with_duration(120), "a.mp4", "video/mp4", "invalid_media_metadata"),       # 2 min en 80 bytes
])
def test_rejected_files(lib, db, data, name, mime, code):
    assert err(up, lib, data, name, mime) == code
    assert db.storage == {} and db.tables["marketing_media"] == []


def test_plausible_video_duration_accepted(lib):
    m = up(lib, mp4_with_duration(2, pad=4000), "clip.mp4", "video/mp4")
    assert m["duration_ms"] == 2000


def test_parsers_are_bounded():
    # JPEG con 2 MB de metadatos antes del tamaño: no se recorre más allá de la ventana
    big = b"\xff\xd8" + (b"\xff\xe1\xff\xff" + b"\x00" * 65533) * 32 + b"\xff\xc0\x00\x11\x08\x00\x10\x00\x20" + b"\xff\xd9"
    assert mf.dimensions(big, "image/jpeg") == (None, None, None)
    # MP4 con cajas moov anidadas sin fin: profundidad acotada, sin RecursionError
    inner = b""
    for _ in range(2000):
        inner = struct.pack(">I4s", 8 + len(inner), b"moov") + inner
    assert mf.dimensions(inner, "video/mp4") == (None, None, None)
    many = b"".join(struct.pack(">I4s", 8, b"free") for _ in range(20000))
    assert mf.dimensions(many, "video/mp4") == (None, None, None)


def test_too_large_and_storage_limit(lib, db):
    db.tables["marketing_settings"][0]["max_upload_bytes"] = 50
    assert lib.upload_limit(jwt(OWNER), T1) == 50
    assert err(up, lib) == "file_too_large"
    db.tables["marketing_settings"][0].update({"max_upload_bytes": 10_000, "library_storage_limit_bytes": 50})
    assert err(up, lib) == "file_too_large"
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = len(PNG)
    up(lib)
    assert lib.library(jwt(OWNER), T1)["storage"]["state"] == "full"
    assert err(lib.upload_limit, jwt(OWNER), T1) == "limit_library_storage"


@pytest.mark.parametrize("limit,used,state", [(0, 0, "disabled"), (None, 10, "unlimited"), (100, 10, "enabled"),
                                              (100, 100, "full")])
def test_library_states(limit, used, state):
    from services import marketing_jobs_domain as jd
    assert jd.library_state(limit, used) == state


def test_library_disabled_by_default_until_operator_sets_quota(lib, db):
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = 0
    data = lib.library(jwt(OWNER), T1)
    assert data["storage"]["state"] == "disabled" and data["can_upload"] is False
    assert err(lib.upload_limit, jwt(OWNER), T1) == "library_disabled"
    assert err(up, lib) == "library_disabled" and db.storage == {}
    db.tables["marketing_settings"][0]["library_storage_limit_bytes"] = None        # null = sin límite
    assert lib.library(jwt(OWNER), T1)["storage"]["state"] == "unlimited" and up(lib)["id"]


@pytest.mark.parametrize("name", ["../../etc/passwd.png", "..\\..\\x.png", "/abs/olute.png", "a/../../b.png",
                                  "%2e%2e%2fx.png", "con:x.png", "ñandú foto (1).png"])
def test_path_traversal_and_names_sanitized(lib, name):
    m = up(lib, png(w=len(name) + 1), name)
    parts = m["storage_path"].split("/")
    assert len(parts) == 4 and parts[0] == T1 and parts[1] == "originals" and ".." not in m["storage_path"]
    assert all(ch.isalnum() or ch in "._-" for ch in parts[3])


def test_other_tenant_paths_never_built_or_read(lib, db):
    m = up(lib)
    assert mf.path_belongs_to(m["storage_path"], T1) and not mf.path_belongs_to(m["storage_path"], T2)
    row = db.tables["marketing_media"][0]                        # fila manipulada hacia otro tenant
    row["storage_path"] = f"{T2}/originals/{m['id']}/foto.png"
    assert err(lib.content, jwt(OWNER), T1, m["id"]) == "not_found"
    row["storage_path"] = f"{T1}/originals/{m['id']}/../../{T2}/x.png"
    assert err(lib.content, jwt(OWNER), T1, m["id"]) == "not_found"
    assert db.streamed == []
    with pytest.raises(mf.MediaFileError):
        mf.original_path("../" + T2, m["id"], "x.png")


def test_duplicate_upload_reuses_original(lib, db):
    a, b = up(lib), up(lib)
    assert b["duplicate"] and a["id"] == b["id"] and len(db.storage) == 1


def test_preview_is_streamed_by_backend_only_for_own_tenant(lib, db):
    m = up(lib)
    res = lib.content(jwt(OWNER), T1, m["id"])
    assert res["status"] == 200 and b"".join(res["chunks"]) == PNG and res["headers"]["Content-Type"] == "image/png"
    # el owner de T1 es manager de T2: aun así no puede ver el archivo de T1 pasando T2
    assert err(lib.content, jwt(OWNER), T2, m["id"]) == "not_found"
    assert err(lib.content, jwt(OTHER), T2, m["id"]) == "not_found"


def test_original_never_overwritten(lib, db):
    m = up(lib)
    with pytest.raises(RuntimeError):
        db.storage_upload("marketing-assets", m["storage_path"], b"otra cosa", "image/png")
    assert db.storage[("marketing-assets", m["storage_path"])][0] == PNG


def test_archive_keeps_file(lib, db):
    m = up(lib)
    assert lib.set_archived(jwt(OWNER), T1, m["id"], True)["processing_status"] == "archived"
    assert lib.set_archived(jwt(OWNER), T1, m["id"], False)["processing_status"] == "ready"
    assert len(db.storage) == 1


# ================================================================== antivirus
def test_no_scanner_means_unavailable_never_clean(lib, db):
    m = up(lib)
    assert m["malware_scan_status"] == "unavailable" and m["validation_status"] == "passed"

    class Liar:
        name = "unavailable"

        def scan(self, data):
            return "clean"
    m2 = LibraryService(db, now=NOW, scanner=Liar()).upload(jwt(OWNER), T1, "b.gif", "image/gif", GIF)
    assert m2["malware_scan_status"] == "unavailable"
    assert lib.library(jwt(OWNER), T1)["malware_scanner"] == "unavailable"


# ================================================================== borrado controlado
def test_controlled_delete_with_audit(lib, db):
    m = up(lib)
    assert err(lib.delete, jwt(OWNER), T1, m["id"], "x", False) == "confirmation_required"
    out = lib.delete(jwt(MANAGER), T1, m["id"], "Pedido del cliente", True)
    assert out["processing_status"] == "deleted" and out["retention_status"] == "purged" and out["storage_removed"]
    assert db.storage == {} and db.removed == [m["storage_path"]]
    row = db.tables["marketing_media"][0]
    assert row["deleted_by"] == MANAGER and row["delete_reason"] == "Pedido del cliente"
    assert row["purge_reason"] == "user_deleted" and row["original_filename"] is None and row["metadata"] == {}
    assert row["storage_path"] == f"{T1}/originals/{m['id']}/purged" and row["checksum"] == m["checksum"]
    assert [e["action"] for e in db.tables["marketing_media_events"]] == ["upload", "delete", "purged"]
    assert lib.library(jwt(OWNER), T1)["items"] == []
    assert err(lib.content, jwt(OWNER), T1, m["id"]) == "not_found"
    assert up(lib)["id"] != m["id"]                         # se puede volver a subir


def test_delete_blocked_while_used_by_active_job(lib, studio, db):
    m = up(lib)
    no_people(lib, m)
    j = new_job(studio, [m["id"]])
    assert err(lib.delete, jwt(OWNER), T1, m["id"], "", True) == "media_in_use"
    assert db.storage
    studio.cancel_job(jwt(OWNER), T1, j["id"])
    assert lib.delete(jwt(OWNER), T1, m["id"], "", True)["processing_status"] == "deleted"


def test_delete_other_tenant_or_staff(lib):
    m = up(lib)
    assert err(lib.delete, jwt(OTHER), T2, m["id"], "", True) == "not_found"
    assert err(lib.delete, jwt(STAFF), T1, m["id"], "", True) == "forbidden"


# ================================================================== privacidad
def test_unknown_people_defaults_to_exclude_and_blocks_use(lib, studio):
    m = up(lib)
    assert err(new_job, studio, [m["id"]]) == "minors_excluded"


@pytest.mark.parametrize("body,code", [
    ({"contains_people": None, "people_policy": "no_people"}, "no_people_requires_confirmation"),
    ({"contains_people": True, "contains_minors": False, "people_policy": "consented", "consent_status": "pending"},
     "consent_required"),
    ({"contains_people": True, "people_policy": "magic"}, "invalid_people_policy"),
    ({"contains_people": True, "contains_minors": None, "people_policy": "consented", "consent_status": "granted"},
     "minors_excluded"),
    ({"contains_people": True, "contains_minors": True, "people_policy": "anonymize"}, "minors_excluded"),
    ({"contains_people": None, "contains_minors": None, "people_policy": "anonymize"}, "minors_excluded"),
    ({"contains_people": False, "contains_minors": True, "people_policy": "no_people"}, "no_people_requires_confirmation"),
    ({"contains_people": True, "contains_minors": False, "people_policy": "consented", "consent_status": "revoked"},
     "consent_revoked"),
])
def test_classification_rules(lib, body, code):
    m = up(lib)
    assert err(lib.classify, jwt(OWNER), T1, m["id"], body) == code


def test_minors_may_be_classified_as_excluded(lib, studio):
    m = up(lib)
    r = lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "contains_minors": True, "people_policy": "exclude"})
    assert r["privacy_class"] == "restricted"
    assert err(new_job, studio, [m["id"]]) == "minors_excluded"


def test_consent_can_be_revoked_and_blocks_new_and_pending_jobs(lib, studio, db):
    m = up(lib)
    consented(lib, m)
    pending = new_job(studio, [m["id"]])
    studio.estimate_job(jwt(OWNER), T1, pending["id"])
    r = lib.revoke_consent(jwt(OWNER), T1, m["id"])
    assert r["consent_status"] == "revoked" and r["people_policy"] == "exclude"
    assert r["retention_status"] == "purge_pending" and r["cancelled_jobs"] == [pending["id"]]
    assert [e["action"] for e in db.tables["marketing_media_events"]][-3:] == ["consent_revoke", "jobs_cancelled",
                                                                               "purge_requested"]
    assert err(new_job, studio, [m["id"]]) == "consent_revoked"                   # nuevos: bloqueados
    job = studio.job(jwt(OWNER), T1, pending["id"])                              # pendientes: cancelados
    assert job["status"] == "cancelled" and job["error_code"] == "consent_revoked"
    assert err(studio.approve_job, jwt(OWNER), T1, pending["id"], True) == "invalid_transition"
    assert err(lib.content, jwt(OWNER), T1, m["id"]) == "not_found"              # sin acceso al instante


def test_consent_revoked_after_approval_fails_job_before_sending(lib, studio, db):
    m = up(lib)
    consented(lib, m)
    j = approved_job(studio, [m["id"]], 50, 50)
    # aunque la cancelación automática no llegara a ejecutarse, el worker vuelve a comprobar
    db.tables["marketing_media"][0].update({"consent_status": "revoked", "people_policy": "exclude"})
    out = studio.process_job(jwt(OWNER), T1, j["id"])
    assert out["status"] == "failed" and out["error_code"] == "consent_revoked"
    assert db.tables["marketing_model_usage"] == [] and studio.adapters["mock"].submissions == 0


def test_mock_anonymization_never_counts_as_anonymized(lib, studio, db):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "contains_minors": False, "people_policy": "anonymize"})
    assert err(new_job, studio, [m["id"]]) == "anonymization_required"
    der = lib.anonymize(jwt(OWNER), T1, m["id"], "pixelate_faces")
    assert der["status"] == "mock_only" and der["is_mock"] and der["storage_path"] is None
    assert der["metadata"]["guarantee"] == "not_anonymized" and "pixelation_not_guaranteed" in der["warnings"]
    assert db.storage[("marketing-assets", m["storage_path"])][0] == PNG          # original intacto
    assert err(lib.review_derivative, jwt(OWNER), T1, der["id"], True, True) == "mock_derivative"
    assert err(new_job, studio, [m["id"]], derivative_ids=[der["id"]]) == "anonymization_required"
    db.tables["marketing_media_derivatives"][0]["status"] = "ready"              # aunque se forzara la fila
    assert err(new_job, studio, [m["id"]], derivative_ids=[der["id"]]) == "anonymization_required"
    assert lib.review_derivative(jwt(OWNER), T1, der["id"], False)["status"] == "rejected"


def test_without_detector_derivative_awaits_processing(db):
    lib = LibraryService(db, now=NOW)                         # detector por defecto: no disponible
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "contains_minors": False, "people_policy": "anonymize"})
    der = lib.anonymize(jwt(OWNER), T1, m["id"], "silhouette")
    assert der["status"] == "awaiting_processing" and not der["is_mock"]
    assert err(lib.review_derivative, jwt(OWNER), T1, der["id"], True, True) == "derivative_not_processed"


def test_invalid_anonymization_method(lib):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "contains_minors": False, "people_policy": "anonymize"})
    assert err(lib.anonymize, jwt(OWNER), T1, m["id"], "face_swap") == "invalid_anonymization_method"


def test_consented_people_allowed(lib, studio):
    m = up(lib)
    lib.classify(jwt(OWNER), T1, m["id"], {"contains_people": True, "contains_minors": False, "people_policy": "consented",
                                           "consent_status": "granted", "consent_note": "Formulario 12/10"})
    assert new_job(studio, [m["id"]])["people_policy"] == "consented"


def test_other_tenant_media_cannot_be_used(lib, studio, db):
    m = up(lib)
    no_people(lib, m)
    s2 = StudioService(db, now=NOW, router=MarketingAIRouter(env={}))
    assert err(s2.create_job, jwt(OTHER), T2, {"brief": BRIEF, "real_media_percent": 50, "ai_media_percent": 50,
                                               "quality_tier": "draft", "maximum_cost": 1,
                                               "media_ids": [m["id"]]}) == "not_found"


def test_unscanned_real_media_never_routed_externally(lib, studio):
    m = up(lib)
    no_people(lib, m)
    j = studio.estimate_job(jwt(OWNER), T1, new_job(studio, [m["id"]], 50, 50, adapt_real=True)["id"])
    assert j["request_metadata"]["estimate"]["external_calls"] == 0
    assert j["request_metadata"]["malware_clean"] is False
