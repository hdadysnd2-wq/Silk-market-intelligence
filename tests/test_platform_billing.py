"""اختبارات PR-3 — التقرير المدفوع والخصم المقيس الخامل.

هرمتية: قاعدة منصّة معزولة لكل اختبار، بلا شبكة وبلا مفاتيح.

المحور: **لا خصم مزدوج، ولا خصم مقابل لا شيء، ولا رقم مختلَق في التقرير.**
"""
from __future__ import annotations

import pytest

from tests.platform_helpers import (add_active_smtp, client, hdr, login,
                                    make_draft_study, make_factory, seed)


def _study(account_id: int, user_id: int, *, state: str = "in_progress",
           target: int = 5) -> int:
    """دراسة بحالة محدّدة مباشرةً في القاعدة — a study row in a given state."""
    from silk_platform import db as pdb
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO studies (owner_id, title_en, title_ar, state, "
            "target_count, created_by_user_id, created_at, updated_at) "
            "VALUES (?,'Campaign','حملة',?,?,?,?,?)",
            (account_id, state, target, user_id, now, now))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _balance(account_id: int) -> int:
    from silk_platform import db as pdb, wallet
    conn = pdb.connect()
    try:
        w = wallet.get_wallet(conn, account_id)
        return int(w["balance"]) if w else 0
    finally:
        conn.close()


def _report_entries(account_id: int) -> list[dict]:
    from silk_platform import db as pdb
    conn = pdb.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ledger_entries WHERE account_id = ? AND "
            "operation_type = 'report_generated' ORDER BY id", (account_id,))]
    finally:
        conn.close()


# ═══════════════════════ الخصم الأساسي · the basic charge ════════════════════
def test_report_charges_one_dollar_and_posts_exactly_one_ledger_entry(monkeypatch):
    """التقرير يخصم ١٠٠ سنت بقيدٍ واحد بالضبط + لقطة رصيد صحيحة."""
    seed(monkeypatch)
    f = make_factory("gold", "rep-charge@example.com", fund_cents=500)
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok))
    assert r.status_code == 200, r.text
    b = r.json()["billing"]
    assert b["charged"] is True
    assert b["amount_cents"] == 100
    assert b["balance_after"] == 400
    assert _balance(f["account_id"]) == 400
    entries = _report_entries(f["account_id"])
    assert len(entries) == 1
    assert entries[0]["amount"] == -100
    assert entries[0]["balance_after"] == 400
    assert entries[0]["actor_user_id"] == f["user_id"]   # مختوم بالفاعل


def test_second_report_same_day_is_idempotent_and_does_not_charge_again(monkeypatch):
    """نقرةٌ مزدوجة ⇒ نفس القيد، `charged=false`، والرصيد **لا يتغيّر**.

    الدفتر غير قابل للتعديل، فالخصم المزدوج لا يُصحَّح لاحقاً — الوقاية الوحيدة
    عدم كتابته أصلاً.
    """
    seed(monkeypatch)
    f = make_factory("gold", "rep-idem@example.com", fund_cents=500)
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    first = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok)).json()
    second = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok)).json()
    assert first["billing"]["charged"] is True
    assert second["billing"]["charged"] is False
    assert second["billing"]["ledger_entry_id"] == first["billing"]["ledger_entry_id"]
    assert _balance(f["account_id"]) == 400          # خُصِم مرّة واحدة فقط
    assert len(_report_entries(f["account_id"])) == 1


def test_explicit_idempotency_key_allows_a_second_paid_report(monkeypatch):
    """مفتاح صريح ⇒ تقرير مدفوع ثانٍ في نفس اليوم (المفتاح الافتراضي يومي)."""
    seed(monkeypatch)
    f = make_factory("gold", "rep-key@example.com", fund_cents=500)
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok))
    r = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok),
                json={"idempotency_key": "manual-rerun-1"})
    assert r.status_code == 200, r.text
    assert r.json()["billing"]["charged"] is True
    assert _balance(f["account_id"]) == 300          # خُصِم مرّتين بقصد
    assert len(_report_entries(f["account_id"])) == 2


def test_non_string_idempotency_key_is_422(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "rep-badkey@example.com", fund_cents=500)
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok),
                json={"idempotency_key": {"nested": 1}})
    assert r.status_code == 422
    assert _report_entries(f["account_id"]) == []


# ═════════════════════ بوّابات المال · the money gates ═══════════════════════
def test_insufficient_funds_is_402_and_writes_nothing(monkeypatch):
    """رصيد غير كافٍ ⇒ 402 **ولا قيد**: لم يحدث شيء فلا يُسجَّل دَين.

    بخلاف البريد (`allow_negative=True` لأن الرسالة خرجت فعلاً)، التقرير لم
    يُنتَج بعد — فالرفض المسبق هو الصحيح.
    """
    seed(monkeypatch)
    f = make_factory("gold", "rep-poor@example.com", fund_cents=50)   # < 100
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok))
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["error"] == "insufficient_funds"
    assert r.json()["detail"]["price_cents"] == 100
    assert _balance(f["account_id"]) == 50            # لم يُمَس
    assert _report_entries(f["account_id"]) == []


def test_delinquent_account_cannot_buy_a_report(monkeypatch):
    """حساب رصيده سالب ⇒ 402 — نفس قاعدة «لا إطلاق ولا إرسال ما دام سالباً»."""
    seed(monkeypatch)
    f = make_factory("gold", "rep-delinq@example.com")
    from silk_platform import db as pdb, wallet
    from silk_platform.models import Operation
    conn = pdb.connect()
    try:
        wallet.ensure_wallet(conn, f["account_id"])
        # مديونية حقيقية عبر الدفتر (فاتورة تخزين) لا بكتابة رصيد خام.
        wallet.post_entry(conn, account_id=f["account_id"], actor_user_id=None,
                          operation=Operation.STORAGE_CHARGE, amount=-250,
                          description="storage debt", allow_negative=True)
    finally:
        conn.close()
    assert _balance(f["account_id"]) == -250
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok))
    assert r.status_code == 402
    assert r.json()["detail"]["error"] == "delinquent"
    assert _report_entries(f["account_id"]) == []


def test_build_happens_before_charge_so_a_bad_study_never_costs(monkeypatch):
    """دراسة غير موجودة ⇒ 404 بلا أي خصم — لا دفعٌ مقابل لا شيء.

    الترتيب مقصود في `generate_charged_report`: البناء (قراءة خالصة) أوّلاً ثم
    الخصم. لو عُكس لكان العميل قد دفع ثم فشل البناء — والدفتر لا يُردّ.
    """
    seed(monkeypatch)
    f = make_factory("gold", "rep-nostudy@example.com", fund_cents=500)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post("/platform/studies/999999/report",
                   headers=hdr(tok)).status_code == 404
    assert _balance(f["account_id"]) == 500
    assert _report_entries(f["account_id"]) == []


# ═══════════════════ العزل بين المستأجرين · isolation ═══════════════════════
def test_cannot_buy_a_report_for_another_tenants_study(monkeypatch):
    """دراسة حسابٍ آخر ⇒ 404 بلا تسريب وجود + قيد تدقيق + بلا خصم."""
    seed(monkeypatch)
    a = make_factory("gold", "rep-iso-a@example.com", fund_cents=500)
    b = make_factory("gold", "rep-iso-b@example.com", fund_cents=500)
    victim = _study(b["account_id"], b["user_id"])
    cl = client()
    tok = login(cl, a["email"], a["password"])
    r = cl.post(f"/platform/studies/{victim}/report", headers=hdr(tok))
    assert r.status_code == 404
    assert _balance(a["account_id"]) == 500        # لم يُخصَم المهاجم
    assert _balance(b["account_id"]) == 500        # ولا الضحيّة
    assert _report_entries(a["account_id"]) == []
    assert _report_entries(b["account_id"]) == []
    from silk_platform import audit, db as pdb
    conn = pdb.connect()
    try:
        denied = audit.search(conn, account_id=a["account_id"],
                              action="cross_tenant_read")
    finally:
        conn.close()
    assert denied, "cross-tenant report attempt must be audit-logged"


def test_an_idempotency_key_is_scoped_per_account(monkeypatch):
    """نفس المفتاح لحسابين مختلفين يخصم كلًّا منهما — الخمول ليس عالمياً.

    لو كان البحث بلا قيد مالك لكان تقرير حسابٍ يُلغي خصمَ حسابٍ آخر صمتاً.
    """
    seed(monkeypatch)
    a = make_factory("gold", "rep-keyscope-a@example.com", fund_cents=500)
    b = make_factory("gold", "rep-keyscope-b@example.com", fund_cents=500)
    sa = _study(a["account_id"], a["user_id"])
    sb = _study(b["account_id"], b["user_id"])
    cl = client()
    ta = login(cl, a["email"], a["password"])
    tb = login(cl, b["email"], b["password"])
    ka = cl.post(f"/platform/studies/{sa}/report", headers=hdr(ta),
                 json={"idempotency_key": "shared-key"})
    kb = cl.post(f"/platform/studies/{sb}/report", headers=hdr(tb),
                 json={"idempotency_key": "shared-key"})
    assert ka.json()["billing"]["charged"] is True
    assert kb.json()["billing"]["charged"] is True    # لم يُلغِه خمولُ الآخر
    assert _balance(a["account_id"]) == 400
    assert _balance(b["account_id"]) == 400


def test_analyst_and_admin_cannot_buy_a_factory_report(monkeypatch):
    """التقرير مسارُ مصنعٍ فقط — الأدمِن/المحلّل 403 (جدار PII قائم)."""
    info = seed(monkeypatch)
    f = make_factory("gold", "rep-roles@example.com", fund_cents=500)
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    for who in ("admin", "analyst"):
        tok = login(cl, info[who]["email"], info[who]["password"])
        assert cl.post(f"/platform/studies/{sid}/report",
                       headers=hdr(tok)).status_code == 403
    assert _report_entries(f["account_id"]) == []


def test_report_requires_authentication(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "rep-noauth@example.com", fund_cents=500)
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    assert cl.post(f"/platform/studies/{sid}/report").status_code == 401


# ═══════════════════ محتوى التقرير · report content (no fabrication) ═════════
def test_report_numbers_come_from_real_tenant_rows(monkeypatch):
    """عدّادات التقرير مطابقة لصفوف الطابور والموافقة الحقيقية لهذه الدراسة."""
    seed(monkeypatch)
    f = make_factory("gold", "rep-content@example.com", fund_cents=1000)
    smtp = add_active_smtp(f["account_id"])
    sid, _did = make_draft_study(f["account_id"], f["user_id"], smtp,
                                 target_count=3)
    from silk_platform import db as pdb
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        now = now_iso()
        for status in ("sent", "sent", "failed", "suppressed", "queued"):
            conn.execute(
                "INSERT INTO email_queue (account_id, study_id, prospect_email, "
                "status, queued_at) VALUES (?,?,?,?,?)",
                (f["account_id"], sid, f"p-{status}-{now}@x.com", status, now))
        # قيدا موافقة، أحدهما ألغى الاشتراك.
        for email, unsub in (("c1@x.com", None), ("c2@x.com", now)):
            conn.execute(
                "INSERT INTO consent_registry (prospect_email, study_id, "
                "sending_account_id, message_verbatim, sent_at, "
                "consent_granted_at, unsubscribed_at) VALUES (?,?,?,?,?,?,?)",
                (email, sid, f["account_id"], "S\n\nB", now, now, unsub))
        conn.commit()
    finally:
        conn.close()
    cl = client()
    tok = login(cl, f["email"], f["password"])
    out = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok)).json()
    assert out["outreach"]["sent"] == 2
    assert out["outreach"]["failed"] == 1
    assert out["outreach"]["suppressed"] == 1
    assert out["outreach"]["queued"] == 1
    assert out["outreach"]["delivered"] == 2
    assert out["compliance"] == {"consent_records": 2, "unsubscribed": 1}
    # الإنفاق مطابق: رسالتان × ٥ سنتات.
    assert out["spend"]["emails_cents"] == 10
    assert out["study"]["target_count"] == 3
    assert out["study"]["title_en"] == "S"


def test_report_counts_exclude_other_studies_and_other_accounts(monkeypatch):
    """عدّادات دراسةٍ لا تتسرّب من دراسةٍ أخرى ولا من حسابٍ آخر."""
    seed(monkeypatch)
    a = make_factory("gold", "rep-leak-a@example.com", fund_cents=500)
    b = make_factory("gold", "rep-leak-b@example.com", fund_cents=500)
    mine = _study(a["account_id"], a["user_id"])
    other_study = _study(a["account_id"], a["user_id"])
    theirs = _study(b["account_id"], b["user_id"])
    from silk_platform import db as pdb
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        now = now_iso()
        rows = [(a["account_id"], mine, 1), (a["account_id"], other_study, 4),
                (b["account_id"], theirs, 7)]
        for acct, study, n in rows:
            for i in range(n):
                conn.execute(
                    "INSERT INTO email_queue (account_id, study_id, "
                    "prospect_email, status, queued_at) VALUES (?,?,?, 'sent',?)",
                    (acct, study, f"x{acct}-{study}-{i}@x.com", now))
        conn.commit()
    finally:
        conn.close()
    cl = client()
    tok = login(cl, a["email"], a["password"])
    out = cl.post(f"/platform/studies/{mine}/report", headers=hdr(tok)).json()
    assert out["outreach"]["sent"] == 1, "counts leaked across study/account"


def test_response_count_is_declared_as_stored_not_as_a_metric(monkeypatch):
    """عدد الردود يُعرَض «كما هو مخزَّن» بمصدرٍ معلَن — لا رقم محسوب مختلَق.

    لا مسار يكتب `response_count` بعد، فعرضُه كمقياسٍ محسوب كان سيكون اختلاقاً.
    عقد عدم الاختلاق نفسه المطبَّق في المحرّك.
    """
    seed(monkeypatch)
    f = make_factory("gold", "rep-prov@example.com", fund_cents=500)
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    out = cl.post(f"/platform/studies/{sid}/report", headers=hdr(tok)).json()
    assert out["response_count_as_stored"] == 0
    prov = out["provenance"]
    assert "not a computed metric" in prov["response_count"]
    # كل قسم رقمي يحمل مصدره · every numeric section names its source.
    for k in ("outreach", "compliance", "emails_cents"):
        assert prov.get(k), f"missing provenance for {k}"


# ═══════════════════ الطبقة المستخلَصة · the billing primitive ═══════════════
def test_charge_metered_rejects_a_non_positive_amount(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "bill-zero@example.com", fund_cents=500)
    from silk_platform import billing, db as pdb
    from silk_platform.models import Operation
    conn = pdb.connect()
    try:
        for bad in (0, -100):
            with pytest.raises(billing.BillingError):
                billing.charge_metered(
                    conn, account_id=f["account_id"], actor_user_id=f["user_id"],
                    operation=Operation.REPORT_GENERATED, amount_cents=bad,
                    key="k")
    finally:
        conn.close()


def test_charge_key_is_recoverable_from_the_ledger(monkeypatch):
    """المفتاح مخزَّن في وصف القيد فيُستعلَم بلا جدول جديد (نمط jobs.already_billed)."""
    seed(monkeypatch)
    f = make_factory("gold", "bill-key@example.com", fund_cents=500)
    from silk_platform import billing, db as pdb
    from silk_platform.models import Operation
    conn = pdb.connect()
    try:
        assert billing.already_charged(conn, f["account_id"],
                                       Operation.REPORT_GENERATED, "k1") is None
        billing.charge_metered(conn, account_id=f["account_id"],
                               actor_user_id=f["user_id"],
                               operation=Operation.REPORT_GENERATED,
                               amount_cents=100, key="k1")
        found = billing.already_charged(conn, f["account_id"],
                                        Operation.REPORT_GENERATED, "k1")
        assert found is not None and found["amount"] == -100
        # مفتاح آخر لم يُخصَم · a different key is still unbilled.
        assert billing.already_charged(conn, f["account_id"],
                                       Operation.REPORT_GENERATED, "k2") is None
    finally:
        conn.close()
