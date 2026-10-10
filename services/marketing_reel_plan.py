"""
AITA Marketing (Fase 2) — Estudio de Reels: opciones del asistente y estimación de coste.

Asistente en 7 pasos: 1 objetivo · 2 audiencia · 3 fuentes (archivos + mezcla real/IA) ·
4 estilo · 5 guion · 6 coste y aprobación de la generación · 7 revisión.
Formato: 9:16, MP4, subtítulos dentro del área segura, portada seleccionable. Destinos futuros:
Instagram, Facebook, TikTok y YouTube Shorts (aquí NO se publica nada).

La estimación descompone el Reel en subtareas y pide al MarketingAIRouter un modelo para cada
una. Si la suma supera el coste máximo aprobado → budget_exceeded (nunca se sube de nivel solo).
"""
import re
from typing import Any, Dict, List

from services.marketing_ai_router import MarketingAIRouter, RouteRequest, RouterError
from services.marketing_domain import DomainError
from services.marketing_mix import plan_scenes

OBJECTIVES = ("new_members", "class_promo", "event", "offer", "brand", "education")
AUDIENCES = ("general", "seniors_60_plus", "beginners", "athletes", "parents", "custom")
STYLES = ("energetic", "calm", "professional", "cinematic", "educational")
DURATIONS = (15, 30, 45, 60)
FUTURE_TARGETS = ("instagram", "facebook", "tiktok", "youtube_shorts")
ASPECT_RATIO, OUTPUT_FORMAT = "9:16", "mp4"
# Prohibido en adaptaciones y guiones (también se comprueba con moderación cuando exista proveedor).
FORBIDDEN_CLAIMS = re.compile(r"(?i)(antes\s*y\s*despu[eé]s|before\s*(and|&)\s*after|garantizad|guaranteed|"
                              r"pierde\s+\d+\s*(kg|lb|libras|kilos)|lose\s+\d+\s*(kg|lb|pounds)|testimoni)")


def _text(v: Any, n: int) -> str:
    s = str(v or "").strip()
    if len(s) > n:
        raise DomainError("text_too_long", 400)
    return s


def validate_brief(b: Dict[str, Any]) -> Dict[str, Any]:
    if b.get("objective") not in OBJECTIVES:
        raise DomainError("invalid_objective", 400)
    if b.get("audience") not in AUDIENCES:
        raise DomainError("invalid_audience", 400)
    if b.get("style") not in STYLES:
        raise DomainError("invalid_style", 400)
    try:
        duration = int(b.get("duration_seconds") or 30)
    except (TypeError, ValueError):
        raise DomainError("invalid_duration", 400)
    if duration not in DURATIONS:
        raise DomainError("invalid_duration", 400)
    targets = [t for t in (b.get("targets") or []) if t in FUTURE_TARGETS]
    script = {k: _text((b.get("script") or {}).get(k), 300) for k in ("hook", "body", "cta")}
    for v in (*script.values(), b.get("audience_notes")):
        if v and FORBIDDEN_CLAIMS.search(str(v)):
            raise DomainError("forbidden_claim", 400)
    return {"objective": b["objective"], "audience": b["audience"], "audience_notes": _text(b.get("audience_notes"), 300),
            "style": b["style"], "duration_seconds": duration, "subtitles": b.get("subtitles") is not False,
            "aspect_ratio": ASPECT_RATIO, "format": OUTPUT_FORMAT, "targets": targets, "script": script,
            "cover_scene": int(b.get("cover_scene") or 0), "language": "en" if b.get("language") == "en" else "es"}


def subtasks(plan: Dict[str, Any], input_class: str, people_policy: str, real_kinds: List[str],
             subtitles: bool) -> List[Dict[str, Any]]:
    """Lista de subtareas con su clase de privacidad y unidades. Las herramientas locales cubren
    subtítulos, montaje y anonimización; la IA solo se usa en las escenas generadas o adaptadas."""
    out = [{"task_type": "storyboard", "units": 1, "privacy_class": "synthetic_only", "inputs": ()},
           {"task_type": "moderation", "units": 1, "privacy_class": "synthetic_only", "inputs": ()}]
    by = plan["by_origin"]
    if by["ai_generated"]["seconds"]:
        out.append({"task_type": "text_to_video", "units": by["ai_generated"]["seconds"],
                    "privacy_class": "synthetic_only", "inputs": ()})
    if by["client_ai_adapted"]["seconds"]:
        out.append({"task_type": "image_to_video", "units": by["client_ai_adapted"]["seconds"],
                    "privacy_class": input_class, "inputs": tuple(sorted(set(real_kinds) or {"image"}))})
    if people_policy == "anonymize" and (by["client_original"]["scenes"] or by["client_ai_adapted"]["scenes"]):
        out.append({"task_type": "face_anonymization", "units": 1, "privacy_class": "restricted",
                    "inputs": tuple(sorted(set(real_kinds) or {"image"}))})
    if subtitles:
        out.append({"task_type": "subtitles", "units": 1, "privacy_class": "synthetic_only", "inputs": ()})
    out.append({"task_type": "final_render", "units": 1, "privacy_class": input_class
                if (by["client_original"]["scenes"] or by["client_ai_adapted"]["scenes"]) else "synthetic_only",
                "inputs": ()})
    return out


def estimate(router: MarketingAIRouter, *, tenant_id: str, job_key: str, brief: Dict[str, Any], mix: Dict[str, int],
             scenes: int, adapt_real: bool, input_class: str, people_policy: str, real_kinds: List[str],
             quality_tier: str, maximum_cost: float, inputs_malware_clean: bool = False) -> Dict[str, Any]:
    plan = plan_scenes(mix["real_media_percent"], mix["ai_media_percent"], scenes, brief["duration_seconds"], adapt_real)
    if plan["by_origin"]["client_original"]["scenes"] + plan["by_origin"]["client_ai_adapted"]["scenes"] and not real_kinds:
        raise DomainError("real_media_required", 400)
    rows, total = [], 0.0
    for i, st in enumerate(subtasks(plan, input_class, people_policy, real_kinds, brief["subtitles"])):
        req = RouteRequest(tenant_id=tenant_id, task_type=st["task_type"], quality_tier=quality_tier,
                           maximum_cost=float(maximum_cost), privacy_class=st["privacy_class"],
                           idempotency_key=f"{job_key}:{i}:{st['task_type']}"[:160], units=float(st["units"]),
                           input_media_types=st["inputs"], aspect_ratio=ASPECT_RATIO,
                           inputs_malware_clean=inputs_malware_clean,
                           duration=brief["duration_seconds"], language=brief["language"])
        try:
            route = router.route(req)
        except RouterError as e:
            raise DomainError("privacy_blocked" if e.code == "no_eligible_model" and st["privacy_class"] == "restricted"
                              else e.code, e.status)
        total += route.estimated_cost
        rows.append({"task_type": st["task_type"], "units": st["units"], "billing_unit": route.model.billing_unit,
                     "provider": route.model.provider, "model_id": route.model.model_id,
                     "external": route.model.external, "privacy_class": st["privacy_class"],
                     "estimated_cost": route.estimated_cost, "fallbacks": [m.model_id for m in route.fallbacks],
                     "idempotency_key": req.idempotency_key})
    total = round(total, 4)
    if total > float(maximum_cost):
        raise DomainError("budget_exceeded", 409)
    images = 0
    video_seconds = plan["by_origin"]["ai_generated"]["seconds"] + plan["by_origin"]["client_ai_adapted"]["seconds"]
    return {"plan": plan, "subtasks": rows, "estimated_cost": total, "currency": router.catalog.currency,
            "catalog_version": router.catalog.version, "price_source": router.catalog.price_source,
            "generated_images": images, "generated_video_seconds": video_seconds,
            "external_calls": sum(1 for r in rows if r["external"])}
