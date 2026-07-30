"""دورة حياة الدراسة — the explicit completed/archived transitions (PR-4).

`draft→in_progress` (الإطلاق) وحده كان يملك مسار انتقال. الحالتان
`completed`/`archived` موجودتان في قيد `CHECK` بالمخطّط منذ PR-1 لكن **لا
مسار يصل إليهما**، وعمود `state` غير مُدرَج في `repository._WRITABLE` عمداً
(آلةُ حالاتٍ لا تُكتَب بـCRUD عامّ) — فلا سبيل إليهما إلا من هنا.

**لماذا هذا ليس مسكاً للدفاتر:** عامل الطابور **لا يفحص حالة الدراسة إطلاقاً**
(`email_queue.process_queue` يقرأ `WHERE status = 'queued'` ولا شيء غير ذلك).
فلو كانت الأرشفة تغييرَ عمودٍ فقط، لظلّت رسائل الدراسة **التي سحبها العميل**
تُرسَل، وتُخصَم من محفظته، وتُكتب لها قيود موافقة — أي أن الحالة تكون **ادعاءً
كاذباً** عن الواقع. لذلك:

- **`completed` يُرفَض ما دام هناك بريدٌ معلّق.** «مكتملة» وفي الطابور رسائل
  ستخرج بعد دقائق = وصفٌ مخالف للواقع. ينتظر العميل النفاد أو يؤرشف.
- **`archive` يُلغي المعلّق فعلاً** (يحذف صفوف `queued`) فتصير الأرشفة سحباً
  حقيقياً لا تسمية. الحذف آمن: صفٌّ `queued` لم يُرسَل، فلا قيد موافقة له ولا
  قيد دفتر — لا أثر تدقيق يُفقَد، والعدد يُسجَّل في التدقيق.
- **صفوف `sending` لا تُمَسّ أبداً.** العامل يملكها (طالب بها ذرّياً) وقد تكون
  رسالتها في الطريق فعلاً؛ لمسُها يعني إمّا خصماً بلا قيد موافقة أو رسالةً بلا
  أثر. الحالة عابرة (ثوانٍ) فالردّ 409 «أعِد المحاولة».

Archiving actually cancels pending sends — otherwise the state would be a
claim the system contradicts. In-flight ('sending') rows are worker-owned,
never touched.
"""
from __future__ import annotations

import sqlite3

from . import audit, repository
from .db import now_iso

# الانتقالات المسموحة صراحةً · the explicitly allowed transitions.
COMPLETE_FROM = ("in_progress",)
ARCHIVE_FROM = ("draft", "in_progress", "completed")
TERMINAL = ("archived",)


class LifecycleError(Exception):
    """انتقال مرفوض — carries a machine-readable code for the endpoint."""

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.extra = extra
        super().__init__(message)

    def as_detail(self) -> dict:
        return {"error": self.code, "message": str(self), **self.extra}


def _pending_counts(conn: sqlite3.Connection, account_id: int,
                    study_id: int) -> dict[str, int]:
    """المعلّق في الطابور لهذه الدراسة — queued vs in-flight, this account only."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued, "
        "SUM(CASE WHEN status = 'sending' THEN 1 ELSE 0 END) AS sending "
        "FROM email_queue WHERE account_id = ? AND study_id = ?",
        (account_id, study_id)).fetchone()
    return {"queued": int(row["queued"] or 0), "sending": int(row["sending"] or 0)}


def _load(conn: sqlite3.Connection, account_id: int, study_id: int) -> dict | None:
    """اقرأ الدراسة عبر طبقة العزل — inherits the owner predicate by construction."""
    return repository.studies(conn).get(account_id, study_id)


def complete_study(conn: sqlite3.Connection, *, account_id: int, study_id: int,
                   actor_user_id: int | None) -> dict | None:
    """أنهِ دراسة — in_progress → completed. None if not this tenant's.

    يرفع `LifecycleError` بـ`pending_emails` ما دام في الطابور معلّقٌ: وسمُها
    «مكتملة» ورسائلها ستخرج بعد دقائق ادعاءٌ كاذب. Refuses while sends pend.
    """
    study = _load(conn, account_id, study_id)
    if study is None:
        return None
    conn.commit()                       # اطوِ المعلّق قبل BEGIN الصريح
    conn.execute("BEGIN IMMEDIATE")
    try:
        # أعِد القراءة داخل القفل: الحالة قد تتغيّر بين الفحص والكتابة.
        fresh = _load(conn, account_id, study_id)
        if fresh is None:
            conn.rollback()
            return None
        if fresh["state"] not in COMPLETE_FROM:
            conn.rollback()
            raise LifecycleError(
                "invalid_transition",
                f"cannot complete a study that is {fresh['state']}",
                state=fresh["state"], allowed_from=list(COMPLETE_FROM))
        pending = _pending_counts(conn, account_id, study_id)
        if pending["queued"] or pending["sending"]:
            conn.rollback()
            raise LifecycleError(
                "pending_emails",
                "cannot complete while emails are still queued or in flight — "
                "let the queue drain, or archive to cancel the remainder",
                **pending)
        now = now_iso()
        cur = conn.execute(
            "UPDATE studies SET state = 'completed', completed_at = ?, "
            "updated_at = ? WHERE id = ? AND owner_id = ? AND state = 'in_progress'",
            (now, now, study_id, account_id))
        if cur.rowcount == 0:           # سبقنا طلبٌ متزامن · a concurrent call won
            conn.rollback()
            raise LifecycleError("invalid_transition",
                                 "study is no longer in_progress")
        audit.record(conn, action="study_completed", user_id=actor_user_id,
                     account_id=account_id, resource_type="study",
                     resource_id=study_id, changes={"from": "in_progress"})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return _load(conn, account_id, study_id)


def archive_study(conn: sqlite3.Connection, *, account_id: int, study_id: int,
                  actor_user_id: int | None) -> dict | None:
    """أرشِف دراسة — سحبٌ حقيقي: يُلغي المعلّق ثم يُغلق الحالة.

    يحذف صفوف `queued` لهذه الدراسة (لم تُرسَل ⇒ لا موافقة ولا قيد دفتر يُفقَد)
    ويسجّل عددها تدقيقاً. صفوف `sending` مملوكة للعامل فلا تُمَسّ — وجودها يرفع
    `in_flight_emails` (409) لأن الحالة عابرة ثوانٍ.

    Deletes pending queue rows so the archive is a real withdrawal; refuses
    while any row is mid-flight rather than racing the worker.
    """
    study = _load(conn, account_id, study_id)
    if study is None:
        return None
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = _load(conn, account_id, study_id)
        if fresh is None:
            conn.rollback()
            return None
        if fresh["state"] in TERMINAL:
            conn.rollback()
            raise LifecycleError("already_archived",
                                 "study is already archived", state=fresh["state"])
        if fresh["state"] not in ARCHIVE_FROM:
            conn.rollback()
            raise LifecycleError(
                "invalid_transition",
                f"cannot archive a study that is {fresh['state']}",
                state=fresh["state"], allowed_from=list(ARCHIVE_FROM))
        pending = _pending_counts(conn, account_id, study_id)
        if pending["sending"]:
            conn.rollback()
            raise LifecycleError(
                "in_flight_emails",
                "an email for this study is mid-send — retry in a moment",
                **pending)
        # ألغِ المعلّق فعلاً · actually cancel the pending sends.
        cancelled = conn.execute(
            "DELETE FROM email_queue WHERE account_id = ? AND study_id = ? "
            "AND status = 'queued'", (account_id, study_id)).rowcount
        now = now_iso()
        marks = ",".join("?" for _ in ARCHIVE_FROM)
        cur = conn.execute(
            f"UPDATE studies SET state = 'archived', updated_at = ? "
            f"WHERE id = ? AND owner_id = ? AND state IN ({marks})",
            (now, study_id, account_id, *ARCHIVE_FROM))
        if cur.rowcount == 0:
            conn.rollback()
            raise LifecycleError("invalid_transition",
                                 "study state changed concurrently")
        audit.record(conn, action="study_archived", user_id=actor_user_id,
                     account_id=account_id, resource_type="study",
                     resource_id=study_id,
                     changes={"from": fresh["state"],
                              "cancelled_queued_emails": int(cancelled)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    out = _load(conn, account_id, study_id)
    if out is not None:
        out = {**out, "cancelled_queued_emails": int(cancelled)}
    return out
