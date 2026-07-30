"""الحصص والطبقات — subscription quota enforcement.

- العدّاد يزيد **فقط** عند انتقال draft→in_progress (أوّل بريد يُصفّ)، لا عند
  إنشاء/تحرير المسودّة.
- Basic: دراسة واحدة مدى الحياة (لا تُصفَّر أبداً). البقية: عدّاد شهري.
- إعادة التصفير الشهرية: أوّل الشهر 00:00 UTC، تصفّر كل الطبقات عدا Basic.
- تجاوز الحصّة عند الإطلاق: امنع + سجّل تدقيقاً (الواجهة تعرض دعوة ترقية).

The counter increments only on launch (first email queued), never on draft edit.
"""
from __future__ import annotations

import dataclasses
import datetime
import sqlite3

from . import audit
from .db import now_iso
from .models import Tier, tier_limits


def current_period(at: datetime.datetime | None = None) -> str:
    """الشهر الحالي بصيغة 'YYYY-MM' UTC — the quota period key."""
    dt = at or datetime.datetime.now(datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m")


@dataclasses.dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    reason: str = ""          # كود سبب المنع · machine-readable reason code
    limit: int = 0
    used: int = 0
    tier: str = ""


def _account(conn: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise ValueError(f"no account {account_id}")
    return row


def _roll_period_if_needed(conn: sqlite3.Connection, acc: sqlite3.Row) -> int:
    """صفّر العدّاد الشهري كسولاً عند دخول شهر جديد — lazy monthly roll.

    تصفير **ذرّي مشروط** بتعليمة UPDATE واحدة: يعيد الضبط فقط حين يختلف الشهر
    المخزَّن (أو كان NULL)، فتشغيلات متزامنة على حساب حديث لا تصفّر العدّاد
    مراراً وتُفسِد السقف (خلل التقطه تدقيق التزامن). لا يمسّ Basic.
    Atomic conditional roll — a single guarded UPDATE, so concurrent first
    launches can't each reset the counter and break the cap.
    """
    if Tier(acc["tier"]) == Tier.BASIC:
        return int(acc["current_month_study_count"])
    period = current_period()
    conn.execute(
        "UPDATE accounts SET current_month_study_count = 0, quota_period = ?, "
        "updated_at = ? WHERE id = ? AND (quota_period IS NULL OR quota_period != ?)",
        (period, now_iso(), acc["id"], period))
    conn.commit()
    row = conn.execute("SELECT current_month_study_count FROM accounts WHERE id = ?",
                       (acc["id"],)).fetchone()
    return int(row["current_month_study_count"])


# لا دالّة `evaluate()` منفصلة عمداً: مسار الحصّة **واحد** هو `reserve_launch`
# (فحص وزيادة ذرّيان في تعليمة واحدة). نسخة «قراءة فقط» ثانية كانت تكرّر منطق
# الطبقات بلا الشكل الآمن من TOCTOU، فأيّ مُنادٍ لها يعيد إدخال السباق المُغلَق.
# Deliberately one quota path (reserve_launch); a read-only twin would duplicate
# the tier rules without the race-safe guarded UPDATE.


def reserve_launch(conn: sqlite3.Connection, account_id: int, *,
                   actor_user_id: int | None) -> QuotaDecision:
    """احجز إطلاق دراسة — check the quota and, if allowed, increment the counter.

    يُستدعى مرّة واحدة عند draft→in_progress. عند المنع يكتب قيد تدقيق
    (over-quota launch attempt) ولا يزيد شيئاً.
    """
    # زيادة ذرّية محروسة بالسقف — atomic guarded increment closes the
    # check-then-increment TOCTOU (ملاحظة مراجعة خصامية): تشغيلان متزامنان لا
    # يتجاوزان الحدّ لأن الشرط `< limit` والزيادة في تعليمة UPDATE واحدة.
    acc = _account(conn, account_id)
    tier = Tier(acc["tier"])
    limits = tier_limits(tier)
    if tier == Tier.BASIC:
        cur = conn.execute(
            "UPDATE accounts SET lifetime_study_count = lifetime_study_count + 1, "
            "updated_at = ? WHERE id = ? AND lifetime_study_count < ?",
            (now_iso(), account_id, limits.lifetime_studies))
        conn.commit()
        used = int(_account(conn, account_id)["lifetime_study_count"])
        reason, limit = "lifetime_quota_exceeded", limits.lifetime_studies
    else:
        _roll_period_if_needed(conn, acc)   # صفّر كسولاً عند شهر جديد أولاً
        cur = conn.execute(
            "UPDATE accounts SET current_month_study_count = "
            "current_month_study_count + 1, quota_period = ?, updated_at = ? "
            "WHERE id = ? AND current_month_study_count < ?",
            (current_period(), now_iso(), account_id, limits.monthly_studies))
        conn.commit()
        used = int(_account(conn, account_id)["current_month_study_count"])
        reason, limit = "monthly_quota_exceeded", limits.monthly_studies
    if cur.rowcount == 0:   # الشرط لم يتحقّق ⇒ عند/فوق الحدّ · at/over the cap
        audit.record(conn, action="quota_exceeded", user_id=actor_user_id,
                     account_id=account_id, resource_type="study",
                     changes={"reason": reason, "tier": tier.value,
                              "limit": limit, "used": used})
        conn.commit()
        return QuotaDecision(False, reason, limit, used, tier.value)
    return QuotaDecision(True, "", limit, used, tier.value)


def monthly_reset(conn: sqlite3.Connection) -> int:
    """التصفير الشهري — reset current_month counters for all tiers EXCEPT Basic.

    مهمّة أوّل الشهر 00:00 UTC. يكتب قيد تدقيق. يرجّع عدد الحسابات المصفَّرة.
    Basic's lifetime counter is deliberately untouched (survives the reset).

    محروسة بمفتاح الفترة (`quota_period != period`) فتكون **خاملة التكرار**:
    نداء ثانٍ في نفس الشهر (إعادة تشغيل المجدول، أو إطلاق مزدوج) لا يصفّر شيئاً
    ولا يمنح حصّة إضافية. Period-guarded ⇒ idempotent within a month.
    """
    period = current_period()
    cur = conn.execute(
        "UPDATE accounts SET current_month_study_count = 0, quota_period = ?, "
        "updated_at = ? WHERE tier != 'basic' "
        "AND (quota_period IS NULL OR quota_period != ?)",
        (period, now_iso(), period))
    audit.record(conn, action="monthly_quota_reset", resource_type="accounts",
                 changes={"period": period, "accounts_reset": cur.rowcount})
    conn.commit()
    return cur.rowcount
