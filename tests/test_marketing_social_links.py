"""Brand Kit: direcciones de las redes sociales (solo enlaces públicos, no credenciales)."""
import pytest

from tests.test_marketing import OWNER, T1, T2, err, jwt, svc, db  # noqa: F401  (fixtures)


def test_social_links_saved_and_normalized(svc):
    p = svc.save_brand(jwt(OWNER), T1, {"social_links": {
        "instagram": "instagram.com/goldenage", "facebook": "https://www.facebook.com/goldenage",
        "tiktok": " ", "x": "https://twitter.com/goldenage", "google_business": "https://g.page/goldenage"}})["profile"]
    assert p["social_links"] == {"instagram": "https://instagram.com/goldenage",
                                 "facebook": "https://www.facebook.com/goldenage",
                                 "x": "https://twitter.com/goldenage", "google_business": "https://g.page/goldenage"}
    assert svc.brand(jwt(OWNER), T1)["profile"]["social_links"]["instagram"] == "https://instagram.com/goldenage"


def test_social_links_default_empty_and_per_tenant(svc):
    assert svc.brand(jwt(OWNER), T1)["profile"]["social_links"] == {}
    svc.save_brand(jwt(OWNER), T1, {"social_links": {"instagram": "https://instagram.com/goldenage"}})
    assert svc.brand(jwt(OWNER), T2)["profile"]["social_links"] == {}


@pytest.mark.parametrize("links", [
    {"instagram": "https://evil.example/goldenage"},          # dominio de otra web
    {"instagram": "https://instagram.com.evil.example/x"},    # imitación del dominio
    {"facebook": "javascript:alert(1)"}, {"youtube": "//youtube.com/x"},
    {"myspace": "https://myspace.com/x"},                      # red no soportada
    {"instagram": "https://instagram.com/" + "x" * 300}, "https://instagram.com/x", ["instagram"]])
def test_social_links_rejected(svc, links):
    assert err(svc.save_brand, jwt(OWNER), T1, {"social_links": links}) == ("invalid_social_link", 400)
