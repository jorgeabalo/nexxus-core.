import pytest

from aita_orchestrator import AITAOrchestrator, AGENT_REGISTRY


def test_registry_has_core_agents():
    expected = {
        "onboarding", "reception", "customer_success", "technical_support",
        "marketing", "sales", "finance", "inventory",
    }
    assert expected.issubset(set(AGENT_REGISTRY))


def test_internal_marketing_work_does_not_require_approval():
    o = AITAOrchestrator()
    decision = o.decide("marketing", "content_draft", "golden-age")
    assert decision.requires_human_approval is False
    assert decision.context["negocio_id"] == "golden-age"


def test_external_marketing_action_requires_human_approval():
    o = AITAOrchestrator()
    decision = o.decide("marketing", "publish_content", "golden-age")
    assert decision.requires_human_approval is True


def test_sales_research_allowed_but_contact_requires_approval():
    o = AITAOrchestrator()
    research = o.decide("sales", "prospect_research", "aita-operator")
    contact = o.decide("sales", "contact_prospect", "aita-operator")
    assert research.requires_human_approval is False
    assert contact.requires_human_approval is True


def test_unknown_agent_is_rejected():
    with pytest.raises(ValueError):
        AITAOrchestrator().decide("magic_agent", "anything", "golden-age")


def test_agent_cannot_claim_unregistered_capability():
    with pytest.raises(ValueError):
        AITAOrchestrator().decide("marketing", "initiate_payment", "golden-age")
