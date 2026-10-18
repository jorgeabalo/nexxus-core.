"""Regresiones SEO de la landing pública (/), robots.txt y sitemap.xml."""
import json
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


def _html(client):
    r = client.get("/")
    assert r.status_code == 200
    return r.text


def test_robots_txt_allows_landing_and_points_to_sitemap(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "Allow: /" in r.text
    assert "Disallow: /m/" in r.text
    assert "Disallow: /manager/" in r.text
    assert "Sitemap: https://goldenagefit.com/sitemap.xml" in r.text


def test_sitemap_lists_canonical_home(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<loc>https://goldenagefit.com/</loc>" in r.text


def test_landing_head_has_spanish_title_description_and_canonical(client):
    html = _html(client)
    title = re.search(r"<title>(.*?)</title>", html).group(1)
    assert "Houston" in title and "longevidad" in title and "cardiometabólica" in title
    desc = re.search(r'<meta name="description" content="(.*?)"', html).group(1)
    assert 50 <= len(desc) <= 160
    assert '<link rel="canonical" href="https://goldenagefit.com/">' in html
    assert 'lang="es"' in html


def test_landing_has_open_graph_and_twitter_tags(client):
    html = _html(client)
    for tag in ('property="og:title"', 'property="og:url"', 'name="twitter:card"'):
        assert tag in html


def test_landing_json_ld_matches_public_business_data(client):
    html = _html(client)
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert m, "falta JSON-LD"
    data = json.loads(m.group(1))
    assert data["@type"] == "HealthClub"
    assert data["telephone"] == "+1-346-245-7940"
    assert data["address"]["postalCode"] == "77080"
    days = data["openingHoursSpecification"]
    assert any("Friday" in d["dayOfWeek"] for d in days)


def test_json_ld_hours_match_visible_hours(client):
    """El horario del JSON-LD debe coincidir con el que se muestra en la página."""
    html = _html(client)
    assert "6:00 AM – 9:00 PM" in html and "8:00 AM – 2:00 PM" in html
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
    specs = {tuple(d["dayOfWeek"]) if isinstance(d["dayOfWeek"], list) else (d["dayOfWeek"],): d
             for d in data["openingHoursSpecification"]}
    weekday = next(v for k, v in specs.items() if "Monday" in k)
    saturday = next(v for k, v in specs.items() if "Saturday" in k)
    assert (weekday["opens"], weekday["closes"]) == ("06:00", "21:00")
    assert (saturday["opens"], saturday["closes"]) == ("08:00", "14:00")


def test_existing_routes_unchanged_by_seo_endpoints(client):
    """robots.txt y sitemap.xml no deben sombrear rutas existentes."""
    paths = {getattr(r, "path", None) for r in client.app.routes}
    assert "/robots.txt" in paths and "/sitemap.xml" in paths
    for legacy in ("/", "/m/q/{token}", "/manager", "/m"):
        assert legacy in paths, legacy
    # /manager y /m no deben devolver el robots ni el sitemap
    assert client.get("/manager").text.strip() != client.get("/robots.txt").text.strip()
