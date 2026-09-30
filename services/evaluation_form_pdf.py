"""
PDF RELLENABLE del cuestionario del gimnasio (AcroForm).

* Se genera SOLO desde la definición versionada que el gimnasio cargó en
  Supabase (tabla questionnaires). Si no hay definición no se genera nada:
  nunca se inventan preguntas.
* Cada campo lleva un nombre estable (q__<id>, q__<id>__<opción>, body__*)
  para leer las respuestas sin adivinar cuando el socio lo devuelve lleno.
* Lleva una referencia firmada (campo oculto aita_ref) que identifica socio,
  versión del cuestionario y tipo de evaluación: al subirlo se comprueba que
  pertenece a ese socio.
* Si se imprime y se llena a mano, se sube como foto/escaneo y se extrae como
  cualquier otro documento (con revisión del socio).
"""
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

NAVY = colors.HexColor("#0B1F3A")
GOLD = colors.HexColor("#C9A227")

T = {
    "es": {"initial": "Evaluación inicial", "reevaluation": "Reevaluación trimestral", "member": "Socio",
           "date": "Fecha", "version": "Cuestionario v{v}", "body": "Medidas (opcional)",
           "weight": "Peso", "height": "Estatura", "waist": "Cintura", "arm": "Brazo", "leg": "Muslo/pierna",
           "unit": "Unidad", "yes": "Sí", "no": "No", "required": "* obligatoria",
           "how": "Llénalo en tu teléfono o computadora y súbelo en tu portal (Subir mi cuestionario). "
                  "Si lo imprimes y lo llenas a mano, sube una foto clara de cada página. "
                  "Deja en blanco lo que no quieras o no sepas responder.",
           "conf": "Documento confidencial. Solo tú y la administración de {gym} pueden verlo."},
    "en": {"initial": "Initial evaluation", "reevaluation": "Quarterly re-evaluation", "member": "Member",
           "date": "Date", "version": "Questionnaire v{v}", "body": "Measurements (optional)",
           "weight": "Weight", "height": "Height", "waist": "Waist", "arm": "Arm", "leg": "Thigh/leg",
           "unit": "Unit", "yes": "Yes", "no": "No", "required": "* required",
           "how": "Fill it in on your phone or computer and upload it in your portal (Upload my questionnaire). "
                  "If you print it and fill it by hand, upload a clear photo of each page. "
                  "Leave blank anything you prefer not to or can't answer.",
           "conf": "Confidential document. Only you and {gym} management can see it."},
}

BODY_FIELDS = [("weight", "w"), ("height", "l"), ("waist", "l"), ("arm", "l"), ("leg", "l")]


def L(obj: Any, lang: str) -> str:
    if isinstance(obj, dict):
        return str(obj.get(lang) or obj.get("es") or obj.get("en") or "")
    return str(obj or "")


def field_name(qid: str, opt: Optional[str] = None) -> str:
    return f"q__{qid}" + (f"__{opt}" if opt is not None else "")


def build_fillable_form(questionnaire: Dict[str, Any], member: Dict[str, Any], tenant: Dict[str, Any],
                        kind: str, ref: str, lang: str = "es") -> bytes:
    if not questionnaire or not (questionnaire.get("definition") or {}).get("sections"):
        raise ValueError("questionnaire definition required")
    lang = lang if lang in T else "es"
    tx = T[lang]
    gym = (tenant.get("branding") or {}).get("display_name") or tenant.get("name") or ""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"{L(questionnaire.get('title'), lang)} · {gym}")
    c.setSubject(f"aita_ref:{ref}")
    c.setAuthor(gym)
    W, H = letter
    X0, X1 = 54, W - 54
    form = c.acroForm
    state = {"y": H - 54, "page": 1}

    def header():
        c.setFillColor(NAVY)
        c.rect(0, H - 40, W, 40, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(X0, H - 25, f"{gym} · {L(questionnaire.get('title'), lang)}")
        c.setFont("Helvetica", 8)
        c.drawRightString(X1, H - 25, tx["version"].format(v=questionnaire.get("version")))
        c.setFillColor(colors.black)
        state["y"] = H - 60

    def footer():
        c.setFont("Helvetica", 7)
        c.setFillColor(colors.grey)
        c.drawString(X0, 30, tx["conf"].format(gym=gym))
        c.drawRightString(X1, 30, str(state["page"]))
        c.setFillColor(colors.black)

    def need(h: float):
        if state["y"] - h < 50:
            footer()
            c.showPage()
            state["page"] += 1
            header()

    def text(s: str, size=10, bold=False, color=colors.black, indent=0, lead=None):
        font = "Helvetica-Bold" if bold else "Helvetica"
        lines = simpleSplit(s, font, size, X1 - X0 - indent)
        lead = lead or size + 3
        need(lead * len(lines))
        c.setFont(font, size)
        c.setFillColor(color)
        for ln in lines:
            c.drawString(X0 + indent, state["y"] - size, ln)
            state["y"] -= lead
        c.setFillColor(colors.black)

    header()
    name = " ".join(x for x in [member.get("first_name"), member.get("last_name")] if x)
    text(tx[kind] if kind in tx else kind, 14, True, NAVY)
    text(f"{tx['member']}: {name}    {tx['date']}: {datetime.now(timezone.utc).date().isoformat()}", 10)
    state["y"] -= 4
    text(tx["how"], 8, color=colors.HexColor("#444444"))
    state["y"] -= 6
    # referencia firmada (campo oculto de solo lectura)
    form.textfield(name="aita_ref", value=ref, x=X0, y=10, width=1, height=1, borderWidth=0,
                   fieldFlags="readOnly", annotationFlags="hidden print")

    for sec in questionnaire["definition"]["sections"]:
        need(40)
        state["y"] -= 6
        c.setFillColor(GOLD)
        c.rect(X0, state["y"] - 14, 3, 14, fill=1, stroke=0)
        text(L(sec.get("title"), lang), 12, True, NAVY, indent=8)
        for q in sec.get("questions") or []:
            qid, qtype = q["id"], q.get("type") or "text"
            label = L(q.get("label"), lang) + (" *" if q.get("required") else "")
            state["y"] -= 4
            text(label, 10, True)
            if q.get("help"):
                text(L(q.get("help"), lang), 8, color=colors.grey)
            if qtype in ("single", "multi", "yesno"):
                opts = ([{"value": "yes", "label": {lang: tx["yes"]}}, {"value": "no", "label": {lang: tx["no"]}}]
                        if qtype == "yesno" else (q.get("options") or []))
                for o in opts:
                    olab = L(o.get("label"), lang) or str(o["value"])
                    lines = simpleSplit(olab, "Helvetica", 10, X1 - X0 - 30)
                    need(14 * len(lines) + 2)
                    y = state["y"] - 12
                    if qtype == "multi":
                        form.checkbox(name=field_name(qid, str(o["value"])), x=X0 + 6, y=y, size=11,
                                      buttonStyle="check", borderColor=NAVY, fillColor=colors.white,
                                      textColor=NAVY, forceBorder=True, checked=False)
                    else:
                        form.radio(name=field_name(qid), value=str(o["value"]), selected=False, x=X0 + 6, y=y,
                                   size=11, buttonStyle="circle", borderColor=NAVY, fillColor=colors.white,
                                   textColor=NAVY, forceBorder=True)
                    c.setFont("Helvetica", 10)
                    for i, ln in enumerate(lines):
                        c.drawString(X0 + 24, y + 2 - i * 13, ln)
                    state["y"] -= 14 * len(lines) + 2
            else:
                h = 54 if qtype == "text" else 18
                need(h + 4)
                form.textfield(name=field_name(qid), x=X0 + 6, y=state["y"] - h, width=(X1 - X0 - 6) if qtype == "text" else 180,
                               height=h, borderColor=NAVY, fillColor=colors.white, fontSize=10,
                               fieldFlags="multiline" if qtype == "text" else "", maxlen=2000 if qtype == "text" else 40,
                               tooltip=label[:120])
                if qtype in ("number", "date"):
                    c.setFont("Helvetica", 8)
                    c.setFillColor(colors.grey)
                    c.drawString(X0 + 194, state["y"] - 12, "AAAA-MM-DD" if qtype == "date" else "")
                    c.setFillColor(colors.black)
                state["y"] -= h + 4

    # medidas: el socio indica la unidad (no se asume)
    need(150)
    state["y"] -= 10
    text(tx["body"], 12, True, NAVY)
    for key, dim in BODY_FIELDS:
        need(24)
        y = state["y"] - 18
        c.setFont("Helvetica", 10)
        c.drawString(X0 + 6, y + 5, tx[key])
        form.textfield(name=f"body__{key}", x=X0 + 110, y=y, width=80, height=18, borderColor=NAVY,
                       fillColor=colors.white, fontSize=10, maxlen=8)
        units = ("lb", "kg") if dim == "w" else ("in", "cm")
        for i, u in enumerate(units):
            form.radio(name=f"body__{key}__unit", value=u, selected=False, x=X0 + 205 + i * 50, y=y + 3, size=11,
                       buttonStyle="circle", borderColor=NAVY, fillColor=colors.white, textColor=NAVY, forceBorder=True)
            c.drawString(X0 + 220 + i * 50, y + 5, u)
        state["y"] -= 24
    footer()
    c.save()
    return buf.getvalue()
