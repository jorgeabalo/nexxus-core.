"""
AITA Marketing (Fase 2) — personas reales en la Biblioteca.

Regla crítica: un archivo con personas reales NUNCA va a un proveedor externo salvo que tenga
consentimiento registrado (people_policy = consented y consent_status = granted) o que se use un
DERIVADO anonimizado y revisado por una persona. El original nunca se altera.

* Política por defecto cuando no se sabe si hay personas: exclude (no se usa en IA externa).
* Anonimizar crea un derivado. Se intenta en local; si la detección no es fiable (confianza baja
  o detector no disponible), se detiene y se pide revisión humana. Nunca se afirma anonimato total.
* Sin reconocimiento facial: no se identifica a nadie ni se comparan rasgos biométricos. El
  detector solo dice "hay una cara aquí" (cajas), nunca "quién es".
* Menores: si hay (o puede haber) personas y no se ha confirmado que NO hay menores, el archivo
  queda excluido. El consentimiento puede retirarse; retirado = excluido al instante.
* Mientras la anonimización sea simulada, ningún derivado se considera anonimizado de verdad:
  queda 'mock_only' (o 'awaiting_processing') y nunca sale, nunca se aprueba ni se usa.
* Validar el formato no es un antivirus: hacia un proveedor EXTERNO solo puede salir material
  real con malware_scan_status = 'clean' (hoy no hay escáner → nunca sale).
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from services.marketing_domain import DomainError

PEOPLE_POLICIES = ("exclude", "anonymize", "consented", "no_people")
CONSENT_STATUSES = ("unknown", "not_required", "pending", "granted", "revoked")
ANONYMIZE_METHODS = ("pixelate_faces", "blur_faces", "crop_people", "silhouette", "replace_background_and_people")
PRIVACY_CLASSES = ("synthetic_only", "business_media_no_people", "anonymized_people", "consented_people", "restricted")
# Clases que pueden salir hacia un proveedor externo (restricted nunca).
EXTERNAL_OK = ("synthetic_only", "business_media_no_people", "anonymized_people", "consented_people")
MIN_CONFIDENCE = 0.85

# Avisos que la interfaz muestra siempre (ES/EN en el frontend).
WARNINGS = ("pixelation_not_guaranteed", "body_tattoos_uniform_voice_location_identify", "business_responsible_consent")


def classify(contains_people: Optional[bool], people_policy: str, consent_status: str,
             contains_minors: Optional[bool] = None) -> Dict[str, Any]:
    """Valida la clasificación que hace owner/manager y devuelve la clase de privacidad del ORIGINAL."""
    if people_policy not in PEOPLE_POLICIES:
        raise DomainError("invalid_people_policy", 400)
    if consent_status not in CONSENT_STATUSES:
        raise DomainError("invalid_consent_status", 400)
    if contains_people not in (True, False, None) or contains_minors not in (True, False, None):
        raise DomainError("invalid_contains_people", 400)
    if people_policy == "no_people" and (contains_people is not False or contains_minors is True):
        raise DomainError("no_people_requires_confirmation", 400)
    if contains_people is False and contains_minors is None:
        contains_minors = False                      # sin personas no hay menores
    if people_policy != "exclude" and contains_people is not False and contains_minors is not False:
        raise DomainError("minors_excluded", 400)    # menores o desconocido: siempre excluido
    if consent_status == "revoked" and people_policy != "exclude":
        raise DomainError("consent_revoked", 409)
    if people_policy == "consented" and consent_status != "granted":
        raise DomainError("consent_required", 400)
    if contains_people is False and people_policy == "no_people":
        cls = "business_media_no_people"
    elif people_policy == "consented" and consent_status == "granted":
        cls = "consented_people"
    else:
        cls = "restricted"           # exclude, anonymize (el original) o desconocido
    return {"contains_people": contains_people, "contains_minors": contains_minors, "people_policy": people_policy,
            "consent_status": consent_status, "privacy_class": cls}


def original_class(media: Dict[str, Any]) -> str:
    try:
        return classify(media.get("contains_people"), media.get("people_policy") or "exclude",
                        media.get("consent_status") or "unknown", media.get("contains_minors"))["privacy_class"]
    except DomainError:
        return "restricted"


def usable_media(media: Dict[str, Any]) -> Optional[str]:
    """None si el original puede usarse en un trabajo; si no, el código del motivo."""
    if media.get("consent_status") == "revoked":
        return "consent_revoked"
    if media.get("retention_status") in ("purge_pending", "purged", "purge_failed"):
        return "media_pending_deletion"
    if media.get("processing_status") != "ready" or media.get("validation_status") != "passed":
        return "media_not_ready"
    if media.get("contains_people") is not False and media.get("contains_minors") is not False:
        return "minors_excluded"
    if media.get("people_policy") == "exclude" and media.get("contains_people") is not False:
        return "media_excluded"
    return None


def input_class(media: Dict[str, Any], derivative: Optional[Dict[str, Any]] = None) -> str:
    """Clase de privacidad de una entrada concreta (original o derivado)."""
    if usable_media(media) is not None:
        return "restricted"
    if derivative is not None:
        ok = (derivative.get("kind") == "anonymized" and derivative.get("status") == "ready"
              and not derivative.get("is_mock") and derivative.get("reviewed_by")
              and derivative.get("media_id") == media.get("id"))
        return "anonymized_people" if ok else "restricted"
    if media.get("people_policy") == "anonymize":
        return "restricted"          # hay que usar el derivado anonimizado, nunca el original
    return original_class(media)


def malware_clean(media: Dict[str, Any]) -> bool:
    return media.get("malware_scan_status") == "clean"


def can_go_external(privacy_class: str) -> bool:
    return privacy_class in EXTERNAL_OK


def require_external_ok(classes: List[str]) -> None:
    if any(not can_go_external(c) for c in classes):
        raise DomainError("privacy_blocked", 409)


# ---------------------------------------------------------------- detección local (sin identidad)
@dataclass(frozen=True)
class Detection:
    available: bool
    confidence: Optional[float]                 # 0..1; None = no se pudo evaluar
    boxes: List[Dict[str, int]] = field(default_factory=list)   # solo posiciones, nunca identidades


class FaceDetector(Protocol):
    name: str
    def detect(self, data: bytes, mime: str) -> Detection: ...


class UnavailableFaceDetector:
    """En producción no hay detector local instalado: siempre se pide revisión humana."""
    name = "unavailable"

    def detect(self, data: bytes, mime: str) -> Detection:
        return Detection(available=False, confidence=None)


class MockFaceDetector:
    """Para pruebas y demostración: confianza fija, sin mirar a nadie."""
    name = "mock"

    def __init__(self, confidence: float = 0.95, boxes: Optional[List[Dict[str, int]]] = None):
        self.confidence = confidence
        self.boxes = boxes if boxes is not None else [{"x": 10, "y": 10, "w": 40, "h": 40}]

    def detect(self, data: bytes, mime: str) -> Detection:
        return Detection(available=True, confidence=self.confidence, boxes=list(self.boxes))


def anonymization_plan(method: str, detection: Detection, *, simulated: bool = True) -> Dict[str, Any]:
    """Decide el estado inicial del derivado. Mientras sea simulado: 'mock_only' (con detección
    simulada) o 'awaiting_processing' (sin detector). Nunca 'ready' ni "anonimizado"."""
    if method not in ANONYMIZE_METHODS:
        raise DomainError("invalid_anonymization_method", 400)
    low = (not detection.available or detection.confidence is None or detection.confidence < MIN_CONFIDENCE)
    status = "mock_only" if (simulated and detection.available) else (
        "awaiting_processing" if (simulated or low) else "needs_review")
    return {"method": method, "status": status, "is_mock": status == "mock_only", "review_required": True,
            "detection_confidence": detection.confidence, "low_confidence": low,
            "stop_reason": "simulated_not_anonymized" if simulated else ("low_confidence_human_review" if low else None),
            "guarantee": "not_anonymized" if simulated else "not_full_anonymity", "warnings": list(WARNINGS)}
