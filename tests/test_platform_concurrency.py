"""اختبارات التزامن الحقيقية — real threaded concurrency proofs.

بخلاف الاختبارات المتسلسلة، هذي تُطلق خيوطاً متزامنة تضرب **نفس الحساب** عبر
حاجز بدء موحّد (Barrier) كي تتصادم فعلاً، فتُثبِت أن الأقفال الذرّية (UPDATE
محروس + BEGIN IMMEDIATE + busy_timeout) تمنع تجاوز السقف وفقدان التحديثات.
Each worker uses its OWN sqlite connection (connections aren't thread-shareable).
"""
import threading

from platform_helpers import make_factory, seed
from silk_platform import (billing, db as pdb, email_queue, entitlements,
                           lifecycle, quota, users, wallet)
from silk_platform.models import Operation


def _run_concurrent(n, fn):
    """شغّل fn(i) في n خيطاً تبدأ معاً عبر حاجز — maximize contention."""
    barrier = threading.Barrier(n)
    results = [None] * n
    errors = [None] * n

    def worker(i):
        try:
            barrier.wait()
            results[i] = fn(i)
        except Exception as exc:  # noqa: BLE001
            errors[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(e is None for e in errors), f"worker errors: {[e for e in errors if e]}"
    return results


def test_concurrent_launches_never_exceed_quota_cap(monkeypatch):
    """١٢ إطلاقاً متزامناً على حساب Silver (سقف ٢) ⇒ ينجح ٢ بالضبط.

    يقفل خلل TOCTOU الحقيقي: بلا الزيادة الذرّية المحروسة + التصفير المشروط،
    كانت خيوط متزامنة قد تتجاوز السقف أو تصفّر بعضها. This is genuinely
    concurrent (Barrier-synchronized), not sequential.
    """
    seed(monkeypatch)
    f = make_factory("silver", "conc-quota@f.local")  # monthly cap = 2
    N = 12

    def attempt(_i):
        conn = pdb.connect()
        try:
            return quota.reserve_launch(conn, f["account_id"],
                                        actor_user_id=f["user_id"]).allowed
        finally:
            conn.close()

    results = _run_concurrent(N, attempt)
    assert sum(1 for r in results if r) == 2   # exactly the cap, never more
    conn = pdb.connect()
    try:
        cnt = conn.execute("SELECT current_month_study_count FROM accounts "
                           "WHERE id = ?", (f["account_id"],)).fetchone()[0]
    finally:
        conn.close()
    assert cnt == 2


def test_concurrent_basic_lifetime_cap_is_one(monkeypatch):
    """١٠ إطلاقات متزامنة على Basic (مدى الحياة ١) ⇒ ينجح واحد فقط."""
    seed(monkeypatch)
    f = make_factory("basic", "conc-basic@f.local")
    N = 10

    def attempt(_i):
        conn = pdb.connect()
        try:
            return quota.reserve_launch(conn, f["account_id"],
                                        actor_user_id=f["user_id"]).allowed
        finally:
            conn.close()

    assert sum(1 for r in _run_concurrent(N, attempt) if r) == 1


def test_concurrent_funding_no_lost_updates(monkeypatch):
    """٨ تمويلات متزامنة على نفس المصنع ⇒ الرصيد = مجموعها بالضبط (لا فقدان).

    بلا BEGIN IMMEDIATE كانت القراءة-التعديل-الكتابة المتزامنة تفقد تحديثات
    فيقلّ الرصيد عن N×المبلغ. This proves fund_wallet serializes correctly.
    """
    info = seed(monkeypatch)
    vault = info["vault_account_id"]
    fa = info["factory_a"]["account_id"]
    N, amount = 8, 100

    def fund(_i):
        conn = pdb.connect()
        try:
            wallet.fund_wallet(conn, admin_user_id=info["admin"]["id"],
                               factory_account_id=fa, amount_cents=amount,
                               vault_account_id=vault)
            return True
        finally:
            conn.close()

    _run_concurrent(N, fund)
    conn = pdb.connect()
    try:
        w = wallet.get_wallet(conn, fa)
        credits = conn.execute(
            "SELECT COUNT(*) c FROM ledger_entries WHERE account_id = ? AND "
            "operation_type = 'wallet_funded' AND amount = ?", (fa, amount)
        ).fetchone()["c"]
        # لقطات balance_after فريدة ومتّسقة (لا تحديث ضائع) · distinct snapshots.
        snaps = [r["balance_after"] for r in conn.execute(
            "SELECT balance_after FROM ledger_entries WHERE account_id = ? AND "
            "operation_type = 'wallet_funded' ORDER BY balance_after", (fa,)).fetchall()]
    finally:
        conn.close()
    assert w["balance"] == N * amount            # no lost updates
    assert w["lifetime_funded"] == N * amount
    assert credits == N
    assert snaps == sorted(set(snaps))           # every snapshot unique & ordered


def _widen_seat_check_window(monkeypatch, delay_s=0.15):
    """وسّع نافذة فحص المقعد صناعياً — make the check-then-write window observable.

    **لماذا هذا ضروري:** حاجزُ البدء وحده لا يُثبِت ذرّية هنا. عملُ المقعد أقصر
    من ميلي ثانية، فخيطان يتسلسلان فعلياً بحكم GIL وينتهي كلٌّ قبل أن يبدأ
    الآخر — فيجتاز الاختبارَ **كودٌ غير ذرّي** ولا يُثبِت شيئاً (وهذا ما حدث
    بالضبط: نسخةٌ بلا `BEGIN IMMEDIATE` اجتازت اختباراً بحاجز فقط).

    بتأخير الفحص، يصير التشابك مضموناً: بلا قفلٍ فوري يقرأ الخيطان «مقعد متاح»
    كلاهما فيتجاوزان السقف؛ ومع القفل الفوري يُحتجَز الثاني حتى يلتزم الأوّل
    فيرى المقعد مأخوذاً. فحصُ الفرق هو ما يجعل هذا الاختبار حارساً حقيقياً.

    Without a widened window the GIL serializes such short critical sections and
    a NON-atomic implementation passes — an empty green. The delay makes the
    interleaving deterministic, so the lock is what the assertion measures.
    """
    import time
    real = entitlements.seats_used

    def slow_seats_used(conn, account_id):
        n = real(conn, account_id)
        time.sleep(delay_s)          # النافذة التي يجب أن يُغلقها القفل
        return n

    monkeypatch.setattr(entitlements, "seats_used", slow_seats_used)


def test_concurrent_sub_user_creates_never_exceed_seat_cap(monkeypatch):
    """إنشاءان متزامنان على مقعد واحد متبقٍّ ⇒ ينجح واحد فقط.

    المقعد مالٌ (الطبقة مدفوعة)، فتجاوزه تجاوزُ فوترة. نافذة الفحص مُوسَّعة كي
    يكون القفل الفوري هو ما يُقاس فعلاً لا ترتيبُ GIL العابر.
    """
    seed(monkeypatch)
    f = make_factory("silver", "conc-seat@f.local")     # seats = 3
    conn = pdb.connect()
    try:
        users.create_sub_user(conn, f["account_id"], email="seat-filler@f.local",
                              password="Racer1234")     # نشط ٢ ⇒ مقعد واحد
        assert entitlements.seats_used(conn, f["account_id"]) == 2
    finally:
        conn.close()

    _widen_seat_check_window(monkeypatch)

    def attempt(i):
        conn = pdb.connect()
        try:
            users.create_sub_user(conn, f["account_id"],
                                  email=f"conc-seat-{i}@f.local",
                                  password="Racer1234")
            return True
        except entitlements.TierGateError:
            return False
        finally:
            conn.close()

    results = _run_concurrent(2, attempt)
    assert sum(1 for r in results if r) == 1, f"only one seat may be granted: {results}"
    conn = pdb.connect()
    try:
        # `seats_used` مُرقَّع هنا، فاقرأ العدد من القاعدة مباشرةً.
        used = conn.execute("SELECT COUNT(*) c FROM users WHERE account_id = ? "
                            "AND is_active = 1", (f["account_id"],)).fetchone()["c"]
    finally:
        conn.close()
    assert used == 3, f"seat cap breached: {used} active on a 3-seat tier"


def test_concurrent_reactivations_never_exceed_seat_cap(monkeypatch):
    """تنشيطان متزامنان على مقعد واحد متبقٍّ ⇒ ينجح واحد فقط.

    الثقب الذي التقطته المراجعة الذاتية (§58): مسار التنشيط كان فحصاً-ثمّ-تحديثاً
    بلا قفل فوري بخلاف مسار الإنشاء، فطلبان متوازيان يمرّان معاً من `require_seat`
    ويكتبان ⇒ تجاوز السقف، أي تعطيلُ البوّابة التي وُجدت إعادة التنشيط لتمرّ منها.

    ملاحظة: الشرط `is_active = 0` في التحديث **لا يكفي** وحده، لأن الخيطين
    يستهدفان صفَّين مختلفين فيمرّ حرسُ كلٍّ منهما — القفل الفوري هو الفاعل.
    Locks the re-activation TOCTOU found by the §58 self-review.
    """
    seed(monkeypatch)
    f = make_factory("silver", "conc-react@f.local")    # seats = 3
    conn = pdb.connect()
    try:
        ids = [users.create_sub_user(conn, f["account_id"],
                                     email=f"react-{i}@f.local",
                                     password="Racer1234")["id"]
               for i in range(2)]
        for uid in ids:
            users.set_active(conn, f["account_id"], uid, False,
                             acting_user_id=f["user_id"])
        users.create_sub_user(conn, f["account_id"], email="react-filler@f.local",
                              password="Racer1234")
        assert entitlements.seats_used(conn, f["account_id"]) == 2   # ⇒ مقعد واحد
    finally:
        conn.close()

    _widen_seat_check_window(monkeypatch)

    def attempt(i):
        conn = pdb.connect()
        try:
            users.set_active(conn, f["account_id"], ids[i], True,
                             acting_user_id=f["user_id"])
            return True
        except entitlements.TierGateError:
            return False
        finally:
            conn.close()

    results = _run_concurrent(2, attempt)
    assert sum(1 for r in results if r) == 1, f"only one may win: {results}"
    conn = pdb.connect()
    try:
        used = conn.execute("SELECT COUNT(*) c FROM users WHERE account_id = ? "
                            "AND is_active = 1", (f["account_id"],)).fetchone()["c"]
    finally:
        conn.close()
    assert used == 3, f"seat cap breached: {used} active on a 3-seat tier"


def test_concurrent_metered_charges_on_one_key_charge_exactly_once(monkeypatch):
    """نقرتان متزامنتان على نفس مفتاح الخمول ⇒ خصمٌ **واحد** بالضبط.

    فحصُ «هل خُصِم؟» ثمّ الخصم على اتصالين متزامنين كان يسمح للاثنين بأن يريا
    «لم يُخصَم بعد» فيخصمان مرّتين على مفتاح واحد — والدفتر غير قابل للتعديل
    فالتكرار **لا يُصحَّح**. نفس عائلة `LESSONS` ٧٦.

    النافذة مُوسَّعة صناعياً (`_widen_charge_check_window`) لأن الحاجز وحده لا
    يكفي لقسمٍ حرجٍ أقصر من ميلي ثانية — الدرس نفسه: حارسٌ لا يُثبَت أنه يصطاد
    قد يكون خاملاً. Proven: without BEGIN IMMEDIATE this yields two charges.
    """
    seed(monkeypatch)
    f = make_factory("gold", "conc-charge@f.local", fund_cents=1000)
    _widen_charge_check_window(monkeypatch)

    def attempt(_i):
        conn = pdb.connect()
        try:
            _entry, charged = billing.charge_metered(
                conn, account_id=f["account_id"], actor_user_id=f["user_id"],
                operation=Operation.REPORT_GENERATED, amount_cents=100,
                key="one-report")
            return charged
        finally:
            conn.close()

    results = _run_concurrent(2, attempt)
    assert sum(1 for r in results if r) == 1, f"exactly one charge: {results}"
    conn = pdb.connect()
    try:
        n = conn.execute(
            "SELECT COUNT(*) c FROM ledger_entries WHERE account_id = ? AND "
            "operation_type = 'report_generated'", (f["account_id"],)
        ).fetchone()["c"]
        bal = wallet.get_wallet(conn, f["account_id"])["balance"]
    finally:
        conn.close()
    assert n == 1, f"double-charged one idempotency key: {n} entries"
    assert bal == 900, f"balance says double charge: {bal}"


def _widen_charge_check_window(monkeypatch, delay_s=0.15):
    """وسّع نافذة فحص الخمول — same rationale as `_widen_seat_check_window`."""
    import time
    real = billing.already_charged

    def slow_already_charged(conn, account_id, operation, key):
        out = real(conn, account_id, operation, key)
        time.sleep(delay_s)
        return out

    monkeypatch.setattr(billing, "already_charged", slow_already_charged)


def _mk_study(account_id, user_id, state="in_progress"):
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        n = now_iso()
        cur = conn.execute(
            "INSERT INTO studies (owner_id, title_en, state, created_by_user_id, "
            "created_at, updated_at) VALUES (?,'S',?,?,?,?)",
            (account_id, state, user_id, n, n))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _queue(account_id, study_id, n):
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO email_queue (account_id, study_id, prospect_email, "
                "subject, body, status, queued_at) "
                "VALUES (?,?,?,'S','B','queued',?)",
                (account_id, study_id, f"race{i}@f.local", now_iso()))
        conn.commit()
    finally:
        conn.close()


def test_concurrent_archives_only_one_wins(monkeypatch):
    """أرشفتان متزامنتان ⇒ واحدة تنجح والأخرى تُرفَض — لا إلغاء مزدوج محسوب."""
    seed(monkeypatch)
    f = make_factory("gold", "conc-archive@f.local")
    sid = _mk_study(f["account_id"], f["user_id"])
    _queue(f["account_id"], sid, 3)

    def attempt(_i):
        conn = pdb.connect()
        try:
            out = lifecycle.archive_study(conn, account_id=f["account_id"],
                                          study_id=sid, actor_user_id=f["user_id"])
            return out["cancelled_queued_emails"]
        except lifecycle.LifecycleError:
            return None
        finally:
            conn.close()

    results = _run_concurrent(2, attempt)
    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"exactly one archive may win: {results}"
    assert winners[0] == 3, f"the winner must report all 3 cancelled: {winners}"


def test_archive_never_races_the_worker_into_sending_an_archived_study(monkeypatch):
    """الأرشفة والعامل متزامنان ⇒ **لا صفّ يُرسَل ويُلغى معاً**، ولا صفّ عالق.

    الخطر الحقيقي: لو طالب العامل بصفٍّ (`queued`→`sending`) **بين** فحص الأرشفة
    وحذفها، لأمكن أن يُرسَل الصفّ **ويُحسَب ملغىً** في الوقت نفسه — أي رسالةٌ
    خرجت وخُصِمت ثم قيل للعميل إنها سُحبت. القفل الفوري يمنع ذلك: مطالبةُ العامل
    كتابةٌ فتُحتجَز حتى تلتزم الأرشفة.

    **الثابتة المقيسة ليست «لا إرسال بعد الأرشفة»** — رسالةٌ خرجت **قبل** التزام
    الأرشفة لا يمكن سحبها، وأرشفةٌ بعدها سلوكٌ صحيح لا خلل (وهذا ما رصده أوّل
    تشغيل لهذا الاختبار فصُحّحت صياغته). الثابتة: **المجموع محفوظ** —
    `مُرسَل + ملغى = الأصل`، فلا صفّ يُحسَب مرّتين ولا يضيع.

    Invariant: sent + cancelled == original, with no row both delivered and
    withdrawn. (Mail that left BEFORE the archive committed cannot be recalled.)
    """
    import time
    seed(monkeypatch)
    f = make_factory("gold", "conc-arch-worker@f.local", fund_cents=1000)
    sid = _mk_study(f["account_id"], f["user_id"])
    _queue(f["account_id"], sid, 4)

    real_pending = lifecycle._pending_counts

    def slow_pending(conn, account_id, study_id):
        out = real_pending(conn, account_id, study_id)
        time.sleep(0.2)          # النافذة التي يجب أن يُغلقها القفل
        return out

    monkeypatch.setattr(lifecycle, "_pending_counts", slow_pending)
    sent_calls = []

    cancelled = []

    def do_archive(_i):
        conn = pdb.connect()
        try:
            out = lifecycle.archive_study(conn, account_id=f["account_id"],
                                          study_id=sid,
                                          actor_user_id=f["user_id"])
            cancelled.append(out["cancelled_queued_emails"])
            return "archived"
        except lifecycle.LifecycleError as exc:
            return exc.code
        finally:
            conn.close()

    def do_worker(_i):
        conn = pdb.connect()
        try:
            email_queue.process_queue(
                conn, sender=lambda cfg, row: sent_calls.append(row["id"]))
            return "worked"
        finally:
            conn.close()

    _run_concurrent(2, lambda i: do_archive(i) if i == 0 else do_worker(i))

    conn = pdb.connect()
    try:
        stuck = conn.execute(
            "SELECT COUNT(*) c FROM email_queue WHERE account_id = ? AND "
            "study_id = ? AND status = 'sending'", (f["account_id"], sid)
        ).fetchone()["c"]
        by_status = {r["status"]: r["c"] for r in conn.execute(
            "SELECT status, COUNT(*) c FROM email_queue WHERE account_id = ? "
            "AND study_id = ? GROUP BY status", (f["account_id"], sid)).fetchall()}
        bal = int(wallet.get_wallet(conn, f["account_id"])["balance"])
        consents = conn.execute(
            "SELECT COUNT(*) c FROM consent_registry WHERE sending_account_id = ? "
            "AND study_id = ?", (f["account_id"], sid)).fetchone()["c"]
    finally:
        conn.close()

    assert stuck == 0, "a row was left stranded in 'sending'"
    sent_rows = by_status.get("sent", 0)
    cancelled_n = cancelled[0] if cancelled else 0
    # (١) المجموع محفوظ: لا صفّ ضاع ولا حُسِب مرّتين.
    assert sent_rows + cancelled_n + by_status.get("queued", 0) == 4, (
        f"rows lost or double-counted: sent={sent_rows} "
        f"cancelled={cancelled_n} left={by_status}")
    # (٢) كل رسالة خرجت لها قيد موافقة وخصمها — والملغاة لا شيء منهما.
    assert consents == sent_rows, (
        f"consent records ({consents}) must match delivered mail ({sent_rows})")
    assert bal == 1000 - sent_rows * 5, (
        f"balance {bal} does not match {sent_rows} delivered emails")
    assert len(sent_calls) == sent_rows, (
        f"sender called {len(sent_calls)}× but {sent_rows} rows are 'sent'")


def test_concurrent_debits_no_lost_updates(monkeypatch):
    """١٠ خصومات متزامنة على محفظة واحدة ⇒ الرصيد ينقص بالضبط N×المبلغ."""
    seed(monkeypatch)
    f = make_factory("gold", "conc-debit@f.local", fund_cents=1000)
    N, cost = 10, 10

    def debit(_i):
        conn = pdb.connect()
        try:
            wallet.post_entry(conn, account_id=f["account_id"],
                              actor_user_id=f["user_id"],
                              operation=Operation.EMAIL_SENT, amount=-cost)
            return True
        finally:
            conn.close()

    _run_concurrent(N, debit)
    conn = pdb.connect()
    try:
        w = wallet.get_wallet(conn, f["account_id"])
        n_entries = conn.execute("SELECT COUNT(*) c FROM ledger_entries WHERE "
                               "account_id = ? AND operation_type = 'email_sent'",
                               (f["account_id"],)).fetchone()["c"]
    finally:
        conn.close()
    assert w["balance"] == 1000 - N * cost       # no lost updates
    assert w["lifetime_spent"] == N * cost
    assert n_entries == N                          # exactly one entry per debit
