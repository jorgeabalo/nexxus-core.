"""
Exportación del reporte contable (CSV compatible con Excel y PDF).

Pensado para entregar al CPA como resumen de gestión. NO es una declaración
fiscal oficial y así se indica en el propio archivo.
"""
import csv
import io
from typing import Any, Dict, List

L = {
    "es": {
        "title": "Reporte contable", "company": "Empresa", "period": "Período", "generated": "Generado",
        "disclaimer": "Reporte interno de gestión preparado a partir de los registros del sistema. "
                      "No es una declaración fiscal oficial ni sustituye la revisión de un contador.",
        "summary": "Resumen", "income": "Ingresos", "expenses": "Gastos", "net": "Ganancia neta",
        "receivable": "Por cobrar", "payable": "Por pagar", "previous": "Período anterior",
        "by_category": "Gastos por categoría", "income_by_category": "Ingresos por categoría",
        "category": "Categoría", "total": "Total", "count": "Movimientos",
        "movements": "Detalle de movimientos", "date": "Fecha", "type": "Tipo", "description": "Descripción",
        "method": "Método de pago", "status": "Estado", "amount": "Cantidad (USD)", "source": "Origen",
        "pending": "Cuentas pendientes", "counterparty": "Nombre", "due": "Vence",
        "t_income": "Ingreso", "t_expense": "Gasto", "t_receivable": "Por cobrar", "t_payable": "Por pagar",
        "s_paid": "Pagado", "s_pending": "Pendiente", "s_overdue": "Atrasado", "s_cancelled": "Cancelado",
        "src_payment": "Pago de socio", "src_manual": "Manual", "src_obligation": "Cuenta pendiente",
        "m_cash": "Efectivo", "m_card": "Tarjeta", "m_bank": "Banco / transferencia", "m_check": "Cheque", "m_other": "Otro",
        "member_payment": "Pago de socio", "none": "Sin datos",
    },
    "en": {
        "title": "Accounting report", "company": "Business", "period": "Period", "generated": "Generated",
        "disclaimer": "Internal management report prepared from the system records. "
                      "It is not an official tax return and does not replace review by an accountant.",
        "summary": "Summary", "income": "Income", "expenses": "Expenses", "net": "Net profit",
        "receivable": "To collect", "payable": "To pay", "previous": "Previous period",
        "by_category": "Expenses by category", "income_by_category": "Income by category",
        "category": "Category", "total": "Total", "count": "Entries",
        "movements": "Transactions", "date": "Date", "type": "Type", "description": "Description",
        "method": "Payment method", "status": "Status", "amount": "Amount (USD)", "source": "Source",
        "pending": "Open items", "counterparty": "Name", "due": "Due",
        "t_income": "Income", "t_expense": "Expense", "t_receivable": "To collect", "t_payable": "To pay",
        "s_paid": "Paid", "s_pending": "Pending", "s_overdue": "Overdue", "s_cancelled": "Cancelled",
        "src_payment": "Member payment", "src_manual": "Manual", "src_obligation": "Open item",
        "m_cash": "Cash", "m_card": "Card", "m_bank": "Bank / transfer", "m_check": "Check", "m_other": "Other",
        "member_payment": "Member payment", "none": "No data",
    },
}


def _t(lang: str) -> Dict[str, str]:
    return L.get(lang, L["es"])


def _safe(v: Any) -> str:
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _amt(v: Any) -> str:
    return f"{float(v or 0):.2f}"


def _open_items(report: Dict[str, Any], t: Dict[str, str]) -> List[List[str]]:
    rows = [[t["t_" + o["obligation_type"]], o["counterparty"], o.get("description") or "", str(o["due_date"])[:10],
             t["s_" + o["effective_status"]], _amt(o["amount"])] for o in report["obligations"]]
    rows += [[t["t_receivable"], p.get("member_name") or t["member_payment"], p.get("description") or t["member_payment"],
              str(p.get("due_date") or "")[:10], t["s_" + p["effective_status"]], _amt(p["amount"])]
             for p in report["member_payments"]]
    return rows


def to_csv(report: Dict[str, Any], lang: str = "es") -> bytes:
    t, s = _t(lang), report["summary"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([t["title"]])
    w.writerow([t["company"], _safe(report["tenant"]["name"])])
    w.writerow([t["period"], s["period"]["from"], s["period"]["to"]])
    w.writerow([t["generated"], report["generated_at"]])
    w.writerow([t["disclaimer"]])
    w.writerow([])
    w.writerow([t["summary"], t["amount"], t["previous"]])
    for k in ("income", "expenses", "net"):
        w.writerow([t[k], _amt(s[k]), _amt(s["previous"][k])])
    w.writerow([t["receivable"], _amt(s["balances"]["receivable"]["total"])])
    w.writerow([t["payable"], _amt(s["balances"]["payable"]["total"])])
    for key, typ in (("by_category", "expense"), ("income_by_category", "income")):
        w.writerow([])
        w.writerow([t[key]])
        w.writerow([t["category"], t["count"], t["total"]])
        for r in s["by_category"][typ]:
            w.writerow([_safe(r["name"]), r["count"], _amt(r["total"])])
    w.writerow([])
    w.writerow([t["movements"]])
    w.writerow([t["date"], t["type"], t["category"], t["description"], t["method"], t["status"], t["source"], t["amount"]])
    for m in report["movements"]:
        w.writerow([m["date"], t["t_" + m["type"]], _safe(m.get("category_name") or ""), _safe(m["description"]),
                    t["m_" + m["payment_method"]], t["s_" + m["status"]],
                    t["src_" + (m.get("source_type") or "manual")], _amt(m["amount"])])
    w.writerow([])
    w.writerow([t["pending"]])
    w.writerow([t["type"], t["counterparty"], t["description"], t["due"], t["status"], t["amount"]])
    for r in _open_items(report, t):
        w.writerow([_safe(x) for x in r])
    return buf.getvalue().encode("utf-8-sig")   # BOM: Excel abre bien los acentos


def to_pdf(report: Dict[str, Any], lang: str = "es") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    t, s = _t(lang), report["summary"]
    navy, gold = colors.HexColor("#0B1F3A"), colors.HexColor("#C9A227")
    st = getSampleStyleSheet()
    small = st["BodyText"].clone("small", fontSize=8, leading=10)
    h2 = st["Heading2"].clone("h2", textColor=navy, spaceBefore=12)
    esc = lambda v: (str(v or "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")   # noqa: E731

    def grid(rows, widths, num_cols=()):
        tbl = Table(rows, colWidths=widths, repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                 ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E3E8EF")),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")])]
        style += [("ALIGN", (c, 0), (c, -1), "RIGHT") for c in num_cols]
        tbl.setStyle(TableStyle(style))
        return tbl

    money = lambda v: f"{'-' if float(v or 0) < 0 else ''}${abs(float(v or 0)):,.2f}"   # noqa: E731
    story = [Paragraph(f"<font color='#0B1F3A'><b>{esc(report['tenant']['name'])}</b></font>", st["Title"]),
             Paragraph(f"{t['title']} · {t['period']}: {s['period']['from']} → {s['period']['to']}", st["Heading3"]),
             Paragraph(f"{t['generated']}: {report['generated_at']}", small),
             Paragraph(f"<i>{t['disclaimer']}</i>", small), Spacer(1, 8)]
    kp = [[t["summary"], t["amount"], t["previous"]]]
    kp += [[t[k], money(s[k]), money(s["previous"][k])] for k in ("income", "expenses", "net")]
    kp += [[t["receivable"], money(s["balances"]["receivable"]["total"]), ""],
           [t["payable"], money(s["balances"]["payable"]["total"]), ""]]
    story.append(grid(kp, [2.4 * inch, 1.6 * inch, 1.6 * inch], (1, 2)))
    story[-1].setStyle(TableStyle([("LINEBELOW", (0, 3), (-1, 3), 1, gold)]))

    for key, typ in (("by_category", "expense"), ("income_by_category", "income")):
        story.append(Paragraph(t[key], h2))
        rows = [[t["category"], t["count"], t["total"]]] + [[esc(r["name"]), r["count"], money(r["total"])]
                                                           for r in s["by_category"][typ]]
        story.append(grid(rows, [3.4 * inch, 1 * inch, 1.6 * inch], (1, 2)) if len(rows) > 1 else Paragraph(t["none"], small))

    story.append(Paragraph(t["movements"], h2))
    rows = [[t["date"], t["type"], t["category"], t["description"], t["method"], t["status"], t["amount"]]]
    rows += [[m["date"], t["t_" + m["type"]], Paragraph(esc(m.get("category_name")), small),
              Paragraph(esc(m["description"]), small), Paragraph(t["m_" + m["payment_method"]], small), t["s_" + m["status"]],
              money(m["amount"])] for m in report["movements"]]
    story.append(grid(rows, [0.75 * inch, 0.6 * inch, 1.1 * inch, 2.0 * inch, 1.05 * inch, 0.75 * inch, 0.95 * inch], (6,))
                 if len(rows) > 1 else Paragraph(t["none"], small))

    story.append(Paragraph(t["pending"], h2))
    items = _open_items(report, t)
    rows = [[t["type"], t["counterparty"], t["description"], t["due"], t["status"], t["amount"]]]
    rows += [[r[0], Paragraph(esc(r[1]), small), Paragraph(esc(r[2]), small), r[3], r[4], money(r[5])] for r in items]
    story.append(grid(rows, [0.8 * inch, 1.5 * inch, 2.2 * inch, 0.8 * inch, 0.7 * inch, 0.9 * inch], (5,))
                 if items else Paragraph(t["none"], small))

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                      topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                      title=t["title"], author=report["tenant"]["name"]).build(story)
    return buf.getvalue()
