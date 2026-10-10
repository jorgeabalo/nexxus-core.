"""
AITA Marketing (Fase 2) — mezcla de material real y generado con IA en un Reel.

* El usuario elige el porcentaje por Reel: presets 100/0, 75/25, 50/50, 25/75, 0/100 o uno
  personalizado en pasos de 5 %. Real + IA = 100 siempre.
* El porcentaje se convierte en escenas y segundos con el método del resto mayor (Hamilton),
  así el total de escenas y de segundos cuadra exactamente. La aproximación se muestra ANTES
  de generar.
* La IA puede SUGERIR una mezcla (queda marcada como sugerencia), pero no la cambia: solo
  owner/manager la confirman, y se guarda con el trabajo. Cambiarla en una regeneración exige
  confirmar de nuevo.
"""
from typing import Any, Dict, List, Optional

from services.marketing_domain import DomainError

PRESETS = ((100, 0), (75, 25), (50, 50), (25, 75), (0, 100))
STEP = 5
MIN_SCENES, MAX_SCENES = 1, 20
MIN_SECONDS, MAX_SECONDS = 5, 90


def validate_mix(real: Any, ai: Any) -> Dict[str, int]:
    try:
        r, a = int(real), int(ai)
    except (TypeError, ValueError):
        raise DomainError("invalid_mix", 400)
    if isinstance(real, bool) or isinstance(ai, bool) or str(real).strip() != str(r) or str(ai).strip() != str(a):
        raise DomainError("invalid_mix", 400)
    if not (0 <= r <= 100 and 0 <= a <= 100) or r + a != 100:
        raise DomainError("mix_must_total_100", 400)
    if r % STEP:
        raise DomainError("mix_step", 400)
    return {"real_media_percent": r, "ai_media_percent": a}


def _hamilton(total: int, weights: List[int]) -> List[int]:
    """Reparte `total` en enteros proporcionales a `weights` (resto mayor; empate → primero)."""
    s = sum(weights)
    if s == 0 or total == 0:
        return [0] * len(weights)
    exact = [total * w / s for w in weights]
    out = [int(x) for x in exact]
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - out[i]), i))
    for i in order[: total - sum(out)]:
        out[i] += 1
    return out


def plan_scenes(real: int, ai: int, scenes: int, duration_s: int, adapt_real: bool = False) -> Dict[str, Any]:
    """Aproximación de la mezcla en escenas y segundos. Si adapt_real, el material real se usa
    adaptado con IA (client_ai_adapted) en lugar de tal cual (client_original)."""
    mix = validate_mix(real, ai)
    if not (MIN_SCENES <= int(scenes) <= MAX_SCENES):
        raise DomainError("invalid_scene_count", 400)
    if not (MIN_SECONDS <= int(duration_s) <= MAX_SECONDS):
        raise DomainError("invalid_duration", 400)
    n_real, n_ai = _hamilton(int(scenes), [mix["real_media_percent"], mix["ai_media_percent"]])
    sec_scene = _hamilton(int(duration_s), [1] * int(scenes))      # segundos por escena, total exacto
    real_origin = "client_ai_adapted" if adapt_real else "client_original"
    # intercalado estable: reparte las escenas reales a lo largo del Reel
    slots, ri = [], 0
    for i in range(int(scenes)):
        want_real = n_real and ri < n_real and (i * n_real) // int(scenes) == ri
        slots.append(real_origin if want_real else "ai_generated")
        ri += 1 if want_real else 0
    # ajuste si el intercalado no colocó todas las reales (no debería ocurrir)
    while slots.count(real_origin) < n_real:
        slots[slots.index("ai_generated")] = real_origin
    plan = [{"index": i, "origin": o, "seconds": sec_scene[i]} for i, o in enumerate(slots)]
    by = {o: {"scenes": 0, "seconds": 0} for o in ("client_original", "client_ai_adapted", "ai_generated")}
    for p in plan:
        by[p["origin"]]["scenes"] += 1
        by[p["origin"]]["seconds"] += p["seconds"]
    real_s = by["client_original"]["seconds"] + by["client_ai_adapted"]["seconds"]
    return {**mix, "scenes": int(scenes), "duration_seconds": int(duration_s), "plan": plan, "by_origin": by,
            "approx_real_percent": round(100 * real_s / int(duration_s)),
            "note": "approximation"}


def suggest_mix(real_assets: int, consented_or_clean: int) -> Dict[str, Any]:
    """Sugerencia determinista (no vinculante) según cuánto material utilizable hay."""
    if real_assets <= 0 or consented_or_clean <= 0:
        r = 0
    elif consented_or_clean >= 6:
        r = 75
    elif consented_or_clean >= 3:
        r = 50
    else:
        r = 25
    return {"real_media_percent": r, "ai_media_percent": 100 - r, "is_suggestion": True,
            "requires_confirmation": True}


def confirm_mix(role: str, real: Any, ai: Any, previous: Optional[Dict[str, int]] = None,
                reconfirmed: bool = False) -> Dict[str, int]:
    """Solo owner/manager confirman la mezcla. Una regeneración que la cambia exige reconfirmar."""
    if role not in ("owner", "manager"):
        raise DomainError("forbidden", 403)
    mix = validate_mix(real, ai)
    if previous and mix != {k: previous.get(k) for k in mix} and not reconfirmed:
        raise DomainError("mix_change_requires_confirmation", 409)
    return mix
