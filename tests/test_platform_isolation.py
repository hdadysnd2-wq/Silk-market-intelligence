"""اختبارات العزل بين المستأجرين (القسم ١٣: ISOLATION) — Section 13 isolation.

الحساب A لا يسرد/يقرأ/يعدّل/يحذف كيانات الحساب B (404، لا 403)، ولا يتلاعب
بمعامل الاستعلام ليعبر، والقاعدة تبقى دون تغيير والمحاولات تُسجَّل تدقيقاً.
ربط SMTP عابر مرفوض؛ الروابط الموقّعة لا تُولَّد لمالك أجنبي؛ المحفظة/الدفتر
حساب المنادي فقط.
"""
from platform_helpers import client, hdr, login, seed
from silk_platform import db as pdb


def _audit_rows(action=None):
    conn = pdb.connect()
    try:
        sql = "SELECT * FROM audit_log"
        args = ()
        if action:
            sql += " WHERE action = ?"
            args = (action,)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _setup(monkeypatch):
    """A ينشئ دراسة/عميل/صورة/SMTP؛ يرجّع (cl, A, B, ids)."""
    info = seed(monkeypatch)
    cl = client()
    ta = login(cl, info["factory_a"]["email"], info["factory_a"]["password"])
    tb = login(cl, info["factory_b"]["email"], info["factory_b"]["password"])
    study = cl.post("/platform/studies", headers=hdr(ta),
                    json={"title_en": "A-Study", "target_count": 3}).json()
    prospect = cl.post("/platform/prospects", headers=hdr(ta),
                       json={"email": "lead@a.local", "first_name": "Lead"}).json()
    image = cl.post("/platform/images", headers=hdr(ta),
                    files={"file": ("a.png", b"\x89PNG-fake-bytes", "image/png")}
                    ).json()
    smtp = cl.post("/platform/smtp-configs", headers=hdr(ta),
                   json={"host": "h", "port": 25, "from_email": "a@a.local",
                         "username": "u", "password": "p"}).json()
    return cl, ta, tb, info, {"study": study["id"], "prospect": prospect["id"],
                              "image": image["id"], "smtp": smtp["id"]}


def test_list_is_account_scoped(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    b_studies = cl.get("/platform/studies", headers=hdr(tb)).json()["studies"]
    assert all(s["id"] != ids["study"] for s in b_studies)
    a_studies = cl.get("/platform/studies", headers=hdr(ta)).json()["studies"]
    assert any(s["id"] == ids["study"] for s in a_studies)


def test_read_by_id_cross_tenant_404(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    for path in (f"/platform/studies/{ids['study']}",
                 f"/platform/prospects/{ids['prospect']}"):
        assert cl.get(path, headers=hdr(tb)).status_code == 404


def test_query_param_manipulation_cannot_cross_tenant(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    a_account = info["factory_a"]["account_id"]
    # B يمرّر owner_id=A في الاستعلام — يُتجاهَل تماماً.
    r = cl.get(f"/platform/studies?owner_id={a_account}", headers=hdr(tb))
    assert all(s["id"] != ids["study"] for s in r.json()["studies"])
    # وكذلك دفتر المحفظة — ledger ignores any account_id query param.
    led = cl.get(f"/platform/wallet/ledger?account_id={a_account}", headers=hdr(tb))
    assert led.json()["account_id"] == info["factory_b"]["account_id"]


def test_cross_tenant_patch_404_db_unchanged_audited(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    before = cl.get(f"/platform/studies/{ids['study']}", headers=hdr(ta)).json()
    r = cl.patch(f"/platform/studies/{ids['study']}", headers=hdr(tb),
                 json={"title_en": "HACKED"})
    assert r.status_code == 404
    after = cl.get(f"/platform/studies/{ids['study']}", headers=hdr(ta)).json()
    assert after["title_en"] == before["title_en"] == "A-Study"  # unchanged
    denied = _audit_rows("cross_tenant_write")
    assert any(row["account_id"] == info["factory_b"]["account_id"]
               and str(ids["study"]) == row["resource_id"] for row in denied)


def test_cross_tenant_delete_404_db_unchanged_audited(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    r = cl.delete(f"/platform/studies/{ids['study']}", headers=hdr(tb))
    assert r.status_code == 404
    # الصفّ لا يزال موجوداً للمالك — still present for the real owner.
    assert cl.get(f"/platform/studies/{ids['study']}",
                  headers=hdr(ta)).status_code == 200
    assert _audit_rows("cross_tenant_delete")


def test_cross_tenant_smtp_binding_rejected_and_launch_blocked(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    # B يحاول ربط SMTP الخاص بـ A بدراسته — 422 عند الإنشاء (ملكية غير مطابقة).
    r = cl.post("/platform/studies", headers=hdr(tb),
                json={"title_en": "B", "smtp_config_id": ids["smtp"]})
    assert r.status_code == 422
    # وحتى عند التعديل — patch binding a foreign smtp is rejected too.
    b_study = cl.post("/platform/studies", headers=hdr(tb),
                      json={"title_en": "B2"}).json()
    r2 = cl.patch(f"/platform/studies/{b_study['id']}", headers=hdr(tb),
                  json={"smtp_config_id": ids["smtp"]})
    assert r2.status_code == 422
    # الإطلاق بلا SMTP مملوك محجوب — launch without an owned smtp is blocked.
    r3 = cl.post(f"/platform/studies/{b_study['id']}/launch", headers=hdr(tb), json={})
    assert r3.status_code == 422


def test_signed_url_never_for_foreign_owner(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    # A (المالك) يحصل على رابط موقّع — owner gets a signed url.
    ok = cl.get(f"/platform/images/{ids['image']}/signed-url", headers=hdr(ta))
    assert ok.status_code == 200 and ok.json()["signed_url"]
    # B لا يحصل على شيء — foreign owner: 404, no url generated.
    bad = cl.get(f"/platform/images/{ids['image']}/signed-url", headers=hdr(tb))
    assert bad.status_code == 404
    assert "signed_url" not in bad.json()


def test_wallet_ledger_returns_own_entries_only(monkeypatch):
    cl, ta, tb, info, ids = _setup(monkeypatch)
    # موّل A وB بمبالغ مختلفة عبر مسار الأدمِن — fund both via the admin path.
    tadmin = login(cl, info["admin"]["email"], info["admin"]["password"])
    cl.post("/platform/admin/fund", headers=hdr(tadmin),
            json={"account_id": info["factory_a"]["account_id"], "amount_cents": 500})
    cl.post("/platform/admin/fund", headers=hdr(tadmin),
            json={"account_id": info["factory_b"]["account_id"], "amount_cents": 900})
    a_led = cl.get("/platform/wallet/ledger", headers=hdr(ta)).json()
    b_led = cl.get("/platform/wallet/ledger", headers=hdr(tb)).json()
    assert all(e["account_id"] == info["factory_a"]["account_id"]
               for e in a_led["entries"])
    assert all(e["account_id"] == info["factory_b"]["account_id"]
               for e in b_led["entries"])
    # A لا يرى قيد 900 الخاص بـ B — A never sees B's 900 credit.
    assert all(e["amount"] != 900 for e in a_led["entries"])
