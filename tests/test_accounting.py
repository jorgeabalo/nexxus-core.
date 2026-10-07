"""
Contabilidad básica: permisos, separación entre tenants, cálculos, fechas,
pendientes/atrasos, duplicados, exportación, recibos y navegación del panel.
Sin red: Supabase (PostgREST + Storage) en memoria.
"""
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
from services import accounting_calc as calc
from services.accounting import AccountingService
from services.accounting_report import to_csv, to_pdf
from services.member_portal import PortalError

T1, T2 = str(uuid.uuid4()), str(uuid.uuid4())
OWNER, MANAGER, STAFF, OTHER, MEMBER = "u-owner", "u-manager", "u-staff", "u-other", "u-member"
TODAY = date(2026, 10, 15)
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


class FakeDB:
    enabled = True

    def __init__(self):
        self.tables = {
            "tenants": [{"id": T1, "name": "Golden Age", "timezone": "America/Chicago", "branding": {}},
                        {"id": T2, "name": "Other Gym", "timezone": "America/Chicago", "branding": {}}],
            "tenant_users": [
                {"tenant_id": T1, "user_id": OWNER, "role": "owner", "active": True},
                {"tenant_id": T1, "user_id": MANAGER, "role": "manager", "active": True},
                {"tenant_id": T1, "user_id": STAFF, "role": "staff", "active": True},
                {"tenant_id": T2, "user_id": OTHER, "role": "owner", "active": True},
            ],
            "members": [{"id": str(uuid.uuid4()), "tenant_id": T1, "user_id": MEMBER}],
            "accounting_categories": [], "accounting_transactions": [], "accounting_obligations": [],
            "payment_overview": [],
        }
        for t in (T1, T2):
            for name, typ in (("Membresías", "income"), ("Otros ingresos", "income"), ("Masajes", "income"),
                              ("Alquiler", "expense"), ("Equipos", "expense"), ("Otros gastos", "expense")):
                self.tables["accounting_categories"].append(
                    {"id": str(uuid.uuid4()), "tenant_id": t, "name": name, "type": typ, "active": True})
        self.storage = {}

    # -- filtros PostgREST mínimos --
    @classmethod
    def _cond(cls, row, k, v):
        op, _, val = v.partition(".")
        cur = row.get(k)
        s = "" if cur is None else str(cur)
        if op == "eq":
            return s.lower() == val.lower()
        if op == "neq":
            return s.lower() != val.lower()
        if op == "in":
            return s in val.strip("()").split(",")
        if op == "is":
            return cur is None
        if cur is None:
            return False
        return {"gte": s >= val, "lte": s <= val, "lt": s < val, "gt": s > val}[op]

    @classmethod
    def _match(cls, row, filters):
        for k, v in filters.items():
            if k in ("select", "limit", "order"):
                continue
            if k == "and":
                for part in v.strip("()").split(","):
                    col, _, rest = part.partition(".")
                    if not cls._cond(row, col, rest):
                        return False
            elif not cls._cond(row, k, v):
                return False
        return True

    def select(self, table, params):
        rows = [dict(r) for r in self.tables.get(table, []) if self._match(r, params)]
        for spec in reversed((params.get("order") or "").split(",")):
            if spec:
                col, _, d = spec.partition(".")
                rows.sort(key=lambda r: str(r.get(col) or ""), reverse=d == "desc")
        return rows[: int(params.get("limit", 1000))]

    def insert(self, table, row):
        if table == "accounting_transactions" and row.get("source_id"):
            if any(r.get("source_id") == row["source_id"] and r["status"] != "cancelled"
                   for r in self.tables[table]):
                raise RuntimeError("Supabase POST accounting_transactions -> 409: 23505")
        row = dict(row, id=str(uuid.uuid4()), created_at="2026-10-15T12:00:00Z")
        row.setdefault("receipt_url", None)
        self.tables.setdefault(table, []).append(row)
        return dict(row)

    def update(self, table, filters, values):
        out = []
        for r in self.tables.get(table, []):
            if self._match(r, filters):
                r.update(values)
                out.append(dict(r))
        return out

    def user_from_jwt(self, jwt):
        return {"id": jwt[4:]} if jwt.startswith("jwt-") else None

    def storage_upload(self, bucket, key, data, mime):
        self.storage[(bucket, key)] = data

    def storage_download(self, bucket, key):
        return self.storage[(bucket, key)]


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def svc(db):
    return AccountingService(db, today=TODAY)


def cat(db, tenant, name):
    return next(c["id"] for c in db.tables["accounting_categories"] if c["tenant_id"] == tenant and c["name"] == name)


def txn(svc, who=OWNER, tenant=T1, **kw):
    body = {"type": "income", "transaction_date": "2026-10-05", "description": "Session", "amount": "100",
            "payment_method": "cash", "status": "paid"}
    body.update(kw)
    return svc.create_transaction(f"jwt-{who}", tenant, body)


def pay(db, tenant=T1, amount=50, status="paid", when="2026-10-03T15:00:00Z", due=None):
    p = {"id": str(uuid.uuid4()), "tenant_id": tenant, "member_id": None, "member_name": "Ana Ruiz",
         "amount": amount, "payment_status": status, "payment_date": when if status == "paid" else None,
         "payment_method": "zelle", "description": None, "due_date": due, "created_at": "2026-10-01"}
    db.tables["payment_overview"].append(p)
    db.tables.setdefault("payments", []).append({"id": p["id"], "tenant_id": tenant})
    return p


# ---------------------------------------------------------------- cálculos
def test_totals_profit_and_categories(svc, db):
    txn(svc, amount="1200.50", category_id=cat(db, T1, "Masajes"))
    txn(svc, type="expense", amount="800", category_id=cat(db, T1, "Alquiler"), description="Rent")
    txn(svc, type="expense", amount="99.99", category_id=cat(db, T1, "Equipos"), description="Bands")
    txn(svc, amount="500", status="pending")                      # pendiente: no suma
    c = txn(svc, amount="300")
    svc.cancel_transaction(f"jwt-{OWNER}", T1, c["id"])            # cancelado: no suma
    pay(db, amount=60)                                             # pago de socio: suma como ingreso
    s = svc.summary(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert (s["income"], s["expenses"], s["net"]) == (1260.5, 899.99, 360.51)
    assert [r["name"] for r in s["by_category"]["expense"]] == ["Alquiler", "Equipos"]
    assert {r["name"]: r["total"] for r in s["by_category"]["income"]} == {"Masajes": 1200.5, "Membresías": 60.0}
    assert s["monthly"][-1] == {"month": "2026-10", "income": 1260.5, "expense": 899.99}
    assert len(s["monthly"]) == 6


def test_previous_month_comparison(svc):
    txn(svc, amount="200", transaction_date="2026-09-10")
    txn(svc, amount="300", transaction_date="2026-10-10")
    s = svc.summary(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert s["previous_period"] == {"from": "2026-09-01", "to": "2026-09-30"}
    assert s["previous"]["income"] == 200.0 and s["change_pct"]["income"] == 50.0
    assert s["change_pct"]["expenses"] is None                     # sin base de comparación


def test_previous_period_rules():
    assert calc.previous_period(date(2026, 3, 1), date(2026, 3, 31)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert calc.previous_period(date(2026, 10, 1), date(2026, 10, 15)) == (date(2026, 9, 16), date(2026, 9, 30))


# ---------------------------------------------------------------- fechas y filtros
def test_date_filters_and_validation(svc):
    txn(svc, transaction_date="2026-09-30", description="Sept")
    txn(svc, transaction_date="2026-10-01", description="Oct")
    txn(svc, type="expense", transaction_date="2026-10-02", description="Oct exp")
    r = svc.transactions(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert [m["description"] for m in r["movements"]] == ["Oct exp", "Oct"]
    r = svc.transactions(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31", typ="income")
    assert [m["description"] for m in r["movements"]] == ["Oct"]
    for a, b, code in (("2026-10-31", "2026-10-01", "invalid_range"), ("2026-13-01", "2026-10-01", "invalid_date"),
                       ("2020-01-01", "2026-10-01", "range_too_long")):
        with pytest.raises(PortalError) as e:
            svc.summary(f"jwt-{OWNER}", T1, a, b)
        assert e.value.code == code


def test_member_payment_uses_tenant_local_date(svc, db):
    pay(db, amount=40, when="2026-10-01T03:00:00Z")    # 30-sep 22:00 en Chicago → septiembre
    r = svc.transactions(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert r["movements"] == []
    r = svc.transactions(f"jwt-{OWNER}", T1, "2026-09-30", "2026-09-30")
    assert r["movements"][0]["kind"] == "payment" and r["movements"][0]["payment_method"] == "bank"


@pytest.mark.parametrize("field,value,code", [
    ("amount", "-5", "invalid_amount"), ("amount", "0", "invalid_amount"), ("amount", "abc", "invalid_amount"),
    ("amount", "NaN", "invalid_amount"), ("type", "transfer", "invalid_type"), ("status", "cancelled", "invalid_status"),
    ("payment_method", "bitcoin", "invalid_payment_method"), ("description", "  ", "invalid_description"),
    ("transaction_date", "yesterday", "invalid_date"),
])
def test_transaction_validation(svc, field, value, code):
    with pytest.raises(PortalError) as e:
        txn(svc, **{field: value})
    assert e.value.code == code


def test_category_must_match_type_and_tenant(svc, db):
    with pytest.raises(PortalError) as e:
        txn(svc, category_id=cat(db, T1, "Alquiler"))                # categoría de gasto en un ingreso
    assert e.value.code == "invalid_category"
    with pytest.raises(PortalError) as e:
        txn(svc, category_id=cat(db, T2, "Masajes"))                 # categoría de otro tenant
    assert e.value.code == "invalid_category"


# ---------------------------------------------------------------- permisos y tenants
def test_tenant_isolation(svc, db):
    mine = txn(svc, amount="100")
    txn(svc, who=OTHER, tenant=T2, amount="999")
    s = svc.summary(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert s["income"] == 100.0
    with pytest.raises(PortalError) as e:
        svc.summary(f"jwt-{OTHER}", T1, None, None)                   # owner de otro gym
    assert e.value.code == "forbidden"
    with pytest.raises(PortalError) as e:
        svc.update_transaction(f"jwt-{OTHER}", T2, mine["id"], {"amount": "1"})   # id de T1 desde T2
    assert e.value.code == "not_found"
    assert db.tables["accounting_transactions"][0]["amount"] == "100.00"


def test_roles(svc):
    t = txn(svc, who=STAFF)                                           # staff registra
    assert t["created_by"] == STAFF
    assert svc.transactions(f"jwt-{STAFF}", T1, None, None)["role"] == "staff"
    for fn in (lambda: svc.update_transaction(f"jwt-{STAFF}", T1, t["id"], {"amount": "5"}),
               lambda: svc.cancel_transaction(f"jwt-{STAFF}", T1, t["id"]),
               lambda: svc.create_category(f"jwt-{STAFF}", T1, {"name": "X", "type": "income"}),
               lambda: svc.create_obligation(f"jwt-{STAFF}", T1, {}),
               lambda: svc.report(f"jwt-{STAFF}", T1, None, None)):
        with pytest.raises(PortalError) as e:
            fn()
        assert e.value.code == "forbidden"
    svc.update_transaction(f"jwt-{MANAGER}", T1, t["id"], {"amount": "5"})
    for who, code in ((MEMBER, "forbidden"), ("nobody", "unauthorized")):
        with pytest.raises(PortalError) as e:
            svc.summary(f"jwt-{who}" if who != "nobody" else "bad", T1, None, None)
        assert e.value.code == code


def test_edit_and_cancel_without_delete(svc, db):
    t = txn(svc, amount="100")
    svc.update_transaction(f"jwt-{OWNER}", T1, t["id"], {"amount": "120.456", "description": "Edited"})
    row = db.tables["accounting_transactions"][0]
    assert row["amount"] == "120.46" and row["description"] == "Edited"
    svc.cancel_transaction(f"jwt-{OWNER}", T1, t["id"])
    assert len(db.tables["accounting_transactions"]) == 1 and row["status"] == "cancelled"
    with pytest.raises(PortalError) as e:
        svc.update_transaction(f"jwt-{OWNER}", T1, t["id"], {"amount": "1"})
    assert e.value.code == "transaction_cancelled"


def test_categories_admin(svc):
    c = svc.create_category(f"jwt-{MANAGER}", T1, {"name": " Yoga ", "type": "income"})
    assert c["name"] == "Yoga"
    with pytest.raises(PortalError) as e:
        svc.create_category(f"jwt-{MANAGER}", T1, {"name": "yoga", "type": "income"})
    assert e.value.code == "duplicate_category"
    svc.update_category(f"jwt-{MANAGER}", T1, c["id"], {"active": False})
    with pytest.raises(PortalError):
        txn(svc, category_id=c["id"])                                # inactiva: no se usa en movimientos nuevos


# ---------------------------------------------------------------- pendientes
def test_obligations_overdue_and_pay(svc, db):
    late = svc.create_obligation(f"jwt-{OWNER}", T1, {"obligation_type": "payable", "counterparty": "Landlord",
                                                      "amount": "1500", "due_date": "2026-10-01"})
    svc.create_obligation(f"jwt-{OWNER}", T1, {"obligation_type": "receivable", "counterparty": "Corp client",
                                               "amount": "300", "due_date": "2026-11-01"})
    pay(db, amount=45, status="pending", due="2026-10-10")             # pago de socio vencido
    ob = svc.obligations(f"jwt-{STAFF}", T1)
    assert {o["counterparty"]: o["effective_status"] for o in ob["obligations"]} == {"Landlord": "overdue", "Corp client": "pending"}
    s = svc.summary(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert s["balances"]["payable"] == {"total": 1500.0, "count": 1, "overdue_total": 1500.0, "overdue_count": 1}
    assert s["balances"]["receivable"]["total"] == 345.0 and s["balances"]["receivable"]["overdue_total"] == 45.0

    r = svc.pay_obligation(f"jwt-{OWNER}", T1, late["id"], {"payment_method": "bank"})
    assert r["obligation"]["status"] == "paid" and r["obligation"]["related_transaction_id"] == r["transaction"]["id"]
    assert r["transaction"]["type"] == "expense" and r["transaction"]["category_id"] == cat(db, T1, "Otros gastos")
    s = svc.summary(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert s["expenses"] == 1500.0 and s["balances"]["payable"]["total"] == 0
    with pytest.raises(PortalError) as e:
        svc.pay_obligation(f"jwt-{OWNER}", T1, late["id"], {})
    assert e.value.code == "obligation_closed"


# ---------------------------------------------------------------- duplicados
def test_member_payment_counted_once(svc, db):
    p = pay(db, amount=80)
    assert svc.summary(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")["income"] == 80.0
    txn(svc, amount="80", source_type="payment", source_id=p["id"], transaction_date="2026-10-03")
    s = svc.summary(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31")
    assert s["income"] == 80.0                                         # no 160
    with pytest.raises(PortalError) as e:
        txn(svc, amount="80", source_type="payment", source_id=p["id"])
    assert e.value.code == "duplicate_source"
    other = pay(db, tenant=T2, amount=10)
    with pytest.raises(PortalError) as e:
        txn(svc, amount="10", source_type="payment", source_id=other["id"])   # pago de otro tenant
    assert e.value.code == "invalid_source"


# ---------------------------------------------------------------- exportación
def test_csv_export(svc, db):
    txn(svc, amount="100", description="=HYPERLINK(\"x\")", category_id=cat(db, T1, "Masajes"))
    txn(svc, type="expense", amount="40", category_id=cat(db, T1, "Alquiler"), description="Rent")
    data = to_csv(svc.report(f"jwt-{MANAGER}", T1, "2026-10-01", "2026-10-31"), "es")
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert "Golden Age" in text and "2026-10-01,2026-10-31" in text
    assert "No es una declaración fiscal oficial" in text
    assert "Ganancia neta,60.00" in text and "Alquiler,1,40.00" in text
    assert "'=HYPERLINK" in text                                        # fórmula neutralizada


def test_pdf_export(svc):
    txn(svc, amount="100")
    pdf = to_pdf(svc.report(f"jwt-{OWNER}", T1, "2026-10-01", "2026-10-31"), "en")
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


# ---------------------------------------------------------------- recibos
def test_receipt_upload_rules(svc, db):
    t = txn(svc, type="expense", description="Gym mats")
    with pytest.raises(PortalError) as e:
        svc.upload_receipt(f"jwt-{OWNER}", T1, t["id"], "x.exe", b"MZ\x90\x00" * 50)
    assert e.value.code == "unsupported_file"
    with pytest.raises(PortalError) as e:
        svc.upload_receipt(f"jwt-{OWNER}", T1, t["id"], "big.jpg", JPEG + b"0" * (10 * 1024 * 1024))
    assert e.value.code == "file_too_large"
    with pytest.raises(PortalError) as e:
        svc.upload_receipt(f"jwt-{STAFF}", T1, t["id"], "r.jpg", JPEG)          # staff: solo los suyos
    assert e.value.code == "forbidden"
    with pytest.raises(PortalError) as e:
        svc.upload_receipt(f"jwt-{OTHER}", T2, t["id"], "r.jpg", JPEG)          # otro tenant
    assert e.value.code == "not_found"

    svc.upload_receipt(f"jwt-{OWNER}", T1, t["id"], "recibo.jpg", JPEG)
    key = db.tables["accounting_transactions"][0]["receipt_url"]
    assert key.startswith(f"{T1}/{t['id']}/") and key.endswith(".jpg")
    data, mime, _ = svc.receipt(f"jwt-{STAFF}", T1, t["id"])
    assert data == JPEG and mime == "image/jpeg"
    db.tables["accounting_transactions"][0]["receipt_url"] = f"{T2}/x/y.jpg"      # ruta ajena manipulada
    with pytest.raises(PortalError):
        svc.receipt(f"jwt-{OWNER}", T1, t["id"])


# ---------------------------------------------------------------- HTTP y navegación
@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(main, "member_portal", SimpleNamespace(db=db))
    return TestClient(main.app)


def test_endpoints(client, db):
    h = {"Authorization": f"Bearer jwt-{OWNER}"}
    r = client.post("/api/manager/accounting/transactions", headers=h, json={
        "tenant_id": T1, "type": "expense", "transaction_date": "2026-10-02", "description": "Paint",
        "amount": 25, "payment_method": "card", "category_id": cat(db, T1, "Equipos")})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    r = client.post(f"/api/manager/accounting/transactions/{tid}/receipt", headers=h,
                    data={"tenant_id": T1}, files={"file": ("r.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200 and r.json()["has_receipt"]
    r = client.get(f"/api/manager/accounting/transactions/{tid}/receipt?tenant_id={T1}", headers=h)
    assert r.status_code == 200 and r.content == JPEG and r.headers["cache-control"] == "no-store"
    r = client.get(f"/api/manager/accounting/summary?tenant_id={T1}&start=2026-10-01&end=2026-10-31", headers=h)
    assert r.status_code == 200 and r.json()["expenses"] == 25.0
    r = client.get(f"/api/manager/accounting/export?tenant_id={T1}&start=2026-10-01&end=2026-10-31&format=csv", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    r = client.get(f"/api/manager/accounting/export?tenant_id={T1}&format=pdf&lang=en", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = client.post(f"/api/manager/accounting/transactions/{tid}/cancel", headers=h, json={"tenant_id": T1})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_endpoint_errors_hide_details(client):
    r = client.get(f"/api/manager/accounting/summary?tenant_id={T1}")
    assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
    r = client.get(f"/api/manager/accounting/summary?tenant_id={T1}", headers={"Authorization": f"Bearer jwt-{MEMBER}"})
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    r = client.get(f"/api/manager/accounting/export?tenant_id={T1}", headers={"Authorization": f"Bearer jwt-{STAFF}"})
    assert r.status_code == 403
    r = client.get("/api/manager/accounting/summary?tenant_id=not-a-uuid", headers={"Authorization": f"Bearer jwt-{OWNER}"})
    assert r.status_code == 403
    assert r.headers["cache-control"] == "no-store"


def test_not_configured(monkeypatch):
    monkeypatch.setattr(main, "member_portal", None)
    r = TestClient(main.app).get(f"/api/manager/accounting/summary?tenant_id={T1}")
    assert r.status_code == 503


def test_manager_navigation_includes_accounting(client):
    assert client.get("/manager").status_code == 200
    root = Path(__file__).resolve().parent.parent / "manager" / "assets" / "js"
    app_js = (root / "app.js").read_text()
    assert "import * as accounting from './modules/accounting.js'" in app_js
    for key in ("dashboard", "members", "schedule", "claudia", "payments", "team", "accounting"):
        assert f"key: '{key}'" in app_js
    r = client.get("/manager/assets/js/modules/accounting.js")
    assert r.status_code == 200 and "export async function render" in r.text
    assert client.get("/m").status_code == 200                         # el portal del socio sigue igual
