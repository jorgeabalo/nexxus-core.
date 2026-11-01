"""Webs propias de clientes (Dr. Redden): /drredden siempre; la raíz solo con el dominio declarado en SITE_DOMAINS."""
from fastapi.testclient import TestClient

import main


def client():
    return TestClient(main.app)


def test_dr_redden_page_by_path():
    r = client().get("/drredden")
    assert r.status_code == 200 and "Dr. Redden" in r.text and "instagram.com/drredden" in r.text
    assert "/manager?empresa=dr_redden" in r.text and "panel-link" in r.text
    assert "No sustituye la consulta médica" in r.text


def test_root_keeps_main_landing_without_domain(monkeypatch):
    monkeypatch.delenv("SITE_DOMAINS", raising=False)
    r = client().get("/", headers={"Host": "drredden.example"})
    assert r.status_code == 200 and "Dr. Redden · Medicina explicada" not in r.text


def test_root_serves_site_for_its_domain(monkeypatch):
    monkeypatch.setenv("SITE_DOMAINS", "drredden.example=dr_redden, www.drredden.example=dr_redden")
    assert "Dr. Redden · Medicina explicada" in client().get("/", headers={"Host": "www.drredden.example"}).text
    assert "Dr. Redden · Medicina explicada" not in client().get("/", headers={"Host": "otro.example"}).text


def test_unknown_or_unsafe_slug_falls_back(monkeypatch):
    monkeypatch.setenv("SITE_DOMAINS", "a.example=../etc,b.example=no_existe")
    for host in ("a.example", "b.example"):
        r = client().get("/", headers={"Host": host})
        assert r.status_code == 200 and "Dr. Redden · Medicina explicada" not in r.text
