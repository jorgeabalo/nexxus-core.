"""
AITA Marketing (Fase 2): el owner/manager solo ve SU presupuesto de generación con IA y su consumo.
Nunca: el objetivo interno de costo total de AITA (USD 80), márgenes, distribución interna de costos,
proveedores, modelos, precios unitarios, el tope técnico temporal (USD 20) ni explicaciones sobre
presupuestos globales. Ni en la API del tenant, ni en el HTML/JS servido al navegador, ni en errores.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services import marketing_jobs_domain as jd
from test_marketing import T1, OWNER
from test_marketing_studio import StudioDB

ROOT = Path(__file__).resolve().parents[1]
BANNED_TEXT = [r"\b80\s*USD\b", r"\bUSD\s*80\b", r"US?\$\s*80\b",
               r"presupuesto global", r"global budget", r"AITA global", r"cost[oe] total de AITA", r"total cost of AITA",
               r"margen de AITA", r"AITA('s)? margin", r"profit margin", r"Marketing AI budget", r"omniroute",
               r"price_source", r"unit_price", r"precio unitario", r"unit price", r"billing_unit",
               r"(?:tope|max(?:imum|imo)?)\s+(?:t[eé]cnico\s+)?(?:de\s+)?(?:USD\s*)?20"]
BANNED_KEYS = {"selected_provider", "selected_model", "provider", "providers", "model", "model_id", "provider_job_id",
               "router_strategy", "catalog", "catalog_version", "unit_price", "price_source", "billing_unit", "margin",
               "global_budget", "aita_budget", "cap", "limit", "scope", "usage_by_model", "demo_mode", "cost_category"}
H = {"Authorization": f"Bearer jwt-{OWNER}"}


def banned_in(text):
    return [p for p in BANNED_TEXT if re.search(p, text, re.I)]


def keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from keys(v)


@pytest.fixture
def db():
    return StudioDB()


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return TestClient(main.app)


def settings(db, **kw):
    db.tables["marketing_settings"][0].update(kw)


# ------------------------------------------------------------------ estados deshabilitado / habilitado
@pytest.mark.parametrize("change", [{"ai_generation_enabled": False}, {"monthly_ai_cost_limit": 0},
                                    {"monthly_ai_cost_limit": None}, {"monthly_ai_cost_limit": 80}])
def test_disabled_budget_shows_nothing_but_disabled(client, db, change):
    settings(db, **change)
    ov = client.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H).json()
    assert set(ov) == {"generation_enabled", "publication_plan"} and ov["generation_enabled"] is False


def test_enabled_shows_only_generation_state_and_publication_plan(client, db):
    """El presupuesto de IA en USD es interno: el tenant ve publicaciones, no dólares de IA ni límites técnicos."""
    settings(db, ai_generation_enabled=True, monthly_ai_cost_limit=10)
    ov = client.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H).json()
    assert set(ov) == {"generation_enabled", "publication_plan"} and ov["generation_enabled"] is True
    assert set(ov["publication_plan"]) == {"included", "added", "total", "used", "available", "offers"}
    assert jd.budget_summary(10, {"consumed": 2.5, "reserved": 1}) == {
        "enabled": True, "currency": "USD", "assigned": 10.0, "used": 2.5, "reserved": 1.0, "available": 6.5,
        "warning": None, "available_pct": 65}


# ------------------------------------------------------------------ la API del tenant no expone nada interno
def test_tenant_api_never_exposes_global_budget_providers_models_prices_or_margins(client, db):
    settings(db, ai_generation_enabled=True, monthly_ai_cost_limit=20)
    body = {"tenant_id": T1, "brief": {"objective": "brand", "audience": "general", "style": "calm", "duration_seconds": 15},
            "real_media_percent": 0, "ai_media_percent": 100, "quality_tier": "draft", "maximum_cost": 1}
    created = client.post("/api/manager/marketing/jobs", json=body, headers=H)
    jid = created.json()["id"]
    next(r for r in db.tables["marketing_generation_jobs"] if r["id"] == jid).update(
        {"router_strategy": "lowest_cost", "selected_provider": "omniroute", "selected_model": "m"})   # como en la base real
    responses = [created]
    for action, extra in (("estimate", {}), ("approve", {"confirm": True}), ("process", {})):
        responses.append(client.post(f"/api/manager/marketing/jobs/{jid}/{action}", json={"tenant_id": T1, **extra}, headers=H))
    responses += [client.get(f"/api/manager/marketing/jobs/{jid}?tenant_id={T1}", headers=H),
                  client.get(f"/api/manager/marketing/jobs?tenant_id={T1}", headers=H),
                  client.get(f"/api/manager/marketing/studio?tenant_id={T1}", headers=H)]
    # un error de presupuesto tampoco explica nada interno
    settings(db, monthly_ai_cost_limit=0)
    j2 = client.post("/api/manager/marketing/jobs", json={**body, "idempotency_key": "job-privacy-0002"}, headers=H).json()
    client.post(f"/api/manager/marketing/jobs/{j2['id']}/estimate", json={"tenant_id": T1}, headers=H)
    responses.append(client.post(f"/api/manager/marketing/jobs/{j2['id']}/approve", json={"tenant_id": T1, "confirm": True}, headers=H))
    assert responses[-1].status_code >= 400 and responses[-1].json().get("error")
    for r in responses:
        assert r.status_code < 500, r.text
        assert banned_in(r.text) == [], (r.request.url, banned_in(r.text))
        found = BANNED_KEYS & set(keys(r.json()))
        assert not found, (r.request.url, found)
        assert "mock-media-v1" not in r.text                     # id de modelo del catálogo


# ------------------------------------------------------------------ archivos servidos al Manager
def manager_files():
    return [p for p in (ROOT / "manager").rglob("*") if p.is_file() and p.suffix in (".html", ".js", ".mjs", ".css", ".json")]


def test_no_manager_file_mentions_internal_budgets_or_margins():
    files = manager_files()
    assert len(files) > 10
    bad = {str(p.relative_to(ROOT)): banned_in(p.read_text("utf-8")) for p in files}
    assert {k: v for k, v in bad.items() if v} == {}


def test_served_manager_html_and_scripts_are_clean(client):
    page = client.get("/manager/")
    assert page.status_code == 200 and banned_in(page.text) == []
    for p in manager_files():
        rel = p.relative_to(ROOT / "manager" / "assets") if "assets" in p.parts else None
        if rel is None:
            continue
        r = client.get(f"/manager/assets/{rel.as_posix()}")
        assert r.status_code == 200 and banned_in(r.text) == [], (rel, banned_in(r.text))


@pytest.mark.parametrize("lang,title,disabled,labels", [
    ("es", "Presupuesto de generación con IA", "La generación con IA no está habilitada en tu plan.",
     ["Disponible este mes", "Utilizado", "Reservado", "Disponible"]),
    ("en", "AI generation budget", "AI generation is not enabled in your plan.",
     ["Available this month", "Used", "Reserved", "Available"])])
def test_new_budget_texts_are_served(client, lang, title, disabled, labels):
    js = client.get("/manager/assets/js/modules/marketing-studio-i18n.js").text
    block = js.split("\n  en: {")[0 if lang == "es" else 1]
    for t in (title, disabled, *labels):
        assert json.dumps(t, ensure_ascii=False)[1:-1] in block, t
    assert "budgetScopeNote" not in js
