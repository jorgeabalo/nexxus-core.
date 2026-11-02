"""AITA matrix: authenticated, tenant-scoped adapters to current services.

Only registered read operations can run. This is not an execution engine for
the external actions proposed in the original orchestrator branch.
"""
from dataclasses import dataclass
from types import MappingProxyType, SimpleNamespace
from uuid import UUID

from services.accounting import AccountingService
from services.marketing import MarketingService
from services.member_portal import PortalError


@dataclass(frozen=True)
class AgentSpec:
    module: str | None
    responsibility: str
    capabilities: tuple[str, ...] = ()


AGENT_REGISTRY = MappingProxyType({
    "coordination": AgentSpec(None, "Descomponer objetivos, asignar tareas y verificar resultados."),
    "onboarding": AgentSpec(None, "Dar de alta empresas y configurar servicios, planes y marca."),
    "reception": AgentSpec("claudia", "Atender consultas, citas y derivaciones a personas."),
    "customer_success": AgentSpec(None, "Dar seguimiento, resolver solicitudes y recuperar clientes."),
    "technical_support": AgentSpec(None, "Diagnosticar incidencias y preparar su resolución."),
    "marketing": AgentSpec("marketing", "Gestionar marca, contenido, campañas y resultados.",
                           ("dashboard", "brand", "campaigns")),
    "sales": AgentSpec(None, "Calificar prospectos, preparar propuestas y seguir oportunidades."),
    "finance": AgentSpec("accounting", "Controlar ingresos, gastos, cobros y reportes.", ("summary",)),
    "inventory": AgentSpec("inventory", "Controlar existencias y proponer reposiciones."),
    "web": AgentSpec(None, "Crear y mantener sitios, contenido web y SEO."),
    "integrations": AgentSpec(None, "Conectar canales y servicios y vigilar sus fallos."),
    "cost_control": AgentSpec(None, "Vigilar consumo, presupuestos y costos por empresa."),
    "quality": AgentSpec(None, "Verificar tareas, consistencia de marca y aislamiento de datos."),
})


class AITAOrchestrator:
    def __init__(self, db):
        self.db = db

    def _context(self, jwt: str, tenant_id: str):
        if self.db is None or not getattr(self.db, "enabled", True):
            raise PortalError("portal_not_configured", 503)
        user = self.db.user_from_jwt(jwt)
        if not user:
            raise PortalError("unauthorized", 401)
        try:
            tenant_id = str(UUID(tenant_id))
        except (ValueError, TypeError, AttributeError):
            raise PortalError("forbidden", 403)
        membership = self.db.select("tenant_users", {
            "user_id": f"eq.{user['id']}", "tenant_id": f"eq.{tenant_id}",
            "active": "eq.true", "role": "in.(owner,manager)",
            "select": "role", "limit": "1",
        })
        if not membership:
            raise PortalError("forbidden", 403)
        tenants = self.db.select("tenants", {
            "id": f"eq.{tenant_id}", "select": "id,name,timezone,modules,settings", "limit": "1",
        })
        if not tenants:
            raise PortalError("forbidden", 403)
        return SimpleNamespace(tenant_id=tenant_id, role=membership[0]["role"], tenant=tenants[0])

    @staticmethod
    def _status(c, spec):
        if not spec.capabilities:
            return "planned"
        modules = c.tenant.get("modules")
        if not isinstance(modules, dict) or modules.get(spec.module) is not True:
            return "disabled"
        if c.role == "manager":
            settings = c.tenant.get("settings")
            settings = settings if isinstance(settings, dict) else {}
            overrides = settings.get("role_modules")
            if overrides is not None:
                if not isinstance(overrides, dict):
                    return "restricted"
                allowed = overrides.get("manager")
                if "manager" in overrides and (
                    not isinstance(allowed, list) or spec.module not in allowed
                ):
                    return "restricted"
        return "available"

    def list_agents(self, jwt: str, tenant_id: str):
        c = self._context(jwt, tenant_id)
        return {
            "tenant": {"id": c.tenant_id, "name": c.tenant.get("name"),
                       "timezone": c.tenant.get("timezone")},
            "role": c.role,
            "agents": [
                {"key": key, "module": spec.module, "responsibility": spec.responsibility,
                 "status": self._status(c, spec),
                 "capabilities": list(spec.capabilities) if self._status(c, spec) == "available" else [],
                 "read_only": True}
                for key, spec in AGENT_REGISTRY.items()
            ],
        }

    def run(self, jwt: str, tenant_id: str, agent: str, capability: str):
        c = self._context(jwt, tenant_id)
        spec = AGENT_REGISTRY.get(agent)
        if spec is None or capability not in spec.capabilities:
            raise PortalError("capability_unavailable", 409)
        if self._status(c, spec) != "available":
            raise PortalError("forbidden", 403)
        # Explicit dispatch, never getattr on user input. The target service
        # revalidates JWT, role and tenant and retains its own domain gates.
        if agent == "finance" and capability == "summary":
            data = AccountingService(self.db).summary(jwt, c.tenant_id, None, None)
        else:
            service = MarketingService(self.db)
            handlers = {"dashboard": service.dashboard, "brand": service.brand,
                        "campaigns": service.campaigns}
            data = handlers[capability](jwt, c.tenant_id)
            if capability == "dashboard":
                # Reuse the strictly read-only base dashboard; the full public
                # planner dashboard also synchronizes jobs. Expose only counts
                # and upcoming content, not internal settings or format limits.
                data = {key: data[key] for key in ("counts", "upcoming")}
        return {"tenant_id": c.tenant_id, "agent": agent, "capability": capability,
                "read_only": True, "data": data}
