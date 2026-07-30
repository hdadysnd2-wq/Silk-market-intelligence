"""اختبارات PR-4 — انتقالا `completed`/`archived` كانتقالين صريحين.

المحور: **الحالة ليست تسمية.** عامل الطابور لا يفحص حالة الدراسة إطلاقاً، فلو
كانت الأرشفة تغييرَ عمودٍ فقط لظلّت رسائل الدراسة المسحوبة تُرسَل وتُخصَم.
The state must change reality, not just the column.
"""
from __future__ import annotations

import pytest

from platform_helpers import (add_active_smtp, client, hdr, login,
                              make_draft_study, make_factory, seed)


def _study(account_id: int, user_id: int, state: str = "in_progress") -> int:
    from silk_platform import db as pdb
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO studies (owner_id, title_en, state, target_count, "
            "created_by_user_id, created_at, updated_at) "
            "VALUES (?,'S',?,5,?,?,?)", (account_id, state, user_id, now, now))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _enqueue(account_id: int, study_id: int, status: str, n: int = 1) -> None:
    from silk_platform import db as pdb
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO email_queue (account_id, study_id, prospect_email, "
                "subject, body, status, queued_at) VALUES (?,?,?,'S','B',?,?)",
                (account_id, study_id, f"p{status}{i}@example.com", status,
                 now_iso()))
        conn.commit()
    finally:
        conn.close()


def _queue_states(account_id: int, study_id: int) -> dict[str, int]:
    from silk_platform import db as pdb
    conn = pdb.connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) c FROM email_queue WHERE account_id = ? "
            "AND study_id = ? GROUP BY status", (account_id, study_id)).fetchall()
        return {r["status"]: r["c"] for r in rows}
    finally:
        conn.close()


def _state(account_id: int, study_id: int) -> str:
    from silk_platform import db as pdb, repository
    conn = pdb.connect()
    try:
        return repository.studies(conn).get(account_id, study_id)["state"]
    finally:
        conn.close()


# ═══════════════════════ الإنهاء · complete ══════════════════════════════════
def test_complete_moves_in_progress_to_completed_and_stamps_the_time(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "lc-complete@example.com")
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"
    assert r.json()["completed_at"], "completed_at must be stamped"


def test_complete_is_refused_while_emails_are_still_queued(monkeypatch):
    """«مكتملة» ورسائلها ستخرج بعد دقائق ادعاءٌ كاذب — 409 بالعدّادات.

    العامل لا يفحص حالة الدراسة، فلا شيء يوقف تلك الرسائل: الحالة ستكون وصفاً
    يخالف الواقع.
    """
    seed(monkeypatch)
    f = make_factory("gold", "lc-pending@example.com")
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "queued", 3)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["error"] == "pending_emails" and d["queued"] == 3
    assert _state(f["account_id"], sid) == "in_progress"   # لم تتغيّر


def test_complete_is_refused_while_an_email_is_in_flight(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "lc-inflight@example.com")
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "sending", 1)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r.status_code == 409
    assert r.json()["detail"]["sending"] == 1


def test_complete_succeeds_once_the_queue_has_drained(monkeypatch):
    """الرفض بوّابة لا طريق مسدود — يمرّ بعد نفاد الطابور."""
    seed(monkeypatch)
    f = make_factory("gold", "lc-drained@example.com")
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "sent", 2)          # نفدت · already delivered
    _enqueue(f["account_id"], sid, "failed", 1)        # فشلت نهائياً · terminal
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "completed"


@pytest.mark.parametrize("state", ["draft", "completed", "archived"])
def test_complete_only_from_in_progress(monkeypatch, state):
    seed(monkeypatch)
    f = make_factory("gold", f"lc-from-{state}@example.com")
    sid = _study(f["account_id"], f["user_id"], state)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "invalid_transition"
    assert _state(f["account_id"], sid) == state


# ═══════════════════════ الأرشفة · archive (a real withdrawal) ═══════════════
def test_archive_cancels_pending_emails_and_keeps_the_sent_ones(monkeypatch):
    """الأرشفة سحبٌ حقيقي: المعلّق يُلغى، والمُرسَل يبقى (له موافقة وقيد دفتر)."""
    seed(monkeypatch)
    f = make_factory("gold", "lc-archive@example.com")
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "queued", 4)
    _enqueue(f["account_id"], sid, "sent", 2)
    _enqueue(f["account_id"], sid, "failed", 1)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "archived"
    assert r.json()["cancelled_queued_emails"] == 4
    left = _queue_states(f["account_id"], sid)
    assert "queued" not in left, "pending rows must be cancelled"
    assert left.get("sent") == 2 and left.get("failed") == 1


def test_archived_study_emails_are_never_sent_by_the_worker(monkeypatch):
    """**الاختبار الحاسم**: بعد الأرشفة لا يُرسَل شيء ولا يُخصَم قرش.

    العامل لا يفحص حالة الدراسة إطلاقاً — فلو كانت الأرشفة تسميةً فقط لأرسل
    العامل رسائل الدراسة المسحوبة وخصم قيمتها من محفظة العميل. هذا الاختبار
    يقيس **الواقع** لا العمود.
    """
    seed(monkeypatch)
    f = make_factory("gold", "lc-worker@example.com", fund_cents=1000)
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "queued", 5)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{sid}/archive",
                   headers=hdr(tok)).status_code == 200
    # شغّل العامل فعلياً بمرسِل يحصي · run the real worker with a counting sender.
    from silk_platform import db as pdb, email_queue, wallet
    sent_calls = []
    conn = pdb.connect()
    try:
        summary = email_queue.process_queue(
            conn, sender=lambda cfg, row: sent_calls.append(row["id"]))
        balance = int(wallet.get_wallet(conn, f["account_id"])["balance"])
    finally:
        conn.close()
    assert sent_calls == [], f"worker sent mail for an archived study: {sent_calls}"
    assert summary["sent"] == [], f"worker reported sends: {summary['sent']}"
    assert summary["remaining"] == 0, "no queued row may survive the archive"
    assert balance == 1000, f"an archived study still cost money: {balance}"
    # ولا قيد موافقة كُتب · and no consent record was written.
    conn = pdb.connect()
    try:
        consents = conn.execute(
            "SELECT COUNT(*) c FROM consent_registry WHERE sending_account_id = ? "
            "AND study_id = ?", (f["account_id"], sid)).fetchone()["c"]
    finally:
        conn.close()
    assert consents == 0, "an archived study produced consent records"


def test_archive_is_refused_while_an_email_is_mid_send(monkeypatch):
    """صفٌّ `sending` مملوكٌ للعامل — لا نتسابق معه؛ 409 عابر."""
    seed(monkeypatch)
    f = make_factory("gold", "lc-arch-inflight@example.com")
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "sending", 1)
    _enqueue(f["account_id"], sid, "queued", 2)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "in_flight_emails"
    # ولم يُلغَ شيء · nothing was cancelled on the refused path.
    assert _queue_states(f["account_id"], sid).get("queued") == 2
    assert _state(f["account_id"], sid) == "in_progress"


@pytest.mark.parametrize("state", ["draft", "in_progress", "completed"])
def test_archive_allowed_from_every_non_terminal_state(monkeypatch, state):
    seed(monkeypatch)
    f = make_factory("gold", f"lc-arch-{state}@example.com")
    sid = _study(f["account_id"], f["user_id"], state)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "archived"


def test_archiving_twice_is_refused_not_silently_repeated(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "lc-arch-twice@example.com")
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{sid}/archive",
                   headers=hdr(tok)).status_code == 200
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "already_archived"


def test_an_archived_study_cannot_be_relaunched(monkeypatch):
    """الأرشفة تُغلق الإطلاق — الإطلاق يشترط draft فيرفض 409."""
    seed(monkeypatch)
    f = make_factory("gold", "lc-relaunch@example.com", fund_cents=1000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=1)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{sid}/archive",
                   headers=hdr(tok)).status_code == 200
    r = cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok))
    assert r.status_code == 409, r.text
    assert _state(f["account_id"], sid) == "archived"


# ═════════════════ العزل والصلاحيات · isolation & roles ══════════════════════
@pytest.mark.parametrize("action", ["complete", "archive"])
def test_cannot_transition_another_tenants_study(monkeypatch, action):
    """دراسة حسابٍ آخر ⇒ 404 بلا تسريب وجود + تدقيق + بلا تغيير حالة."""
    seed(monkeypatch)
    a = make_factory("gold", f"lc-iso-a-{action}@example.com")
    b = make_factory("gold", f"lc-iso-b-{action}@example.com")
    victim = _study(b["account_id"], b["user_id"])
    cl = client()
    tok = login(cl, a["email"], a["password"])
    r = cl.post(f"/platform/studies/{victim}/{action}", headers=hdr(tok))
    assert r.status_code == 404
    assert _state(b["account_id"], victim) == "in_progress"
    from silk_platform import audit, db as pdb
    conn = pdb.connect()
    try:
        denied = audit.search(conn, account_id=a["account_id"],
                              action=f"cross_tenant_{action}")
    finally:
        conn.close()
    assert denied, "a cross-tenant transition attempt must be audit-logged"


@pytest.mark.parametrize("action", ["complete", "archive"])
def test_analyst_cannot_transition_a_study(monkeypatch, action):
    info = seed(monkeypatch)
    f = make_factory("gold", f"lc-analyst-{action}@example.com")
    sid = _study(f["account_id"], f["user_id"])
    cl = client()
    tok = login(cl, info["analyst"]["email"], info["analyst"]["password"])
    assert cl.post(f"/platform/studies/{sid}/{action}",
                   headers=hdr(tok)).status_code == 403
    assert _state(f["account_id"], sid) == "in_progress"


@pytest.mark.parametrize("action", ["complete", "archive"])
def test_transitions_require_authentication(monkeypatch, action):
    seed(monkeypatch)
    f = make_factory("gold", f"lc-noauth-{action}@example.com")
    sid = _study(f["account_id"], f["user_id"])
    assert client().post(f"/platform/studies/{sid}/{action}").status_code == 401


def test_state_is_not_writable_through_the_generic_patch(monkeypatch):
    """`state` خارج `_WRITABLE` — فلا يُتخطّى الانتقالُ بـPATCH عامّ.

    آلةُ الحالات لها بوّابات (بريد معلّق، إلغاء الطابور)؛ عمودٌ قابل للكتابة
    عامّةً كان سيفتح باباً ثانياً يتخطّاها كلها.
    """
    seed(monkeypatch)
    f = make_factory("gold", "lc-patch@example.com")
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "queued", 2)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.patch(f"/platform/studies/{sid}", headers=hdr(tok),
                 json={"state": "completed", "title_en": "New"})
    assert r.status_code == 200, r.text
    assert r.json()["title_en"] == "New"          # المسموح طُبِّق
    assert _state(f["account_id"], sid) == "in_progress"   # الحالة لم تتغيّر
    assert _queue_states(f["account_id"], sid).get("queued") == 2


def test_the_audit_trail_records_the_cancelled_count(monkeypatch):
    """عدد الملغى مسجَّل تدقيقاً — سحبٌ بلا أثر ليس سحباً قابلاً للمراجعة."""
    seed(monkeypatch)
    f = make_factory("gold", "lc-audit@example.com")
    sid = _study(f["account_id"], f["user_id"])
    _enqueue(f["account_id"], sid, "queued", 3)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    from silk_platform import audit, db as pdb
    import json as _json
    conn = pdb.connect()
    try:
        rows = audit.search(conn, account_id=f["account_id"],
                            action="study_archived")
    finally:
        conn.close()
    assert len(rows) == 1
    changes = _json.loads(rows[0]["changes"])
    assert changes["cancelled_queued_emails"] == 3
    assert changes["from"] == "in_progress"
