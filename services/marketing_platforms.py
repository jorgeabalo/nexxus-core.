"""
AITA Marketing — reglas de cada plataforma (sin red ni base de datos).

Una sola publicación se adapta a cada red: medida del visual según el formato,
longitud máxima del texto y cuántos hashtags caben. Lo usan el redactor con IA
(marketing_copywriter) y, sin IA, la adaptación automática del texto que escribió
la persona. La misma tabla existe en manager/assets/js/modules/marketing-platforms.js
para la vista previa; si se cambia aquí, cambiar allí.
"""
import re
from typing import Any, Dict, List

from services.marketing_domain import CHANNELS

# text_max: límite del texto que publica la red. hashtags: cuántos conviene usar.
RULES: Dict[str, Dict[str, Any]] = {
    "instagram": {"text_max": 2200, "hashtags": 5},
    "facebook": {"text_max": 2000, "hashtags": 3},
    "tiktok": {"text_max": 2200, "hashtags": 5},
    "linkedin": {"text_max": 3000, "hashtags": 3},
    "x": {"text_max": 280, "hashtags": 2},
    "youtube": {"text_max": 5000, "hashtags": 3},
    "google_business": {"text_max": 1500, "hashtags": 0},
    "threads": {"text_max": 500, "hashtags": 1},
}

# Medida del visual (ancho:alto) por formato y red. Las verticales sirven para reels, TikTok y stories.
ASPECT: Dict[str, Dict[str, str]] = {
    "image": {"instagram": "4:5", "facebook": "4:5", "tiktok": "9:16", "linkedin": "1:1", "x": "16:9",
              "youtube": "16:9", "google_business": "4:3", "threads": "4:5"},
    "carousel": {"instagram": "4:5", "facebook": "1:1", "tiktok": "9:16", "linkedin": "1:1", "x": "1:1",
                 "youtube": "16:9", "google_business": "4:3", "threads": "4:5"},
    "reel": {c: "9:16" for c in CHANNELS},
    "story": {c: "9:16" for c in CHANNELS},
    "video": {"instagram": "9:16", "facebook": "9:16", "tiktok": "9:16", "linkedin": "1:1", "x": "16:9",
              "youtube": "16:9", "google_business": "16:9", "threads": "9:16"},
    "text": {c: "" for c in CHANNELS},
}


def _cut(text: str, limit: int) -> str:
    """Recorta sin partir palabras ni frases a medias (preferimos terminar en punto)."""
    if len(text) <= limit:
        return text
    head = text[: max(limit - 1, 0)]
    stop = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind("\n"))
    if stop >= limit // 2:
        return head[: stop + 1].rstrip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip(" ,;:") + "…"


def compose(caption: str, cta: str, hashtags: List[str], channel: str) -> str:
    """Texto final para una red: mensaje + llamada a la acción + hashtags, dentro del límite."""
    rule = RULES[channel]
    tags = " ".join(hashtags[: rule["hashtags"]])
    tail = "\n\n".join(x for x in ((cta or "").strip(), tags) if x)
    body = (caption or "").strip()
    if not tail:
        return _cut(body, rule["text_max"])
    room = rule["text_max"] - len(tail) - 2
    if room < 40:                                   # la red es muy corta (X): sin hashtags si no caben
        tail = (cta or "").strip()
        room = rule["text_max"] - len(tail) - 2
    return "\n\n".join(x for x in (_cut(body, max(room, 0)), tail) if x)


def variants(content: Dict[str, Any], texts: Dict[str, str] = None) -> Dict[str, Dict[str, Any]]:
    """Una versión por red elegida. `texts` son los textos propios que propuso la IA por red (opcionales)."""
    texts = texts or {}
    fmt = content.get("format") or "image"
    tags = [t for t in (content.get("hashtags") or []) if isinstance(t, str)]
    out: Dict[str, Dict[str, Any]] = {}
    for ch in content.get("channels") or []:
        if ch not in RULES:
            continue
        own = re.sub(r"[ \t]+\n", "\n", str(texts.get(ch) or "")).strip()
        text = _cut(own, RULES[ch]["text_max"]) if own else compose(content.get("caption") or "",
                                                                   content.get("cta") or "", tags, ch)
        out[ch] = {"text": text, "aspect": ASPECT.get(fmt, {}).get(ch, ""), "limit": RULES[ch]["text_max"]}
    return out
