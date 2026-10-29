"""
AITA Marketing — redactor con IA (Claude) para publicaciones, reels y vídeos.

Escribe el texto de una publicación a partir del Brand Kit y de lo que pidió la
persona: mensaje, llamada a la acción, hashtags, guion paso a paso para reels y
vídeos, y una versión adaptada a cada red elegida.

  * Solo sale texto: nunca fotos, vídeos ni datos de clientes.
  * Sin ANTHROPIC_API_KEY (o con MARKETING_TEXT_AI=0) está apagado y no se llama a nadie.
  * Nada se publica: el resultado queda como borrador para que una persona lo revise.
  * Se rechaza lo que prometa resultados, curas o "antes y después", y los temas
    prohibidos del Brand Kit, aunque la IA los proponga.
"""
import json
import os
from typing import Any, Dict, List, Optional

from services.marketing_domain import CHANNELS, DomainError
from services.marketing_platforms import RULES
from services.marketing_reel_plan import FORBIDDEN_CLAIMS

DEFAULT_MODEL = "claude-opus-5-5"
SCRIPT_FORMATS = ("reel", "video", "story", "carousel")

SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string", "description": "Texto principal de la publicación."},
        "cta": {"type": "string", "description": "Llamada a la acción breve y concreta."},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "script": {"type": "string", "description": "Guion por escenas (reel/vídeo/historia) o diapositivas (carrusel)."},
        "per_platform": {"type": "array", "items": {
            "type": "object",
            "properties": {"channel": {"type": "string", "enum": list(CHANNELS)}, "text": {"type": "string"}},
            "required": ["channel", "text"], "additionalProperties": False}},
    },
    "required": ["caption", "cta", "hashtags", "script", "per_platform"],
    "additionalProperties": False,
}

SYSTEM = """Eres el redactor de marketing de un negocio local. Escribes publicaciones para redes sociales \
que suenan humanas, claras y fieles a la marca.

Reglas que nunca se rompen:
- Nunca prometas resultados, curas, pérdidas de peso concretas ni "antes y después". No inventes testimonios, \
precios, horarios ni datos que no estén en la información de la marca.
- No uses los temas prohibidos de la marca.
- Nunca prometas que algo se hará viral.
- Escribe en el idioma pedido.

Qué entregas:
- caption: el mensaje principal, con un gancho en la primera línea.
- cta: una llamada a la acción breve (llamar, escribir, reservar una prueba…), usando el teléfono o la web de \
la marca si existen.
- hashtags: entre 3 y 8, relevantes y locales si hay ciudad, sin repetir.
- script: para reel, vídeo o historia. Escenas numeradas, cada una con: duración en segundos, qué se ve \
(plano sugerido), texto en pantalla y voz o subtítulo. Empieza con un gancho de 2–3 segundos. Para \
carrusel, el texto de cada diapositiva numerada. Para imagen o texto déjalo vacío.
- per_platform: una versión para cada red pedida, respetando su límite de caracteres y su estilo \
(X corto y directo; LinkedIn más profesional; Google Business informativo y sin hashtags; TikTok e Instagram \
cercanos). Incluye la llamada a la acción en cada versión."""


def enabled(env: Optional[Dict[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("ANTHROPIC_API_KEY")) and str(env.get("MARKETING_TEXT_AI", "1")) != "0"


def model(env: Optional[Dict[str, str]] = None) -> str:
    env = os.environ if env is None else env
    return (env.get("MARKETING_TEXT_MODEL") or DEFAULT_MODEL).strip()


def _brand_text(brand: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("business_name", "description", "target_audience", "tone", "priority_services", "cta", "phone",
            "website", "banned_topics", "compliance_notes", "ai_instructions")
    return {k: brand.get(k) for k in keys if brand.get(k)}


def prompt(content: Dict[str, Any], brand: Dict[str, Any], request: str = "") -> str:
    channels = [c for c in content.get("channels") or [] if c in RULES]
    ask = {
        "idioma": "español" if content.get("language") == "es" else "English",
        "formato": content.get("format"), "objetivo": content.get("objective"), "tema": content.get("topic"),
        "titulo_interno": content.get("title"), "notas_de_la_persona": content.get("caption") or content.get("notes"),
        "llamada_a_la_accion_sugerida": content.get("cta"),
        "redes": {c: {"limite_caracteres": RULES[c]["text_max"], "hashtags_max": RULES[c]["hashtags"]} for c in channels},
        "cambios_pedidos": request or None,
    }
    return ("Información de la marca:\n" + json.dumps(_brand_text(brand), ensure_ascii=False, indent=1)
            + "\n\nPublicación que hay que escribir:\n"
            + json.dumps({k: v for k, v in ask.items() if v}, ensure_ascii=False, indent=1))


def _check(texts: List[str], brand: Dict[str, Any]) -> None:
    banned = [b.lower() for b in brand.get("banned_topics") or [] if isinstance(b, str) and b.strip()]
    for t in texts:
        low = t.lower()
        if FORBIDDEN_CLAIMS.search(t) or any(b in low for b in banned):
            raise DomainError("ai_output_rejected", 422)


def write(content: Dict[str, Any], brand: Dict[str, Any], request: str = "", client=None,
          env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Devuelve {caption, cta, hashtags, script, per_platform, model, usage}. Lanza DomainError si no se puede."""
    if client is None:
        if not enabled(env):
            raise DomainError("text_ai_unavailable", 503)
        import anthropic
        client = anthropic.Anthropic(timeout=90.0, max_retries=1)
    name = model(env)
    try:
        msg = client.messages.create(
            model=name, max_tokens=8000, system=SYSTEM, output_config={
                "effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt(content, brand, request)}])
    except DomainError:
        raise
    except Exception:                     # red, cuota o clave: nunca se registra el detalle (puede llevar datos)
        raise DomainError("text_ai_unavailable", 503)
    if getattr(msg, "stop_reason", None) == "refusal":
        raise DomainError("ai_output_rejected", 422)
    try:
        data = json.loads(next(b.text for b in msg.content if getattr(b, "type", "") == "text"))
    except (StopIteration, ValueError, TypeError):
        raise DomainError("text_ai_unavailable", 503)
    channels = set(content.get("channels") or [])
    per = {p["channel"]: str(p.get("text") or "").strip() for p in data.get("per_platform") or []
           if isinstance(p, dict) and p.get("channel") in channels}
    out = {"caption": str(data.get("caption") or "").strip()[:5000], "cta": str(data.get("cta") or "").strip()[:200],
           "hashtags": [str(h) for h in data.get("hashtags") or []][:30],
           "script": (str(data.get("script") or "").strip()[:10000]
                      if content.get("format") in SCRIPT_FORMATS else ""),
           "per_platform": per}
    _check([out["caption"], out["cta"], out["script"], *per.values()], brand)
    usage = getattr(msg, "usage", None)
    out["model"] = name
    out["usage"] = {"input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "output_tokens", 0) or 0)}
    return out
