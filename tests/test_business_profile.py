import pytest

from business_profile import BusinessProfile, BrandProfile, ServiceItem, golden_age_template


def test_golden_age_template_is_valid():
    profile = golden_age_template()
    assert profile.validate() == []
    assert profile.negocio_id == "golden-age"
    assert profile.vertical == "gym"


def test_agent_context_contains_business_and_brand():
    context = golden_age_template().to_agent_context()
    assert context["display_name"] == "Golden Age"
    assert context["brand"]["business_name"] == "Golden Age"


def test_profile_rejects_brand_name_mismatch():
    profile = BusinessProfile(
        negocio_id="demo",
        legal_name="Demo LLC",
        display_name="Demo",
        vertical="generic",
        brand=BrandProfile(business_name="Another Brand"),
    )
    assert "brand.business_name debe coincidir con display_name" in profile.validate()


def test_profile_supports_service_catalog():
    profile = golden_age_template()
    profile.services.append(ServiceItem(name="Membresía mensual", price_text="$--"))
    context = profile.to_agent_context()
    assert context["services"][0]["name"] == "Membresía mensual"


def test_sensitive_credentials_are_not_part_of_profile_schema():
    context = golden_age_template().to_agent_context()
    forbidden = {"ein", "bank_account", "api_key", "token", "password"}
    assert forbidden.isdisjoint(context.keys())
