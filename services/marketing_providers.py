"""
AITA Marketing — adaptadores internos hacia los motores externos.

Nexxus es la única interfaz que ve el cliente. Los motores (Postiz para
publicar, ComfyUI/Wan para imagen y vídeo, Remotion para montar reels,
changedetection.io / SerpBear / Google Places para competencia) se conectarán
detrás de estas interfaces en fases posteriores. No se copia ni se importa
código de esos proyectos.

Fase 1: solo existen implementaciones "disabled" que no hacen ninguna llamada
de red y devuelven un resultado explícito `not_connected`. Así se prueba el
flujo completo (aprobar → programar → cola de publicación) sin publicar nada.

Seguridad para las implementaciones reales:
  * credenciales solo en variables de entorno del servidor, nunca en la base de
    datos sin cifrar, ni en el frontend, ni en logs (usar redact()).
  * cada publicación lleva idempotency_key (marketing_publications) para que un
    reintento no publique dos veces.
  * no se hace scraping que viole los términos de las plataformas.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol


@dataclass(frozen=True)
class ProviderResult:
    status: str                      # not_connected | queued | ok | failed
    provider: str
    external_id: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PublishRequest:
    tenant_id: str
    content_id: str
    channels: List[str]
    scheduled_at: Optional[str]
    caption: Optional[str]
    idempotency_key: str
    asset_paths: List[str] = field(default_factory=list)   # rutas de almacenamiento, nunca binarios


class PublisherProvider(Protocol):
    name: str
    def schedule(self, req: PublishRequest) -> ProviderResult: ...
    def cancel(self, tenant_id: str, external_id: str) -> ProviderResult: ...
    def publish(self, req: PublishRequest) -> ProviderResult: ...
    def fetch_metrics(self, tenant_id: str, external_id: str) -> ProviderResult: ...


class CreativeProvider(Protocol):
    name: str
    def generate_image(self, tenant_id: str, prompt: str, brand: Dict[str, Any]) -> ProviderResult: ...
    def generate_video(self, tenant_id: str, prompt: str, brand: Dict[str, Any]) -> ProviderResult: ...


class ReelRenderer(Protocol):
    name: str
    def render(self, tenant_id: str, spec: Dict[str, Any]) -> ProviderResult: ...


class CompetitorDataProvider(Protocol):
    name: str
    def collect_snapshot(self, tenant_id: str, competitor: Dict[str, Any]) -> ProviderResult: ...


def _off(name: str) -> ProviderResult:
    return ProviderResult(status="not_connected", provider=name)


class DisabledPublisher:
    """Postiz (Fase 2). Hoy: no publica, no llama a nadie."""
    name = "disabled"

    def schedule(self, req: PublishRequest) -> ProviderResult: return _off(self.name)
    def cancel(self, tenant_id: str, external_id: str) -> ProviderResult: return _off(self.name)
    def publish(self, req: PublishRequest) -> ProviderResult: return _off(self.name)
    def fetch_metrics(self, tenant_id: str, external_id: str) -> ProviderResult: return _off(self.name)


class DisabledCreative:
    name = "disabled"

    def generate_image(self, tenant_id: str, prompt: str, brand: Dict[str, Any]) -> ProviderResult: return _off(self.name)
    def generate_video(self, tenant_id: str, prompt: str, brand: Dict[str, Any]) -> ProviderResult: return _off(self.name)


class DisabledReelRenderer:
    name = "disabled"

    def render(self, tenant_id: str, spec: Dict[str, Any]) -> ProviderResult: return _off(self.name)


class DisabledCompetitorData:
    name = "disabled"

    def collect_snapshot(self, tenant_id: str, competitor: Dict[str, Any]) -> ProviderResult: return _off(self.name)


class MockPublisher:
    """Para pruebas: registra las llamadas en memoria. Tampoco usa la red."""
    name = "mock"

    def __init__(self):
        self.calls: List[tuple] = []

    def schedule(self, req: PublishRequest) -> ProviderResult:
        self.calls.append(("schedule", req.idempotency_key))
        return ProviderResult(status="queued", provider=self.name, external_id=f"mock-{req.idempotency_key[:12]}")

    def cancel(self, tenant_id: str, external_id: str) -> ProviderResult:
        self.calls.append(("cancel", external_id))
        return ProviderResult(status="ok", provider=self.name, external_id=external_id)

    def publish(self, req: PublishRequest) -> ProviderResult:
        self.calls.append(("publish", req.idempotency_key))
        return ProviderResult(status="ok", provider=self.name)

    def fetch_metrics(self, tenant_id: str, external_id: str) -> ProviderResult:
        self.calls.append(("metrics", external_id))
        return ProviderResult(status="ok", provider=self.name, detail={})


def default_providers() -> Dict[str, Any]:
    """Proveedores activos. En Fase 1 todos están desactivados (sin variables de entorno ni red)."""
    return {"publisher": DisabledPublisher(), "creative": DisabledCreative(),
            "reels": DisabledReelRenderer(), "competitors": DisabledCompetitorData()}


_SECRETISH = re.compile(r"(?i)(bearer\s+|token[=:]\s*|key[=:]\s*|secret[=:]\s*)[^\s&\"']+")


def redact(message: Any) -> str:
    """Texto de error apto para logs / last_error: sin tokens ni claves."""
    s = _SECRETISH.sub(lambda m: m.group(1) + "***", str(message or ""))
    s = re.sub(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", "***", s)
    return s[:300]
