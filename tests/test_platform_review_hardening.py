"""أقفال مراجعة الشيفرة — regression locks for the 27 code-review findings.

كل اختبار هنا يقفل إصلاحاً بعينه: الحارس يصعد عبر المدخل الحقيقي، إطلاق ذرّي،
رمز أحادي فعلاً، خنق الدخول، عدم تكرار الإرسال/الخصم/الفوترة، وتحويل أخطاء
المدخلات إلى 4xx بدل 500. Each test locks one review fix.
"""
import os
import sqlite3
import threading

import pytest

from platform_helpers import (add_active_smtp, client, hdr, login, make_draft_study,
                              make_factory, seed, setup_env)
from silk_platform import db as pdb, email_queue, jobs, passwords, quota, settings, wallet
from silk_platform.models import Operation


# ── ١) حارس الإقلاع يصعد عبر api.py الجذر · boot guard propagates ────────────
def test_boot_guard_not_swallowed_by_root_app(monkeypatch):
    """إشارة إنتاج بلا سرّ ⇒ create_app() يرفض الإقلاع (لا تحذير صامت).

    يُستورَد `api` أولاً بتهيئة سليمة (استيراده ينشئ `app` على المستوى الأعلى)،
    ثم تُفسَد التهيئة ويُنادى المصنع مباشرةً. Import first, then misconfigure.
    """
    setup_env(monkeypatch)          # يضبط SILK_PLATFORM_SECRET
    import api as root_api
    monkeypatch.setenv("SILK_PLATFORM_SECURE_COOKIES", "1")
    monkeypatch.delenv("SILK_PLATFORM_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SILK_PLATFORM_SECRET"):
        root_api.create_app()


def test_root_app_boots_and_mounts_platform_when_configured(monkeypatch):
    """مع السرّ مضبوطاً: الإقلاع ينجح و/platform مُركَّبة فعلاً (لا 404 صامت)."""
    setup_env(monkeypatch)          # يضبط SILK_PLATFORM_SECRET
    monkeypatch.setenv("SILK_PLATFORM_SECURE_COOKIES", "1")
    monkeypatch.setenv("SILK_PLATFORM_BCRYPT_ROUNDS", "12")   # محاكاة إنتاج كاملة
    import api as root_api
    app = root_api.create_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/platform/auth/login" in paths


# ── ١٧) /health يكشف قاعدة المنصّة · platform DB visible in /health ──────────
def test_health_exposes_platform_db_path(monkeypatch):
    setup_env(monkeypatch)
    import api as root_api
    assert root_api._platform_db_path().endswith("platform.db")


# ── ٢) الإطلاق ذرّي ومعوَّض · atomic launch + quota compensation ─────────────
def test_launch_claim_is_atomic_under_threads(monkeypatch):
    """المطالبة المحروسة بالحالة: خيوط متزامنة ⇒ رابح واحد بالضبط."""
    seed(monkeypatch)
    f = make_factory("gold", "atomic-launch@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=1)
    barrier = threading.Barrier(8)
    wins: list[int] = []
    lock = threading.Lock()

    def claim():
        conn = pdb.connect()
        try:
            barrier.wait(timeout=10)
            cur = conn.execute(
                "UPDATE studies SET state = 'in_progress', launched_at = ?, "
                "updated_at = ? WHERE id = ? AND owner_id = ? AND state = 'draft'",
                ("t", "t", sid, f["account_id"]))
            conn.commit()
            if cur.rowcount:
                with lock:
                    wins.append(1)
        finally:
            conn.close()

    threads = [threading.Thread(target=claim) for _ in range(8)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    assert sum(wins) == 1   # exactly one launcher wins the transition


def test_quota_denied_launch_reverts_study_to_draft(monkeypatch):
    """رفض الحصّة يُعيد الدراسة مسودّة — no quota burned with nothing launched."""
    seed(monkeypatch)
    f = make_factory("basic", "revert@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    s1, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=1)
    s2, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=1)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{s1}/launch", headers=hdr(tok), json={}
                   ).status_code == 200
    r2 = cl.post(f"/platform/studies/{s2}/launch", headers=hdr(tok), json={})
    assert r2.status_code == 403
    conn = pdb.connect()
    try:
        row = conn.execute("SELECT state, launched_at FROM studies WHERE id = ?",
                           (s2,)).fetchone()
        used = conn.execute("SELECT lifetime_study_count FROM accounts WHERE id = ?",
                            (f["account_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert row["state"] == "draft" and row["launched_at"] is None
    assert used == 1   # the denied launch did not consume a second slot


def test_relaunch_of_launched_study_is_409_and_quota_counted_once(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "relaunch@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=1)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={}
                   ).status_code == 200
    assert cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={}
                   ).status_code == 409
    conn = pdb.connect()
    try:
        used = conn.execute("SELECT current_month_study_count FROM accounts "
                            "WHERE id = ?", (f["account_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert used == 1


# ── ٣) الرمز أحادي الاستخدام ذرّياً · atomic single-use reset ────────────────
def test_reset_token_single_use_under_threads(monkeypatch):
    """مطالبتان متزامنتان بنفس الرمز ⇒ واحدة تنجح فقط."""
    info = seed(monkeypatch)
    conn = pdb.connect()
    try:
        from silk_platform import auth
        raw = auth.issue_reset_token(conn, info["factory_a"]["email"])
    finally:
        conn.close()
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def confirm(pw):
        from silk_platform import auth as a
        c = pdb.connect()
        try:
            barrier.wait(timeout=10)
            ok = a.consume_reset_token(c, raw, pw)
            with lock:
                results.append(ok)
        except sqlite3.DatabaseError:
            with lock:
                results.append(False)
        finally:
            c.close()

    t1 = threading.Thread(target=confirm, args=("Winner123",))
    t2 = threading.Thread(target=confirm, args=("Loser1234",))
    t1.start(); t2.start(); t1.join(timeout=30); t2.join(timeout=30)
    assert results.count(True) == 1   # single-use holds under a race


# ── ٤) خنق محاولات الدخول · login throttle ──────────────────────────────────
def test_login_throttled_after_repeated_failures(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    email = info["admin"]["email"]
    codes = [cl.post("/platform/auth/login",
                     json={"email": email, "password": "WrongPass9"}).status_code
             for _ in range(12)]
    assert 401 in codes and 429 in codes          # brute force gets locked out
    # والرمز الصحيح محجوب أيضاً أثناء القفل (لا تجاوز بالنجاح).
    assert cl.post("/platform/auth/login",
                   json={"email": email, "password": info["admin"]["password"]}
                   ).status_code == 429


# ── ٥) لا إرسال/خصم مزدوج · claimed rows are not re-sent ────────────────────
def test_claimed_row_is_skipped_by_a_second_pass(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "claim@f.local", fund_cents=10_000)
    smtp = add_active_smtp(f["account_id"])
    sid, did = make_draft_study(f["account_id"], f["user_id"], smtp)
    conn = pdb.connect()
    try:
        draft = dict(conn.execute("SELECT * FROM drafts WHERE id = ?",
                                  (did,)).fetchone())
        eid = email_queue.enqueue(
            conn, account_id=f["account_id"], study_id=sid,
            prospect={"id": 1, "email": "p@x.local", "first_name": "P",
                      "language_preference": "en"},
            draft=draft, smtp_config_id=None, actor_user_id=f["user_id"])
        # مرورٌ آخر طالب بالصفّ سلفاً (status='sending') ⇒ يُتخطّى بلا إرسال.
        conn.execute("UPDATE email_queue SET status = 'sending' WHERE id = ?", (eid,))
        conn.commit()
        sent = []
        res = email_queue.process_queue(conn, sender=lambda c, e: sent.append(e["id"]))
        assert sent == [] and res["sent"] == []
        assert conn.execute("SELECT COUNT(*) c FROM ledger_entries WHERE "
                            "operation_type='email_sent'").fetchone()["c"] == 0
    finally:
        conn.close()


# ── ٦) بريد خرج ⇒ موافقة محفوظة وخصم مقيَّد · consent survives a low balance ─
def test_sent_email_keeps_consent_even_if_balance_drains_mid_send(monkeypatch):
    """رصيد يُستنزَف أثناء الإرسال لا يمحو أثر رسالة أُرسِلت فعلاً."""
    seed(monkeypatch)
    from silk_platform.models import PRICE_EMAIL_SENT_CENTS
    f = make_factory("gold", "drain@f.local", fund_cents=PRICE_EMAIL_SENT_CENTS)
    smtp = add_active_smtp(f["account_id"])
    sid, did = make_draft_study(f["account_id"], f["user_id"], smtp)
    conn = pdb.connect()
    try:
        draft = dict(conn.execute("SELECT * FROM drafts WHERE id = ?",
                                  (did,)).fetchone())
        email_queue.enqueue(
            conn, account_id=f["account_id"], study_id=sid,
            prospect={"id": 1, "email": "p@x.local", "first_name": "P",
                      "language_preference": "en"},
            draft=draft, smtp_config_id=None, actor_user_id=f["user_id"])

        def draining_sender(cfg, email):
            """يحاكي فوترة تخزين متزامنة تستنزف الرصيد بعد خروج البريد."""
            c2 = pdb.connect()
            try:
                wallet.post_entry(c2, account_id=f["account_id"], actor_user_id=None,
                                  operation=Operation.STORAGE_CHARGE, amount=-1000,
                                  description="concurrent storage charge",
                                  allow_negative=True)
            finally:
                c2.close()

        res = email_queue.process_queue(conn, sender=draining_sender)
        assert len(res["sent"]) == 1 and res["failed"] == []
        # سجلّ الموافقة موجود (امتثال) والخصم مقيَّد (لا إيراد مفقود).
        assert conn.execute("SELECT COUNT(*) c FROM consent_registry"
                            ).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) c FROM ledger_entries WHERE "
                            "operation_type='email_sent'").fetchone()["c"] == 1
    finally:
        conn.close()


# ── ٨) لا اقتطاع صامت عند ٧٢ بايت · no bcrypt truncation ────────────────────
def test_long_passwords_are_not_truncated_at_72_bytes():
    base = "A1" + ("x" * 80)
    h = passwords.hash_password(base)
    assert passwords.verify_password(base, h)
    assert not passwords.verify_password(base[:72] + "DIFFERENT", h)
    assert not passwords.verify_password(base + "TAIL", h)


# ── ١١) التصفير الشهري خامل التكرار · idempotent monthly reset ──────────────
def test_monthly_reset_is_idempotent_within_a_month(monkeypatch):
    """التصفير يعمل عند دخول شهر جديد، ونداؤه المكرَّر في نفس الشهر لا يمنح حصّة."""
    seed(monkeypatch)
    f = make_factory("silver", "idem@f.local", fund_cents=0)
    conn = pdb.connect()

    def used() -> int:
        return conn.execute("SELECT current_month_study_count FROM accounts "
                            "WHERE id = ?", (f["account_id"],)).fetchone()[0]

    try:
        quota.reserve_launch(conn, f["account_id"], actor_user_id=f["user_id"])
        quota.reserve_launch(conn, f["account_id"], actor_user_id=f["user_id"])
        assert used() == 2 and not quota.reserve_launch(
            conn, f["account_id"], actor_user_id=f["user_id"]).allowed

        # (أ) إطلاقٌ في منتصف الشهر لا يرفع حصّة حسابٍ فترتُه الحالية مضبوطة —
        # هذا جوهر الخمول: العدّاد لا يعود صفراً ولا تُمنَح محاولة إضافية.
        quota.monthly_reset(conn)
        assert used() == 2
        assert not quota.reserve_launch(conn, f["account_id"],
                                        actor_user_id=f["user_id"]).allowed
        # ونداء تالٍ لا يمسّ أيّ حساب (كلّها صارت على الفترة الحالية).
        assert quota.monthly_reset(conn) == 0

        # (ب) دخول شهر جديد (فترة مخزَّنة قديمة): التصفير الحقيقي يعمل مرّة.
        conn.execute("UPDATE accounts SET quota_period = '2000-01' WHERE id = ?",
                     (f["account_id"],))
        conn.commit()
        assert quota.monthly_reset(conn) >= 1
        assert used() == 0
        assert quota.monthly_reset(conn) == 0   # وتكرارها بعدها خامل · then idle
        assert used() == 0
    finally:
        conn.close()


# ── ١٨) فوترة التخزين خاملة التكرار · idempotent storage billing ────────────
def test_storage_billing_does_not_double_charge_on_retry(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "storage@f.local", fund_cents=100_000)
    conn = pdb.connect()
    try:
        conn.execute(
            "INSERT INTO images (owner_id, filename, storage_key, mime_type, "
            "size_bytes, uploaded_at) VALUES (?,?,?,?,?,?)",
            (f["account_id"], "a.png", f"{f['account_id']}/a.png", "image/png",
             2 * 1024 ** 3, "2026-07-01T00:00:00Z"))
        conn.commit()
        first = jobs.run_storage_billing(conn)
        second = jobs.run_storage_billing(conn)   # إعادة تشغيل بعد تعطّل
        assert len(first) == 1 and second == []
        assert conn.execute("SELECT COUNT(*) c FROM ledger_entries WHERE "
                            "operation_type='storage_charge'").fetchone()["c"] == 1
    finally:
        conn.close()


# ── ١٢/١٣) أخطاء المدخلات 4xx لا 500 · client faults never 500 ──────────────
def test_input_validation_returns_422_not_500(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "valid@f.local", fund_cents=1000)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    # SMTP بلا حقول NOT NULL.
    assert cl.post("/platform/smtp-configs", headers=hdr(tok), json={}
                   ).status_code == 422
    # target_count غير رقمي عند الإنشاء والتعديل.
    assert cl.post("/platform/studies", headers=hdr(tok),
                   json={"title_en": "x", "target_count": "ten"}).status_code == 422
    study = cl.post("/platform/studies", headers=hdr(tok),
                    json={"title_en": "ok"}).json()
    assert cl.patch(f"/platform/studies/{study['id']}", headers=hdr(tok),
                    json={"target_count": None}).status_code == 422
    # smtp_config_id = 0 (قيمة زائفة) تُرفَض بدل أن تصل مفتاحاً أجنبياً.
    assert cl.patch(f"/platform/studies/{study['id']}", headers=hdr(tok),
                    json={"smtp_config_id": 0}).status_code == 422
    # امتداد صورة خارج القائمة البيضاء (PR-8: الحجم يُقاس لا يُصرَّح به العميل).
    assert cl.post("/platform/images", headers=hdr(tok),
                   files={"file": ("a.exe", b"MZ-fake", "application/octet-stream")}
                   ).status_code == 422


def test_admin_fund_rejects_non_integer_and_float_amounts(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    tok = login(cl, info["admin"]["email"], info["admin"]["password"])
    fa = info["factory_a"]["account_id"]
    assert cl.post("/platform/admin/fund", headers=hdr(tok),
                   json={"account_id": fa, "amount_cents": "1,000"}).status_code == 422
    # عائم يُرفَض بدل الاقتطاع الصامت إلى 250.
    assert cl.post("/platform/admin/fund", headers=hdr(tok),
                   json={"account_id": fa, "amount_cents": 250.75}).status_code == 422


# ── ١٤) تحديث الصور يعمل · image update path works ──────────────────────────
def test_image_update_does_not_reference_missing_updated_at(monkeypatch):
    seed(monkeypatch)
    from silk_platform import repository
    f = make_factory("gold", "img@f.local")
    conn = pdb.connect()
    try:
        repo = repository.images(conn)
        row = repo.create(f["account_id"], {"filename": "a.png",
                                            "storage_key": f"{f['account_id']}/a.png",
                                            "size_bytes": 10})
        updated = repo.update(f["account_id"], row["id"], {"alt_text_en": "cat"})
        assert updated is not None and updated["alt_text_en"] == "cat"
    finally:
        conn.close()


# ── ٢٢) القمع ليس قابلاً للكتابة عبر CRUD العامّ · funnel not generic-writable ─
def test_comparison_funnels_not_exposed_through_generic_repository():
    from silk_platform import repository
    assert "comparison_funnels" not in repository._WRITABLE
    assert not hasattr(repository, "funnels")


# ── ٧) البذر لا يترك خزنة يتيمة · seed leaves no orphan vault ────────────────
def test_seed_policy_failure_leaves_no_orphan_vault(monkeypatch):
    setup_env(monkeypatch)
    monkeypatch.setenv("SILK_SEED_ADMIN_PASSWORD", "changeme")   # يخالف السياسة
    from silk_platform import seed as pseed
    from silk_platform.passwords import PasswordError
    conn = pdb.connect()
    try:
        with pytest.raises(PasswordError):
            pseed.seed(conn)
        # لا حساب خزنة ملتزَم ⇒ البذر التالي (بكلمة صحيحة) ينجح.
        assert conn.execute("SELECT COUNT(*) c FROM accounts WHERE is_vault = 1"
                            ).fetchone()["c"] == 0
        monkeypatch.setenv("SILK_SEED_ADMIN_PASSWORD", "GoodPass123")
        assert pseed.seed(conn)["seeded"] is True
    finally:
        conn.close()


# ── ٤ب) لا كلمات مرور افتراضية في الشيفرة · no shipped default passwords ────
def test_seed_generates_random_passwords_when_env_unset(monkeypatch):
    setup_env(monkeypatch)
    for k in ("ADMIN", "ANALYST", "FACTORY_A", "FACTORY_B"):
        monkeypatch.delenv(f"SILK_SEED_{k}_PASSWORD", raising=False)
    from silk_platform import seed as pseed
    conn = pdb.connect()
    try:
        info = pseed.seed(conn)
    finally:
        conn.close()
    pw = info["admin"]["password"]
    # لا كلمة مرور افتراضية ثابتة: كل تشغيل يولّد كلمةً مطابقةً للسياسة.
    assert pw not in ("Admin1234", "Analyst1234", "Factory1234")
    assert len(pw) >= 16
    passwords.validate_policy(pw)          # المولَّدة تجتاز السياسة حتماً
    assert info["analyst"]["password"] != pw   # كلمة لكل هويّة · distinct per identity
    # وتعمل فعلاً للدخول (المُرجَع هو ما جُزِّئ) · the returned password authenticates.
    cl = client()
    assert cl.post("/platform/auth/login",
                   json={"email": info["admin"]["email"], "password": pw}
                   ).status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# بلاغ المالك: ضوابط الرصيد السالب + خنق يعبر العمليات
# Owner review: negative-balance controls + cross-process throttle
# ══════════════════════════════════════════════════════════════════════════════

def test_negative_balance_blocks_new_study_launch(monkeypatch):
    """حساب مدين لا يُطلق دراسة جديدة حتى يُسدَّد — explicit delinquency gate."""
    seed(monkeypatch)
    f = make_factory("gold", "delinq@f.local", fund_cents=100)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=1)
    conn = pdb.connect()
    try:  # فاتورة تخزين تدفع الرصيد إلى السالب · storage bill pushes it negative
        wallet.post_entry(conn, account_id=f["account_id"], actor_user_id=None,
                          operation=Operation.STORAGE_CHARGE, amount=-500,
                          description="storage", allow_negative=True)
        assert wallet.is_delinquent(conn, f["account_id"])
    finally:
        conn.close()
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={})
    assert r.status_code == 402
    assert r.json()["detail"]["error"] == "account_delinquent"
    assert r.json()["detail"]["settle_up_required"] is True
    # والمديونية مرئية للعميل في محفظته · surfaced on the wallet endpoint.
    w = cl.get("/platform/wallet", headers=hdr(tok)).json()
    assert w["delinquent"] is True and w["balance"] < 0


def test_settling_up_restores_launch_ability(monkeypatch):
    """التمويل يفكّ الحجب — admin funding clears delinquency and unblocks launch."""
    info = seed(monkeypatch)
    f = make_factory("gold", "settle@f.local", fund_cents=100)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=1)
    conn = pdb.connect()
    try:
        wallet.post_entry(conn, account_id=f["account_id"], actor_user_id=None,
                          operation=Operation.STORAGE_CHARGE, amount=-500,
                          description="storage", allow_negative=True)
    finally:
        conn.close()
    cl = client()
    admin = login(cl, info["admin"]["email"], info["admin"]["password"])
    assert cl.post("/platform/admin/fund", headers=hdr(admin),
                   json={"account_id": f["account_id"], "amount_cents": 5000}
                   ).status_code == 200
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={}
                   ).status_code == 200


def test_overdraft_is_bounded_by_a_floor(monkeypatch):
    """المديونية مسقوفة: خصم يتجاوز السقف يُرفَض بدل الانزلاق بلا حدّ."""
    seed(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_MAX_OVERDRAFT_CENTS", "1000")
    f = make_factory("gold", "floor@f.local", fund_cents=0)
    conn = pdb.connect()
    try:
        # داخل السقف · within the floor: allowed.
        wallet.post_entry(conn, account_id=f["account_id"], actor_user_id=None,
                          operation=Operation.STORAGE_CHARGE, amount=-900,
                          description="storage", allow_negative=True)
        assert wallet.get_wallet(conn, f["account_id"])["balance"] == -900
        # يتجاوز السقف · beyond the floor: refused, and nothing is written.
        with pytest.raises(wallet.InsufficientFunds):
            wallet.post_entry(conn, account_id=f["account_id"], actor_user_id=None,
                              operation=Operation.STORAGE_CHARGE, amount=-5000,
                              description="huge storage", allow_negative=True)
        assert wallet.get_wallet(conn, f["account_id"])["balance"] == -900
    finally:
        conn.close()


def test_negative_balance_stops_further_sends(monkeypatch):
    """الإرسال يتوقّف أيضاً عند المديونية — no sends while overdrawn."""
    seed(monkeypatch)
    f = make_factory("gold", "nosend@f.local", fund_cents=0)
    smtp = add_active_smtp(f["account_id"])
    sid, did = make_draft_study(f["account_id"], f["user_id"], smtp)
    conn = pdb.connect()
    try:
        wallet.post_entry(conn, account_id=f["account_id"], actor_user_id=None,
                          operation=Operation.STORAGE_CHARGE, amount=-100,
                          description="storage", allow_negative=True)
        draft = dict(conn.execute("SELECT * FROM drafts WHERE id = ?",
                                  (did,)).fetchone())
        email_queue.enqueue(
            conn, account_id=f["account_id"], study_id=sid,
            prospect={"id": 1, "email": "p@x.local", "first_name": "P",
                      "language_preference": "en"},
            draft=draft, smtp_config_id=None, actor_user_id=f["user_id"])
        sent = []
        res = email_queue.process_queue(conn, sender=lambda c, e: sent.append(e))
        assert sent == [] and res["sent"] == [] and len(res["failed"]) == 1
    finally:
        conn.close()


def test_login_throttle_is_shared_across_processes(monkeypatch):
    """الخنق في القاعدة: محاولات عمليةٍ تُخنِق الأخرى — one real cap, N workers.

    يحاكي عاملَين مستقلَّين باتصالين منفصلين (لا حالة ذاكرة مشتركة بينهما):
    فشل مسجَّل عبر الاتصال الأوّل يجب أن يراه الثاني فوراً.
    """
    setup_env(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_LOGIN_MAX_FAILURES", "3")
    from silk_platform import throttle
    ident = throttle.identity("victim@f.local", "1.2.3.4")
    worker_a, worker_b = pdb.connect(), pdb.connect()
    try:
        for _ in range(3):                       # كل الفشل عبر «العامل أ»
            throttle.record_failure(worker_a, ident)
        # «العامل ب» — عمليةٌ أخرى بلا أي حالة ذاكرة مشتركة — يرى الخنق.
        assert throttle.is_throttled(worker_b, ident) is True
        throttle.clear(worker_b, ident)          # ونجاحٌ في ب يُصفّر لـأ أيضاً
        assert throttle.is_throttled(worker_a, ident) is False
    finally:
        worker_a.close(); worker_b.close()


def test_login_throttle_survives_process_restart(monkeypatch):
    """الخنق يصمد لإعادة النشر — durable across a simulated restart."""
    setup_env(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_LOGIN_MAX_FAILURES", "2")
    from silk_platform import throttle
    ident = throttle.identity("victim2@f.local", "9.9.9.9")
    c1 = pdb.connect()
    try:
        throttle.record_failure(c1, ident)
        throttle.record_failure(c1, ident)
    finally:
        c1.close()                                # «إعادة تشغيل» · restart
    c2 = pdb.connect()
    try:
        assert throttle.is_throttled(c2, ident) is True
    finally:
        c2.close()
