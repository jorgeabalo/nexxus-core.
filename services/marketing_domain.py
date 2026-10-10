"""
AITA Marketing — reglas de dominio puras (sin red ni base de datos).

  * Formatos, canales, idiomas y estados permitidos.
  * Transiciones de estado del contenido. La misma tabla existe en la
    migración (trigger marketing_content_transition) para que ni siquiera el
    service role pueda saltarse una regla; si se cambia aquí, cambiar allí.
  * Límites del plan: None = sin límite; 0 = nada permitido.
  * Validación de marca (colores, logo, web) con la misma política de logo que
    manager/assets/js/nav.js → safeLogoUrl, sin debilitarla.
"""
import re
from typing import Any, Dict, Iterable, List, Optional

FORMATS = ("image", "carousel", "reel", "story", "video", "text")
CHANNELS = ("instagram", "facebook", "tiktok", "linkedin", "x", "youtube", "google_business", "threads")
LANGUAGES = ("en", "es")
STATUSES = ("idea", "draft", "generating", "review", "approved", "rejected",
            "scheduled", "publishing", "published", "failed", "archived")
CAMPAIGN_STATUSES = ("planned", "active", "paused", "completed", "archived")

# estado actual → estados a los que se puede pasar
TRANSITIONS: Dict[str, tuple] = {
    "idea": ("draft", "archived"),
    "draft": ("idea", "generating", "review", "approved", "archived"),
    "generating": ("draft", "review", "failed"),
    "review": ("approved", "rejected", "draft"),
    "approved": ("scheduled", "draft", "archived"),
    "rejected": ("draft", "archived"),
    "scheduled": ("approved", "publishing"),
    "publishing": ("published", "failed"),
    "published": ("archived",),
    # Un fallo nunca vuelve a programarse solo: se corrige y se aprueba de nuevo.
    "failed": ("draft", "archived"),
    "archived": ("draft",),
}
# Solo se editan los textos mientras el contenido no está en revisión ni aprobado.
EDITABLE = ("idea", "draft", "rejected")
# Estados que cuentan contra el cupo mensual de publicaciones.
COUNTED = ("scheduled", "publishing", "published")
IMAGE_FORMATS = ("image", "carousel")
REEL_FORMATS = ("reel", "video")
LIMIT_KEYS = ("monthly_post_limit", "monthly_image_limit", "monthly_reel_limit",
              "connected_channel_limit", "competitor_limit")

# Valores por omisión cuando el tenant aún no tiene fila en marketing_settings:
# módulo apagado y aprobación humana obligatoria.
DEFAULT_SETTINGS = {
    "marketing_enabled": False, "approval_required": True, "plan_code": "none",
    **{k: None for k in LIMIT_KEYS},
}


class DomainError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def can_transition(current: str, target: str, *, approval_required: bool = True) -> bool:
    if target not in TRANSITIONS.get(current, ()):
        return False
    # Saltarse la revisión solo si el tenant desactivó la aprobación obligatoria.
    if current == "draft" and target == "approved" and approval_required:
        return False
    return True


def check_transition(current: str, target: str, *, approval_required: bool = True) -> None:
    if target not in STATUSES:
        raise DomainError("invalid_status", 400)
    # Nada se programa ni se publica sin haber sido aprobado.
    if (target == "scheduled" and current != "approved") or (target == "publishing" and current != "scheduled"):
        raise DomainError("not_approved", 409)
    if not can_transition(current, target, approval_required=approval_required):
        raise DomainError("invalid_transition", 409)


# --------------------------------------------------------------------- límites
def usage_for(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    """Uso del mes a partir de las filas de contenido que cuentan (COUNTED)."""
    rows = [r for r in rows if r.get("status") in COUNTED]
    return {
        "posts": len(rows),
        "images": sum(1 for r in rows if r.get("format") in IMAGE_FORMATS),
        "reels": sum(1 for r in rows if r.get("format") in REEL_FORMATS),
    }


def limit_reached(settings: Dict[str, Any], usage: Dict[str, int], fmt: str) -> Optional[str]:
    """Código del límite que impediría programar una pieza más de `fmt`, o None."""
    def over(key: str, used: int) -> bool:
        lim = settings.get(key)
        return lim is not None and used + 1 > int(lim)
    if over("monthly_post_limit", usage["posts"]):
        return "monthly_post_limit"
    if fmt in IMAGE_FORMATS and over("monthly_image_limit", usage["images"]):
        return "monthly_image_limit"
    if fmt in REEL_FORMATS and over("monthly_reel_limit", usage["reels"]):
        return "monthly_reel_limit"
    return None


# --------------------------------------------------------------------- marca
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
# Igual que safeLogoUrl en nav.js: ruta local con una sola barra (nunca //host ni /\host)
# o imagen rasterizada en base64. Nada de SVG ni URLs externas.
LOCAL_PATH = re.compile(r"^/(?![/\\])[^\s\\]*$")
DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+={0,2}$")
WEBSITE = re.compile(r"^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d{1,5})?(?:/[^\s<>\"']*)?$")


def safe_logo_url(url: Any) -> Optional[str]:
    if not isinstance(url, str) or len(url) > 2_000_000:
        return None
    return url if LOCAL_PATH.match(url) or DATA_IMAGE.match(url) else None


def color(v: Any) -> Optional[str]:
    return v.upper() if isinstance(v, str) and HEX.match(v) else None


def tenant_brand_defaults(tenant: Dict[str, Any]) -> Dict[str, Any]:
    """Brand Kit inicial a partir de la fila del tenant ACTIVO (no se guarda hasta que se edita).

    Mismas fuentes que tenantProfile() en nav.js. El logo del tenant no se copia
    (puede ser una imagen en base64): se muestra por referencia con tenant_logo_url.
    """
    t = tenant or {}
    b = t.get("branding") or {}
    s = t.get("settings") or {}
    lang = (b.get("language") or str(b.get("locale") or "")[:2]).lower()
    return {
        "business_name": b.get("business_name") or t.get("name") or None,
        "description": None, "target_audience": None, "tone": None,
        "languages": [lang] if lang in LANGUAGES else [],
        "priority_services": [], "cta": None,
        "phone": s.get("public_phone") or b.get("phone") or None,
        "website": b.get("website") if isinstance(b.get("website"), str) and WEBSITE.match(b.get("website")) else None,
        "color_primary": color(b.get("color_primary")),
        "color_accent": color(b.get("color_accent")),
        "color_secondary": None,
        "logo_url": None,
        "banned_topics": [], "compliance_notes": None, "ai_instructions": None,
    }


# --------------------------------------------------------------------- texto
def text(v: Any, limit: int, code: str, required: bool = False) -> Optional[str]:
    s = str(v).strip() if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""
    if required and not s:
        raise DomainError(code, 400)
    if len(s) > limit:
        raise DomainError(code, 400)
    return s or None


def text_list(v: Any, allowed: Optional[Iterable[str]], code: str, *, max_items: int = 30,
              item_limit: int = 120) -> List[str]:
    if v in (None, ""):
        return []
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\n]", v)]
    if not isinstance(v, list) or len(v) > max_items:
        raise DomainError(code, 400)
    out: List[str] = []
    for item in v:
        s = text(item, item_limit, code)
        if not s:
            continue
        if allowed is not None and s not in allowed:
            raise DomainError(code, 400)
        if s not in out:
            out.append(s)
    return out


def hashtags(v: Any) -> List[str]:
    tags = text_list(v if not isinstance(v, str) else re.split(r"[\s,]+", v), None, "invalid_hashtags", item_limit=60)
    out = []
    for t in tags:
        t = t.lstrip("#")
        if not re.fullmatch(r"[\wÀ-ɏ]{1,59}", t):
            raise DomainError("invalid_hashtags", 400)
        out.append(f"#{t}")
    return out
