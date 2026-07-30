"""اختبارات PR-7 — قمع المقارنة (Gold/Platinum) بحالاته الخمس.

المحوران:
1. **البوّابة طبقةٌ لا تُتخطّى:** Basic/Silver لا يملكان قمعاً (403 بدعوة ترقية)،
   وسقف `funnel_max_studies` (١٠ لـGold) مفروضٌ فعلياً لا معلَناً.
2. **جدولا الوصل بلا عمود مالك** — فكل معرّف (دراسة/عميل/مسودّة) تُتحقَّق ملكيته
   قبل الكتابة، وإلا لربط مستأجرٌ بيانات مستأجرٍ آخر بقمعه. هذا أخطر ما في
   الموجة، فله اختبارات صريحة.
3. **`sent` تغيّر الواقع لا العمود** (درس `lifecycle.archive_study`): الإرسال
   يصفّ بريداً فعلياً، وبوّابتا المال تسبقه.
"""
from __future__ import annotations

import pytest

from platform_helpers import (add_active_smtp, client, hdr, login,
                              make_draft_study, make_factory, seed)
from silk_platform import db as pdb


def _prospect(cl, tok, email: str) -> int:
    r = cl.post("/platform/prospects", headers=hdr(tok), json={"email": email,
                                                              "first_name": "P"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _funnel_state(funnel_id: int) -> str:
    conn = pdb.connect()
    try:
        return conn.execute("SELECT state FROM comparison_funnels WHERE id = ?",
                            (funnel_id,)).fetchone()["state"]
    finally:
        conn.close()


def _gold_ready(monkeypatch, email="fn@f.local", *, fund_cents=100_000):
    """حساب Gold + SMTP + عميل مسجَّل الدخول — the common fixture."""
    seed(monkeypatch)
    f = make_factory("gold", email, fund_cents=fund_cents)
    smtp = add_active_smtp(f["account_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    return f, smtp, cl, tok


# ════════════════════════ بوّابة الطبقة · the tier gate ═══════════════════════
@pytest.mark.parametrize("tier", ["basic", "silver"])
def test_funnel_is_forbidden_below_gold_with_an_upgrade_prompt(monkeypatch, tier):
    """طبقةٌ لا تمنح القمع ⇒ 403 بدعوة ترقية قابلة للقراءة آلياً، لا 404 ولا 500."""
    seed(monkeypatch)
    f = make_factory(tier, f"fn-{tier}@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post("/platform/funnels", headers=hdr(tok))
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["error"] == "tier_gate" and d["feature"] == "funnel"
    assert d["upgrade"] is True
    assert "gold" in d["required_tiers"] and "platinum" in d["required_tiers"]


@pytest.mark.parametrize("tier", ["gold", "platinum"])
def test_funnel_is_allowed_on_gold_and_platinum(monkeypatch, tier):
    seed(monkeypatch)
    f = make_factory(tier, f"fn-ok-{tier}@f.local")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post("/platform/funnels", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "compared"
    assert r.json()["study_ids"] == [] and r.json()["prospect_ids"] == []


def test_gold_funnel_study_cap_is_enforced_at_ten(monkeypatch):
    """سقف Gold عشر دراسات — الحادية عشرة تُرفَض بدعوة ترقية، والعدد لا يتجاوز."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-cap@f.local")
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    for i in range(10):
        sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
        r = cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
                    json={"study_id": sid})
        assert r.status_code == 200, f"study {i} rejected: {r.text}"
    extra, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    r = cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
                json={"study_id": extra})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["error"] == "funnel_study_cap" and d["limit"] == 10
    assert d["used"] == 10 and d["upgrade"] is True
    detail = cl.get(f"/platform/funnels/{fid}", headers=hdr(tok)).json()
    assert len(detail["study_ids"]) == 10, "the cap must not be exceeded"


def test_platinum_funnel_has_no_study_cap(monkeypatch):
    """Platinum بلا سقف (-1) — أحد عشر تمرّ حيث يرفض Gold."""
    seed(monkeypatch)
    f = make_factory("platinum", "fn-plat@f.local")
    smtp = add_active_smtp(f["account_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    for _ in range(11):
        sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
        assert cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
                       json={"study_id": sid}).status_code == 200
    detail = cl.get(f"/platform/funnels/{fid}", headers=hdr(tok)).json()
    assert len(detail["study_ids"]) == 11


def test_reads_stay_available_after_a_downgrade(monkeypatch):
    """تخفيضٌ بعد الإنشاء لا يُخفي الأقماع القائمة — القراءة غير مبوَّبة بالطبقة.

    احتجازُ بيانات العميل خلف ترقية سلوكٌ عدائي؛ الكتابة وحدها مبوَّبة.
    """
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-down@f.local")
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    conn = pdb.connect()
    try:   # تخفيضٌ مباشر (مسار الأدمِن مُختبَر في PR-2)
        conn.execute("UPDATE accounts SET tier = 'silver' WHERE id = ?",
                     (f["account_id"],))
        conn.commit()
    finally:
        conn.close()
    assert cl.get("/platform/funnels", headers=hdr(tok)).status_code == 200
    assert cl.get(f"/platform/funnels/{fid}", headers=hdr(tok)).status_code == 200
    # لكن الكتابة مبوَّبة · but writes are gated
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    assert cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
                   json={"study_id": sid}).status_code == 403


# ═══════════ العزل على جدولَي الوصل (بلا عمود مالك) · join-table isolation ════
def test_cannot_attach_another_tenants_study_to_my_funnel(monkeypatch):
    """**الاختبار الحاسم للعزل**: دراسةُ حسابٍ آخر لا تُربَط بقمعي.

    `funnel_studies` بلا عمود مالك، فلا حارس بنيوي هناك — التحقّق في الشيفرة
    هو الحارس الوحيد. لو مرّ هذا لربط مستأجرٌ بيانات مستأجرٍ آخر بقمعه.
    """
    seed(monkeypatch)
    a = make_factory("gold", "fn-iso-a@f.local")
    b = make_factory("gold", "fn-iso-b@f.local")
    smtp_b = add_active_smtp(b["account_id"])
    victim_study, _ = make_draft_study(b["account_id"], b["user_id"], smtp_b)
    cl = client()
    tok_a = login(cl, a["email"], a["password"])
    fid = cl.post("/platform/funnels", headers=hdr(tok_a)).json()["id"]
    r = cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok_a),
                json={"study_id": victim_study})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "study_not_found"
    # ولا صفّ وصلٍ كُتب · and no join row was written
    conn = pdb.connect()
    try:
        rows = conn.execute("SELECT COUNT(*) c FROM funnel_studies WHERE "
                            "funnel_id = ?", (fid,)).fetchone()["c"]
    finally:
        conn.close()
    assert rows == 0, "a cross-tenant study was linked into the funnel"


def test_cannot_extract_another_tenants_prospect(monkeypatch):
    """عميلُ حسابٍ آخر لا يُربَط بقمعي — نفس الثغرة على `funnel_prospects`."""
    seed(monkeypatch)
    a = make_factory("gold", "fn-isop-a@f.local")
    b = make_factory("gold", "fn-isop-b@f.local")
    smtp_a = add_active_smtp(a["account_id"])
    cl = client()
    tok_a = login(cl, a["email"], a["password"])
    tok_b = login(cl, b["email"], b["password"])
    victim_prospect = _prospect(cl, tok_b, "victim@b.local")
    fid = cl.post("/platform/funnels", headers=hdr(tok_a)).json()["id"]
    sid, _ = make_draft_study(a["account_id"], a["user_id"], smtp_a)
    cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok_a),
            json={"study_id": sid})
    cl.post(f"/platform/funnels/{fid}/select", headers=hdr(tok_a),
            json={"study_id": sid})
    r = cl.post(f"/platform/funnels/{fid}/extract", headers=hdr(tok_a),
                json={"prospect_ids": [victim_prospect]})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "prospect_not_found"
    conn = pdb.connect()
    try:
        rows = conn.execute("SELECT COUNT(*) c FROM funnel_prospects WHERE "
                            "funnel_id = ?", (fid,)).fetchone()["c"]
    finally:
        conn.close()
    assert rows == 0, "a cross-tenant prospect was linked into the funnel"
    assert _funnel_state(fid) == "selected", "a refused extract must not transition"


def test_cannot_read_or_drive_another_tenants_funnel(monkeypatch):
    """قمعُ حسابٍ آخر ⇒ 404 بلا تسريب وجود + قيد تدقيق."""
    seed(monkeypatch)
    a = make_factory("gold", "fn-isof-a@f.local")
    b = make_factory("gold", "fn-isof-b@f.local")
    cl = client()
    tok_a = login(cl, a["email"], a["password"])
    tok_b = login(cl, b["email"], b["password"])
    victim = cl.post("/platform/funnels", headers=hdr(tok_b)).json()["id"]
    assert cl.get(f"/platform/funnels/{victim}",
                  headers=hdr(tok_a)).status_code == 404
    assert cl.post(f"/platform/funnels/{victim}/studies", headers=hdr(tok_a),
                   json={"study_id": 1}).status_code == 404
    assert cl.post(f"/platform/funnels/{victim}/send",
                   headers=hdr(tok_a)).status_code == 404
    from silk_platform import audit
    conn = pdb.connect()
    try:
        denied = audit.search(conn, account_id=a["account_id"])
    finally:
        conn.close()
    actions = {r["action"] for r in denied}
    assert any("cross_tenant_funnel" in x for x in actions), (
        f"a cross-tenant funnel attempt must be audited: {actions}")


def test_funnel_list_is_account_scoped(monkeypatch):
    seed(monkeypatch)
    a = make_factory("gold", "fn-list-a@f.local")
    b = make_factory("gold", "fn-list-b@f.local")
    cl = client()
    tok_a = login(cl, a["email"], a["password"])
    tok_b = login(cl, b["email"], b["password"])
    cl.post("/platform/funnels", headers=hdr(tok_b))
    mine = cl.post("/platform/funnels", headers=hdr(tok_a)).json()["id"]
    listed = cl.get("/platform/funnels", headers=hdr(tok_a)).json()["funnels"]
    assert [f["id"] for f in listed] == [mine]


# ════════════════════════ آلة الحالات · the state machine ════════════════════
def _walk_to(cl, tok, f, smtp, upto: str):
    """امشِ بالقمع إلى حالةٍ معيّنة — returns (funnel_id, study_id, prospect_id)."""
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    sid, did = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
            json={"study_id": sid})
    if upto == "compared":
        return fid, sid, did, None
    cl.post(f"/platform/funnels/{fid}/select", headers=hdr(tok),
            json={"study_id": sid})
    if upto == "selected":
        return fid, sid, did, None
    pid = _prospect(cl, tok, f"walk-{fid}@x.local")
    cl.post(f"/platform/funnels/{fid}/extract", headers=hdr(tok),
            json={"prospect_ids": [pid]})
    if upto == "extracted":
        return fid, sid, did, pid
    cl.post(f"/platform/funnels/{fid}/draft", headers=hdr(tok),
            json={"draft_id": did})
    return fid, sid, did, pid


def test_the_happy_path_walks_all_five_states(monkeypatch):
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-happy@f.local")
    fid, sid, did, pid = _walk_to(cl, tok, f, smtp, "drafted")
    detail = cl.get(f"/platform/funnels/{fid}", headers=hdr(tok)).json()
    assert detail["state"] == "drafted"
    assert detail["selected_study_id"] == sid and detail["draft_id"] == did
    assert detail["prospect_ids"] == [pid]
    r = cl.post(f"/platform/funnels/{fid}/send", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "sent" and r.json()["queued"] == 1
    assert r.json()["sent_at"]


def test_selecting_keeps_the_other_compared_studies(monkeypatch):
    """الاختيار لا يحذف المقارنة — سجلّ «قارَنّا ثلاثاً» هو قيمة القمع."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-keep@f.local")
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    ids = []
    for _ in range(3):
        sid, _d = make_draft_study(f["account_id"], f["user_id"], smtp)
        cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
                json={"study_id": sid})
        ids.append(sid)
    cl.post(f"/platform/funnels/{fid}/select", headers=hdr(tok),
            json={"study_id": ids[1]})
    detail = cl.get(f"/platform/funnels/{fid}", headers=hdr(tok)).json()
    assert detail["selected_study_id"] == ids[1]
    assert sorted(detail["study_ids"]) == sorted(ids), (
        "the comparison record must survive the selection")


def test_cannot_select_a_study_that_was_never_compared(monkeypatch):
    """الفائز يجب أن يكون من دراسات القمع — وإلا الحالة ادعاء."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-outsider@f.local")
    fid, sid, _did, _ = _walk_to(cl, tok, f, smtp, "compared")
    outsider, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    r = cl.post(f"/platform/funnels/{fid}/select", headers=hdr(tok),
                json={"study_id": outsider})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "study_not_in_funnel"
    assert _funnel_state(fid) == "compared"


@pytest.mark.parametrize("step,body", [
    ("select", {"study_id": 1}),
    ("extract", {"prospect_ids": [1]}),
    ("draft", {"draft_id": 1}),
])
def test_steps_are_refused_out_of_order(monkeypatch, step, body):
    """كل خطوة تشترط حالتها السابقة — لا قفز في آلة الحالات."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, f"fn-order-{step}@f.local")
    # القمع في `drafted`: كل الخطوات الثلاث صارت خارج ترتيبها.
    fid, sid, did, pid = _walk_to(cl, tok, f, smtp, "drafted")
    payload = dict(body)
    if "study_id" in payload:
        payload["study_id"] = sid
    if "draft_id" in payload:
        payload["draft_id"] = did
    if "prospect_ids" in payload:
        payload["prospect_ids"] = [pid]
    r = cl.post(f"/platform/funnels/{fid}/{step}", headers=hdr(tok), json=payload)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error"] == "invalid_transition"
    assert _funnel_state(fid) == "drafted"


def test_studies_cannot_be_added_after_selection(monkeypatch):
    """ضمُّ دراسةٍ بعد الاختيار يجعل الاختيار وصفاً لمقارنةٍ لم تحدث."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-late@f.local")
    fid, sid, _did, _ = _walk_to(cl, tok, f, smtp, "selected")
    late, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    r = cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
                json={"study_id": late})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "not_comparing"


def test_attaching_the_same_study_twice_is_refused(monkeypatch):
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-dup@f.local")
    fid, sid, _did, _ = _walk_to(cl, tok, f, smtp, "compared")
    r = cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
                json={"study_id": sid})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "already_attached"


def test_detach_removes_a_compared_study(monkeypatch):
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-detach@f.local")
    fid, sid, _did, _ = _walk_to(cl, tok, f, smtp, "compared")
    r = cl.delete(f"/platform/funnels/{fid}/studies/{sid}", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["study_ids"] == []
    again = cl.delete(f"/platform/funnels/{fid}/studies/{sid}", headers=hdr(tok))
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "not_attached"


def test_extract_requires_at_least_one_prospect(monkeypatch):
    """قمعٌ «استُخرج» بلا عميل واحد حالةٌ بلا معنى وستفشل عند الإرسال."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-empty@f.local")
    fid, sid, _did, _ = _walk_to(cl, tok, f, smtp, "selected")
    r = cl.post(f"/platform/funnels/{fid}/extract", headers=hdr(tok),
                json={"prospect_ids": []})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "no_prospects"
    assert _funnel_state(fid) == "selected"


def test_extract_rejects_a_non_list_payload_with_422(monkeypatch):
    """عيب عميل ⇒ 422 لا 500."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-badpayload@f.local")
    fid, sid, _did, _ = _walk_to(cl, tok, f, smtp, "selected")
    r = cl.post(f"/platform/funnels/{fid}/extract", headers=hdr(tok),
                json={"prospect_ids": "1,2"})
    assert r.status_code == 422


def test_sending_twice_is_refused(monkeypatch):
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-twice@f.local")
    fid, *_ = _walk_to(cl, tok, f, smtp, "drafted")
    assert cl.post(f"/platform/funnels/{fid}/send",
                   headers=hdr(tok)).status_code == 200
    r = cl.post(f"/platform/funnels/{fid}/send", headers=hdr(tok))
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "invalid_transition"


# ═══════════════ `sent` تغيّر الواقع · sending actually queues mail ═══════════
def test_send_actually_queues_one_email_per_prospect(monkeypatch):
    """**الاختبار الحاسم**: `sent` ليست تسمية — البريد يُصفّ فعلاً ثم يخرج.

    لو كان الإرسال تبديل عمودٍ فقط لكان القمع «مُرسَلاً» بلا رسالة واحدة —
    نفس عائلة عيب الأرشفة في PR-4.
    """
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-real@f.local")
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    sid, did = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
            json={"study_id": sid})
    cl.post(f"/platform/funnels/{fid}/select", headers=hdr(tok),
            json={"study_id": sid})
    pids = [_prospect(cl, tok, f"real{i}@x.local") for i in range(3)]
    cl.post(f"/platform/funnels/{fid}/extract", headers=hdr(tok),
            json={"prospect_ids": pids})
    cl.post(f"/platform/funnels/{fid}/draft", headers=hdr(tok),
            json={"draft_id": did})
    r = cl.post(f"/platform/funnels/{fid}/send", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["queued"] == 3
    # البريد مصفوفٌ فعلاً · the rows really exist
    conn = pdb.connect()
    try:
        queued = conn.execute(
            "SELECT COUNT(*) c FROM email_queue WHERE account_id = ? AND "
            "study_id = ? AND status = 'queued'", (f["account_id"], sid)
        ).fetchone()["c"]
    finally:
        conn.close()
    assert queued == 3, "sending a funnel must actually enqueue mail"
    # ويخرج فعلاً عبر العامل الحقيقي · and the real worker delivers it
    from silk_platform import email_queue, wallet
    sent = []
    conn = pdb.connect()
    try:
        email_queue.process_queue(conn, sender=lambda cfg, row: sent.append(row["id"]))
        balance = int(wallet.get_wallet(conn, f["account_id"])["balance"])
    finally:
        conn.close()
    assert len(sent) == 3
    assert balance == 100_000 - 3 * 5, "each delivered email must debit $0.05"


def test_send_is_blocked_when_the_wallet_cannot_cover_it(monkeypatch):
    """بوّابة الرصيد قبل الصفّ — 402 لا 409، وصفر بريد مصفوف."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-poor@f.local", fund_cents=5)
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    sid, did = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
            json={"study_id": sid})
    cl.post(f"/platform/funnels/{fid}/select", headers=hdr(tok),
            json={"study_id": sid})
    pids = [_prospect(cl, tok, f"poor{i}@x.local") for i in range(3)]
    cl.post(f"/platform/funnels/{fid}/extract", headers=hdr(tok),
            json={"prospect_ids": pids})
    cl.post(f"/platform/funnels/{fid}/draft", headers=hdr(tok),
            json={"draft_id": did})
    r = cl.post(f"/platform/funnels/{fid}/send", headers=hdr(tok))
    assert r.status_code == 402, r.text
    d = r.json()["detail"]
    assert d["error"] == "insufficient_balance" and d["projected_cents"] == 15
    assert _funnel_state(fid) == "drafted", "a refused send must not transition"
    conn = pdb.connect()
    try:
        queued = conn.execute("SELECT COUNT(*) c FROM email_queue WHERE "
                              "account_id = ?", (f["account_id"],)).fetchone()["c"]
    finally:
        conn.close()
    assert queued == 0, "a refused send must queue nothing"


def test_send_is_blocked_for_a_delinquent_account(monkeypatch):
    """المديونية تُصرَّح صريحةً وتحجب الإرسال — 402 بـsettle_up_required."""
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-delinq@f.local", fund_cents=0)
    conn = pdb.connect()
    try:   # ادفع الرصيد إلى السالب عبر الدفتر (لا كتابة رصيد خام)
        from silk_platform import wallet
        from silk_platform.models import Operation
        wallet.ensure_wallet(conn, f["account_id"])
        wallet.apply_entry(conn, account_id=f["account_id"],
                           actor_user_id=f["user_id"],
                           operation=Operation.EMAIL_SENT, amount=-50,
                           description="test overdraft", allow_negative=True)
        conn.commit()      # apply_entry لا يلتزم (المُنادي يملك المعاملة)
    finally:
        conn.close()
    fid = cl.post("/platform/funnels", headers=hdr(tok)).json()["id"]
    sid, did = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl.post(f"/platform/funnels/{fid}/studies", headers=hdr(tok),
            json={"study_id": sid})
    cl.post(f"/platform/funnels/{fid}/select", headers=hdr(tok),
            json={"study_id": sid})
    pid = _prospect(cl, tok, "delinq@x.local")
    cl.post(f"/platform/funnels/{fid}/extract", headers=hdr(tok),
            json={"prospect_ids": [pid]})
    cl.post(f"/platform/funnels/{fid}/draft", headers=hdr(tok),
            json={"draft_id": did})
    r = cl.post(f"/platform/funnels/{fid}/send", headers=hdr(tok))
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["error"] == "account_delinquent"
    assert r.json()["detail"]["settle_up_required"] is True


def test_the_foreign_key_protects_a_funnels_linked_draft_and_study(monkeypatch):
    """المرجعان الجديدان (`draft_id`/`selected_study_id`) يحميهما مفتاح أجنبي.

    الترحيل 004 أضافهما كـ`REFERENCES`، و`PRAGMA foreign_keys = ON` مُفعَّل في
    `db.connect` — فحذفُ مسودّةٍ أو دراسةٍ يشير إليها قمعٌ **يفشل في القاعدة**
    بدل أن يترك القمع يشير إلى صفٍّ مفقود. هذه ثابتةٌ يستحقّ أن تُقفَل: بلا
    المفتاح الأجنبي كان القمع سيصير مرجعاً معلّقاً صامتاً.

    (فرعُ `draft_not_found` في `funnels.send` يبقى دفاعاً في العمق — لا نقطة
    HTTP لحذف المسودّات اليوم، فالسيناريو غير قابل للوصول من العميل.)
    """
    import sqlite3
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-fk@f.local")
    fid, sid, did, _pid = _walk_to(cl, tok, f, smtp, "drafted")
    conn = pdb.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM drafts WHERE id = ? AND owner_id = ?",
                         (did, f["account_id"]))
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM studies WHERE id = ? AND owner_id = ?",
                         (sid, f["account_id"]))
        conn.rollback()
    finally:
        conn.close()


# ════════════════════════ الأدوار · roles ═════════════════════════════════════
@pytest.mark.parametrize("role", ["admin", "analyst"])
def test_only_the_factory_drives_a_funnel(monkeypatch, role):
    """القمع مِلكُ المصنع: يجمع عملاءه (PII) ويُرسِل باسمه — أدمِن/محلّل 403."""
    info = seed(monkeypatch)
    cl = client()
    tok = login(cl, info[role]["email"], info[role]["password"])
    assert cl.post("/platform/funnels", headers=hdr(tok)).status_code == 403
    assert cl.get("/platform/funnels", headers=hdr(tok)).status_code == 403


def test_funnel_endpoints_require_authentication(monkeypatch):
    seed(monkeypatch)
    cl = client()
    assert cl.get("/platform/funnels").status_code == 401
    assert cl.post("/platform/funnels").status_code == 401
    assert cl.post("/platform/funnels/1/send").status_code == 401


# ════════════════════════ التدقيق · audit trail ═══════════════════════════════
def test_every_funnel_step_is_audited(monkeypatch):
    f, smtp, cl, tok = _gold_ready(monkeypatch, "fn-audit@f.local")
    fid, *_ = _walk_to(cl, tok, f, smtp, "drafted")
    cl.post(f"/platform/funnels/{fid}/send", headers=hdr(tok))
    from silk_platform import audit
    conn = pdb.connect()
    try:
        rows = audit.search(conn, account_id=f["account_id"], limit=200)
    finally:
        conn.close()
    actions = {r["action"] for r in rows}
    for expected in ("funnel_created", "funnel_study_attached",
                     "funnel_study_selected", "funnel_prospects_extracted",
                     "funnel_draft_attached", "funnel_sent"):
        assert expected in actions, f"missing audit entry: {expected}"
