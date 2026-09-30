"""
Cuestionario subido por el socio (PDF rellenable, escaneo o fotografías).

Flujo:
  1. upload(): valida el archivo por su contenido real (no por la extensión),
     guarda el ORIGINAL sin modificar en el bucket privado `evaluation-documents`
     (ruta tenant/member/documento/n.ext) y crea la fila en evaluation_documents.
  2. extract() (en segundo plano):
       * PDF rellenable generado por el portal -> se leen los campos del formulario
         (determinista, sin IA). Campo vacío = "en blanco", nunca "No".
       * Foto / escaneo / PDF sin campos -> se transcribe con el modelo configurado
         (EVALUATION_EXTRACTION_MODEL / ANTHROPIC_MODEL; no se fija un ID en código).
         El modelo debe marcar lo ilegible o dudoso; el servidor vuelve a validar y
         cualquier valor fuera de las opciones queda "dudoso" para revisión.
  3. El socio revisa y corrige en el portal y confirma con la RPC
     member_confirm_document (Supabase). Solo entonces son datos confirmados.

Privacidad: los archivos solo los lee el servidor con la service key; se entregan
al propio socio o a owner/manager de su gimnasio, y cada acceso queda registrado
en document_access_log. Los errores nunca incluyen contenido del documento.
"""
import base64
import hashlib
import hmac
import io
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.member_portal import PortalError, _secret, _b64

logger = logging.getLogger(__name__)

BUCKET = "evaluation-documents"
MAX_FILE = 15 * 1024 * 1024
MAX_FILES = 10
MAX_TOTAL = 40 * 1024 * 1024
MAX_PENDING = 10
EXT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}
STATUSES = ("answered", "blank", "illegible", "uncertain", "pending")
BODY_KEYS = ("weight", "height", "waist", "arm", "leg")


# ---------------------------------------------------------------------------
# utilidades puras (probadas en tests)
# ---------------------------------------------------------------------------
def sniff(data: bytes) -> Optional[str]:
    """Tipo real del archivo por sus primeros bytes."""
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis", b"hevc"):
        return "image/heic"
    return None


def safe_name(name: str) -> str:
    base = os.path.basename(name or "")[-80:]
    return re.sub(r"[^\w.\- ]+", "_", base).strip() or "archivo"


def form_ref(member_id: str, questionnaire_id: Optional[str], version: Optional[int], kind: str) -> str:
    payload = f"v1.{uuid.UUID(str(member_id)).hex}.{uuid.UUID(str(questionnaire_id)).hex if questionnaire_id else '-'}." \
              f"{int(version or 0)}.{kind}"
    sig = _b64(hmac.new(_secret(), f"form:{payload}".encode(), hashlib.sha256).digest())[:22]
    return f"{payload}.{sig}"


def parse_form_ref(ref: str) -> Optional[Dict[str, Any]]:
    """Devuelve los datos de la referencia solo si la firma es válida."""
    try:
        v, mid, qid, ver, kind, sig = (ref or "").strip().split(".")
        payload = f"{v}.{mid}.{qid}.{ver}.{kind}"
        good = _b64(hmac.new(_secret(), f"form:{payload}".encode(), hashlib.sha256).digest())[:22]
        if v != "v1" or not hmac.compare_digest(good, sig):
            return None
        return {"member_id": str(uuid.UUID(mid)), "questionnaire_id": None if qid == "-" else str(uuid.UUID(qid)),
                "version": int(ver), "kind": kind}
    except Exception:
        return None


def _L(obj: Any, lang: str) -> str:
    if isinstance(obj, dict):
        return str(obj.get(lang) or obj.get("es") or obj.get("en") or "")
    return str(obj or "")


def _questions(definition: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not definition:
        return []
    return [q for s in definition.get("sections") or [] for q in s.get("questions") or []]


def _num(s: Any) -> Optional[float]:
    if isinstance(s, bool) or s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).strip().replace(",", ".")
    return float(t) if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


def _display(q: Dict[str, Any], value: Any, lang: str) -> Optional[str]:
    if value is None:
        return None
    labels = {str(o.get("value")): _L(o.get("label"), lang) or str(o.get("value")) for o in q.get("options") or []}
    if q.get("type") == "yesno":
        return ("Sí" if lang == "es" else "Yes") if value else "No"
    if q.get("type") == "multi":
        return ", ".join(labels.get(str(v), str(v)) for v in value)
    if q.get("type") == "single":
        return labels.get(str(value), str(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def check_value(q: Dict[str, Any], value: Any) -> Tuple[bool, Any]:
    """¿El valor es válido para la pregunta? Devuelve (ok, valor_normalizado)."""
    qt = q.get("type") or "text"
    opts = [str(o.get("value")) for o in q.get("options") or []]
    if qt == "single":
        return (isinstance(value, str) and value in opts), value
    if qt == "multi":
        if isinstance(value, list) and value and all(isinstance(v, str) and v in opts for v in value):
            return True, list(dict.fromkeys(value))
        return False, None
    if qt == "yesno":
        return isinstance(value, bool), value
    if qt == "number":
        n = _num(value)
        ok = n is not None and ("min" not in q or n >= float(q["min"])) and ("max" not in q or n <= float(q["max"]))
        return ok, n
    if qt == "date":
        ok = isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None
        if ok:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                ok = False
        return ok, value
    return (isinstance(value, str) and 0 < len(value.strip()) <= 2000), (value.strip() if isinstance(value, str) else None)


def _item(q: Optional[Dict[str, Any]], lang: str, status: str, value: Any = None, answer: Optional[str] = None,
          question: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    it = {"ref": q["id"] if q else None, "type": (q.get("type") or "text") if q else "text",
          "question": (question or (_L(q.get("label"), lang) if q else "") or "")[:500],
          "status": status, "value": value if status == "answered" else None,
          "answer": (answer if answer is not None else (_display(q, value, lang) if q else None)) if status in ("answered", "uncertain") else None,
          "note": (note or None) and str(note)[:200]}
    if it["answer"] is not None:
        it["answer"] = str(it["answer"])[:2000]
    return it


def items_from_form(fields: Dict[str, Any], definition: Dict[str, Any], lang: str) -> List[Dict[str, Any]]:
    """PDF rellenable -> respuestas. Vacío = en blanco (nunca "No")."""
    def val(name):
        v = fields.get(name)
        if v is None:
            return None
        v = str(v)
        if v in ("/Off", "Off", ""):
            return None
        return v[1:] if v.startswith("/") else v

    out = []
    for q in _questions(definition):
        qt, qid = q.get("type") or "text", q["id"]
        if qt == "multi":
            raw = [str(o["value"]) for o in q.get("options") or [] if val(f"q__{qid}__{o['value']}") is not None]
            raw = raw or None
        elif qt == "yesno":
            r = val(f"q__{qid}")
            raw = None if r is None else (True if r == "yes" else False if r == "no" else r)
        else:
            raw = val(f"q__{qid}")
            if isinstance(raw, str):
                raw = raw.strip() or None
        if raw is None:
            out.append(_item(q, lang, "blank"))
            continue
        ok, norm = check_value(q, raw)
        out.append(_item(q, lang, "answered", norm) if ok else
                   _item(q, lang, "uncertain", answer=str(raw), note="format"))
    return out


def body_from_form(fields: Dict[str, Any]) -> Dict[str, Any]:
    body = {}
    for k in BODY_KEYS:
        raw = str(fields.get(f"body__{k}") or "").strip()
        unit = str(fields.get(f"body__{k}__unit") or "").lstrip("/") or None
        if unit in ("Off", ""):
            unit = None
        if not raw:
            body[k] = {"value": None, "unit": unit, "status": "blank"}
            continue
        n = _num(raw)
        ok = n is not None and n > 0 and unit is not None
        body[k] = {"value": n, "unit": unit, "raw": raw[:20], "status": "answered" if ok else "uncertain"}
    return body


def normalize_ai(result: Dict[str, Any], definition: Optional[Dict[str, Any]], lang: str) -> Dict[str, Any]:
    """Revalida lo que devolvió el modelo. Nada se da por bueno si no cumple el formato."""
    raw_items = result.get("items") if isinstance(result.get("items"), list) else []
    items: List[Dict[str, Any]] = []
    qs = _questions(definition)
    if qs:
        by_ref = {}
        extra = []
        for it in raw_items[:400]:
            if isinstance(it, dict) and it.get("ref") and it["ref"] not in by_ref:
                by_ref[str(it["ref"])] = it
            elif isinstance(it, dict):
                extra.append(it)
        for q in qs:
            it = by_ref.get(q["id"])
            if not it:
                items.append(_item(q, lang, "pending", note="not_found"))
                continue
            st = it.get("status") if it.get("status") in ("answered", "blank", "illegible", "uncertain") else "uncertain"
            if st == "answered":
                ok, norm = check_value(q, it.get("value"))
                items.append(_item(q, lang, "answered", norm) if ok else
                             _item(q, lang, "uncertain", answer=str(it.get("answer") or it.get("value") or "")[:2000] or None, note="format"))
            elif st == "uncertain":
                items.append(_item(q, lang, "uncertain", answer=(str(it.get("answer"))[:2000] if it.get("answer") else None), note=it.get("note")))
            else:
                items.append(_item(q, lang, st, note=it.get("note")))
        raw_items = extra      # preguntas impresas que no están en la definición
    for it in raw_items[:300 - len(items)]:
        if not isinstance(it, dict):
            continue
        question = str(it.get("question") or "").strip()[:500]
        if not question:
            continue
        st = it.get("status") if it.get("status") in ("answered", "blank", "illegible", "uncertain") else "uncertain"
        ans = str(it.get("answer")).strip()[:2000] if it.get("answer") not in (None, "") else None
        if st == "answered" and not ans:
            st = "blank"
        items.append({"ref": None, "type": "text", "question": question, "status": st,
                      "answer": ans if st in ("answered", "uncertain") else None, "value": None,
                      "note": (str(it.get("note"))[:200] if it.get("note") else None)})
    body = {}
    rb = result.get("body") if isinstance(result.get("body"), dict) else {}
    for k in BODY_KEYS:
        b = rb.get(k) if isinstance(rb.get(k), dict) else {}
        n = _num(b.get("value"))
        unit = b.get("unit") if b.get("unit") in (("lb", "kg") if k == "weight" else ("in", "cm")) else None
        st = b.get("status") if b.get("status") in ("answered", "blank", "illegible", "uncertain") else ("blank" if n is None else "uncertain")
        if n is None or n <= 0:
            st = "blank" if st in ("answered", "blank") else st
            n = None
        elif st == "answered" and unit is None:
            st = "uncertain"
        body[k] = {"value": n, "unit": unit, "status": st}
    warnings = [str(w)[:200] for w in (result.get("warnings") or [])[:10] if w]
    return {"items": items, "body": body, "warnings": warnings}


def extraction_model() -> str:
    """Modelo configurable por entorno (igual que Claudia): no se acopla a un ID."""
    return (os.getenv("EVALUATION_EXTRACTION_MODEL") or os.getenv("ANTHROPIC_MODEL")
            or os.getenv("MODELO_CLAUDE") or "claude-haiku-4-5-20251001")


def ai_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv("EVALUATION_AI_EXTRACTION", "1") != "0"


TOOL = {
    "name": "record_questionnaire",
    "description": "Record exactly what is written on the questionnaire pages.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": ["string", "null"], "description": "question id from the definition, or null"},
                "question": {"type": "string", "description": "question text exactly as printed"},
                "answer": {"type": ["string", "null"], "description": "answer exactly as written/marked; null if blank or illegible"},
                "value": {"description": "normalized value per the definition type; null if not clearly readable"},
                "status": {"type": "string", "enum": ["answered", "blank", "illegible", "uncertain"]},
                "note": {"type": ["string", "null"]}},
                "required": ["question", "status"]}},
            "body": {"type": "object", "properties": {k: {"type": "object", "properties": {
                "value": {"type": ["number", "null"]}, "unit": {"type": ["string", "null"]},
                "status": {"type": "string", "enum": ["answered", "blank", "illegible", "uncertain"]}}}
                for k in BODY_KEYS}},
            "warnings": {"type": "array", "items": {"type": "string"}}},
        "required": ["items"]},
}

PROMPT = """You are transcribing a gym member's health/fitness intake questionnaire from the attached page images/PDF.
Rules (strict):
- Transcribe ONLY what is on the pages. Never guess, infer, or complete an answer.
- If an answer space is empty -> status "blank", answer null. A blank is NOT "No".
- If handwriting or a mark cannot be read with confidence -> status "illegible" (or "uncertain" if you can read part of it; put what you can read in answer and explain in note). Never pick the most likely option.
- A checkbox/option counts as selected only if it is clearly marked. If two options seem marked, use "uncertain".
- Keep the member's words and language; do not translate or correct spelling.
- Measurements (weight, height, waist, arm, thigh/leg): report value and unit only if the unit is written/marked; otherwise unit null and status "uncertain".
- Do not include names, phone numbers, addresses or signatures in notes.
{definition_rules}
Call record_questionnaire once with all items in page order."""

DEF_RULES = """The gym's questionnaire definition is below (JSON). For each question in it, output one item with ref = its id.
value must be: an option "value" for type single; an array of option values for multi; true/false for yesno (only if clearly marked);
a number for number; YYYY-MM-DD for date; the text for text. If the page contains questions that are not in the definition,
add them with ref null.
DEFINITION: {definition}"""

NO_DEF_RULES = """No digital definition is available: output one item per printed question, with the question text exactly as printed
(ref null, value null)."""


def _image_block(data: bytes, mime: str) -> Dict[str, Any]:
    from PIL import Image, ImageOps
    if mime == "image/heic":
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            raise PortalError("heic_not_supported", 415)
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img = img.convert("RGB")
    img.thumbnail((2000, 2000))
    for quality in (85, 70, 55):
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality)
        if out.tell() < 4_500_000:
            break
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(out.getvalue()).decode()}}


def ai_extract(files: List[Tuple[bytes, str]], definition: Optional[Dict[str, Any]], lang: str, client=None) -> Dict[str, Any]:
    blocks = []
    for data, mime in files:
        if mime == "application/pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                          "data": base64.b64encode(data).decode()}})
        else:
            blocks.append(_image_block(data, mime))
    rules = DEF_RULES.format(definition=json.dumps(definition, ensure_ascii=False)) if _questions(definition) else NO_DEF_RULES
    blocks.append({"type": "text", "text": PROMPT.format(definition_rules=rules)})
    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=180.0, max_retries=1)
    model = extraction_model()
    msg = client.messages.create(model=model, max_tokens=8000, tools=[TOOL],
                                 tool_choice={"type": "tool", "name": TOOL["name"]},
                                 messages=[{"role": "user", "content": blocks}])
    data = next((b.input for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    if not isinstance(data, dict):
        raise RuntimeError("no structured output")
    res = normalize_ai(data, definition, lang)
    res["model"] = model
    return res


def read_pdf_fields(data: bytes) -> Optional[Dict[str, Any]]:
    try:
        import pypdf
        fields = pypdf.PdfReader(io.BytesIO(data)).get_fields() or {}
    except Exception:
        return None
    out = {k: v.get("/V") for k, v in fields.items()}
    return out if any(k.startswith(("q__", "body__")) or k == "aita_ref" for k in out) else None


# ---------------------------------------------------------------------------
# servicio
# ---------------------------------------------------------------------------
PUBLIC_COLS = ("id,kind,status,files,form_ref_ok,questionnaire_version,extraction,extraction_error,"
               "evaluation_id,created_at,extracted_at,confirmed_at,uploaded_via")


def public_doc(row: Dict[str, Any]) -> Dict[str, Any]:
    d = {k: row.get(k) for k in PUBLIC_COLS.split(",")}
    d["files"] = [{k: f.get(k) for k in ("n", "name", "mime", "size")} for f in row.get("files") or []]
    return d


class EvaluationDocuments:
    def __init__(self, portal, ai=None, run_async: bool = True):
        self.portal = portal
        self.db = portal.db
        self._ai = ai or ai_extract
        self._async = run_async

    # -- helpers -----------------------------------------------------------
    def _log(self, row: Dict[str, Any], actor: Optional[str], role: str, action: str, file_n=None, detail=None):
        try:
            self.db.insert("document_access_log", {"tenant_id": row["tenant_id"], "member_id": row["member_id"],
                                                   "document_id": row.get("doc_id") or row.get("id") if action != "download_form" else None,
                                                   "actor_user_id": actor, "actor_role": role, "action": action,
                                                   "file_n": file_n, "detail": detail})
        except Exception as e:
            logger.error(f"DOC_LOG_ERROR {type(e).__name__}")

    def _questionnaire(self, tenant_id: str, qid: Optional[str] = None) -> Optional[Dict[str, Any]]:
        params = {"select": "id,version,title,definition", "limit": "1"}
        if qid:
            params["id"] = f"eq.{qid}"
        else:
            params.update({"tenant_id": f"eq.{tenant_id}", "code": "eq.onboarding", "active": "eq.true"})
        rows = self.db.select("questionnaires", params) or []
        return rows[0] if rows else None

    def _kind(self, member: Dict[str, Any]) -> str:
        return "reevaluation" if self.portal._has_initial(member["id"]) else "initial"

    def _doc(self, doc_id: str) -> Dict[str, Any]:
        try:
            uuid.UUID(str(doc_id))
        except ValueError:
            raise PortalError("not_found", 404)
        rows = self.db.select("evaluation_documents", {"id": f"eq.{doc_id}", "select": "*", "limit": "1"}) or []
        if not rows:
            raise PortalError("not_found", 404)
        return rows[0]

    def _member_doc(self, jwt: str, doc_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        member = self.portal.member_from_jwt(jwt)
        doc = self._doc(doc_id)
        if doc["member_id"] != member["id"]:
            raise PortalError("not_found", 404)      # no revelar que existe
        return member, doc

    def _staff_doc(self, jwt: str, doc_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
        user = self.db.user_from_jwt(jwt)
        if not user:
            raise PortalError("unauthorized", 401)
        doc = self._doc(doc_id)
        rows = self.db.select("tenant_users", {"user_id": f"eq.{user['id']}", "tenant_id": f"eq.{doc['tenant_id']}",
                                               "active": "eq.true", "role": "in.(owner,manager)",
                                               "select": "role", "limit": "1"}) or []
        if not rows:
            raise PortalError("not_found", 404)
        return user, doc, rows[0]["role"]

    # -- PDF rellenable ------------------------------------------------------
    def fillable_form(self, jwt: str, lang: str = "es") -> bytes:
        from services.evaluation_form_pdf import build_fillable_form
        member = self.portal.member_from_jwt(jwt)
        q = self._questionnaire(member["tenant_id"])
        if not q:
            raise PortalError("questionnaire_missing", 409)     # no se inventan preguntas
        kind = self._kind(member)
        pdf = build_fillable_form(q, member, self.portal._tenant(member["tenant_id"]), kind,
                                  form_ref(member["id"], q["id"], q["version"], kind), lang)
        self._log({"tenant_id": member["tenant_id"], "member_id": member["id"]}, member.get("user_id"),
                  "member", "download_form", detail=f"questionnaire v{q['version']}")
        return pdf

    # -- subida ---------------------------------------------------------------
    def upload(self, jwt: str, files: List[Tuple[str, bytes]]) -> Dict[str, Any]:
        member = self.portal.member_from_jwt(jwt)
        if not files:
            raise PortalError("no_file", 400)
        if len(files) > MAX_FILES:
            raise PortalError("too_many_files", 413)
        if sum(len(b) for _, b in files) > MAX_TOTAL:
            raise PortalError("file_too_large", 413)
        checked = []
        for name, data in files:
            if not data:
                raise PortalError("empty_file", 400)
            if len(data) > MAX_FILE:
                raise PortalError("file_too_large", 413)
            mime = sniff(data)
            if not mime:
                raise PortalError("unsupported_file", 415)
            checked.append((safe_name(name), data, mime))
        pending = self.db.select("evaluation_documents", {"member_id": f"eq.{member['id']}",
                                                          "status": "neq.confirmed", "select": "id"}) or []
        if len(pending) >= MAX_PENDING:
            raise PortalError("too_many_pending", 429)

        kind = self._kind(member)
        q = self._questionnaire(member["tenant_id"])
        ref_ok = False
        # ¿Es el PDF rellenable del portal? Debe pertenecer a este socio.
        for _, data, mime in checked:
            if mime == "application/pdf":
                fields = read_pdf_fields(data)
                if fields and fields.get("aita_ref"):
                    ref = parse_form_ref(str(fields["aita_ref"]))
                    if not ref or ref["member_id"] != member["id"]:
                        raise PortalError("form_other_member", 422)
                    ref_ok = True
                    if ref["questionnaire_id"]:
                        q = self._questionnaire(member["tenant_id"], ref["questionnaire_id"]) or q

        doc_id = str(uuid.uuid4())
        stored = []
        for n, (name, data, mime) in enumerate(checked, start=1):
            key = f"{member['tenant_id']}/{member['id']}/{doc_id}/{n}.{EXT[mime]}"
            self.db.storage_upload(BUCKET, key, data, mime)
            stored.append({"n": n, "path": key, "name": name, "mime": mime, "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})
        row = self.db.insert("evaluation_documents", {
            "id": doc_id, "tenant_id": member["tenant_id"], "member_id": member["id"], "kind": kind,
            "questionnaire_id": q["id"] if q else None, "questionnaire_version": q["version"] if q else None,
            "files": stored, "form_ref_ok": ref_ok, "uploaded_by": member["user_id"], "uploaded_via": "member",
            "status": "extracting"})
        self._log(row, member.get("user_id"), "member", "upload", detail=f"{len(stored)} file(s)")
        self.start_extraction(doc_id)
        return public_doc(row)

    def start_extraction(self, doc_id: str, lang: str = "es"):
        if self._async:
            threading.Thread(target=self.extract, args=(doc_id, lang), daemon=True).start()
        else:
            self.extract(doc_id, lang)

    def extract(self, doc_id: str, lang: str = "es") -> None:
        doc = self._doc(doc_id)
        if doc["status"] == "confirmed":
            return
        q = self._questionnaire(doc["tenant_id"], doc.get("questionnaire_id")) if doc.get("questionnaire_id") else None
        definition = q["definition"] if q else None
        try:
            files = [(self.db.storage_download(BUCKET, f["path"]), f["mime"]) for f in doc["files"]]
            result = None
            if doc.get("form_ref_ok"):
                pdfs = [d for d, m in files if m == "application/pdf"]
                fields = read_pdf_fields(pdfs[0]) if pdfs else None
                if fields is not None and definition:
                    result = {"method": "pdf_form", "items": items_from_form(fields, definition, lang),
                              "body": body_from_form(fields), "warnings": []}
                    if all(i["status"] == "blank" for i in result["items"]):
                        result["warnings"].append("form_empty")
            if result is None:
                if not ai_enabled():
                    # Sin extracción automática: el socio transcribe revisando su documento.
                    items = [_item(qq, lang, "pending") for qq in _questions(definition)]
                    result = {"method": "manual", "items": items,
                              "body": {k: {"value": None, "unit": None, "status": "pending"} for k in BODY_KEYS},
                              "warnings": ["ai_disabled"]}
                else:
                    result = self._ai(files, definition, lang)
                    result["method"] = "ai_vision"
            self.db.update("evaluation_documents", {"id": f"eq.{doc_id}", "status": "neq.confirmed"}, {
                "status": "needs_review", "extraction": result, "extraction_error": None,
                "extracted_at": datetime.now(timezone.utc).isoformat()})
            self._log(dict(doc, doc_id=doc_id), None, "system", "extract", detail=result.get("method"))
        except Exception as e:
            code = e.code if isinstance(e, PortalError) else "extraction_failed"
            logger.error(f"DOC_EXTRACT_ERROR doc=…{doc_id[-6:]} {type(e).__name__}")
            try:
                self.db.update("evaluation_documents", {"id": f"eq.{doc_id}", "status": "neq.confirmed"}, {
                    "status": "failed", "extraction_error": code,
                    "extraction": {"method": "failed", "items": [_item(qq, lang, "pending") for qq in _questions(definition)],
                                   "body": {k: {"value": None, "unit": None, "status": "pending"} for k in BODY_KEYS},
                                   "warnings": []}})
            except Exception:
                pass

    # -- lectura --------------------------------------------------------------
    def get_for_member(self, jwt: str, doc_id: str) -> Dict[str, Any]:
        _, doc = self._member_doc(jwt, doc_id)
        return public_doc(doc)

    def retry(self, jwt: str, doc_id: str) -> Dict[str, Any]:
        _, doc = self._member_doc(jwt, doc_id)
        stale = doc["status"] == "extracting" and doc.get("created_at") and \
            datetime.fromisoformat(str(doc["created_at"]).replace("Z", "+00:00")) < datetime.now(timezone.utc) - timedelta(minutes=5)
        if doc["status"] != "failed" and not stale:
            raise PortalError("not_retryable", 409)
        self.db.update("evaluation_documents", {"id": f"eq.{doc_id}", "status": "neq.confirmed"}, {"status": "extracting"})
        self.start_extraction(doc_id)
        return public_doc(dict(doc, status="extracting"))

    def _file(self, doc: Dict[str, Any], n: int) -> Tuple[bytes, str, str]:
        f = next((f for f in doc["files"] if int(f.get("n", 0)) == int(n)), None)
        if not f:
            raise PortalError("not_found", 404)
        return self.db.storage_download(BUCKET, f["path"]), f["mime"], f.get("name") or f"documento-{n}"

    def file_for_member(self, jwt: str, doc_id: str, n: int) -> Tuple[bytes, str, str]:
        member, doc = self._member_doc(jwt, doc_id)
        out = self._file(doc, n)
        self._log(doc, member.get("user_id"), "member", "view_file", file_n=n)
        return out

    def file_for_staff(self, jwt: str, doc_id: str, n: int) -> Tuple[bytes, str, str]:
        user, doc, role = self._staff_doc(jwt, doc_id)
        out = self._file(doc, n)
        self._log(doc, user["id"], role, "view_file", file_n=n)
        return out
