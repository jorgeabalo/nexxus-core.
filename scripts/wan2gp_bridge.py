"""
AITA Marketing — puente HTTP para Wan2GP (opcional). Se ejecuta en el SERVIDOR GPU, no en Railway.

Wan2GP (github.com/deepbeepmeep/Wan2GP) genera vídeo con modelos Wan en GPUs NVIDIA/AMD de 6-8 GB o más.
Este puente expone lo justo para el adaptador Wan2GPAdapter (services/marketing_ai_router.py):

  POST   /v1/jobs            {idempotency_key, model_type, prompt, negative_prompt, resolution,
                              duration_seconds, fps}  -> {id, status}   (misma clave = mismo trabajo)
  GET    /v1/jobs/{id}       -> {id, status: queued|running|succeeded|failed}
  GET    /v1/jobs/{id}/file  -> el MP4 generado
  DELETE /v1/jobs/{id}       -> borra el trabajo y su archivo

Todas las rutas exigen "Authorization: Bearer <WAN2GP_TOKEN>". Una sola generación a la vez (la GPU es una);
el resto espera en cola. Los trabajos y sus vídeos se borran solos a las 24 h aunque nadie llame a DELETE.

Puesta en marcha (en el servidor GPU, con Wan2GP ya instalado según su README):
  pip install fastapi uvicorn
  WAN2GP_ROOT=/ruta/a/Wan2GP WAN2GP_TOKEN=<token largo aleatorio> python wan2gp_bridge.py
Exponerlo solo por HTTPS (túnel o proxy con TLS) y poner esa URL en WAN2GP_BASE_URL del worker.

Integración con Wan2GP: usa su API de Python documentada (shared.api.init / submit_task / result).
No se ha podido probar de punta a punta sin GPU; _generate es el único punto que toca Wan2GP.
"""
import hmac
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

TOKEN = os.environ.get("WAN2GP_TOKEN", "")
ROOT = Path(os.environ.get("WAN2GP_ROOT", ".")).resolve()
OUT = Path(os.environ.get("WAN2GP_BRIDGE_OUT", str(ROOT / "bridge_outputs"))).resolve()
TTL_SECONDS = 24 * 3600
MODELS = ("t2v_1.3B", "t2v")                       # solo texto a vídeo

app = FastAPI(title="wan2gp-bridge", docs_url=None, redoc_url=None, openapi_url=None)
_jobs: Dict[str, Dict[str, Any]] = {}
_by_key: Dict[str, str] = {}
_lock = threading.Lock()
_queue: "queue.Queue[str]" = queue.Queue()
_session = None


class JobIn(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=160)
    model_type: str = "t2v_1.3B"
    prompt: str = Field(min_length=1, max_length=1500)
    negative_prompt: str = Field(default="", max_length=600)
    resolution: str = Field(default="480x832", pattern=r"^\d{3,4}x\d{3,4}$")
    duration_seconds: int = Field(default=5, ge=1, le=10)
    fps: int = Field(default=16, ge=8, le=30)


def auth(authorization: str = Header(default="")) -> None:
    if not TOKEN or not hmac.compare_digest(authorization.encode(), f"Bearer {TOKEN}".encode()):
        raise HTTPException(401, "unauthorized")


def _public(j: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": j["id"], "status": j["status"]}


def _generate(job: JobIn) -> Path:
    """Llama a Wan2GP y devuelve la ruta del MP4 generado."""
    global _session
    if _session is None:
        sys.path.insert(0, str(ROOT))
        from shared.api import init                     # API de Python de Wan2GP
        _session = init(root=str(ROOT), cli_args=[])
    frames = job.duration_seconds * job.fps + 1         # Wan usa 4n+1 fotogramas
    settings = {"model_type": job.model_type, "prompt": job.prompt, "negative_prompt": job.negative_prompt,
                "resolution": job.resolution, "video_length": frames, "force_fps": str(job.fps),
                "num_inference_steps": 30}
    result = _session.submit_task(settings).result()
    files = [Path(f) for f in (getattr(result, "generated_files", None) or []) if str(f).endswith(".mp4")]
    if not files:
        raise RuntimeError("no_output")
    return files[0]


def _worker() -> None:
    while True:
        jid = _queue.get()
        with _lock:
            j = _jobs.get(jid)
            if not j:
                continue
            j["status"] = "running"
        try:
            src = _generate(j["request"])
            dst = OUT / f"{jid}.mp4"
            src.replace(dst)
            with _lock:
                j.update(status="succeeded", file=dst)
        except Exception as e:                              # noqa: BLE001 — el cliente solo ve "failed"
            print(f"job {jid} failed: {type(e).__name__}", flush=True)
            with _lock:
                j["status"] = "failed"


def _janitor() -> None:
    while True:
        time.sleep(600)
        now = time.time()
        with _lock:
            old = [k for k, j in _jobs.items() if now - j["created"] > TTL_SECONDS and j["status"] != "running"]
        for k in old:
            _drop(k)


def _drop(jid: str) -> None:
    with _lock:
        j = _jobs.pop(jid, None)
        if j:
            _by_key.pop(j["key"], None)
    if j and j.get("file"):
        Path(j["file"]).unlink(missing_ok=True)


@app.post("/v1/jobs", dependencies=[Depends(auth)])
def create(job: JobIn):
    if job.model_type not in MODELS:
        raise HTTPException(400, "invalid_model")
    with _lock:
        if job.idempotency_key in _by_key:              # reintento: el mismo trabajo, nunca otra generación
            return _public(_jobs[_by_key[job.idempotency_key]])
        jid = str(uuid.uuid4())
        _jobs[jid] = {"id": jid, "key": job.idempotency_key, "status": "queued", "request": job,
                      "created": time.time(), "file": None}
        _by_key[job.idempotency_key] = jid
    _queue.put(jid)
    return _public(_jobs[jid])


def _get(jid: str) -> Dict[str, Any]:
    with _lock:
        j = _jobs.get(jid)
    if not j:
        raise HTTPException(404, "not_found")
    return j


@app.get("/v1/jobs/{jid}", dependencies=[Depends(auth)])
def status(jid: str):
    return _public(_get(jid))


@app.get("/v1/jobs/{jid}/file", dependencies=[Depends(auth)])
def file(jid: str):
    j = _get(jid)
    if j["status"] != "succeeded":
        raise HTTPException(409, "not_ready")
    return FileResponse(j["file"], media_type="video/mp4")


@app.delete("/v1/jobs/{jid}", dependencies=[Depends(auth)])
def delete(jid: str):
    _get(jid)
    _drop(jid)
    return {"deleted": True}


if __name__ == "__main__":
    if len(TOKEN) < 32:
        sys.exit("WAN2GP_TOKEN debe tener al menos 32 caracteres")
    OUT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_janitor, daemon=True).start()
    import uvicorn
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "7870")))
