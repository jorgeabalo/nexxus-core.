"""BusinessProfile / BrandProfile para AITA.

Configuración estructurada por tenant que los agentes comparten. No guarda
secretos, API keys, números bancarios ni credenciales. Esos datos deben vivir
en un secret manager y ser referenciados por identificadores seguros.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional


@dataclass
class ContactProfile:
    public_phone: Optional[str] = None
    public_email: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    timezone: str = "America/Chicago"


@dataclass
class BusinessHours:
    monday: List[str] = field(default_factory=list)
    tuesday: List[str] = field(default_factory=list)
    wednesday: List[str] = field(default_factory=list)
    thursday: List[str] = field(default_factory=list)
    friday: List[str] = field(default_factory=list)
    saturday: List[str] = field(default_factory=list)
    sunday: List[str] = field(default_factory=list)


@dataclass
class ServiceItem:
    name: str
    description: str = ""
    price_text: Optional[str] = None
    duration_minutes: Optional[int] = None
    active: bool = True


@dataclass
class BrandProfile:
    business_name: str
    tagline: Optional[str] = None
    value_proposition: Optional[str] = None
    audience: List[str] = field(default_factory=list)
    tone: List[str] = field(default_factory=lambda: ["profesional", "amable"])
    primary_language: str = "es"
    secondary_language: Optional[str] = "en"
    colors: List[str] = field(default_factory=list)
    visual_style: List[str] = field(default_factory=list)
    calls_to_action: List[str] = field(default_factory=list)
    social_channels: Dict[str, str] = field(default_factory=dict)
    approved_claims: List[str] = field(default_factory=list)
    prohibited_claims: List[str] = field(default_factory=list)
    prohibited_topics: List[str] = field(default_factory=list)


@dataclass
class BusinessProfile:
    negocio_id: str
    legal_name: str
    display_name: str
    vertical: str
    owner_name: Optional[str] = None
    description: str = ""
    contact: ContactProfile = field(default_factory=ContactProfile)
    hours: BusinessHours = field(default_factory=BusinessHours)
    services: List[ServiceItem] = field(default_factory=list)
    brand: Optional[BrandProfile] = None
    booking_rules: Dict[str, object] = field(default_factory=dict)
    operational_notes: List[str] = field(default_factory=list)
    enabled_channels: List[str] = field(default_factory=lambda: ["web"])
    human_escalation_contact: Optional[str] = None

    def validate(self) -> List[str]:
        errors = []
        if not self.negocio_id.strip():
            errors.append("negocio_id es obligatorio")
        if not self.legal_name.strip():
            errors.append("legal_name es obligatorio")
        if not self.display_name.strip():
            errors.append("display_name es obligatorio")
        if not self.vertical.strip():
            errors.append("vertical es obligatorio")
        if self.brand and self.brand.business_name != self.display_name:
            errors.append("brand.business_name debe coincidir con display_name")
        return errors

    def to_agent_context(self) -> dict:
        """Contexto seguro que puede entregarse a agentes/LLMs.

        Deliberadamente no existe un campo para EIN, banco, API keys o tokens.
        """
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return asdict(self)


def golden_age_template() -> BusinessProfile:
    """Plantilla incompleta del piloto Golden Age.

    Los valores desconocidos quedan vacíos para que onboarding los pregunte;
    no inventamos información del negocio.
    """
    return BusinessProfile(
        negocio_id="golden-age",
        legal_name="Golden Age",
        display_name="Golden Age",
        vertical="gym",
        brand=BrandProfile(
            business_name="Golden Age",
            audience=["socios actuales", "socios inactivos", "prospectos locales"],
            tone=["cercano", "motivador", "profesional"],
            calls_to_action=["Solicita información", "Reserva una visita", "Reactiva tu membresía"],
        ),
        enabled_channels=["web"],
        operational_notes=["Piloto AITA: completar datos reales durante onboarding."],
    )
