"""PR-8: رفع صور حقيقي + خدمة `/files` بروابط موقَّعة + بحث تدقيق المصنع.

قبل هذا الفرق كانت `POST /platform/images` تسجّل `size_bytes`/`ext` كما
يرسلهما العميل بلا أي بايت فعليّ — الحجم الآن مقيسٌ من المحتوى المرفوع فعلاً
(يُستهلَك في فوترة التخزين الحقيقية)، والامتداد مُقيَّد بقائمة بيضاء، ومسار
`/files/...` يخدم الملف فعلياً وراء توقيع HMAC (لا يخدم شيئاً قبل هذا الفرق).
"""
import os
import time

from platform_helpers import client, hdr, login, make_factory, seed, setup_env
from silk_platform import db as pdb, storage, tokens


def _upload(cl, tok, *, filename="a.png", content=b"\x89PNG-fake-bytes",
           mime="image/png"):
    return cl.post("/platform/images", headers=hdr(tok),
                   files={"file": (filename, content, mime)})


# ════════════════════════════ upload ══════════════════════════════════════════
def test_upload_measures_real_size_and_writes_to_disk(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "up1@f.local")
    conn = pdb.connect()
    cl = client()
    tok = login(cl, f["email"], f["password"])
    content = b"\x89PNG-fake-bytes-0123456789"
    r = _upload(cl, tok, content=content)
    assert r.status_code == 200
    row = r.json()
    assert row["size_bytes"] == len(content)
    assert row["mime_type"] == "image/png"
    path = storage.path_for(row["storage_key"])
    assert os.path.isfile(path)
    with open(path, "rb") as fh:
        assert fh.read() == content
    db_row = conn.execute("SELECT size_bytes FROM images WHERE id = ?",
                          (row["id"],)).fetchone()
    assert db_row["size_bytes"] == len(content)


def test_upload_rejects_disallowed_extension_and_writes_nothing(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "up2@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = _upload(cl, tok, filename="a.exe", content=b"MZ-fake",
               mime="application/octet-stream")
    assert r.status_code == 422
    conn = pdb.connect()
    count = conn.execute("SELECT COUNT(*) c FROM images WHERE owner_id = ?",
                         (f["account_id"],)).fetchone()["c"]
    assert count == 0


def test_upload_rejects_oversized_file(monkeypatch):
    setup_env(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_MAX_IMAGE_BYTES", "10")
    f = make_factory("gold", "up3@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = _upload(cl, tok, content=b"0123456789ABCDEF")   # 16 bytes > 10-byte cap
    assert r.status_code == 422
    conn = pdb.connect()
    count = conn.execute("SELECT COUNT(*) c FROM images WHERE owner_id = ?",
                         (f["account_id"],)).fetchone()["c"]
    assert count == 0


# ════════════════════════════ signed-url + /files ═════════════════════════════
def test_signed_url_then_serve_file_round_trip(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "serve1@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    content = b"\x89PNG-round-trip-bytes"
    image = _upload(cl, tok, content=content).json()
    signed = cl.get(f"/platform/images/{image['id']}/signed-url",
                    headers=hdr(tok)).json()
    assert signed["serving_available"] is True
    url = signed["signed_url"]
    r = cl.get(url)
    assert r.status_code == 200
    assert r.content == content
    assert r.headers["content-type"].startswith("image/png")


def test_serve_file_rejects_tampered_signature(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "serve2@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    image = _upload(cl, tok).json()
    signed = cl.get(f"/platform/images/{image['id']}/signed-url",
                    headers=hdr(tok)).json()
    tampered = signed["signed_url"][:-1] + ("0" if signed["signed_url"][-1] != "0" else "1")
    r = cl.get(tampered)
    assert r.status_code == 404


def test_serve_file_rejects_expired_signature(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "serve3@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    image = _upload(cl, tok).json()
    storage_key = image["storage_key"]
    expired = int(time.time()) - 10
    sig = tokens.sign(f"{storage_key}:{expired}")
    r = cl.get(f"/files/{storage_key}?expires={expired}&sig={sig}")
    assert r.status_code == 404


def test_serve_file_for_unknown_storage_key_is_404_even_with_valid_signature(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "serve4@f.local")
    cl = client()
    login(cl, f["email"], f["password"])
    fake_key = f"{f['account_id']}/never-uploaded.png"
    expiry = int(time.time()) + 900
    sig = tokens.sign(f"{fake_key}:{expiry}")
    r = cl.get(f"/files/{fake_key}?expires={expiry}&sig={sig}")
    assert r.status_code == 404


def test_serve_file_rejects_path_traversal_signed_for_a_fake_key(monkeypatch):
    """اجتياز مسار مُوقَّع بصورة صحيحة يبقى 404 — الفحص ببحث DB لا بمسار خام.

    `serve_file` لا يبني مساراً على القرص من الوسيط الخام مباشرة أبداً — يبحث
    عن صفّ بـstorage_key أولاً، فمفتاحٌ لا يطابق صفّاً حقيقياً (حتى لو موقَّعاً
    بصحّة) لا يصل نظام الملفات إطلاقاً. A signed-but-never-issued key never
    reaches the filesystem: the DB lookup is the gate, not the raw path.
    """
    setup_env(monkeypatch)
    evil_key = "../../../../etc/passwd"
    expiry = int(time.time()) + 900
    sig = tokens.sign(f"{evil_key}:{expiry}")
    cl = client()
    r = cl.get(f"/files/{evil_key}?expires={expiry}&sig={sig}")
    assert r.status_code == 404


def test_serve_file_cannot_be_signed_by_a_foreign_account(monkeypatch):
    """رابط موقَّع لصورة حساب آخر يُرفَض قبل التوقيع أصلاً — 404 لا تسريب وجود."""
    seed(monkeypatch)
    a = make_factory("gold", "own_a@f.local")
    b = make_factory("gold", "own_b@f.local")
    cl = client()
    tok_a = login(cl, a["email"], a["password"])
    tok_b = login(cl, b["email"], b["password"])
    image = _upload(cl, tok_a).json()
    r = cl.get(f"/platform/images/{image['id']}/signed-url", headers=hdr(tok_b))
    assert r.status_code == 404   # B never gets a signed URL for A's image


# ════════════════════════════ audit search (factory) ══════════════════════════
def test_factory_audit_filters_by_action(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "audit1@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post("/platform/studies", headers=hdr(tok), json={"title_en": "S"})
    _upload(cl, tok)
    all_rows = cl.get("/platform/audit", headers=hdr(tok)).json()["audit"]
    actions = {r["action"] for r in all_rows}
    assert "study_created" in actions
    filtered = cl.get("/platform/audit?action=study_created",
                      headers=hdr(tok)).json()["audit"]
    assert filtered and all(r["action"] == "study_created" for r in filtered)
