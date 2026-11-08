"""
AITA Marketing — carrusel (láminas editables) dentro de un contenido de formato «carousel».

* Sin migración: el carrusel se guarda en marketing_content.script como JSON con "kind": "carousel"
  (la columna admite 10 000 caracteres; 10 láminas con sus textos y referencias caben con holgura).
  Un guion de texto libre sigue siendo válido: solo se trata como carrusel si es este JSON.
* Cada lámina: papel (portada, desarrollo, cierre), título, texto, imagen opcional de la Biblioteca,
  referencias que la respaldan y una marca «revisar» para datos que aún no se han comprobado.
* Las imágenes se comprueban en el servidor: del mismo tenant, imagen, lista y con privacidad apta. En un
  canal médico solo material sin personas (nada de pacientes, aunque haya consentimiento).
* Las láminas se dibujan y se exportan en el navegador (PNG 1080 × 1350); nada sale a proveedores.
"""
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from services import marketing_domain as d
from services import marketing_privacy as pv
from services.marketing_copywriter import MEDICAL_FORBIDDEN
from services.marketing_domain import DomainError
from services.marketing_reel_plan import FORBIDDEN_CLAIMS

KIND = "carousel"
ROLES = ("cover", "content", "close")
THEMES = ("light", "dark", "brand")
MIN_SLIDES, MAX_SLIDES = 2, 10
MAX_REFS = 15
SIZE = "1080x1350"                       # 4:5 vertical, el formato de carrusel de Instagram
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
HTTPS = re.compile(r"^https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d{1,5})?(?:/[^\s<>\"']*)?$")
SCRIPT_MAX = 10000


def parse(script: Any) -> Optional[Dict[str, Any]]:
    """El carrusel guardado en `script`, o None si el guion es texto libre."""
    if not isinstance(script, str) or not script.lstrip().startswith("{"):
        return None
    try:
        doc = json.loads(script)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) and doc.get("kind") == KIND else None


def _bool(v: Any) -> bool:
    return v is True


def validate(raw: Any) -> Dict[str, Any]:
    """Normaliza el carrusel que envía el panel. Lanza DomainError con un código si algo no cabe."""
    if not isinstance(raw, dict):
        raise DomainError("invalid_carousel", 400)
    refs_in = raw.get("references") or []
    if not isinstance(refs_in, list) or len(refs_in) > MAX_REFS:
        raise DomainError("invalid_carousel_reference", 400)
    refs: List[Dict[str, str]] = []
    for r in refs_in:
        if not isinstance(r, dict):
            raise DomainError("invalid_carousel_reference", 400)
        label = d.text(r.get("label"), 240, "invalid_carousel_reference", True)
        url = d.text(r.get("url"), 500, "invalid_carousel_reference")
        if url and not HTTPS.match(url):
            raise DomainError("invalid_carousel_reference", 400)
        refs.append({"label": label, "url": url or ""})
    slides_in = raw.get("slides")
    if not isinstance(slides_in, list) or not (MIN_SLIDES <= len(slides_in) <= MAX_SLIDES):
        raise DomainError("invalid_carousel_slides", 400)
    slides = []
    for i, s in enumerate(slides_in):
        if not isinstance(s, dict):
            raise DomainError("invalid_carousel_slides", 400)
        role = s.get("role") or ("cover" if i == 0 else "close" if i == len(slides_in) - 1 else "content")
        if role not in ROLES:
            raise DomainError("invalid_carousel_slides", 400)
        media = s.get("media_id") or None
        if media is not None and not (isinstance(media, str) and UUID.match(media)):
            raise DomainError("invalid_carousel_media", 400)
        cites = s.get("refs") or []
        if not isinstance(cites, list) or any(not isinstance(n, int) or isinstance(n, bool)
                                              or not 0 <= n < len(refs) for n in cites):
            raise DomainError("invalid_carousel_reference", 400)
        slides.append({"role": role,
                       "title": d.text(s.get("title"), 90, "invalid_carousel_title") or "",
                       "body": d.text(s.get("body"), 320, "invalid_carousel_body") or "",
                       "media_id": media, "refs": sorted(set(cites)), "review": _bool(s.get("review"))})
    if not any(s["title"] or s["body"] for s in slides):
        raise DomainError("invalid_carousel_slides", 400)
    theme = raw.get("theme") or "light"
    if theme not in THEMES:
        raise DomainError("invalid_carousel", 400)
    return {"kind": KIND, "v": 1, "size": SIZE, "theme": theme,
            "audience": d.text(raw.get("audience"), 200, "invalid_carousel") or "",
            "tone": d.text(raw.get("tone"), 120, "invalid_carousel") or "",
            "slides": slides, "references": refs,
            "medical_review": _bool(raw.get("medical_review"))}


def dump(doc: Dict[str, Any]) -> str:
    out = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    if len(out) > SCRIPT_MAX:
        raise DomainError("carousel_too_long", 400)
    return out


def texts(doc: Dict[str, Any]) -> List[str]:
    return [t for s in doc["slides"] for t in (s["title"], s["body"]) if t]


def check_claims(strings: Iterable[str], account_type: str) -> None:
    """Las mismas frases prohibidas que el redactor con IA: sin curas, garantías ni «deja tu medicación»."""
    for t in strings:
        if FORBIDDEN_CLAIMS.search(t) or (account_type == "medical_education" and MEDICAL_FORBIDDEN.search(t)):
            raise DomainError("carousel_claim_blocked", 422)


def allowed_class(cls: str, account_type: str) -> bool:
    if account_type == "medical_education":
        return cls == "business_media_no_people"
    return cls in ("business_media_no_people", "consented_people")


def check_media(doc: Dict[str, Any], load: Callable[[List[str]], List[Dict[str, Any]]], account_type: str) -> None:
    """Cada imagen elegida existe en ESTE tenant, es una imagen lista y su privacidad permite usarla."""
    ids = sorted({s["media_id"] for s in doc["slides"] if s["media_id"]})
    if not ids:
        return
    rows = {m["id"]: m for m in load(ids) or []}
    for mid in ids:
        m = rows.get(mid)
        if (not m or m.get("deleted_at") or m.get("media_type") != "image" or pv.usable_media(m) is not None
                or not allowed_class(pv.original_class(m), account_type)):
            raise DomainError("carousel_media_not_allowed", 409)


SLIDE_LINE = re.compile(r"^\s*(?:l[aá]mina|slide|diapositiva)?\s*(\d{1,2})\s*[.):\-–—]\s*(.+)$", re.I)


def from_text(text: str, current: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Convierte el texto del redactor («1. Título | texto», una lámina por línea) en láminas.

    Conserva del carrusel actual las imágenes, referencias y ajustes; las marcas «revisar» se encienden en
    todas las láminas nuevas: un texto escrito por IA siempre se revisa. None si no hay láminas reconocibles."""
    found: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        m = SLIDE_LINE.match(line)
        if not m:
            if found and line.strip():                       # continuación de la lámina anterior
                found[-1]["body"] = (found[-1]["body"] + " " + line.strip()).strip()
            continue
        head, _, rest = m.group(2).partition("|")
        found.append({"title": head.strip().strip("*")[:90], "body": rest.strip()[:320]})
    found = found[:MAX_SLIDES]
    if len(found) < MIN_SLIDES:
        return None
    cur = current or {}
    old = cur.get("slides") or []
    slides = []
    for i, s in enumerate(found):
        role = "cover" if i == 0 else "close" if i == len(found) - 1 else "content"
        prev = old[i] if i < len(old) else {}
        slides.append({"role": role, "title": s["title"], "body": s["body"][:320],
                       "media_id": prev.get("media_id"), "refs": prev.get("refs") or [], "review": True})
    return validate({**cur, "slides": slides, "medical_review": False})


def script_for(db, tenant_id: str, fmt: str, raw: Any, caption: Optional[str]) -> str:
    """Carrusel validado (textos, imágenes del tenant con privacidad apta, referencias) como JSON para script."""
    if fmt != "carousel":
        raise DomainError("invalid_carousel", 400)
    doc = validate(raw)
    row = (db.select("marketing_brand_profiles", {"tenant_id": f"eq.{tenant_id}", "select": "account_type",
                                                  "limit": "1"}) or [{}])[0]
    kind = row.get("account_type") or "local_business"
    check_claims([*texts(doc), caption or ""], kind)
    check_media(doc, lambda ids: db.select("marketing_media", {
        "tenant_id": f"eq.{tenant_id}", "id": f"in.({','.join(ids)})", "limit": str(len(ids)),
        "select": "id,media_type,deleted_at,consent_status,retention_status,processing_status,validation_status,"
                  "contains_people,contains_minors,people_policy"}), kind)
    return dump(doc)
