"""
AITA Marketing (Fase 2): lo que hace un reproductor real con un vídeo grande (40 MB), contra un
almacenamiento simulado que genera los bytes bajo demanda (nunca existe el archivo entero en memoria)
y registra cada rango pedido.

HEAD → primera respuesta 206 (bytes=0-) de la que el navegador solo lee el principio → avanzar a una
zona no descargada produce OTRA petición Range → retroceder → Content-Range y Content-Length exactos →
cerrar revoca la sesión al instante.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services import marketing_media_stream as mstream
from services.marketing_media_stream import MediaStreamService
from test_marketing import NOW, T1, OWNER
from test_marketing_media_stream import LIB, H, upload, open_session
from test_marketing_studio import StudioDB

SIZE = 40 * 1024 * 1024
CHUNK = 65536


def gen(start: int, end: int) -> bytes:
    """Contenido determinista del byte start..end (inclusivos), sin materializar el archivo."""
    return bytes((i * 31 + 7) % 251 for i in range(start, end + 1))


class RangeStorage:
    """Almacenamiento simulado: registra los rangos pedidos y cuenta los bytes realmente entregados."""
    def __init__(self):
        self.ranges, self.served, self.max_chunk = [], 0, 0

    def __call__(self, bucket, key, chunk_size=CHUNK, byte_range=None):
        self.ranges.append(byte_range)
        start, end = byte_range if byte_range else (0, SIZE - 1)

        def chunks():
            pos = start
            while pos <= end:
                part = gen(pos, min(pos + chunk_size, end + 1) - 1)
                self.served += len(part)
                self.max_chunk = max(self.max_chunk, len(part))
                pos += len(part)
                yield part
        return chunks()


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(mstream.INSECURE_FLAG, "1")
    db = StudioDB()
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    client = TestClient(main.app)
    video = upload(client)
    row = next(x for x in db.tables["marketing_media"] if x["id"] == video["id"])
    row["byte_size"] = SIZE                                              # el objeto en Storage mide 40 MB
    store = RangeStorage()
    monkeypatch.setattr(db, "storage_stream", store)
    return SimpleNamespace(db=db, client=client, video=video, store=store)


def first_chunk(svc, media_id, token, rng):
    """Como el navegador: abre la respuesta, lee solo el primer trozo y la cancela."""
    res = svc.stream(media_id, token, rng, False)
    it = res["chunks"]
    data = next(it)
    it.close()
    return res, data


def test_player_sequence_on_40mb_video(env):
    url, token = open_session(env.client, env.video)
    assert "?" not in url and token not in url

    h = env.client.head(url)                                             # 1. HEAD: sin tocar Storage
    assert h.status_code == 200 and h.headers["content-length"] == str(SIZE) and h.headers["accept-ranges"] == "bytes"
    assert env.store.ranges == []

    svc = MediaStreamService(env.db)                                     # 2. primera respuesta: 206 de bytes=0-
    res, data = first_chunk(svc, env.video["id"], token, "bytes=0-")
    assert res["status"] == 206 and res["headers"]["Content-Range"] == f"bytes 0-{SIZE - 1}/{SIZE}"
    assert res["headers"]["Content-Length"] == str(SIZE) and data == gen(0, CHUNK - 1)
    assert env.store.served == CHUNK                                     # el navegador cortó: no se leyó el resto

    far = 30_000_000                                                     # 3. avanzar a una zona no descargada
    res, data = first_chunk(svc, env.video["id"], token, f"bytes={far}-")
    assert res["status"] == 206 and res["headers"]["Content-Range"] == f"bytes {far}-{SIZE - 1}/{SIZE}"
    assert res["headers"]["Content-Length"] == str(SIZE - far) and data == gen(far, far + CHUNK - 1)

    back = env.client.get(url, headers={"Range": "bytes=1000000-1065535"})   # 4. retroceder (rango cerrado)
    assert back.status_code == 206 and back.content == gen(1_000_000, 1_065_535)
    assert back.headers["content-range"] == f"bytes 1000000-1065535/{SIZE}" and back.headers["content-length"] == "65536"
    tail = env.client.get(url, headers={"Range": "bytes=-1000"})
    assert tail.status_code == 206 and tail.headers["content-range"] == f"bytes {SIZE - 1000}-{SIZE - 1}/{SIZE}"
    assert tail.content == gen(SIZE - 1000, SIZE - 1)

    # Cada petición pidió a Storage SOLO su rango; nunca el archivo completo.
    assert env.store.ranges == [(0, SIZE - 1), (far, SIZE - 1), (1_000_000, 1_065_535), (SIZE - 1000, SIZE - 1)]
    assert None not in env.store.ranges
    assert env.store.served == 2 * CHUNK + 65536 + 1000 and env.store.served < SIZE // 100
    assert env.store.max_chunk <= CHUNK                                  # en memoria, como mucho un trozo

    r = env.client.post(f"{LIB}/{env.video['id']}/stream-revoke", json={"tenant_id": T1}, headers=H(OWNER))   # 5. cerrar
    assert r.status_code == 200 and "max-age=0" in r.headers["set-cookie"].lower()
    before = len(env.store.ranges)
    assert env.client.get(url, headers={"Range": "bytes=2000000-2000099"}).status_code == 404
    with pytest.raises(Exception) as e:
        svc.stream(env.video["id"], token, "bytes=0-1", False)          # el valor antiguo: revocado al instante
    assert getattr(e.value, "code", "") == "not_found"
    assert len(env.store.ranges) == before                               # después de cerrar no se toca Storage


def test_out_of_range_and_bad_ranges_on_large_file(env):
    url, _ = open_session(env.client, env.video)
    bad = env.client.get(url, headers={"Range": f"bytes={SIZE}-"})
    assert bad.status_code == 416 and bad.headers["content-range"] == f"bytes */{SIZE}"
    assert env.store.ranges == []                                        # 416 no toca Storage


def test_service_level_chunks_are_lazy(env):
    """Sin consumir el iterador no se genera ni un byte: la respuesta se transmite, no se carga."""
    s = MediaStreamService(env.db, now=NOW).issue_session(f"jwt-{OWNER}", T1, env.video["id"])
    res = MediaStreamService(env.db, now=NOW).stream(env.video["id"], s["token"], "bytes=0-", False)
    assert env.store.served == 0 and res["status"] == 206
    res["chunks"].close()
    assert env.store.served == 0
