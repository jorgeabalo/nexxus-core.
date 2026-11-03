"""Wan2GP opcional: apagado por defecto, solo texto sintético, un reintento no genera dos veces."""
import dataclasses
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.marketing_ai_catalog import load
from services.marketing_ai_router import MarketingAIRouter, RouteRequest, RouterError, Wan2GPAdapter, default_adapters
from services.marketing_reel_plan import video_prompt
from services.marketing_runtime import providers_disabled

ENV = {"WAN2GP_ENABLED": "true", "WAN2GP_BASE_URL": "https://gpu.example", "WAN2GP_TOKEN": "t" * 40}
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64


def wan_model(**kw):
    m = next(m for m in load().models if m.provider == "wan2gp")
    return dataclasses.replace(m, **{"estimated_cost": 0.01, **kw})          # precio que pondría el operador


def req(**kw):
    base = dict(tenant_id="t1", task_type="text_to_video", quality_tier="draft", maximum_cost=5.0,
                privacy_class="synthetic_only", idempotency_key="job-1234567890:2:text_to_video", units=6.0,
                duration=6, prompt="A sunny gym", output_requirements={"temp_dir": "t1/derivatives/j1/w-"})
    base.update(kw)
    return RouteRequest(**base)


class Resp:
    def __init__(self, data=None, body=b""):
        self.data, self.body = data, body

    def json(self):
        return self.data

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeBridge:
    def __init__(self, statuses=("running", "succeeded"), body=MP4):
        self.statuses, self.body = list(statuses), body
        self.posts, self.deleted = [], []

    def post(self, path, json):
        self.posts.append(json)
        return Resp({"id": "gpu-1", "status": "queued"})

    def get(self, path):
        return Resp({"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]})

    def stream(self, method, path):
        return Resp(body=self.body)

    def delete(self, path):
        self.deleted.append(path)


class FakeDB:
    def __init__(self):
        self.uploads = []

    def storage_upload(self, bucket, key, data, mime):
        self.uploads.append((bucket, key, len(data), mime))


def test_off_by_default():
    assert not next(m for m in load().models if m.provider == "wan2gp").enabled
    r = MarketingAIRouter(env={})
    assert r.route(req()).model.provider == "mock"
    res = Wan2GPAdapter(db=FakeDB(), env={}).submit(req(), wan_model())
    assert res.status == "failed" and res.error_code == "provider_disabled"
    assert "wan2gp" in default_adapters()


def test_switch_without_catalog_does_not_route():
    assert MarketingAIRouter(env=ENV).route(req()).model.provider == "mock"   # catálogo sigue cerrado


def test_real_model_beats_mock_when_both_enabled():
    cat = load()
    cat = dataclasses.replace(cat, models=tuple(dataclasses.replace(m, enabled=True, price_verified=True, estimated_cost=0.01)
                                                if m.provider == "wan2gp" else m for m in cat.models))
    route = MarketingAIRouter(catalog=cat, env=ENV).route(req())
    assert route.model.provider == "wan2gp" and route.estimated_cost == 0.06
    with pytest.raises(RouterError):            # sin presupuesto suficiente no se elige; tampoco se sube de nivel
        MarketingAIRouter(catalog=dataclasses.replace(cat, models=tuple(m for m in cat.models if m.provider != "mock")),
                          env=ENV).route(req(maximum_cost=0.01))


def test_generates_and_stores_temp_file():
    db, http = FakeDB(), FakeBridge()
    res = Wan2GPAdapter(db=db, env=ENV, client=http, sleep=lambda s: None).submit(req(), wan_model())
    assert res.status == "succeeded" and res.provider_job_id == "gpu-1" and res.actual_cost == 0.06
    f = res.output["files"][0]
    assert f["temp_path"].startswith("t1/derivatives/j1/w-") and f["kind"] == "video" and f["byte_size"] == len(MP4)
    assert db.uploads == [("marketing-assets", f["temp_path"], len(MP4), "video/mp4")]
    p = http.posts[0]
    assert p["prompt"] == "A sunny gym" and p["resolution"] == "480x832" and p["duration_seconds"] == 6
    assert p["idempotency_key"] == "job-1234567890:2:text_to_video" and "ultrasound" in p["negative_prompt"]
    assert http.deleted == ["/v1/jobs/gpu-1"]


def test_cost_capped_to_clip_length():
    res = Wan2GPAdapter(db=FakeDB(), env=ENV, client=FakeBridge(), sleep=lambda s: None).submit(
        req(units=30.0, duration=30), wan_model())
    assert res.actual_cost == 0.1                        # clip de 10 s, no 30


@pytest.mark.parametrize("kw", [dict(privacy_class="consented_people"), dict(task_type="image_to_video"),
                                dict(prompt="  ")])
def test_only_synthetic_text_to_video(kw):
    http = FakeBridge()
    res = Wan2GPAdapter(db=FakeDB(), env=ENV, client=http).submit(req(**kw), wan_model())
    assert res.status == "failed" and res.error_code == "privacy_blocked" and not http.posts


@pytest.mark.parametrize("bridge", [FakeBridge(statuses=("failed",)), FakeBridge(body=b"<html>not a video</html>")])
def test_failures_store_nothing(bridge):
    db = FakeDB()
    res = Wan2GPAdapter(db=db, env=ENV, client=bridge, sleep=lambda s: None).submit(req(), wan_model())
    assert res.status == "failed" and res.error_code == "provider_unavailable" and not db.uploads


def test_wan2gp_does_not_block_worker_guard():
    assert providers_disabled({**ENV})


def test_video_prompt_uses_validated_script():
    p = video_prompt({"style": "calm", "audience": "seniors_60_plus",
                      "script": {"hook": "Move better at 60.", "body": "Gentle strength classes", "cta": "Call us"}})
    assert "calm" in p and "older adults" in p and "Move better at 60. Gentle strength classes" in p
    assert "Call us" not in p and "No text" in p


# ---------------------------------------------------------------- puente (servidor GPU)
def bridge_module(monkeypatch, tmp_path):
    monkeypatch.setenv("WAN2GP_TOKEN", "k" * 40)
    monkeypatch.setenv("WAN2GP_BRIDGE_OUT", str(tmp_path))
    spec = importlib.util.spec_from_file_location("wan2gp_bridge", Path(__file__).parents[1] / "scripts" / "wan2gp_bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bridge_auth_dedupe_and_file(monkeypatch, tmp_path):
    mod = bridge_module(monkeypatch, tmp_path)
    src = tmp_path / "gen.mp4"
    calls = []

    def fake_generate(job):
        calls.append(job)
        src.write_bytes(MP4)
        return src
    monkeypatch.setattr(mod, "_generate", fake_generate)
    c, h = TestClient(mod.app), {"Authorization": "Bearer " + "k" * 40}
    body = {"idempotency_key": "k" * 20, "prompt": "A gym", "duration_seconds": 5}
    assert c.post("/v1/jobs", json=body).status_code == 401
    assert c.post("/v1/jobs", json={**body, "model_type": "i2v"}, headers=h).status_code == 400
    jid = c.post("/v1/jobs", json=body, headers=h).json()["id"]
    assert c.post("/v1/jobs", json=body, headers=h).json()["id"] == jid      # misma clave, mismo trabajo
    assert mod._queue.qsize() == 1
    assert c.get(f"/v1/jobs/{jid}/file", headers=h).status_code == 409
    threading_worker_once(mod)
    assert c.get(f"/v1/jobs/{jid}", headers=h).json()["status"] == "succeeded" and len(calls) == 1
    r = c.get(f"/v1/jobs/{jid}/file", headers=h)
    assert r.status_code == 200 and r.content == MP4
    assert c.delete(f"/v1/jobs/{jid}", headers=h).json() == {"deleted": True}
    assert not (tmp_path / f"{jid}.mp4").exists() and c.get(f"/v1/jobs/{jid}", headers=h).status_code == 404


def threading_worker_once(mod):
    """Ejecuta el bucle de la GPU sobre lo que hay en cola y lo detiene cuando se vacía."""
    def get_or_stop():
        if mod._queue.empty():
            raise StopIteration
        return mod._queue.queue.popleft()
    mod._queue.get = get_or_stop
    with pytest.raises(StopIteration):
        mod._worker()
