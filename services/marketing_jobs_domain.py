"""
AITA Marketing (Fase 2) — reglas puras de la Biblioteca y de los trabajos de generación.

Las transiciones son las mismas que imponen los triggers de
supabase/migrations/20261011120000_marketing_reel_studio.sql (marketing_media_guard y
marketing_job_transition). Si se cambian aquí, hay que cambiarlas allí: una prueba compara ambas.
"""
from typing import Any, Dict, Optional

from services.marketing_domain import DomainError

# ---------------------------------------------------------------- Biblioteca
MEDIA_STATUSES = ("uploaded", "scanning", "ready", "rejected", "processing", "failed", "archived")
MEDIA_TRANSITIONS: Dict[str, tuple] = {
    "uploaded": ("scanning", "ready", "rejected"),
    "scanning": ("ready", "rejected", "failed"),
    "ready": ("processing", "archived"),
    "processing": ("ready", "failed"),
    "failed": ("ready", "archived"),
    "rejected": ("archived",),
    "archived": ("ready",),
}

# ---------------------------------------------------------------- trabajos
JOB_STATUSES = ("draft", "awaiting_generation_approval", "queued", "processing", "succeeded", "failed", "cancelled")
JOB_TRANSITIONS: Dict[str, tuple] = {
    "draft": ("awaiting_generation_approval", "cancelled"),
    "awaiting_generation_approval": ("queued", "draft", "cancelled"),
    "queued": ("processing", "failed", "cancelled"),
    "processing": ("succeeded", "failed", "cancelled"),
    "succeeded": (),
    "failed": (),
    "cancelled": (),
}
TERMINAL = ("succeeded", "failed", "cancelled")
ACTIVE = ("queued", "processing")
QUALITY_TIERS = ("draft", "standard", "premium")
OUTPUT_REVIEW = ("generated", "in_review", "approved", "rejected")
SCENE_ORIGINS = ("client_original", "client_ai_adapted", "ai_generated")

# Códigos de error públicos (seguros para mostrar; nunca detalles del proveedor).
PUBLIC_ERRORS = ("provider_disabled", "provider_unavailable", "budget_exceeded", "no_eligible_model",
                 "privacy_blocked", "timeout", "moderation_rejected", "render_failed", "cancelled_by_user",
                 "internal_error")

# ---------------------------------------------------------------- límites nuevos
# 0 = nada permitido (por defecto). None = sin límite. El tenant no puede cambiarlos.
GEN_LIMIT_KEYS = ("monthly_generation_job_limit", "monthly_regeneration_limit", "monthly_generated_image_limit",
                  "monthly_generated_video_seconds_limit", "monthly_ai_cost_limit", "library_storage_limit_bytes")
GEN_DEFAULTS: Dict[str, Any] = {"ai_generation_enabled": False, "max_upload_bytes": 52428800,
                                **{k: 0 for k in GEN_LIMIT_KEYS}}


def check_media_transition(current: str, target: str) -> None:
    if target not in MEDIA_STATUSES:
        raise DomainError("invalid_status", 400)
    if target not in MEDIA_TRANSITIONS.get(current, ()):
        raise DomainError("invalid_transition", 409)


def check_job_transition(current: str, target: str, *, approved: bool = False) -> None:
    if target not in JOB_STATUSES:
        raise DomainError("invalid_status", 400)
    if target not in JOB_TRANSITIONS.get(current, ()):
        raise DomainError("invalid_transition", 409)
    # Nada entra en cola sin aprobación explícita de owner/manager.
    if target == "queued" and not approved:
        raise DomainError("generation_approval_required", 409)


def public_error(code: Optional[str]) -> str:
    """Código de error apto para el cliente (cualquier otro se convierte en internal_error)."""
    return code if code in PUBLIC_ERRORS else "internal_error"


def limit_left(limit: Optional[float], used: float) -> Optional[float]:
    """None = sin límite. 0 o negativo = nada disponible."""
    if limit is None:
        return None
    return max(float(limit) - float(used), 0.0)


def check_generation_limits(settings: Dict[str, Any], usage: Dict[str, float], *, regeneration: bool,
                            images: int, video_seconds: int, max_cost: float) -> None:
    """Se comprueba al APROBAR un trabajo (antes de cualquier gasto). Cuenta el mes del tenant."""
    if settings.get("ai_generation_enabled") is not True:
        raise DomainError("generation_disabled", 403)
    checks = (
        ("monthly_generation_job_limit", usage.get("jobs", 0), 1),
        ("monthly_regeneration_limit", usage.get("regenerations", 0), 1 if regeneration else 0),
        ("monthly_generated_image_limit", usage.get("images", 0), images),
        ("monthly_generated_video_seconds_limit", usage.get("video_seconds", 0), video_seconds),
        ("monthly_ai_cost_limit", usage.get("cost", 0), max_cost),
    )
    for key, used, needed in checks:
        if needed <= 0:
            continue
        left = limit_left(settings.get(key, 0), used)
        if left is not None and needed > left:
            raise DomainError(f"limit_{key}", 409)
