"""
Cuestionario subido: validación del archivo, PDF rellenable (ida y vuelta),
extracción sin adivinar, aislamiento entre socios y endpoints. Sin red: Supabase
(tablas + Storage) en memoria y un extractor de IA simulado.
"""
import io
import uuid

import pypdf
import pytest
from fastapi.testclient import TestClient

import main
from services import evaluation_documents as ed
from services.evaluation_documents import EvaluationDocuments, check_value, form_ref, normalize_ai, parse_form_ref, sniff
from services.evaluation_form_pdf import build_fillable_form
from services.member_portal import MemberPortal, PortalError
from test_member_portal import FakeDB, SECRET, T1, T2, M1, M3, MEMBER_USER

M4, USER4 = str(uuid.uuid4()), "u-member4"
Q_ID = str(uuid.uuid4())
DEF = {"sections": [{"id": "s1", "title": {"es": "Salud (PRUEBA)"}, "questions": [
    {"id": "q1", "type": "single", "required": True, "label": {"es": "Salud general"},
     "options": [{"value": "a", "label": {"es": "Buena"}}, {"value": "b", "label": {"es": "Regular"}}]},
    {"id": "q2", "type": "multi", "label": {"es": "Condiciones"},
     "options": [{"value": "hta", "label": {"es": "Hipertensión"}}, {"value": "dm", "label": {"es": "Diabetes"}}]},
    {"id": "q3", "type": "yesno", "label": {"es": "¿Fuma?"}},
    {"id": "q4", "type": "number", "min": 0, "max": 24, "label": {"es": "Horas de sueño"}},
    {"id": "q5", "type": "text", "label": {"es": "Lesiones"}}]}]}
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


class DocDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.storage = {}
        self.tables["members"].append({"id": M4, "tenant_id": T1, "first_name": "Eva", "last_name": "Ruiz",
                                       "user_id": USER4, "membership_status": "active", "portal_token_version": 1})
        self.jwts["jwt-m4"] = USER4
        self.tables["questionnaires"] = []

    @staticmethod
    def _match(row, filters):
        f2 = {}
        for k, v in filters.items():
            if isinstance(v, str) and v.startswith("neq."):
                if str(row.get(k)) == v[4:]:
                    return False
            else:
                f2[k] = v
        return FakeDB._match(row, f2)

    def storage_upload(self, bucket, key, data, mime):
        assert bucket == "evaluation-documents"
        assert key not in self.storage, "un original nunca se sobrescribe"
        self.storage[key] = data

    def storage_download(self, bucket, key):
        return self.storage[key]


class FakeAI:
    def __init__(self, result=None, fail=False):
        self.calls, self.result, self.fail = [], result, fail

    def __call__(self, files, definition, lang):
        self.calls.append((files, definition))
        if self.fail:
            raise RuntimeError("model down")
        return normalize_ai(self.result or {"items": []}, definition, lang)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PORTAL_TOKEN_SECRET", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv("EVALUATION_AI_EXTRACTION", raising=False)
    db = DocDB()
    portal = MemberPortal(db)
    ai = FakeAI()
    docs = EvaluationDocuments(portal, ai=ai, run_async=False)
    monkeypatch.setattr(main, "member_portal", portal)
    monkeypatch.setattr(main, "evaluation_docs", docs)
    return db, docs, ai, TestClient(main.app)


def with_questionnaire(db):
    db.tables["questionnaires"].append({"id": Q_ID, "tenant_id": T1, "code": "onboarding", "active": True,
                                        "version": 3, "title": {"es": "Cuestionario PRUEBA"}, "definition": DEF})


def H(jwt):
    return {"Authorization": f"Bearer {jwt}"}


# ---------------- utilidades ----------------
def test_sniff_uses_content_not_extension():
    assert sniff(b"%PDF-1.7 ...") == "application/pdf"
    assert sniff(JPEG) == "image/jpeg" and sniff(PNG) == "image/png"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff(b"\x00\x00\x00\x18ftypheic....") == "image/heic"
    assert sniff(b"<html><script>") is None and sniff(b"MZ\x90\x00") is None


def test_form_ref_signed(monkeypatch):
    monkeypatch.setenv("PORTAL_TOKEN_SECRET", SECRET)
    ref = form_ref(M1, Q_ID, 3, "initial")
    assert parse_form_ref(ref) == {"member_id": M1, "questionnaire_id": Q_ID, "version": 3, "kind": "initial"}
    assert parse_form_ref(ref[:-2] + "xx") is None
    forged = ref.replace(uuid.UUID(M1).hex, uuid.UUID(M3).hex)
    assert parse_form_ref(forged) is None


def test_check_value_never_coerces():
    q3 = DEF["sections"][0]["questions"][2]
    assert check_value(q3, True) == (True, True)
    assert check_value(q3, "no")[0] is False          # texto no es booleano
    assert check_value(q3, None)[0] is False          # vacío no es "No"
    q4 = DEF["sections"][0]["questions"][3]
    assert check_value(q4, "7,5") == (True, 7.5) and check_value(q4, 30)[0] is False


def test_normalize_ai_blank_illegible_and_invalid():
    res = normalize_ai({"items": [
        {"ref": "q1", "question": "Salud", "value": "c", "answer": "Excelente", "status": "answered"},   # opción inexistente
        {"ref": "q3", "question": "Fuma", "value": None, "answer": None, "status": "blank"},
        {"ref": "q5", "question": "Lesiones", "answer": None, "status": "illegible"},
        {"ref": None, "question": "Médico de cabecera", "answer": "Dr. X", "status": "answered"},
        {"ref": None, "question": "Alergias", "answer": "", "status": "answered"}],
        "body": {"weight": {"value": 160, "unit": None, "status": "answered"}, "height": {"value": 64, "unit": "in", "status": "answered"}}},
        DEF, "es")
    by = {i["ref"] or i["question"]: i for i in res["items"]}
    assert by["q1"]["status"] == "uncertain" and by["q1"]["value"] is None and by["q1"]["answer"] == "Excelente"
    assert by["q3"]["status"] == "blank" and by["q3"]["answer"] is None and by["q3"]["value"] is None
    assert by["q5"]["status"] == "illegible" and by["q5"]["answer"] is None
    assert by["q2"]["status"] == "pending" and by["q4"]["status"] == "pending"      # no leídas: no se inventan
    assert by["Médico de cabecera"]["status"] == "answered"
    assert by["Alergias"]["status"] == "blank"
    assert res["body"]["weight"]["status"] == "uncertain"      # sin unidad no se asume lb
    assert res["body"]["height"] == {"value": 64.0, "unit": "in", "status": "answered"}


def test_fillable_pdf_roundtrip(env, monkeypatch):
    db, docs, ai, c = env
    with_questionnaire(db)
    r = c.get("/api/member/evaluation-form.pdf", headers=H("jwt-m4"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["cache-control"] == "no-store"
    reader = pypdf.PdfReader(io.BytesIO(r.content))
    w = pypdf.PdfWriter(clone_from=reader)
    w.update_page_form_field_values(w.pages[0], {"q__q1": "/b", "q__q2__dm": "/Yes", "q__q4": "abc",
                                                 "body__weight": "72", "body__weight__unit": "/kg"}, auto_regenerate=False)
    buf = io.BytesIO()
    w.write(buf)
    up = c.post("/api/member/evaluation-documents", headers=H("jwt-m4"),
                files=[("files", ("cuestionario.pdf", buf.getvalue(), "application/pdf"))])
    assert up.status_code == 200, up.text
    doc = c.get(f"/api/member/evaluation-documents/{up.json()['id']}", headers=H("jwt-m4")).json()
    assert doc["status"] == "needs_review" and doc["form_ref_ok"] and doc["extraction"]["method"] == "pdf_form"
    assert not ai.calls, "un PDF rellenable no se envía a la IA"
    by = {i["ref"]: i for i in doc["extraction"]["items"]}
    assert by["q1"]["value"] == "b" and by["q1"]["answer"] == "Regular"
    assert by["q2"]["value"] == ["dm"]
    assert by["q3"]["status"] == "blank" and by["q3"]["value"] is None       # vacío, no "No"
    assert by["q4"]["status"] == "uncertain" and by["q4"]["answer"] == "abc"
    assert by["q5"]["status"] == "blank"
    assert doc["extraction"]["body"]["weight"] == {"value": 72.0, "unit": "kg", "raw": "72", "status": "answered"}
    assert "path" not in doc["files"][0] and "sha256" not in doc["files"][0]
    row = db.tables["evaluation_documents"][0]
    assert db.storage[row["files"][0]["path"]] == buf.getvalue(), "original guardado sin modificar"
    assert row["files"][0]["path"].startswith(f"{T1}/{M4}/")
    actions = [l["action"] for l in db.tables["document_access_log"]]
    assert actions == ["download_form", "upload", "extract"]


def test_form_of_other_member_rejected(env):
    db, docs, ai, c = env
    with_questionnaire(db)
    pdf = build_fillable_form({"id": Q_ID, "version": 3, "title": {"es": "x"}, "definition": DEF},
                              {"first_name": "Otro"}, {"name": "G"}, "initial", form_ref(M1, Q_ID, 3, "initial"))
    r = c.post("/api/member/evaluation-documents", headers=H("jwt-m4"),
               files=[("files", ("c.pdf", pdf, "application/pdf"))])
    assert r.status_code == 422 and r.json()["error"] == "form_other_member"
    assert not db.storage


def test_form_without_questionnaire_is_409(env):
    db, docs, ai, c = env
    r = c.get("/api/member/evaluation-form.pdf", headers=H("jwt-m4"))
    assert r.status_code == 409 and r.json()["error"] == "questionnaire_missing"


def test_photos_go_to_ai_and_stay_private(env):
    db, docs, ai, c = env
    ai.result = {"items": [{"ref": None, "question": "¿Lesiones?", "answer": "rodilla", "status": "answered"},
                           {"ref": None, "question": "Medicamentos", "answer": None, "status": "illegible"}]}
    r = c.post("/api/member/evaluation-documents", headers=H("jwt-m4"),
               files=[("files", ("p1.jpg", JPEG, "image/jpeg")), ("files", ("p2.png", PNG, "image/png"))])
    assert r.status_code == 200, r.text
    doc_id = r.json()["id"]
    assert len(ai.calls) == 1 and [m for _, m in ai.calls[0][0]] == ["image/jpeg", "image/png"]
    assert ai.calls[0][1] is None           # sin definición cargada: se transcribe tal cual está impreso
    doc = c.get(f"/api/member/evaluation-documents/{doc_id}", headers=H("jwt-m4")).json()
    assert [i["status"] for i in doc["extraction"]["items"]] == ["answered", "illegible"]
    # el propio socio puede ver su original; otro socio, owner de otro gimnasio o staff raso no
    assert c.get(f"/api/member/evaluation-documents/{doc_id}/files/2", headers=H("jwt-m4")).content == PNG
    assert c.get(f"/api/member/evaluation-documents/{doc_id}/files/1", headers=H("jwt-member")).status_code in (403, 404)
    assert c.get(f"/api/member/evaluation-documents/{doc_id}", headers=H("jwt-member")).status_code in (403, 404)
    assert c.get(f"/api/manager/evaluation-documents/{doc_id}/files/1", headers=H("jwt-other")).status_code == 404
    assert c.get(f"/api/manager/evaluation-documents/{doc_id}/files/1", headers=H("jwt-staff")).status_code == 404
    ok = c.get(f"/api/manager/evaluation-documents/{doc_id}/files/1", headers=H("jwt-owner"))
    assert ok.status_code == 200 and ok.content == JPEG and ok.headers["cache-control"] == "no-store"
    assert c.get(f"/api/member/evaluation-documents/{doc_id}/files/1").status_code == 401
    views = [l for l in db.tables["document_access_log"] if l["action"] == "view_file"]
    assert [(v["actor_role"], v["file_n"]) for v in views] == [("member", 2), ("owner", 1)]


def test_rejects_bad_files(env):
    db, docs, ai, c = env
    r = c.post("/api/member/evaluation-documents", headers=H("jwt-m4"),
               files=[("files", ("x.jpg", b"<script>alert(1)</script>", "image/jpeg"))])
    assert r.status_code == 415
    r = c.post("/api/member/evaluation-documents", headers=H("jwt-m4"),
               files=[("files", ("big.jpg", JPEG + b"0" * ed.MAX_FILE, "image/jpeg"))])
    assert r.status_code == 413
    assert c.post("/api/member/evaluation-documents", files=[("files", ("a.jpg", JPEG, "image/jpeg"))]).status_code == 401
    assert not db.storage


def test_ai_failure_keeps_original_and_allows_manual(env):
    db, docs, ai, c = env
    with_questionnaire(db)
    ai.fail = True
    r = c.post("/api/member/evaluation-documents", headers=H("jwt-m4"), files=[("files", ("p.jpg", JPEG, "image/jpeg"))])
    doc = c.get(f"/api/member/evaluation-documents/{r.json()['id']}", headers=H("jwt-m4")).json()
    assert doc["status"] == "failed" and doc["extraction_error"] == "extraction_failed"
    assert all(i["status"] == "pending" for i in doc["extraction"]["items"]) and len(doc["extraction"]["items"]) == 5
    assert len(db.storage) == 1
    ai.fail = False
    ai.result = {"items": [{"ref": "q3", "question": "Fuma", "value": False, "answer": "No", "status": "answered"}]}
    rr = c.post(f"/api/member/evaluation-documents/{r.json()['id']}/retry", headers=H("jwt-m4"))
    assert rr.status_code == 200
    doc = c.get(f"/api/member/evaluation-documents/{r.json()['id']}", headers=H("jwt-m4")).json()
    q3 = next(i for i in doc["extraction"]["items"] if i["ref"] == "q3")
    assert doc["status"] == "needs_review" and q3["value"] is False and q3["answer"] == "No"
    assert c.post(f"/api/member/evaluation-documents/{r.json()['id']}/retry", headers=H("jwt-m4")).status_code == 409


def test_ai_disabled_means_manual_transcription(env, monkeypatch):
    db, docs, ai, c = env
    monkeypatch.setenv("EVALUATION_AI_EXTRACTION", "0")
    r = c.post("/api/member/evaluation-documents", headers=H("jwt-m4"), files=[("files", ("p.jpg", JPEG, "image/jpeg"))])
    doc = c.get(f"/api/member/evaluation-documents/{r.json()['id']}", headers=H("jwt-m4")).json()
    assert doc["status"] == "needs_review" and doc["extraction"]["method"] == "manual" and not ai.calls
    assert "ai_disabled" in doc["extraction"]["warnings"]


def test_model_is_configurable(monkeypatch):
    monkeypatch.setenv("EVALUATION_EXTRACTION_MODEL", "modelo-x")
    assert ed.extraction_model() == "modelo-x"
    monkeypatch.delenv("EVALUATION_EXTRACTION_MODEL")
    monkeypatch.setenv("ANTHROPIC_MODEL", "modelo-y")
    assert ed.extraction_model() == "modelo-y"


def test_ai_extract_sends_structured_request(monkeypatch):
    from PIL import Image
    img = io.BytesIO()
    Image.new("RGB", (3000, 1500), "white").save(img, "JPEG")

    class Blk:
        type = "tool_use"
        input = {"items": [{"ref": "q3", "question": "Fuma", "value": True, "answer": "Sí", "status": "answered"}]}

    class Msgs:
        def create(self, **kw):
            self.kw = kw
            return type("M", (), {"content": [Blk()]})()

    client = type("C", (), {"messages": Msgs()})()
    monkeypatch.setenv("ANTHROPIC_MODEL", "cfg-model")
    res = ed.ai_extract([(img.getvalue(), "image/jpeg"), (b"%PDF-1.4 x", "application/pdf")], DEF, "es", client=client)
    kw = client.messages.kw
    assert kw["model"] == "cfg-model" and kw["tool_choice"]["name"] == "record_questionnaire"
    content = kw["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[1]["type"] == "document"
    assert "NOT \"No\"" in content[-1]["text"] and '"q3"' in content[-1]["text"]
    assert res["model"] == "cfg-model" and next(i for i in res["items"] if i["ref"] == "q3")["value"] is True
