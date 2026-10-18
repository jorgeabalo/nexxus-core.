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
    assert "Houston" in title and "adultos mayores" in title
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
