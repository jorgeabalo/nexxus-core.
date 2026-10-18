"""AITA Orchestrator — capa de coordinación de agentes.

Esta capa NO reemplaza generic_service.py ni los verticales existentes.
Coordina capacidades empresariales sobre el mismo tenant (Negocio) y sirve
como contrato estable para añadir marketing, soporte, onboarding, ventas,
finanzas e inventario sin convertir main.py en un monolito.

Regla central: un agente solo puede ejecutar capacidades registradas y
permitidas. Las acciones con efecto externo (publicar, llamar, cobrar,
enviar campañas) deben requerir aprobación humana hasta que exista una
política explícita por negocio.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any


@dataclass(frozen=True)
class AgentSpec:
    key: str
    name: str
    purpose: str
    capabilities: tuple[str, ...]
    external_actions: tuple[str, ...] = ()
    enabled_by_default: bool = True


@dataclass
class OrchestratorDecision:
    agent: str
    capability: str
    requires_human_approval: bool
    reason: str
    context: Dict[str, Any] = field(default_factory=dict)


AGENT_REGISTRY: Dict[str, AgentSpec] = {
    "onboarding": AgentSpec(
        key="onboarding",
        name="Configuration Agent",
        purpose="Configurar un negocio nuevo, su marca, servicios y preferencias.",
        capabilities=("business_profile", "brand_profile", "service_catalog", "channel_setup"),
    ),
    "reception": AgentSpec(
        key="reception",
        name="Reception Agent",
        purpose="Atender conversaciones, resolver preguntas y enrutar solicitudes.",
        capabilities=("customer_questions", "membership_lookup", "appointments", "human_handoff"),
        external_actions=("human_handoff",),
    ),
    "customer_success": AgentSpec(
        key="customer_success",
        name="Customer Success Agent",
        purpose="Gestionar inquietudes, retención, reactivación y seguimiento de clientes.",
        capabilities=("customer_profile", "risk_review", "reactivation_candidates", "follow_up_plan"),
        external_actions=("send_customer_message", "start_reactivation_campaign"),
    ),
    "technical_support": AgentSpec(
        key="technical_support",
        name="Technical Support Agent",
        purpose="Diagnosticar incidencias de la plataforma y preparar resolución o escalado.",
        capabilities=("diagnose_issue", "service_status", "support_ticket", "escalate_issue"),
        external_actions=("change_configuration",),
    ),
    "marketing": AgentSpec(
        key="marketing",
        name="Marketing Agent",
        purpose="Crear campañas orientadas a resultados respetando la identidad de marca.",
        capabilities=("content_plan", "content_draft", "campaign_plan", "brand_consistency", "performance_review"),
        external_actions=("publish_content", "send_campaign", "spend_ad_budget"),
    ),
    "sales": AgentSpec(
        key="sales",
        name="Sales Intelligence Agent",
        purpose="Localizar y calificar prospectos para que el equipo comercial humano los contacte.",
        capabilities=("prospect_research", "lead_scoring", "sales_brief", "pipeline_update"),
        external_actions=("contact_prospect",),
    ),
    "finance": AgentSpec(
        key="finance",
        name="Business Insights Agent",
        purpose="Resumir indicadores operativos y financieros sin sustituir contabilidad profesional.",
        capabilities=("revenue_summary", "expense_summary", "business_kpis", "anomaly_review"),
        external_actions=("initiate_payment", "modify_accounting_records"),
    ),
    "inventory": AgentSpec(
        key="inventory",
        name="Inventory Agent",
        purpose="Vigilar existencias, movimientos y necesidades de reposición.",
        capabilities=("inventory_status", "low_stock", "inventory_report", "reorder_plan"),
        external_actions=("place_order",),
    ),
}


# Las acciones externas empiezan cerradas por defecto. Se podrán habilitar
# por negocio cuando haya autenticación, auditoría y conectores listos.
ALWAYS_REQUIRE_APPROVAL = {
    action
    for spec in AGENT_REGISTRY.values()
    for action in spec.external_actions
}


class AITAOrchestrator:
    """Router determinista inicial.

    No usa un LLM para decidir permisos. La IA puede proponer una intención,
    pero esta clase valida que el agente/capacidad exista y decide si hace
    falta aprobación humana. Así evitamos que un prompt pueda otorgarse
    permisos a sí mismo.
    """

    def __init__(self, registry: Optional[Dict[str, AgentSpec]] = None):
        self.registry = registry or AGENT_REGISTRY

    def list_agents(self) -> List[dict]:
        return [
            {
                "key": spec.key,
                "name": spec.name,
                "purpose": spec.purpose,
                "capabilities": list(spec.capabilities),
                "external_actions": list(spec.external_actions),
                "enabled_by_default": spec.enabled_by_default,
            }
            for spec in self.registry.values()
        ]

    def decide(self, agent: str, capability: str, negocio_id: str, context: Optional[dict] = None) -> OrchestratorDecision:
        spec = self.registry.get(agent)
        if not spec:
            raise ValueError(f"Agente desconocido: {agent}")
        if capability not in spec.capabilities and capability not in spec.external_actions:
            raise ValueError(f"Capacidad '{capability}' no permitida para agente '{agent}'")

        requires_approval = capability in ALWAYS_REQUIRE_APPROVAL
        reason = (
            "Acción con efecto externo: requiere aprobación humana."
            if requires_approval
            else "Capacidad interna registrada: puede preparar/consultar información."
        )
        return OrchestratorDecision(
            agent=agent,
            capability=capability,
            requires_human_approval=requires_approval,
            reason=reason,
            context={"negocio_id": negocio_id, **(context or {})},
        )


orchestrator = AITAOrchestrator()
