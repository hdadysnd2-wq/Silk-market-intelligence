"""اختبارات التزامن الحقيقية — real threaded concurrency proofs.

بخلاف الاختبارات المتسلسلة، هذي تُطلق خيوطاً متزامنة تضرب **نفس الحساب** عبر
حاجز بدء موحّد (Barrier) كي تتصادم فعلاً، فتُثبِت أن الأقفال الذرّية (UPDATE
محروس + BEGIN IMMEDIATE + busy_timeout) تمنع تجاوز السقف وفقدان التحديثات.
Each worker uses its OWN sqlite connection (connections aren't thread-shareable).
"""
import threading

from platform_helpers import make_factory, seed
from silk_platform import db as pdb, quota, wallet
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
