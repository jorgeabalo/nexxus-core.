"""
AITA Marketing (Fase 2) — MarketingAIRouter: elige modelo por tarea sin acoplarse a un proveedor.

Política de selección (en este orden):
  1. excluir modelos desactivados o que no soportan la tarea / los tipos de entrada;
  2. excluir los que violan la privacidad (restricted nunca sale; personas reales solo a modelos
     que lo permiten, con retención conocida y uso comercial);
  3. excluir los que superan el coste máximo (y los externos sin precio verificado);
  4. excluir los que no alcanzan la calidad mínima del nivel elegido;
  5. elegir el menor coste esperado (coste / fiabilidad);
  6. desempatar por latencia y luego fiabilidad;
  7. fallback solo entre los que cumplen TODO lo anterior;
  8. nunca subir automáticamente a un modelo más caro que el presupuesto aprobado.

Proveedores: todo apagado por defecto. OMNIROUTE_ENABLED=false; aunque se active, el adaptador no
tiene transporte de red en esta fase (contrato + mock). El MockProvider es determinista y no usa red.
Wan2GP (opcional): vídeo generado en un servidor GPU propio (scripts/wan2gp_bridge.py). Doble cierre:
la entrada del catálogo está desactivada y además hace falta WAN2GP_ENABLED=true con URL y token.
Un modelo real habilitado gana siempre a un mock que hace la misma tarea (el mock solo es un sustituto).
"""
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.marketing_ai_catalog import TASK_TYPES, Catalog, ModelEntry, load
from services.marketing_privacy import PRIVACY_CLASSES

QUALITY_TIERS = ("draft", "standard", "premium")
LATENCY_RANK = {"fast": 0, "medium": 1, "slow": 2}


class RouterError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class RouteRequest:
    tenant_id: str
    task_type: str
    quality_tier: str
    maximum_cost: float
    privacy_class: str
    idempotency_key: str
    units: float = 1.0                          # imágenes, segundos o peticiones según billing_unit
    maximum_latency: Optional[str] = None       # fast | medium | slow
    input_media_types: Tuple[str, ...] = ()     # image | video | reference_image
    output_requirements: Dict[str, Any] = field(default_factory=dict)
    aspect_ratio: str = "9:16"
    duration: Optional[int] = None
    language: str = "es"
    brand_constraints: Dict[str, Any] = field(default_factory=dict)
    # Descripción de la escena para generar vídeo (sale del guion validado; nunca datos de personas).
    prompt: str = ""
    # Validar el formato no es un antivirus: material real solo sale si está escaneado y limpio.
    inputs_malware_clean: bool = False

    def validate(self) -> None:
        if self.task_type not in TASK_TYPES:
            raise RouterError("invalid_task_type", 400)
        if self.quality_tier not in QUALITY_TIERS:
            raise RouterError("invalid_quality_tier", 400)
        if self.privacy_class not in PRIVACY_CLASSES:
            raise RouterError("invalid_privacy_class", 400)
        if not (isinstance(self.maximum_cost, (int, float)) and self.maximum_cost >= 0):
            raise RouterError("invalid_maximum_cost", 400)
        if self.maximum_latency not in (None, *LATENCY_RANK):
            raise RouterError("invalid_maximum_latency", 400)
        if not (16 <= len(self.idempotency_key) <= 160):
            raise RouterError("invalid_idempotency_key", 400)
        if self.units < 0:
            raise RouterError("invalid_units", 400)


@dataclass(frozen=True)
class Route:
    model: ModelEntry
    estimated_cost: float
    expected_cost: float
    fallbacks: Tuple[ModelEntry, ...]
    excluded: Dict[str, int]


def provider_enabled(provider: str, env: Optional[Dict[str, str]] = None) -> bool:
    """Interruptores por proveedor. Real = apagado salvo variable explícita."""
    env = os.environ if env is None else env
    if provider == "mock":
        return str(env.get("MARKETING_AI_MOCK_ENABLED", "true")).lower() == "true"
    if provider == "omniroute":
        return str(env.get("OMNIROUTE_ENABLED", "false")).lower() == "true"
    if provider == "local_ffmpeg":
        return str(env.get("MARKETING_LOCAL_TOOLS_ENABLED", "false")).lower() == "true"
    if provider == "wan2gp":
        return str(env.get("WAN2GP_ENABLED", "false")).lower() == "true"
    return False


def _privacy_ok(m: ModelEntry, privacy_class: str, malware_clean: bool = False) -> bool:
    if not m.external:
        return True                                         # local / mock: no sale del servidor
    if privacy_class == "restricted":
        return False                                        # nunca hacia fuera
    if privacy_class != "synthetic_only" and not malware_clean:
        return False                                        # material real sin escaneo antivirus: no sale
    if privacy_class in ("consented_people", "anonymized_people"):
        if not m.real_people_allowed or m.data_retention_policy in ("unknown", "provider_default"):
            return False
    return m.supports_commercial_use


def _inputs_ok(m: ModelEntry, kinds: Tuple[str, ...]) -> bool:
    need = {"image": m.supports_image_input, "video": m.supports_video_input,
            "reference_image": m.supports_reference_image}
    return all(need.get(k, False) for k in kinds)


class MarketingAIRouter:
    def __init__(self, catalog: Optional[Catalog] = None, env: Optional[Dict[str, str]] = None):
        self.catalog = catalog or load()
        self.env = env

    def route(self, req: RouteRequest) -> Route:
        req.validate()
        excluded = {"disabled": 0, "unsupported": 0, "privacy": 0, "cost": 0, "quality": 0, "latency": 0}
        qmin = self.catalog.quality_min[req.quality_tier]
        ok: List[Tuple[float, float, ModelEntry]] = []
        for m in self.catalog.models:
            if not m.enabled or not provider_enabled(m.provider, self.env):
                excluded["disabled"] += 1
                continue
            if req.task_type not in m.supported_tasks or not _inputs_ok(m, req.input_media_types):
                excluded["unsupported"] += 1
                continue
            if not _privacy_ok(m, req.privacy_class, req.inputs_malware_clean):
                excluded["privacy"] += 1
                continue
            if m.estimated_cost is None or (m.external and not m.price_verified):
                excluded["cost"] += 1                       # sin precio fiable no hay presupuesto garantizado
                continue
            cost = round(m.estimated_cost * req.units, 4)
            if cost > req.maximum_cost:
                excluded["cost"] += 1
                continue
            if m.quality_score < qmin:
                excluded["quality"] += 1
                continue
            if req.maximum_latency and LATENCY_RANK[m.latency_class] > LATENCY_RANK[req.maximum_latency]:
                excluded["latency"] += 1
                continue
            ok.append((cost, cost / m.reliability_score, m))
        if not ok:
            raise RouterError("no_eligible_model", 409)
        # un modelo real antes que un mock; luego menor costo esperado; a igualdad, procesamiento local antes que externo (prioridad económica)
        ok.sort(key=lambda x: (x[2].provider == "mock", x[1], x[2].external, LATENCY_RANK[x[2].latency_class], -x[2].reliability_score,
                               x[2].model_id))
        cost, expected, best = ok[0]
        return Route(model=best, estimated_cost=cost, expected_cost=round(expected, 4),
                     fallbacks=tuple(x[2] for x in ok[1:]), excluded=excluded)


# ---------------------------------------------------------------- adaptadores (contratos)
@dataclass(frozen=True)
class ProviderJob:
    status: str                      # queued | succeeded | failed
    provider_job_id: Optional[str]
    actual_cost: Optional[float] = None
    output: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None


class MockProvider:
    """Determinista y sin red. Un reintento con la misma idempotency_key devuelve el MISMO trabajo
    (nunca un segundo trabajo facturable)."""
    name = "mock"

    def __init__(self):
        self.jobs: Dict[str, ProviderJob] = {}
        self.submissions = 0

    def submit(self, req: RouteRequest, model: ModelEntry) -> ProviderJob:
        if req.idempotency_key in self.jobs:
            return self.jobs[req.idempotency_key]
        self.submissions += 1
        h = hashlib.sha256(f"{req.tenant_id}:{req.idempotency_key}".encode()).hexdigest()
        job = ProviderJob(status="succeeded", provider_job_id=f"mock-{h[:16]}", actual_cost=0.0,
                          output={"task_type": req.task_type, "model_id": model.model_id, "seed": h[:8],
                                  "mock": True})
        self.jobs[req.idempotency_key] = job
        return job


class OmniRouteAdapter:
    """Contrato del adaptador hacia OmniRoute (gateway compatible con OpenAI). NO se ha confirmado
    qué proyecto OmniRoute se usará, así que aquí no hay transporte de red: sin un transporte
    inyectado explícitamente, cualquier envío falla con provider_disabled. Las credenciales solo
    vivirían en variables de entorno del servidor y nunca se registran."""
    name = "omniroute"

    def __init__(self, env: Optional[Dict[str, str]] = None, transport: Optional[Callable[..., Dict[str, Any]]] = None):
        self.env = os.environ if env is None else env
        self._transport = transport

    def enabled(self) -> bool:
        return provider_enabled("omniroute", self.env) and bool(self.env.get("OMNIROUTE_BASE_URL"))

    def build_payload(self, req: RouteRequest, model: ModelEntry) -> Dict[str, Any]:
        """Cuerpo que se enviaría. Solo rutas de derivados autorizados, nunca binarios ni URLs firmadas."""
        return {"model": model.model_id, "task": req.task_type, "aspect_ratio": req.aspect_ratio,
                "duration": req.duration, "language": req.language, "quality_tier": req.quality_tier,
                "max_cost": req.maximum_cost, "idempotency_key": req.idempotency_key,
                "metadata": {"tenant": hashlib.sha256(req.tenant_id.encode()).hexdigest()[:16]}}

    def submit(self, req: RouteRequest, model: ModelEntry) -> ProviderJob:
        if not self.enabled() or self._transport is None:
            return ProviderJob(status="failed", provider_job_id=None, error_code="provider_disabled")
        try:                                              # pragma: no cover - sin transporte en esta fase
            res = self._transport(self.build_payload(req, model))
            return ProviderJob(status=str(res.get("status", "queued")), provider_job_id=str(res.get("id"))[:200])
        except Exception:                                 # pragma: no cover - nunca se registra el detalle
            return ProviderJob(status="failed", provider_job_id=None, error_code="provider_unavailable")


class DisabledLocalTools:
    """FFmpeg local (preferido para subtítulos, montaje, pixelado y desenfoque). No instalado en
    producción: hasta su PR propio, devuelve provider_disabled."""
    name = "local_ffmpeg"

    def submit(self, req: RouteRequest, model: ModelEntry) -> ProviderJob:
        return ProviderJob(status="failed", provider_job_id=None, error_code="provider_disabled")


class Wan2GPAdapter:
    """Vídeo desde texto con Wan2GP en un servidor GPU propio, detrás de scripts/wan2gp_bridge.py.
    * Solo text_to_video y solo synthetic_only: al servidor GPU no sale ningún archivo de la Biblioteca.
    * El puente deduplica por idempotency_key: un reintento recoge el mismo trabajo, nunca genera dos.
    * El vídeo se baja (tamaño acotado, se comprueba que es MP4) y se guarda como temporal de este
      intento en {tenant}/derivatives/{job}/; lo publica (o lo borra) el worker.
    * El token solo vive en WAN2GP_TOKEN y nunca aparece en errores ni registros."""
    name = "wan2gp"
    MAX_BYTES = 200 * 1024 * 1024
    NEGATIVE = ("text, subtitles, watermark, logo, distorted faces, extra limbs, "
                "medical scans, x-ray, ultrasound images, blood, gore")

    def __init__(self, db=None, env: Optional[Dict[str, str]] = None, client=None, sleep=time.sleep):
        self.db = db
        self.env = os.environ if env is None else env
        self._client = client
        self._sleep = sleep

    def enabled(self) -> bool:
        return (provider_enabled("wan2gp", self.env) and bool(self.env.get("WAN2GP_BASE_URL"))
                and bool(self.env.get("WAN2GP_TOKEN")) and self.db is not None)

    def _http(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(base_url=self.env["WAN2GP_BASE_URL"].rstrip("/"), timeout=30.0,
                                        headers={"Authorization": f"Bearer {self.env['WAN2GP_TOKEN']}"})
        return self._client

    def _resolution(self, aspect_ratio: str) -> str:
        """Por defecto 480p (lo nativo del modelo 1.3B, que cabe en GPUs de 6-8 GB); vertical para Reels."""
        w, h = sorted(int(x) for x in str(self.env.get("WAN2GP_RESOLUTION") or "480x832").split("x"))
        return f"{w}x{h}" if aspect_ratio == "9:16" else f"{h}x{w}"

    def build_payload(self, req: RouteRequest, model: ModelEntry) -> Dict[str, Any]:
        seconds = max(1, min(int(req.duration or req.units or 5), 10))     # Wan genera clips cortos
        return {"idempotency_key": req.idempotency_key, "model_type": model.model_id.split(":", 1)[-1],
                "prompt": req.prompt[:1500], "negative_prompt": self.NEGATIVE,
                "resolution": self._resolution(req.aspect_ratio),
                "duration_seconds": seconds, "fps": 16}

    def submit(self, req: RouteRequest, model: ModelEntry) -> ProviderJob:
        temp_dir = str((req.output_requirements or {}).get("temp_dir") or "")
        if not self.enabled() or not temp_dir:
            return ProviderJob(status="failed", provider_job_id=None, error_code="provider_disabled")
        if req.task_type != "text_to_video" or req.privacy_class != "synthetic_only" or not req.prompt.strip():
            return ProviderJob(status="failed", provider_job_id=None, error_code="privacy_blocked")
        try:
            http = self._http()
            r = http.post("/v1/jobs", json=self.build_payload(req, model))
            r.raise_for_status()
            jid = str(r.json()["id"])[:200]
            deadline = time.monotonic() + float(self.env.get("WAN2GP_TIMEOUT_SECONDS") or 1800)
            while True:
                st = http.get(f"/v1/jobs/{jid}").json()
                if st.get("status") == "succeeded":
                    break
                if st.get("status") == "failed" or time.monotonic() > deadline:
                    return ProviderJob(status="failed", provider_job_id=jid, error_code="provider_unavailable")
                self._sleep(10)
            data = b""
            with http.stream("GET", f"/v1/jobs/{jid}/file") as f:
                f.raise_for_status()
                for chunk in f.iter_bytes():
                    data += chunk
                    if len(data) > self.MAX_BYTES:
                        return ProviderJob(status="failed", provider_job_id=jid, error_code="provider_unavailable")
            if len(data) < 12 or data[4:8] != b"ftyp":
                return ProviderJob(status="failed", provider_job_id=jid, error_code="provider_unavailable")
            path = f"{temp_dir}{hashlib.sha256(req.idempotency_key.encode()).hexdigest()[:24]}.mp4"
            self.db.storage_upload("marketing-assets", path, data, "video/mp4")
        except Exception:                                  # noqa: BLE001 — nunca se registra el detalle
            return ProviderJob(status="failed", provider_job_id=None, error_code="provider_unavailable")
        try:
            http.delete(f"/v1/jobs/{jid}")                  # el servidor GPU no se queda con el vídeo
        except Exception:                                  # noqa: BLE001 — el puente también caduca solo
            pass
        # se cobra lo generado (un clip de hasta 10 s), nunca más que lo estimado para la subtarea
        seconds = self.build_payload(req, model)["duration_seconds"]
        cost = round(float(model.estimated_cost or 0) * min(float(seconds), float(req.units)), 4)
        return ProviderJob(status="succeeded", provider_job_id=jid, actual_cost=cost,
                           output={"files": [{"temp_path": path, "kind": "video", "mime_type": "video/mp4",
                                              "byte_size": len(data)}]})


def default_adapters(db=None) -> Dict[str, Any]:
    return {"mock": MockProvider(), "omniroute": OmniRouteAdapter(), "local_ffmpeg": DisabledLocalTools(),
            "wan2gp": Wan2GPAdapter(db=db)}
