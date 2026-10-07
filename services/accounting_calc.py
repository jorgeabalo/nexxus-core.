"""
Contabilidad básica — cálculos puros (sin base de datos ni red).

Todo lo que suma dinero vive aquí para poder probarlo con datos fijos:
  * intervalos de fechas y período anterior;
  * totales de ingresos / gastos / ganancia (solo movimientos pagados);
  * desglose por categoría y serie mensual;
  * cuentas por cobrar / por pagar y atrasos.

Las cantidades se suman con Decimal y se devuelven como float con 2 decimales.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.member_portal import PortalError

MAX_RANGE_DAYS = 3 * 366
CENT = Decimal("0.01")


def money(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def out(v: Decimal) -> float:
    return float(v.quantize(CENT, rounding=ROUND_HALF_UP))


def parse_date(v: Any, code: str = "invalid_date") -> date:
    try:
        d = date.fromisoformat(str(v)[:10]) if v and len(str(v)) >= 10 else None
    except ValueError:
        d = None
    if not d or d.year < 2000 or d.year > 2100:
        raise PortalError(code, 400)
    return d


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def parse_range(start: Optional[str], end: Optional[str], today: date) -> Tuple[date, date]:
    """Intervalo inclusivo. Por defecto: del día 1 del mes actual a hoy."""
    s = parse_date(start) if start else today.replace(day=1)
    e = parse_date(end) if end else today
    if s > e:
        raise PortalError("invalid_range", 400)
    if (e - s).days > MAX_RANGE_DAYS:
        raise PortalError("range_too_long", 400)
    return s, e


def previous_period(s: date, e: date) -> Tuple[date, date]:
    """Mes completo → mes anterior completo; otro intervalo → mismos días justo antes."""
    if s.day == 1 and e == month_end(s):
        ps = add_months(s, -1)
        return ps, month_end(ps)
    n = (e - s).days + 1
    return s - timedelta(days=n), s - timedelta(days=1)


def months_window(e: date, n: int = 6) -> List[str]:
    first = add_months(e.replace(day=1), -(n - 1))
    return [add_months(first, i).strftime("%Y-%m") for i in range(n)]


def effective_status(status: str, due: Any, today: date) -> str:
    """Una cuenta pendiente con vencimiento pasado se muestra como atrasada."""
    if status == "pending" and due and str(due)[:10] < today.isoformat():
        return "overdue"
    return status


def pct_change(cur: Decimal, prev: Decimal) -> Optional[float]:
    if prev == 0:
        return None
    return round(float((cur - prev) / abs(prev) * 100), 1)


def _in(d: str, s: date, e: date) -> bool:
    return s.isoformat() <= d[:10] <= e.isoformat()


def totals(movements: Iterable[Dict[str, Any]], s: date, e: date) -> Dict[str, Decimal]:
    inc = exp = Decimal(0)
    for m in movements:
        if m["status"] != "paid" or not _in(m["date"], s, e):
            continue
        if m["type"] == "income":
            inc += money(m["amount"])
        else:
            exp += money(m["amount"])
    return {"income": inc, "expenses": exp, "net": inc - exp}


def by_category(movements: Iterable[Dict[str, Any]], s: date, e: date) -> Dict[str, List[Dict[str, Any]]]:
    acc: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for m in movements:
        if m["status"] != "paid" or not _in(m["date"], s, e):
            continue
        key = (m["type"], m.get("category_id") or m.get("category_name") or "-")
        row = acc.setdefault(key, {"category_id": m.get("category_id"),
                                   "name": m.get("category_name") or "Uncategorized",
                                   "total": Decimal(0), "count": 0})
        row["total"] += money(m["amount"])
        row["count"] += 1
    res: Dict[str, List[Dict[str, Any]]] = {"income": [], "expense": []}
    for (typ, _), row in acc.items():
        res[typ].append(dict(row, total=out(row["total"])))
    for typ in res:
        res[typ].sort(key=lambda r: (-r["total"], r["name"]))
    return res


def monthly(movements: Iterable[Dict[str, Any]], months: List[str]) -> List[Dict[str, Any]]:
    acc = {k: {"income": Decimal(0), "expense": Decimal(0)} for k in months}
    for m in movements:
        k = m["date"][:7]
        if m["status"] == "paid" and k in acc:
            acc[k][m["type"]] += money(m["amount"])
    return [{"month": k, "income": out(v["income"]), "expense": out(v["expense"])} for k, v in acc.items()]


def open_balances(obligations: Iterable[Dict[str, Any]], pending_payments: Iterable[Dict[str, Any]],
                  today: date) -> Dict[str, Dict[str, Any]]:
    """Por cobrar = cuentas por cobrar abiertas + pagos de socios pendientes.
    Por pagar = cuentas por pagar abiertas."""
    res = {k: {"total": Decimal(0), "count": 0, "overdue_total": Decimal(0), "overdue_count": 0}
           for k in ("receivable", "payable")}

    def add(kind: str, amount: Any, st: str) -> None:
        r = res[kind]
        r["total"] += money(amount)
        r["count"] += 1
        if st == "overdue":
            r["overdue_total"] += money(amount)
            r["overdue_count"] += 1

    for o in obligations:
        st = effective_status(o["status"], o.get("due_date"), today)
        if st in ("pending", "overdue"):
            add(o["obligation_type"], o["amount"], st)
    for p in pending_payments:
        add("receivable", p["amount"], effective_status("pending", p.get("due_date"), today))
    return {k: dict(v, total=out(v["total"]), overdue_total=out(v["overdue_total"])) for k, v in res.items()}


def summarize(movements: List[Dict[str, Any]], s: date, e: date, obligations: List[Dict[str, Any]],
              pending_payments: List[Dict[str, Any]], today: date, recent: int = 10) -> Dict[str, Any]:
    ps, pe = previous_period(s, e)
    cur, prev = totals(movements, s, e), totals(movements, ps, pe)
    in_range = [m for m in movements if _in(m["date"], s, e)]
    return {
        "period": {"from": s.isoformat(), "to": e.isoformat()},
        "previous_period": {"from": ps.isoformat(), "to": pe.isoformat()},
        "income": out(cur["income"]), "expenses": out(cur["expenses"]), "net": out(cur["net"]),
        "previous": {k: out(v) for k, v in prev.items()},
        "change_pct": {k: pct_change(cur[k], prev[k]) for k in cur},
        "balances": open_balances(obligations, pending_payments, today),
        "by_category": by_category(movements, s, e),
        "monthly": monthly(movements, months_window(e)),
        "recent": in_range[:recent],
    }
