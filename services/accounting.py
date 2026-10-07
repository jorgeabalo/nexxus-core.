"""
Contabilidad básica del Manager Panel (ingresos, gastos, pendientes y recibos).

Seguridad (se comprueba aquí con el JWT de quien llama, además de RLS):
  * owner / manager → todo.
  * staff           → consulta y registra movimientos; no edita, no cancela,
                      no gestiona categorías ni pendientes, no exporta.
  * socio / otro tenant → 403.

Pagos de socios (tabla payments): no se copian. Se consolidan al leer:
  * pagado   → ingreso en la categoría "Membresías" (solo lectura aquí);
  * pendiente → cuenta por cobrar.
Si un pago se enlaza a un movimiento (source_type='payment'), deja de contarse
desde payments; el índice único de la base impide enlazarlo dos veces.

Nada se borra: cancelar = status 'cancelled'. Los recibos van a un bucket
privado y solo se entregan a través del backend.
"""
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from services import accounting_calc as calc
from services.evaluation_documents import EXT, safe_name, sniff
from services.member_portal import PortalError

logger = logging.getLogger(__name__)

TYPES = ("income", "expense")
METHODS = ("cash", "card", "bank", "check", "other")
TXN_STATUSES = ("pending", "paid", "cancelled")
OBL_TYPES = ("receivable", "payable")
OBL_STATUSES = ("pending", "paid", "overdue", "cancelled")
EDITORS = ("owner", "manager")
TEAM = ("owner", "manager", "staff")
BUCKET = "accounting-receipts"
MAX_RECEIPT = 10 * 1024 * 1024
RECEIPT_TYPES = ("application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic")
MEMBERSHIP_CATEGORY = "Membresías"
# payments.payment_method → método de contabilidad
PAYMENT_METHODS = {"cash": "cash", "card": "card", "zelle": "bank", "transfer": "bank", "bank": "bank", "check": "check"}
TXN_COLS = ("id,tenant_id,transaction_date,type,category_id,description,amount,payment_method,status,receipt_url,"
            "member_id,source_type,source_id,notes,created_by,created_at,updated_at")


def _uuid(v: Any, code: str = "not_found", status: int = 404) -> str:
    try:
        return str(uuid.UUID(str(v)))
    except (ValueError, TypeError, AttributeError):
        raise PortalError(code, status)


def _amount(v: Any) -> Decimal:
    try:
        d = calc.money(v)
    except (InvalidOperation, ValueError, TypeError):
        raise PortalError("invalid_amount", 400)
    if v in (None, "") or not d.is_finite() or d <= 0 or d >= Decimal("100000000"):
        raise PortalError("invalid_amount", 400)
    return d


def _text(v: Any, limit: int, code: str, required: bool = False) -> Optional[str]:
    s = re.sub(r"\s+", " ", str(v or "")).strip() if not isinstance(v, (dict, list)) else ""
    if required and not s:
        raise PortalError(code, 400)
    if len(s) > limit:
        raise PortalError(code, 400)
    return s or None


def _notes(v: Any) -> Optional[str]:
    s = str(v or "").strip() if not isinstance(v, (dict, list)) else ""
    if len(s) > 2000:
        raise PortalError("invalid_notes", 400)
    return s or None


def _choice(v: Any, options, code: str) -> str:
    if v not in options:
        raise PortalError(code, 400)
    return v


def _conflict(e: Exception) -> bool:
    return "-> 409" in str(e) or "23505" in str(e)


class AccountingService:
    def __init__(self, db, today: Optional[date] = None):
        self.db = db
        self._today = today

    # ------------------------------------------------------------------ contexto
    def ctx(self, jwt: str, tenant_id: str, roles=TEAM) -> SimpleNamespace:
        if not getattr(self.db, "enabled", True):
            raise PortalError("portal_not_configured", 503)
        user = self.db.user_from_jwt(jwt)
        if not user:
            raise PortalError("unauthorized", 401)
        tenant_id = _uuid(tenant_id, "forbidden", 403)
        rows = self.db.select("tenant_users", {
            "user_id": f"eq.{user['id']}", "tenant_id": f"eq.{tenant_id}", "active": "eq.true",
            "role": f"in.({','.join(roles)})", "select": "role", "limit": "1"})
        if not rows:
            raise PortalError("forbidden", 403)
        t = (self.db.select("tenants", {"id": f"eq.{tenant_id}", "select": "id,name,timezone,branding",
                                        "limit": "1"}) or [{}])[0]
        tz = t.get("timezone") or "America/Chicago"
        try:
            zone = ZoneInfo(tz)
        except Exception:
            zone = ZoneInfo("America/Chicago")
        today = self._today or datetime.now(zone).date()
        return SimpleNamespace(user=user, role=rows[0]["role"], tenant_id=tenant_id, tenant=t, zone=zone, today=today)

    def _utc(self, c, d: date) -> str:
        return datetime.combine(d, time.min, c.zone).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------------ categorías
    def _categories(self, c) -> List[Dict[str, Any]]:
        return self.db.select("accounting_categories", {
            "tenant_id": f"eq.{c.tenant_id}", "select": "id,name,type,active,created_at",
            "order": "type.asc,name.asc", "limit": "500"}) or []

    def categories(self, jwt: str, tenant_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        return {"categories": self._categories(c), "role": c.role}

    def create_category(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, EDITORS)
        name = _text(body.get("name"), 80, "invalid_name", True)
        typ = _choice(body.get("type"), TYPES, "invalid_type")
        if any(x["type"] == typ and x["name"].strip().lower() == name.lower() for x in self._categories(c)):
            raise PortalError("duplicate_category", 409)
        return self.db.insert("accounting_categories", {"tenant_id": c.tenant_id, "name": name, "type": typ})

    def update_category(self, jwt: str, tenant_id: str, cat_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, EDITORS)
        cat = self._category(c, cat_id)
        values: Dict[str, Any] = {}
        if "name" in body:
            values["name"] = _text(body.get("name"), 80, "invalid_name", True)
            if any(x["id"] != cat["id"] and x["type"] == cat["type"] and x["name"].strip().lower() == values["name"].lower()
                   for x in self._categories(c)):
                raise PortalError("duplicate_category", 409)
        if "active" in body:
            values["active"] = bool(body.get("active"))
        if not values:
            raise PortalError("nothing_to_update", 400)
        return (self.db.update("accounting_categories", {"id": f"eq.{cat['id']}", "tenant_id": f"eq.{c.tenant_id}"}, values) or [{}])[0]

    def _category(self, c, cat_id: Any) -> Dict[str, Any]:
        rows = self.db.select("accounting_categories", {"id": f"eq.{_uuid(cat_id, 'invalid_category', 400)}",
                                                        "tenant_id": f"eq.{c.tenant_id}", "select": "id,name,type,active",
                                                        "limit": "1"})
        if not rows:
            raise PortalError("invalid_category", 400)
        return rows[0]

    # ------------------------------------------------------------------ movimientos
    def _txn(self, c, txn_id: str) -> Dict[str, Any]:
        rows = self.db.select("accounting_transactions", {"id": f"eq.{_uuid(txn_id)}", "tenant_id": f"eq.{c.tenant_id}",
                                                          "select": TXN_COLS, "limit": "1"})
        if not rows:
            raise PortalError("not_found", 404)
        return rows[0]

    def _txn_values(self, c, body: Dict[str, Any], current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cur = current or {}
        get = lambda k: body[k] if k in body else cur.get(k)   # noqa: E731
        v: Dict[str, Any] = {}
        typ = _choice(get("type"), TYPES, "invalid_type")
        d = calc.parse_date(get("transaction_date"))
        if d > c.today + timedelta(days=366):
            raise PortalError("invalid_date", 400)
        status = _choice(get("status") or "paid", ("pending", "paid"), "invalid_status")
        v.update(type=typ, transaction_date=d.isoformat(), status=status,
                 description=_text(get("description"), 300, "invalid_description", True),
                 amount=str(_amount(get("amount"))),
                 payment_method=_choice(get("payment_method") or "other", METHODS, "invalid_payment_method"),
                 notes=_notes(get("notes")))
        cat = get("category_id")
        if cat:
            row = self._category(c, cat)
            if row["type"] != typ or (not row["active"] and row["id"] != cur.get("category_id")):
                raise PortalError("invalid_category", 400)
            v["category_id"] = row["id"]
        else:
            v["category_id"] = None
        member = get("member_id")
        if member:
            mid = _uuid(member, "invalid_member", 400)
            if not self.db.select("members", {"id": f"eq.{mid}", "tenant_id": f"eq.{c.tenant_id}", "select": "id", "limit": "1"}):
                raise PortalError("invalid_member", 400)
            v["member_id"] = mid
        else:
            v["member_id"] = None
        return v

    def create_transaction(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, TEAM)
        v = self._txn_values(c, body)
        if body.get("source_type") or body.get("source_id"):
            if body.get("source_type") != "payment" or v["type"] != "income":
                raise PortalError("invalid_source", 400)
            sid = _uuid(body.get("source_id"), "invalid_source", 400)
            if not self.db.select("payments", {"id": f"eq.{sid}", "tenant_id": f"eq.{c.tenant_id}", "select": "id", "limit": "1"}):
                raise PortalError("invalid_source", 400)
            if self.db.select("accounting_transactions", {"tenant_id": f"eq.{c.tenant_id}", "source_type": "eq.payment",
                                                          "source_id": f"eq.{sid}", "status": "neq.cancelled",
                                                          "select": "id", "limit": "1"}):
                raise PortalError("duplicate_source", 409)
            v.update(source_type="payment", source_id=sid)
        v.update(tenant_id=c.tenant_id, created_by=c.user["id"])
        try:
            return self.db.insert("accounting_transactions", v)
        except RuntimeError as e:
            if _conflict(e):
                raise PortalError("duplicate_source", 409)
            raise

    def update_transaction(self, jwt: str, tenant_id: str, txn_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, EDITORS)
        cur = self._txn(c, txn_id)
        if cur["status"] == "cancelled":
            raise PortalError("transaction_cancelled", 409)
        if body.get("status") == "cancelled":
            raise PortalError("use_cancel", 400)
        v = self._txn_values(c, body, cur)
        return (self.db.update("accounting_transactions", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}"}, v) or [{}])[0]

    def cancel_transaction(self, jwt: str, tenant_id: str, txn_id: str) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, EDITORS)
        cur = self._txn(c, txn_id)
        if cur["status"] == "cancelled":
            return cur
        return (self.db.update("accounting_transactions", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                               {"status": "cancelled"}) or [{}])[0]

    # ------------------------------------------------------------------ recibos
    def upload_receipt(self, jwt: str, tenant_id: str, txn_id: str, filename: str, data: bytes) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, TEAM)
        cur = self._txn(c, txn_id)
        if c.role == "staff" and cur["created_by"] != c.user["id"]:
            raise PortalError("forbidden", 403)
        if cur["status"] == "cancelled":
            raise PortalError("transaction_cancelled", 409)
        if not data:
            raise PortalError("empty_file", 400)
        if len(data) > MAX_RECEIPT:
            raise PortalError("file_too_large", 413)
        mime = sniff(data)
        if mime not in RECEIPT_TYPES:
            raise PortalError("unsupported_file", 415)
        key = f"{c.tenant_id}/{cur['id']}/{uuid.uuid4().hex}.{EXT[mime]}"
        self.db.storage_upload(BUCKET, key, data, mime)   # el recibo anterior se conserva en Storage
        self.db.update("accounting_transactions", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}"},
                       {"receipt_url": key})
        logger.info(f"ACCOUNTING_RECEIPT txn=…{cur['id'][-6:]} {mime} {len(data)}B ({safe_name(filename)[:3]}…)")
        return {"ok": True, "has_receipt": True}

    def receipt(self, jwt: str, tenant_id: str, txn_id: str) -> Tuple[bytes, str, str]:
        c = self.ctx(jwt, tenant_id, TEAM)
        cur = self._txn(c, txn_id)
        key = cur.get("receipt_url") or ""
        # la ruta siempre debe estar dentro de la carpeta del propio tenant y movimiento
        if not key.startswith(f"{c.tenant_id}/{cur['id']}/"):
            raise PortalError("not_found", 404)
        ext = key.rsplit(".", 1)[-1]
        mime = next((m for m, e in EXT.items() if e == ext), "application/octet-stream")
        return self.db.storage_download(BUCKET, key), mime, f"recibo-{cur['transaction_date']}.{ext}"

    # ------------------------------------------------------------------ pendientes
    def _obligation(self, c, ob_id: str) -> Dict[str, Any]:
        rows = self.db.select("accounting_obligations", {"id": f"eq.{_uuid(ob_id)}", "tenant_id": f"eq.{c.tenant_id}",
                                                         "select": "*", "limit": "1"})
        if not rows:
            raise PortalError("not_found", 404)
        return rows[0]

    def _open_obligations(self, c) -> List[Dict[str, Any]]:
        return self.db.select("accounting_obligations", {
            "tenant_id": f"eq.{c.tenant_id}", "status": "in.(pending,overdue)", "select": "*",
            "order": "due_date.asc", "limit": "2000"}) or []

    def obligations(self, jwt: str, tenant_id: str, status: str = "open") -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        if status == "open":
            rows = self._open_obligations(c)
        else:
            rows = self.db.select("accounting_obligations", {"tenant_id": f"eq.{c.tenant_id}", "select": "*",
                                                             "order": "due_date.desc", "limit": "500"}) or []
        for r in rows:
            r["effective_status"] = calc.effective_status(r["status"], r.get("due_date"), c.today)
        pending = [dict(p, effective_status=calc.effective_status("pending", p.get("due_date"), c.today))
                   for p in self._pending_payments(c)] if status == "open" else []
        return {"obligations": rows, "member_payments": pending}

    def _obl_values(self, body: Dict[str, Any], cur: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cur = cur or {}
        get = lambda k: body[k] if k in body else cur.get(k)   # noqa: E731
        return {"obligation_type": _choice(get("obligation_type"), OBL_TYPES, "invalid_type"),
                "counterparty": _text(get("counterparty"), 120, "invalid_counterparty", True),
                "description": _text(get("description"), 300, "invalid_description"),
                "amount": str(_amount(get("amount"))),
                "due_date": calc.parse_date(get("due_date")).isoformat()}

    def create_obligation(self, jwt: str, tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, EDITORS)
        v = self._obl_values(body)
        v.update(tenant_id=c.tenant_id, created_by=c.user["id"], status="pending")
        return self.db.insert("accounting_obligations", v)

    def update_obligation(self, jwt: str, tenant_id: str, ob_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, EDITORS)
        cur = self._obligation(c, ob_id)
        if cur["status"] in ("paid", "cancelled"):
            raise PortalError("obligation_closed", 409)
        v = self._obl_values(body, cur)
        if "status" in body:
            v["status"] = _choice(body["status"], ("pending", "cancelled"), "invalid_status")
        return (self.db.update("accounting_obligations", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}"}, v) or [{}])[0]

    def pay_obligation(self, jwt: str, tenant_id: str, ob_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Marca como pagada y (por defecto) registra el movimiento correspondiente."""
        c = self.ctx(jwt, tenant_id, EDITORS)
        cur = self._obligation(c, ob_id)
        if cur["status"] in ("paid", "cancelled"):
            raise PortalError("obligation_closed", 409)
        txn = None
        if body.get("record_transaction", True):
            typ = "income" if cur["obligation_type"] == "receivable" else "expense"
            cat = body.get("category_id") or next(
                (x["id"] for x in self._categories(c) if x["type"] == typ and x["active"]
                 and x["name"] in ("Otros ingresos", "Otros gastos")), None)
            txn = self.db.insert("accounting_transactions", dict(self._txn_values(c, {
                "type": typ, "transaction_date": body.get("paid_date") or c.today.isoformat(), "status": "paid",
                "description": (f"{cur['counterparty']}" + (f" — {cur['description']}" if cur.get("description") else ""))[:300],
                "amount": cur["amount"], "payment_method": body.get("payment_method") or "other", "category_id": cat,
            }), tenant_id=c.tenant_id, created_by=c.user["id"], source_type="obligation", source_id=cur["id"]))
        values = {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()}
        if txn:
            values["related_transaction_id"] = txn["id"]
        row = (self.db.update("accounting_obligations", {"id": f"eq.{cur['id']}", "tenant_id": f"eq.{c.tenant_id}"}, values) or [{}])[0]
        return {"obligation": row, "transaction": txn}

    # ------------------------------------------------------------------ consolidación con payments
    def _linked_payment_ids(self, c) -> set:
        rows = self.db.select("accounting_transactions", {
            "tenant_id": f"eq.{c.tenant_id}", "source_type": "eq.payment", "status": "neq.cancelled",
            "select": "source_id", "limit": "10000"}) or []
        return {r["source_id"] for r in rows}

    def _pending_payments(self, c) -> List[Dict[str, Any]]:
        linked = self._linked_payment_ids(c)
        rows = self.db.select("payment_overview", {
            "tenant_id": f"eq.{c.tenant_id}", "payment_status": "eq.pending",
            "select": "id,member_id,member_name,amount,due_date,description", "order": "due_date.asc", "limit": "2000"}) or []
        return [r for r in rows if r["id"] not in linked]

    def _movements(self, c, s: date, e: date) -> List[Dict[str, Any]]:
        cats = {x["id"]: x["name"] for x in self._categories(c)}
        membership = next((x for x in self._categories(c) if x["type"] == "income" and x["name"] == MEMBERSHIP_CATEGORY), None)
        txns = self.db.select("accounting_transactions", {
            "tenant_id": f"eq.{c.tenant_id}", "and": f"(transaction_date.gte.{s.isoformat()},transaction_date.lte.{e.isoformat()})",
            "select": TXN_COLS, "order": "transaction_date.desc,created_at.desc", "limit": "10000"}) or []
        out: List[Dict[str, Any]] = [{
            "id": t["id"], "kind": "transaction", "date": str(t["transaction_date"])[:10], "type": t["type"],
            "category_id": t.get("category_id"), "category_name": cats.get(t.get("category_id")),
            "description": t["description"], "payment_method": t["payment_method"], "status": t["status"],
            "amount": calc.out(calc.money(t["amount"])), "has_receipt": bool(t.get("receipt_url")),
            "member_id": t.get("member_id"), "notes": t.get("notes"), "source_type": t.get("source_type"),
            "created_by": t.get("created_by"), "created_at": t.get("created_at"),
        } for t in txns]
        linked = self._linked_payment_ids(c)
        pays = self.db.select("payment_overview", {
            "tenant_id": f"eq.{c.tenant_id}", "payment_status": "eq.paid",
            "and": f"(payment_date.gte.{self._utc(c, s)},payment_date.lt.{self._utc(c, e + timedelta(days=1))})",
            "select": "id,member_id,member_name,amount,payment_date,payment_method,description,created_at",
            "order": "payment_date.desc", "limit": "10000"}) or []
        for p in pays:
            if p["id"] in linked or not p.get("payment_date"):
                continue
            local = datetime.fromisoformat(str(p["payment_date"]).replace("Z", "+00:00")).astimezone(c.zone).date()
            out.append({
                "id": p["id"], "kind": "payment", "date": local.isoformat(), "type": "income",
                "category_id": membership["id"] if membership else None,
                "category_name": membership["name"] if membership else MEMBERSHIP_CATEGORY,
                "description": p.get("description") or f"Pago de socio — {p.get('member_name') or ''}".strip(" —"),
                "payment_method": PAYMENT_METHODS.get(str(p.get("payment_method") or "").lower(), "other"),
                "status": "paid", "amount": calc.out(calc.money(p["amount"])), "has_receipt": False,
                "member_id": p.get("member_id"), "member_name": p.get("member_name"), "notes": None,
                "source_type": "payment", "created_by": None, "created_at": p.get("created_at"),
            })
        out.sort(key=lambda m: (m["date"], str(m.get("created_at") or "")), reverse=True)
        return out

    def transactions(self, jwt: str, tenant_id: str, start: Optional[str], end: Optional[str],
                     typ: str = "", status: str = "") -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id)
        s, e = calc.parse_range(start, end, c.today)
        rows = self._movements(c, s, e)
        if typ:
            rows = [m for m in rows if m["type"] == _choice(typ, TYPES, "invalid_type")]
        if status:
            rows = [m for m in rows if m["status"] == _choice(status, TXN_STATUSES, "invalid_status")]
        return {"period": {"from": s.isoformat(), "to": e.isoformat()}, "movements": rows, "role": c.role}

    def summary(self, jwt: str, tenant_id: str, start: Optional[str], end: Optional[str], c=None) -> Dict[str, Any]:
        c = c or self.ctx(jwt, tenant_id)
        s, e = calc.parse_range(start, end, c.today)
        ps, _ = calc.previous_period(s, e)
        load_from = min(ps, calc.add_months(e.replace(day=1), -5))
        movements = self._movements(c, load_from, e)
        res = calc.summarize(movements, s, e, self._open_obligations(c), self._pending_payments(c), c.today)
        res.update(role=c.role, today=c.today.isoformat())
        return res

    def report(self, jwt: str, tenant_id: str, start: Optional[str], end: Optional[str]) -> Dict[str, Any]:
        c = self.ctx(jwt, tenant_id, EDITORS)
        summ = self.summary(jwt, tenant_id, start, end, c)
        s, e = calc.parse_range(start, end, c.today)
        pend = self.obligations(jwt, tenant_id, "open")
        return {"tenant": {"name": c.tenant.get("name") or "", "id": c.tenant_id},
                "generated_at": datetime.now(c.zone).strftime("%Y-%m-%d %H:%M"),
                "summary": summ, "movements": [m for m in self._movements(c, s, e) if m["status"] != "cancelled"],
                "obligations": pend["obligations"], "member_payments": pend["member_payments"]}
