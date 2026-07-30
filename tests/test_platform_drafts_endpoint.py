"""حُرّاس نقطة المسودّات — the endpoint that made launching actually send mail.

**الفجوة التي يُغلقها (اكتُشفت قبل بناء الواجهة، لا بعدها):** جدول `drafts`
موجود و`repository.drafts()` موجود، و`POST /studies/{id}/launch` يصفّ البريد
**فقط** إذا مُرِّر `draft_id` **و**`prospect_ids` معاً (`api.py` — فرع
`if draft_id and prospect_ids`). لكن **لا نقطة نهاية تُنشئ مسودّة**: الوحيدة
المتعلّقة بها `POST /funnels/{id}/draft` وهي تربط مسودّةً **قائمة** بقمع.

فأيّ زرّ «إطلاق» في واجهةٍ كان **يستهلك حصّةً وينقل الحالة إلى `in_progress`
ويصفّ صفر بريد** — نجاحٌ ظاهريّ يفعل أقلّ مما يُظهِر، وهو بعينه ما يمنعه عقد
عدم الاختلاق. فالنقطة شرطُ صحّةٍ للواجهة لا إضافةٌ تجميلية.

الحُرّاس هنا تُثبِت: العزل بين المستأجرين (أخطر شيء في نقطةٍ جديدة)، جدارُ
الدور، والتحقّق من المدخلات — ثم أن الإطلاق **يصفّ فعلاً** بعد توفّرها.
"""
from __future__ import annotations

from tests.platform_helpers import (add_active_smtp, client, hdr, login,
                                    make_factory, seed, setup_env)


def _factory(cl, tier="silver", email="drafts@f.local", **kw):
    f = make_factory(tier, email, **kw)
    return f, login(cl, email, f["password"])


def _new_study(cl, tok, smtp_id, target=2):
    r = cl.post("/platform/studies", headers=hdr(tok),
                json={"title_ar": "دراسة", "smtp_config_id": smtp_id,
                      "target_count": target})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ════════════════════ الإنشاء والسرد · create + list ═════════════════════════
def test_a_factory_can_create_and_list_its_own_drafts(monkeypatch):
    """المسار الأساسي: إنشاء مسودّة ثم رؤيتها في السرد بمفتاح `drafts`."""
    seed(monkeypatch)
    cl = client()
    f, tok = _factory(cl)
    smtp = add_active_smtp(f["account_id"])
    sid = _new_study(cl, tok, smtp)
    r = cl.post("/platform/drafts", headers=hdr(tok),
                json={"study_id": sid, "subject_ar": "عرض تعاون",
                      "body_ar": "نصّ الرسالة", "version": "A"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["subject_ar"] == "عرض تعاون" and row["study_id"] == sid
    listed = cl.get("/platform/drafts", headers=hdr(tok))
    assert listed.status_code == 200
    assert [d["id"] for d in listed.json()["drafts"]] == [row["id"]]


def test_a_draft_needs_no_study_so_a_template_can_exist_first(monkeypatch):
    """`study_id` اختياريّ (العمود يقبل NULL) — قالبٌ قبل وجود دراسة."""
    seed(monkeypatch)
    cl = client()
    _f, tok = _factory(cl)
    r = cl.post("/platform/drafts", headers=hdr(tok),
                json={"subject_ar": "قالب", "body_ar": "نصّ"})
    assert r.status_code == 200, r.text
    assert r.json()["study_id"] is None


# ════════════════════ العزل بين المستأجرين · tenant isolation ════════════════
def test_a_foreign_study_id_is_refused_not_written(monkeypatch):
    """`study_id` لمستأجرٍ آخر يُرفَض 422 — لا يُكتب في عمودٍ بمفتاح أجنبي.

    نفس عقد `_validate_smtp_binding`: الربطُ العابر للمستأجر يُرفَض عند البوّابة
    لا عند القاعدة، وإلا صارت مسودّةُ مصنعٍ معلّقةً بدراسة مصنعٍ آخر.
    """
    seed(monkeypatch)
    cl = client()
    a, tok_a = _factory(cl, email="a@f.local")
    b, tok_b = _factory(cl, email="b@f.local")
    smtp_b = add_active_smtp(b["account_id"])
    sid_b = _new_study(cl, tok_b, smtp_b)
    r = cl.post("/platform/drafts", headers=hdr(tok_a),
                json={"study_id": sid_b, "subject_ar": "تسلّل"})
    assert r.status_code == 422, r.text
    # ولا صفّ كُتب لحساب A.
    assert cl.get("/platform/drafts", headers=hdr(tok_a)).json()["drafts"] == []


def test_one_tenants_drafts_are_invisible_to_another(monkeypatch):
    """سردُ المسودّات مقصورٌ على مالكها — لا تسريب عبر النقطة العامّة."""
    seed(monkeypatch)
    cl = client()
    _a, tok_a = _factory(cl, email="a2@f.local")
    _b, tok_b = _factory(cl, email="b2@f.local")
    cl.post("/platform/drafts", headers=hdr(tok_b),
            json={"subject_ar": "سرّ المصنع ب"})
    mine = cl.get("/platform/drafts", headers=hdr(tok_a)).json()["drafts"]
    assert mine == [], mine


def test_the_admin_cannot_read_factory_drafts(monkeypatch):
    """جدارُ الأدمِن: المسودّات محتوى مصنعٍ (نصّ تسويقيّ) فلا يبلغها الأدمِن."""
    info = seed(monkeypatch)
    cl = client()
    _f, tok = _factory(cl, email="c@f.local")
    cl.post("/platform/drafts", headers=hdr(tok), json={"subject_ar": "محتوى"})
    admin = login(cl, info["admin"]["email"], info["admin"]["password"])
    r = cl.get("/platform/drafts", headers=hdr(admin))
    assert r.status_code == 403, r.text


# ════════════════════ التحقّق من المدخلات · input validation ══════════════════
def test_an_unknown_version_is_refused_not_persisted(monkeypatch):
    """`version` مقيَّد بـA/B في المخطّط — يُرفَض 422 لا يصعد 500 من القاعدة."""
    seed(monkeypatch)
    cl = client()
    _f, tok = _factory(cl, email="d@f.local")
    r = cl.post("/platform/drafts", headers=hdr(tok),
                json={"subject_ar": "س", "version": "Z"})
    assert r.status_code == 422, r.text
    assert cl.get("/platform/drafts", headers=hdr(tok)).json()["drafts"] == []


def test_a_container_value_is_a_422_not_a_500(monkeypatch):
    """قيمةٌ حاوية في حقل نصّ ⇒ 422 — نفس عقد `_as_text` في بقيّة النقاط."""
    seed(monkeypatch)
    cl = client()
    _f, tok = _factory(cl, email="e@f.local")
    r = cl.post("/platform/drafts", headers=hdr(tok),
                json={"subject_ar": {"nested": "x"}})
    assert r.status_code == 422, r.text


def test_a_draft_with_no_content_at_all_is_refused(monkeypatch):
    """مسودّةٌ بلا موضوعٍ ولا نصّ لا تصلح للإرسال — تُرفَض عند الإنشاء.

    لو قُبلت لأمكن إطلاقُ حملةٍ ترسل رسائل فارغة، وهي فشلٌ صامت أمام العميل.
    """
    seed(monkeypatch)
    cl = client()
    _f, tok = _factory(cl, email="f2@f.local")
    r = cl.post("/platform/drafts", headers=hdr(tok), json={"version": "A"})
    assert r.status_code == 422, r.text


# ════════════════════ الغرض كلّه: الإطلاق يصفّ فعلاً ═════════════════════════
def test_with_a_draft_and_prospects_launch_actually_queues_mail(monkeypatch):
    """**الحارس الذي يُبرِّر النقطة**: بلا مسودّة يصفّ الإطلاق صفراً.

    يقيس الفرق مباشرةً: نفس الدراسة، نفس العميل المحتمل — مرّةً بلا `draft_id`
    (فيمرّ الإطلاق و`queued == 0`) ومرّةً بها (فيصفّ فعلاً). هذا هو العطل الذي
    كانت واجهةٌ بزرّ «إطلاق» ستُخفيه: نجاحٌ ظاهريّ يفعل أقلّ مما يُظهِر.
    """
    seed(monkeypatch)
    cl = client()
    f, tok = _factory(cl, email="g@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    pr = cl.post("/platform/prospects", headers=hdr(tok),
                 json={"email": "buyer@nl.example", "first_name": "Jan"})
    assert pr.status_code == 200, pr.text
    pid = pr.json()["id"]

    # (أ) بلا مسودّة — الإطلاق ينجح ويصفّ **صفراً**.
    bare = _new_study(cl, tok, smtp, target=1)
    r = cl.post(f"/platform/studies/{bare}/launch", headers=hdr(tok),
                json={"prospect_ids": [pid]})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 0, "بلا مسودّة يجب أن يبقى الصفّ صفراً"

    # (ب) مع مسودّة — يصفّ فعلاً.
    sid = _new_study(cl, tok, smtp, target=1)
    d = cl.post("/platform/drafts", headers=hdr(tok),
                json={"study_id": sid, "subject_ar": "عرض", "body_ar": "نصّ"})
    assert d.status_code == 200, d.text
    r = cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok),
                json={"draft_id": d.json()["id"], "prospect_ids": [pid]})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 1, r.text


def test_launching_with_a_foreign_draft_queues_nothing(monkeypatch):
    """مسودّةُ مستأجرٍ آخر لا تُصفّ — `repository.drafts().get` مقصورٌ بالمالك."""
    seed(monkeypatch)
    cl = client()
    a, tok_a = _factory(cl, email="h@f.local", fund_cents=100_000)
    _b, tok_b = _factory(cl, email="i@f.local")
    foreign = cl.post("/platform/drafts", headers=hdr(tok_b),
                      json={"subject_ar": "مسودّة ب"}).json()["id"]
    smtp = add_active_smtp(a["account_id"])
    pid = cl.post("/platform/prospects", headers=hdr(tok_a),
                  json={"email": "x@nl.example"}).json()["id"]
    sid = _new_study(cl, tok_a, smtp, target=1)
    r = cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok_a),
                json={"draft_id": foreign, "prospect_ids": [pid]})
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 0, "مسودّةٌ أجنبية صُفَّت — خرقُ عزل!"


def test_an_unauthenticated_caller_gets_401_on_both_verbs(monkeypatch):
    """لا مسارَ مفتوحاً: القراءة والكتابة كلتاهما تحتاجان جلسة."""
    setup_env(monkeypatch)
    cl = client()
    assert cl.get("/platform/drafts").status_code == 401
    assert cl.post("/platform/drafts", json={"subject_ar": "x"}).status_code == 401
