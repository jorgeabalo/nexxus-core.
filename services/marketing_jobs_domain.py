"""
AITA Marketing (Fase 2) — reglas puras de la Biblioteca y de los trabajos de generación.

Las transiciones son las mismas que imponen los triggers de
supabase/migrations/20261011120000_marketing_reel_studio.sql (marketing_media_guard y
marketing_job_transition). Si se cambian aquí, hay que cambiarlas allí: una prueba compara ambas.
"""
import math
from decimal import ROUND_CEILING, Decimal
from typing import Any, Dict, Optional

from services.marketing_domain import DomainError

# ---------------------------------------------------------------- Biblioteca
MEDIA_STATUSES = ("uploaded", "scanning", "ready", "rejected", "processing", "failed", "archived", "deleted")
# 'deleted' = borrado controlado (auditado; la fila queda como registro). Es final.
MEDIA_TRANSITIONS: Dict[str, tuple] = {
    "uploaded": ("scanning", "ready", "rejected", "deleted"),
    "scanning": ("ready", "rejected", "failed", "deleted"),
    "ready": ("processing", "archived", "deleted"),
    "processing": ("ready", "failed"),
    "failed": ("ready", "archived", "deleted"),
    "rejected": ("archived", "deleted"),
    "archived": ("ready", "deleted"),
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
                 "privacy_blocked", "consent_revoked", "minors_excluded", "media_excluded", "media_not_ready",
                 "media_pending_deletion", "media_expired",
                 "timeout", "moderation_rejected", "render_failed", "cancelled_by_user", "storage_quota_exceeded",
                 "internal_error")

# ---------------------------------------------------------------- límites nuevos
# 0 = nada permitido (por defecto). None = sin límite. El tenant no puede cambiarlos (no hay ningún
# endpoint que escriba marketing_settings y RLS/privilegios lo impiden).
GEN_LIMIT_KEYS = ("monthly_generation_job_limit", "monthly_regeneration_limit", "monthly_generated_image_limit",
                  "monthly_generated_video_seconds_limit")
# La Biblioteca no consume generación: tiene su propio límite de almacenamiento, que fija el operador.
# 0 = Biblioteca no habilitada (por defecto: ninguna empresa recibe espacio automáticamente); null = sin límite.
GEN_DEFAULTS: Dict[str, Any] = {"ai_generation_enabled": False, "max_upload_bytes": 52428800,
                                "library_storage_limit_bytes": 0, "max_retention_days": 30, "monthly_ai_cost_limit": 0,
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
                            images: int, video_seconds: int) -> None:
    """Se comprueba al APROBAR un trabajo (antes de cualquier gasto, también para el mock). Cuenta el mes
    del tenant. Interruptor apagado o límite principal en 0 = "generación no habilitada en este plan".
    El COSTO se controla aparte: reserva atómica al aprobar (public.marketing_approve_generation)."""
    if not generation_enabled(settings):
        raise DomainError("generation_disabled", 403)
    checks = (
        ("monthly_generation_job_limit", usage.get("jobs", 0), 1),
        ("monthly_regeneration_limit", usage.get("regenerations", 0), 1 if regeneration else 0),
        ("monthly_generated_image_limit", usage.get("images", 0), images),
        ("monthly_generated_video_seconds_limit", usage.get("video_seconds", 0), video_seconds),
    )
    for key, used, needed in checks:
        if needed <= 0:
            continue
        limit = settings.get(key, 0)
        if limit == 0:
            raise DomainError("generation_disabled", 403)
        left = limit_left(limit, used)
        if left is not None and needed > left:
            raise DomainError(f"limit_{key}", 409)


# "Marketing AI budget": presupuesto EXCLUSIVO de la IA de Marketing (lo fija solo el operador; 0 por defecto).
# Máximo TEMPORAL de USD 20/mes mientras no exista un ledger global que reúna voz, infraestructura,
# almacenamiento e IA. NO es el objetivo de costo total de AITA (USD 80 por tenant) ni lo garantiza por sí solo.
# Presupuesto desconocido (None), 0 o por encima del máximo → cerrado.
AI_BUDGET_CAP_USD = 20.0


def ai_budget(settings: Dict[str, Any]) -> float:
    """Presupuesto efectivo en USD; 0 = cerrado (también si falta, no es numérico o supera el tope)."""
    try:
        v = float(settings.get("monthly_ai_cost_limit"))
    except (TypeError, ValueError):
        return 0.0
    return v if 0 < v <= AI_BUDGET_CAP_USD else 0.0


def generation_enabled(settings: Dict[str, Any]) -> bool:
    """Para la interfaz: ¿el plan permite generar algo (real o simulado)?"""
    return (settings.get("ai_generation_enabled") is True and settings.get("monthly_generation_job_limit", 0) != 0
            and ai_budget(settings) > 0)                        # presupuesto IA 0 o desconocido = nada, ni el mock


def library_state(limit: Optional[int], used: int) -> str:
    """Estado de la Biblioteca para la interfaz: disabled (0) · unlimited (null) · full · enabled."""
    if limit is None:
        return "unlimited"
    if int(limit) <= 0:
        return "disabled"
    return "full" if used >= int(limit) else "enabled"


# ---------------------------------------------------------------- costo de Marketing
# Prioridad de producción económica (de más barata a más cara). El router y el Estudio la siguen.
PRODUCTION_PRIORITY = ("client_material", "local_processing", "templates_ffmpeg", "small_models",
                       "image_generation", "image_to_video", "text_to_video")


def to_cost(usd: Any) -> Optional[float]:
    """Costo máximo estimado en USD redondeado hacia arriba a 4 decimales; None si no es estimable."""
    if usd is None or isinstance(usd, bool):
        return None
    try:
        d = Decimal(str(usd))
    except Exception:
        return None
    if not d.is_finite() or d < 0:
        return None
    return float(d.quantize(Decimal("0.0001"), rounding=ROUND_CEILING))


def warning_level(available: float, limit: Optional[float]) -> Optional[str]:
    """Avisos al quedar 25 %, 10 % o 0 % del presupuesto de IA de Marketing."""
    if limit is None or limit <= 0:                              # desconocido o 0: cerrado
        return "not_enabled"
    pct = 100 * max(available, 0) / limit
    if pct <= 0:
        return "exhausted"
    if pct <= 10:
        return "critical"
    if pct <= 25:
        return "low"
    return None


def budget_summary(limit: Optional[float], usage: Dict[str, float]) -> Dict[str, Any]:
    """"Marketing AI budget": SOLO el gasto de IA de Marketing del propio tenant (presupuesto, consumido,
    reservado y disponible). No representa el costo total de AITA del tenant (voz, infraestructura,
    almacenamiento e IA)."""
    used, held = round(float(usage.get("consumed") or 0), 4), round(float(usage.get("reserved") or 0), 4)
    lim = ai_budget({"monthly_ai_cost_limit": limit})              # desconocido/0/fuera de tope → 0 (cerrado)
    avail = max(round(lim - used - held, 4), 0.0)
    return {"scope": "marketing_ai_budget", "limit": lim, "consumed": used, "reserved": held, "available": avail,
            "currency": "USD",
            "warning": warning_level(avail, lim),
            "available_pct": None if not lim else math.floor(100 * avail / lim)}
