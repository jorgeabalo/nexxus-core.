"""
AITA Marketing (Fase 2) — catálogo central de modelos (configuración, no código).

Los precios NO son verdades fijas: vienen del JSON con su versión, fecha, fuente y moneda, y un
proveedor externo sin precio verificado (price_verified=false) queda excluido por el router, porque
sin precio fiable no se puede garantizar el presupuesto aprobado. El catálogo nunca contiene claves.
Ruta alternativa: variable de entorno MARKETING_AI_CATALOG_PATH (archivo en el servidor).
"""
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

TASK_TYPES = ("marketing_copy", "storyboard", "image_generation", "image_edit", "image_to_video", "text_to_video",
              "video_extension", "background_replacement", "face_detection", "face_anonymization", "transcription",
              "subtitles", "moderation", "final_render")
BILLING_UNITS = ("request", "image", "second", "1k_tokens", "minute")
LATENCY = ("fast", "medium", "slow")
RETENTION = ("none", "zero_retention", "provider_default", "unknown")
DEFAULT_PATH = Path(__file__).with_name("marketing_ai_catalog.json")
_SECRET_KEYS = re.compile(r"(?i)(api[_-]?key|secret|token|password|authorization)")


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class ModelEntry:
    provider: str
    model_id: str
    supported_tasks: Tuple[str, ...]
    quality_score: int
    estimated_cost: Optional[float]
    billing_unit: str
    latency_class: str
    reliability_score: float
    supports_image_input: bool
    supports_video_input: bool
    supports_reference_image: bool
    supports_commercial_use: bool
    data_retention_policy: str
    real_people_allowed: bool
    external: bool
    price_verified: bool
    enabled: bool

    def public(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["supported_tasks"] = list(self.supported_tasks)
        return d


@dataclass(frozen=True)
class Catalog:
    version: str
    updated_at: str
    currency: str
    price_source: str
    quality_min: Dict[str, int]
    models: Tuple[ModelEntry, ...]

    def public(self) -> Dict[str, Any]:
        """Vista técnica para administración: sin claves (el catálogo nunca las tiene)."""
        return {"catalog_version": self.version, "updated_at": self.updated_at, "currency": self.currency,
                "price_source": self.price_source, "quality_min": dict(self.quality_min),
                "models": [m.public() for m in self.models]}


def _entry(raw: Dict[str, Any]) -> ModelEntry:
    if any(_SECRET_KEYS.search(k) for k in raw):
        raise CatalogError("catalog_must_not_contain_secrets")
    tasks = tuple(raw.get("supported_tasks") or ())
    if not tasks or any(t not in TASK_TYPES for t in tasks):
        raise CatalogError("invalid_tasks")
    if not re.fullmatch(r"[a-z0-9_]{1,40}", str(raw.get("provider", ""))):
        raise CatalogError("invalid_provider")
    if raw.get("billing_unit") not in BILLING_UNITS or raw.get("latency_class") not in LATENCY:
        raise CatalogError("invalid_unit_or_latency")
    if raw.get("data_retention_policy") not in RETENTION:
        raise CatalogError("invalid_retention")
    cost = raw.get("estimated_cost")
    if cost is not None and (not isinstance(cost, (int, float)) or cost < 0):
        raise CatalogError("invalid_cost")
    q, rel = raw.get("quality_score"), raw.get("reliability_score")
    if not (isinstance(q, int) and 0 <= q <= 100) or not (isinstance(rel, (int, float)) and 0 < rel <= 1):
        raise CatalogError("invalid_scores")
    return ModelEntry(provider=raw["provider"], model_id=str(raw["model_id"])[:120], supported_tasks=tasks,
                      quality_score=q, estimated_cost=None if cost is None else float(cost),
                      billing_unit=raw["billing_unit"], latency_class=raw["latency_class"],
                      reliability_score=float(rel),
                      **{k: raw.get(k) is True for k in ("supports_image_input", "supports_video_input",
                                                          "supports_reference_image", "supports_commercial_use",
                                                          "real_people_allowed", "external", "price_verified",
                                                          "enabled")},
                      data_retention_policy=raw["data_retention_policy"])


def parse(data: Dict[str, Any]) -> Catalog:
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise CatalogError("invalid_catalog")
    if not re.fullmatch(r"[A-Z]{3}", str(data.get("currency", ""))):
        raise CatalogError("invalid_currency")
    qmin = data.get("quality_min") or {}
    if set(qmin) != {"draft", "standard", "premium"}:
        raise CatalogError("invalid_quality_min")
    return Catalog(version=str(data.get("catalog_version") or "")[:40] or "unknown",
                   updated_at=str(data.get("updated_at") or ""), currency=data["currency"],
                   price_source=str(data.get("price_source") or "")[:500], quality_min={k: int(v) for k, v in qmin.items()},
                   models=tuple(_entry(m) for m in data["models"]))


def load(path: Optional[str] = None) -> Catalog:
    p = Path(path or os.getenv("MARKETING_AI_CATALOG_PATH") or DEFAULT_PATH)
    return parse(json.loads(p.read_text(encoding="utf-8")))
