"""
PDF de archivo de una evaluación guardada (se genera al vuelo desde Supabase;
no se guarda en ningún almacenamiento público).
"""
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

NAVY = colors.HexColor("#0B1F3A")
GOLD = colors.HexColor("#C9A227")

T = {
    "es": {
        "initial": "Evaluación inicial", "reevaluation": "Reevaluación trimestral", "member": "Socio",
        "submitted": "Enviada", "next": "Próxima evaluación", "q": "Cuestionario",
        "q_pending": "Cuestionario original del gimnasio pendiente de cargar (esta evaluación no lo incluye).",
        "version": "versión", "body": "Medidas corporales", "indicators": "Indicadores de progreso",
        "measure": "Medida", "value": "Valor", "baseline": "Línea base", "change": "Cambio",
        "weight": "Peso", "height": "Estatura", "waist": "Cintura", "l_arm": "Brazo izq.", "r_arm": "Brazo der.",
        "l_leg": "Muslo/pierna izq.", "r_leg": "Muslo/pierna der.", "none": "Sin datos registrados",
        "source_member": "declarado por el socio", "source_staff": "tomado por el staff",
        "conditions": "Condiciones", "no_answer": "Sin respuesta",
        "footer": "Documento confidencial de salud. Generado el {d} a partir del registro guardado ({id}). "
                  "Los cambios se presentan como observados durante el programa.",
        "strength": "Fuerza", "endurance": "Resistencia", "mobility": "Movilidad", "wellbeing": "Bienestar",
        "walk": "Caminata (min)", "bike": "Bicicleta (min)", "other": "Otra actividad (min)",
        "chair_rise": "Levantarse de una silla (1-5)", "stairs": "Subir escaleras (1-5)", "walking": "Caminar (1-5)",
        "chair_stand_30s": "Prueba de silla 30 s (repeticiones)", "energy": "Energía (1-5)", "sleep": "Sueño (1-5)",
        "overall": "Cómo se siente (1-5)", "reps": "rep.", "yes": "Sí", "no": "No",
        "doc": "Respuestas del cuestionario subido (revisadas y confirmadas por el socio)",
        "doc_note": "El documento original se conserva sin modificar en el archivo privado del gimnasio.",
        "blank": "En blanco", "illegible": "Ilegible", "corrected": "corregida por el socio",
    },
    "en": {
        "initial": "Initial evaluation", "reevaluation": "Quarterly re-evaluation", "member": "Member",
        "submitted": "Submitted", "next": "Next evaluation", "q": "Questionnaire",
        "q_pending": "The gym's original questionnaire has not been loaded yet (not included in this evaluation).",
        "version": "version", "body": "Body measurements", "indicators": "Progress indicators",
        "measure": "Measure", "value": "Value", "baseline": "Baseline", "change": "Change",
        "weight": "Weight", "height": "Height", "waist": "Waist", "l_arm": "Left arm", "r_arm": "Right arm",
        "l_leg": "Left thigh/leg", "r_leg": "Right thigh/leg", "none": "No data recorded",
        "source_member": "reported by member", "source_staff": "taken by staff",
        "conditions": "Conditions", "no_answer": "No answer",
        "footer": "Confidential health record. Generated on {d} from the saved record ({id}). "
                  "Changes are shown as observed during the program.",
        "strength": "Strength", "endurance": "Endurance", "mobility": "Mobility", "wellbeing": "Well-being",
        "walk": "Walking (min)", "bike": "Bike (min)", "other": "Other activity (min)",
        "chair_rise": "Rising from a chair (1-5)", "stairs": "Climbing stairs (1-5)", "walking": "Walking (1-5)",
        "chair_stand_30s": "30-s chair stand (reps)", "energy": "Energy (1-5)", "sleep": "Sleep (1-5)",
        "overall": "How you feel (1-5)", "reps": "reps", "yes": "Yes", "no": "No",
        "doc": "Answers from the uploaded questionnaire (reviewed and confirmed by the member)",
        "doc_note": "The original document is kept unmodified in the gym's private archive.",
        "blank": "Left blank", "illegible": "Illegible", "corrected": "corrected by member",
    },
}

BODY = [("weight_lb", "weight", "lb", 0.45359237, "kg"), ("height_in", "height", "in", 2.54, "cm"),
        ("waist_in", "waist", "in", 2.54, "cm"), ("left_arm_in", "l_arm", "in", 2.54, "cm"),
        ("right_arm_in", "r_arm", "in", 2.54, "cm"), ("left_leg_in", "l_leg", "in", 2.54, "cm"),
        ("right_leg_in", "r_leg", "in", 2.54, "cm")]


def _fmt(v: Any, unit: str, factor: float, alt: str) -> str:
    x = float(v)
    return f"{x:g} {unit} ({x * factor:.1f} {alt})"


def _label(obj: Any, lang: str) -> str:
    if isinstance(obj, dict):
        return str(obj.get(lang) or obj.get("es") or obj.get("en") or "")
    return str(obj or "")


def build_evaluation_pdf(bundle: Dict[str, Any], lang: str = "es") -> bytes:
    t = T["en" if lang == "en" else "es"]
    ev, member, tenant = bundle["evaluation"], bundle["member"], bundle["tenant"]
    q, meas, base = bundle.get("questionnaire"), bundle.get("measurement"), bundle.get("baseline_measurement")
    progress: List[Dict[str, Any]] = bundle.get("progress") or []

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=NAVY, fontSize=18, spaceAfter=4, alignment=0)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=NAVY, fontSize=12.5, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=10, leading=13)
    small = ParagraphStyle("s", parent=body, fontSize=8, textColor=colors.HexColor("#4A5A70"))
    P = lambda s, st=body: Paragraph(escape(str(s)), st)

    brand = (tenant.get("branding") or {}).get("display_name") or tenant.get("name") or ""
    name = " ".join(x for x in [member.get("first_name"), member.get("last_name")] if x)
    story: list = [P(brand, ParagraphStyle("brand", parent=body, textColor=GOLD, fontName="Helvetica-Bold")),
                   P(t[ev["kind"]], h1)]
    info = [[t["member"], name + (f"  ·  {member.get('member_id')}" if member.get("member_id") else "")],
            [t["submitted"], str(ev.get("submitted_at") or "")[:16].replace("T", " ") + " UTC"],
            [t["next"], str(ev.get("next_due_date") or "—")],
            [t["q"], f"{_label(q.get('title'), lang)} ({t['version']} {ev.get('questionnaire_version')})" if q else t["q_pending"]]]
    story.append(_table([[P(a), P(b)] for a, b in info], [1.6 * inch, 5.2 * inch], header=False))

    # Medidas corporales (con comparación contra la línea base si es otra medición)
    story.append(P(t["body"], h2))
    if meas:
        src = t["source_member"] if meas.get("source") == "member" else t["source_staff"]
        rows = [[P(t["measure"]), P(t["value"]), P(t["baseline"]), P(t["change"])]]
        for key, lab, unit, factor, alt in BODY:
            v = meas.get(key)
            if v is None:
                continue
            b = base.get(key) if (base and base.get("id") != meas.get("id")) else None
            ch = f"{float(v) - float(b):+g} {unit}" if b is not None else "—"
            rows.append([P(t[lab]), P(_fmt(v, unit, factor, alt)), P(_fmt(b, unit, factor, alt) if b is not None else "—"), P(ch)])
        story.append(_table(rows, [1.7 * inch, 1.9 * inch, 1.9 * inch, 1.3 * inch]))
        story.append(P(f"{meas.get('measurement_date')} · {src} · {meas.get('recorded_by_name') or ''}", small))
    else:
        story.append(P(t["none"]))

    # Indicadores
    story.append(P(t["indicators"], h2))
    if progress:
        rows = [[P(t["measure"]), P(t["value"]), P(t["conditions"])]]
        for p in progress:
            cat = t.get(p["category"], p["category"])
            if p["category"] == "strength":
                what = f"{cat}: {p.get('exercise')}"
                val = f"{float(p['value']):g} lb ({float(p['value']) * 0.45359237:.1f} kg) × {p.get('reps')} {t['reps']}"
            else:
                what = f"{cat}: {t.get(p['metric'], p['metric'])}" + (f" — {p.get('exercise')}" if p.get("exercise") else "")
                val = f"{float(p['value']):g}"
            src = t["source_member"] if p.get("source") == "member" else t["source_staff"]
            rows.append([P(what), P(val), P((p.get("conditions") or "—") + f" · {src}")])
        story.append(_table(rows, [2.6 * inch, 2.0 * inch, 2.2 * inch]))
    else:
        story.append(P(t["none"]))

    # Respuestas del cuestionario
    if q:
        answers = (ev.get("answers") or {}).get("q") or {}
        doc_status = {i.get("ref"): i.get("status") for i in ((ev.get("answers") or {}).get("items") or []) if i.get("ref")}
        for sec in (q.get("definition") or {}).get("sections", []):
            story.append(P(_label(sec.get("title"), lang) or t["q"], h2))
            rows = []
            for qu in sec.get("questions", []):
                a = answers.get(qu["id"])
                opts = {o.get("value"): _label(o.get("label"), lang) or o.get("value") for o in qu.get("options", [])}
                if a is None or a == "" or a == []:
                    txt = t["illegible"] if doc_status.get(qu["id"]) == "illegible" else t["no_answer"]
                elif isinstance(a, list):
                    txt = ", ".join(opts.get(x, str(x)) for x in a)
                elif isinstance(a, bool):
                    txt = t["yes"] if a else t["no"]
                else:
                    txt = opts.get(a, str(a))
                rows.append([P(_label(qu.get("label"), lang)), P(txt)])
            if rows:
                story.append(_table(rows, [3.6 * inch, 3.2 * inch], header=False))

    # Transcripción del documento subido (preguntas tal como estaban impresas)
    items = [i for i in ((ev.get("answers") or {}).get("items") or []) if not i.get("ref")]
    if items:
        story.append(P(t["doc"], h2))
        rows = []
        for it in items:
            st = it.get("status")
            txt = it.get("answer") if st == "answered" else t["illegible"] if st == "illegible" else t["blank"]
            if it.get("corrected"):
                txt = f"{txt} ({t['corrected']})"
            rows.append([P(it.get("question") or ""), P(txt or t["no_answer"])])
        story.append(_table(rows, [3.6 * inch, 3.2 * inch], header=False))
        story.append(P(t["doc_note"], small))

    story.append(Spacer(1, 16))
    story.append(P(t["footer"].format(d=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), id=str(ev["id"])[:8]), small))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f"{t[ev['kind']]} - {name}", author=brand)
    doc.build(story)
    return buf.getvalue()


def _table(rows, widths, header: bool = True) -> Table:
    tb = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E3E8EF")),
             ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6FA")))
    tb.setStyle(TableStyle(style))
    return tb
