"""
AITA Marketing: vídeos grandes a la Biblioteca por partes (bucket S3 de Railway).
Sin red: base y bucket en memoria. Mismas puertas y validaciones que la subida normal.
"""
import struct
import uuid
from datetime import timedelta

import pytest

from services import marketing_large_upload as lu
from services import object_store
from services.marketing_large_upload import LargeUploadService, expire_stale
from services.marketing_library import LibraryService
from services.member_portal import PortalError
from test_marketing import NOW, T1, T2, OWNER, MANAGER, STAFF
from test_marketing_studio import PNG, StudioDB, jwt

PART = 1024


class FakeStore:
    bucket = "fake"

    def __init__(self):
        self.objects, self.mp, self.aborted = {}, {}, []

    def size(self, k):
        return len(self.objects[k]) if k in self.objects else None

    def put(self, k, d, mime):
        if k in self.objects:
            raise RuntimeError("Bucket PUT -> 409: Duplicate")
        self.objects[k] = d

    def get(self, k):
        return self.objects[k]

    def read_range(self, k, s, e):
        return self.objects[k][s:e + 1]

    def stream(self, k, chunk_size=65536, byte_range=None):
        d = self.objects[k]
        if byte_range:
            d = d[byte_range[0]:byte_range[1] + 1]
        return iter([d[i:i + chunk_size] for i in range(0, len(d), chunk_size)])

    def remove(self, k):
        self.objects.pop(k, None)

    def create_multipart(self, k, mime):
        uid = str(uuid.uuid4())
        self.mp[uid] = {"key": k, "parts": {}}
        return uid

    def upload_part(self, k, uid, n, d):
        assert self.mp[uid]["key"] == k
        self.mp[uid]["parts"][n] = d
        return f"etag-{n}"

    def complete_multipart(self, k, uid, etags):
        parts = self.mp.pop(uid)["parts"]
        assert etags == [f"etag-{i}" for i in range(1, len(parts) + 1)]
        self.objects[k] = b"".join(parts[i] for i in sorted(parts))

    def abort_multipart(self, k, uid):
        self.mp.pop(uid, None)
        self.aborted.append(uid)


class BigDB(StudioDB):
    def _rpc_marketing_extend_storage_reservation(self, a):
        for r in self.tables["marketing_storage_reservations"]:
            if r["tenant_id"] == a["p_tenant"] and r["reservation_key"] == a["p_key"] and self._live(r):
                return True
        return False


def mp4(seconds=3, pad=6000, tail=b""):
    mvhd = struct.pack(">I4sB3xIIII", 28, b"mvhd", 0, 0, 0, 1000, int(seconds * 1000))
    tkhd = struct.pack(">I4s", 92, b"tkhd") + b"\x00" * 76 + struct.pack(">II", 1080 << 16, 1920 << 16)
    trak = struct.pack(">I4s", 8 + len(tkhd), b"trak") + tkhd
    moov = struct.pack(">I4s", 8 + len(mvhd) + len(trak), b"moov") + mvhd + trak
    ftyp = struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 0)
    body = b"\x00" * pad + tail
    return ftyp + moov + struct.pack(">I4s", 8 + len(body), b"mdat") + body


@pytest.fixture(autouse=True)
def small_parts(monkeypatch):
    monkeypatch.setattr(lu, "PART_BYTES", PART)


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def db(store):
    return object_store.StoreRoutedDB(BigDB(), store)


@pytest.fixture
def big(db):
    return LargeUploadService(db, now=NOW)


def err(fn, *a, **kw):
    with pytest.raises(PortalError) as e:
        fn(*a, **kw)
    return e.value.code


def start(big, data, name="reel.mp4", mime="video/mp4", who=OWNER, tenant=T1):
    return big.start(jwt(who), tenant, {"filename": name, "mime_type": mime, "byte_size": len(data)})


def send(big, s, data, who=OWNER, tenant=T1, upto=None):
    for n in range(1, (upto or s["parts"]) + 1):
        big.put_part(jwt(who), tenant, s["upload_id"], n, data[(n - 1) * PART:n * PART])


def upload(big, data, **kw):
    s = start(big, data, **kw)
    send(big, s, data)
    return big.complete(jwt(OWNER), T1, s["upload_id"])


def reservation(db, key_prefix="upload:"):
    return [r for r in db.tables["marketing_storage_reservations"] if r["reservation_key"].startswith(key_prefix)]


def session(db, sid):
    return next(x for x in db.tables["marketing_upload_sessions"] if x["id"] == sid)


def test_full_upload_creates_ready_video_and_consumes_reservation(big, db, store):
    data = mp4()
    m = upload(big, data)
    assert m["processing_status"] == "ready" and m["media_type"] == "video" and m["byte_size"] == len(data)
    assert (m["width"], m["height"], m["duration_ms"]) == (1080, 1920, 3000)
    assert m["malware_scan_status"] == "unavailable" and m["people_policy"] == "exclude"
    assert store.objects[m["storage_path"]] == data and not store.mp
    assert [r["status"] for r in reservation(db)] == ["consumed"]
    assert db.tables["marketing_upload_sessions"][0]["status"] == "completed"


def test_needs_the_bucket(store):
    plain = LargeUploadService(BigDB(), now=NOW)
    assert err(plain.start, jwt(OWNER), T1, {"filename": "a.mp4", "mime_type": "video/mp4", "byte_size": 10}) \
        == "large_upload_unavailable"
    assert LibraryService(BigDB(), now=NOW).library(jwt(OWNER), T1)["large_upload"] is None


def test_library_announces_large_uploads(db):
    info = LibraryService(db, now=NOW).library(jwt(OWNER), T1)["large_upload"]
    assert info == {"max_bytes": 10_000_000, "part_bytes": lu.PART_BYTES}


@pytest.mark.parametrize("name,mime,code", [("foto.png", "image/png", "unsupported_extension"),
                                            ("reel.mp4", "video/quicktime", "mime_mismatch"),
                                            ("reel", "video/mp4", "unsupported_extension")])
def test_only_videos_with_matching_type(big, name, mime, code):
    assert err(start, big, mp4(), name, mime) == code


def test_size_limit_and_permissions(big):
    assert err(big.start, jwt(OWNER), T1, {"filename": "a.mp4", "mime_type": "video/mp4",
                                           "byte_size": 10_000_001}) == "file_too_large"
    assert err(big.start, jwt(OWNER), T1, {"filename": "a.mp4", "mime_type": "video/mp4", "byte_size": 0}) == "empty_file"
    assert err(start, big, mp4(), who=STAFF) in ("forbidden", "marketing_forbidden", "role_forbidden")


def test_first_part_must_be_the_declared_format(big, db, store):
    fake = PNG + b"\x00" * 3000
    s = start(big, fake)
    assert err(big.put_part, jwt(OWNER), T1, s["upload_id"], 1, fake[:PART]) == "extension_mismatch"
    assert session(db, s["upload_id"])["status"] == "failed" and store.aborted and not store.mp
    assert [r["status"] for r in reservation(db)] == ["released"]


def test_parts_in_order_exact_size_and_retries_are_harmless(big, db):
    data = mp4()
    s = start(big, data)
    sid = s["upload_id"]
    assert err(big.put_part, jwt(OWNER), T1, sid, 2, data[PART:2 * PART]) == "upload_part_out_of_order"
    assert err(big.put_part, jwt(OWNER), T1, sid, 1, data[:PART - 1]) == "invalid_part_size"
    assert err(big.put_part, jwt(OWNER), T1, sid, 99, data[:PART]) == "invalid_part"
    big.put_part(jwt(OWNER), T1, sid, 1, data[:PART])
    assert big.put_part(jwt(OWNER), T1, sid, 1, data[:PART])["parts_done"] == 1        # reintento
    assert session(db, sid)["etags"] == ["etag-1"]
    assert err(big.complete, jwt(OWNER), T1, sid) == "upload_incomplete"


def test_sessions_are_private_to_their_uploader_and_tenant(big):
    data = mp4()
    s = start(big, data)
    assert err(big.put_part, jwt(MANAGER), T1, s["upload_id"], 1, data[:PART]) == "upload_not_found"
    assert err(big.put_part, jwt(OWNER), T2, s["upload_id"], 1, data[:PART]) == "upload_not_found"
    assert err(big.complete, jwt(OWNER), T1, "no-es-uuid") == "upload_not_found"


def test_truncated_video_is_removed_and_space_released(big, db, store):
    data = mp4()
    broken = data[:-10] + b"\x00" * 10
    broken = broken[:len(broken) - 200]                     # mdat promete más bytes de los que hay
    s = start(big, broken)
    send(big, s, broken)
    assert err(big.complete, jwt(OWNER), T1, s["upload_id"]) == "truncated_file"
    assert not store.objects and session(db, s["upload_id"])["status"] == "failed"
    assert [r["status"] for r in reservation(db)] == ["released"]
    assert not db.tables.get("marketing_media")


def test_active_content_at_the_end_is_rejected(big, store):
    data = mp4(tail=b"<script>alert(1)</script>")
    s = start(big, data)
    send(big, s, data)
    assert err(big.complete, jwt(OWNER), T1, s["upload_id"]) == "forbidden_file_type"
    assert not store.objects


def test_impossible_duration_is_rejected(big):
    data = mp4(seconds=600, pad=3000)                        # 10 minutos en 3 KB: duración falsa
    s = start(big, data)
    send(big, s, data)
    assert err(big.complete, jwt(OWNER), T1, s["upload_id"]) == "invalid_media_metadata"


def test_same_video_twice_is_not_stored_twice(big, db, store):
    data = mp4()
    first = upload(big, data)
    again = upload(big, data)
    assert again["duplicate"] and again["id"] == first["id"]
    assert list(store.objects) == [first["storage_path"]]
    assert sorted(r["status"] for r in reservation(db)) == ["consumed", "released"]


def test_abort_cancels_and_releases(big, db, store):
    data = mp4()
    s = start(big, data)
    send(big, s, data, upto=2)
    assert big.abort(jwt(OWNER), T1, s["upload_id"]) == {"status": "aborted"}
    assert store.aborted and not store.mp and [r["status"] for r in reservation(db)] == ["released"]
    assert err(big.put_part, jwt(OWNER), T1, s["upload_id"], 3, data[2 * PART:3 * PART]) == "upload_closed"


def test_expired_reservation_stops_the_upload(big, db):
    data = mp4()
    s = start(big, data)
    for r in db.tables["marketing_storage_reservations"]:
        r["expires_at"] = (NOW - timedelta(minutes=1)).isoformat()
    assert err(big.put_part, jwt(OWNER), T1, s["upload_id"], 1, data[:PART]) == "upload_expired"
    assert session(db, s["upload_id"])["status"] == "expired"


def test_purger_cancels_idle_uploads(big, db, store):
    data = mp4()
    s = start(big, data)
    session(db, s["upload_id"])["updated_at"] = (NOW - timedelta(hours=3)).isoformat()
    assert expire_stale(db, NOW) == 1
    assert session(db, s["upload_id"])["status"] == "expired" and store.aborted
    assert expire_stale(BigDB(), NOW) == 0                              # sin bucket no hace nada


def test_store_routing_only_moves_the_library_bucket(store):
    class Base:
        def __init__(self):
            self.calls = []

        def storage_upload(self, bucket, key, data, mime):
            self.calls.append(bucket)

        def select(self, *a):
            return ["delegado"]
    base = Base()
    routed = object_store.StoreRoutedDB(base, store)
    routed.storage_upload("receipts", "k", b"x", "image/png")
    routed.storage_upload(object_store.MARKETING_BUCKET, "k", b"y", "video/mp4")
    assert base.calls == ["receipts"] and store.objects == {"k": b"y"}
    assert routed.select("t", {}) == ["delegado"]
    with pytest.raises(RuntimeError):
        routed.storage_upload(object_store.MARKETING_BUCKET, "k", b"z", "video/mp4")      # nunca sobrescribe


def test_bucket_config_is_all_or_nothing():
    assert object_store.config({}) is None
    with pytest.raises(object_store.StoreConfigError):
        object_store.config({"MARKETING_S3_BUCKET": "b"})
    full = {"MARKETING_S3_BUCKET": "b", "MARKETING_S3_ENDPOINT": "https://t3.storageapi.dev",
            "MARKETING_S3_ACCESS_KEY_ID": "id", "MARKETING_S3_SECRET_ACCESS_KEY": "secret"}
    assert object_store.config(full)["region"] == "auto"
    with pytest.raises(object_store.StoreConfigError):
        object_store.config({**full, "MARKETING_S3_ADDRESSING": "raro"})
