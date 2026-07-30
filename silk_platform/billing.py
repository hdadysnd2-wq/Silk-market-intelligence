"""العمليات المدفوعة المقيسة — idempotent metered charges (PR-3).

`models.py` يحمل ثوابت التسعير منذ PR-1، لكن اثنين منها لم يكن لهما مسارٌ
يستهلكهما: `PRICE_REPORT_CENTS` و`PRICE_API_CALL_CENTS`. هذه الوحدة هي مسار
الخصم **الواحد** لكل عملية مقيسة تُطلَب صراحةً (بخلاف البريد الذي يخصمه العامل
عند خروج الرسالة فعلاً، وبخلاف فوترة التخزين الشهرية في `jobs.py`).

Three gates, in this order — and the order is the point:

١) **الخمول** (idempotency): مفتاح المرّة الواحدة يُخزَّن في وصف قيد الدفتر —
   نفس نمط `jobs.already_billed` (الدفتر غير قابل للتعديل، فالتكرار **لا
   يُصحَّح** لاحقاً؛ الوقاية الوحيدة عدم كتابته أصلاً). نقرةٌ مزدوجة أو إعادة
   محاولة شبكة ترجع القيد نفسه بـ`charged=False` ولا تخصم ثانية.
٢) **المديونية**: حسابٌ رصيده سالب لا يشتري عملية اختيارية جديدة — نفس قاعدة
   «لا إطلاق ولا إرسال ما دام سالباً» (`wallet.is_delinquent`).
٣) **كفاية الرصيد**: `allow_negative=False` **مقصود**. البريد يُخصَم بـ
   `allow_negative=True` لأن الرسالة **خرجت فعلاً** فالدَّين حقيقي؛ أمّا التقرير
   فلم يحدث شيء بعد، فالرفض المسبق هو الصحيح لا تسجيل دَين.

**الذرّية ليست تفصيلاً:** الفحوص الثلاثة والخصم داخل `BEGIN IMMEDIATE` واحد.
فحصُ الخمول ثمّ الخصم على اتصالين متزامنين كان يسمح لنقرتين متوازيتين بأن تريا
«لم يُخصَم بعد» كلتاهما فتخصمان **مرّتين** على مفتاح واحد — وهو نفس عائلة
TOCTOU المسجَّلة في `LESSONS` ٧٦ (وقبلها `quota.reserve_launch`).
The idempotency check and the debit share one immediate transaction, so a
double-click cannot produce two charges for one key.
"""
from __future__ import annotations

import sqlite3

from . import audit, wallet
from .models import Operation


class BillingError(Exception):
    """خطأ فوترة قابل للعرض — a client-visible billing fault."""


class Delinquent(BillingError):
    """الحساب مدين — must settle up before buying an optional operation."""


def charge_key_description(operation: Operation, key: str) -> str:
    """وصفُ القيد الحامل لمفتاح الخمول — the ledger description IS the key.

    الوصف هو مخزن الخمول (لا جدول جديد): الدفتر غير قابل للتعديل ومفهرَس
    بالحساب، فبحثٌ واحد يجيب «هل خُصِم هذا المفتاح؟». نفس سابقة
    `jobs._billing_description`. The ledger doubles as the idempotency store.
    """
    return f"{operation.value} [{key}]"


def already_charged(conn: sqlite3.Connection, account_id: int,
                    operation: Operation, key: str) -> dict | None:
    """هل خُصِم هذا المفتاح لهذا الحساب؟ — returns the existing entry or None."""
    row = conn.execute(
        "SELECT * FROM ledger_entries WHERE account_id = ? AND "
        "operation_type = ? AND description = ? LIMIT 1",
        (account_id, operation.value,
         charge_key_description(operation, key))).fetchone()
    return dict(row) if row else None


def charge_metered(conn: sqlite3.Connection, *, account_id: int,
                   actor_user_id: int | None, operation: Operation,
                   amount_cents: int, key: str,
                   metadata: dict | None = None) -> tuple[dict, bool]:
    """اخصم عملية مقيسة **مرّة واحدة** لكل مفتاح — returns (entry, charged).

    `charged=False` يعني أن المفتاح كان مخصوماً سلفاً فأُعيد القيد نفسه — ليس
    خطأً بل الجواب الصحيح لإعادة المحاولة. يرفع `Delinquent` أو
    `wallet.InsufficientFunds` قبل أي كتابة.

    Returns the existing entry with charged=False when the key was already
    billed; raises before writing anything on either money gate.
    """
    if amount_cents <= 0:
        raise BillingError("a metered charge must be a positive amount")
    wallet.ensure_wallet(conn, account_id)
    conn.commit()                       # اطوِ المعلّق قبل BEGIN الصريح
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = already_charged(conn, account_id, operation, key)
        if existing is not None:
            conn.rollback()
            return existing, False      # خُصِم سلفاً · already billed, no-op
        if wallet.is_delinquent(conn, account_id):
            conn.rollback()
            raise Delinquent(
                "account balance is negative — settle up before purchasing")
        eid = wallet.apply_entry(
            conn, account_id=account_id, actor_user_id=actor_user_id,
            operation=operation, amount=-int(amount_cents),
            description=charge_key_description(operation, key),
            metadata={**(metadata or {}), "idempotency_key": key},
            allow_negative=False)       # لم يحدث شيء بعد ⇒ ارفض لا تُدِن
        audit.record(conn, action=f"{operation.value}_charged",
                     user_id=actor_user_id, account_id=account_id,
                     resource_type="ledger_entry", resource_id=eid,
                     changes={"amount_cents": -int(amount_cents), "key": key})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    # إعادة القراءة منطَّقة بالمالك أيضاً — لا استثناء لقراءةٍ «نعرف مالكها»:
    # القيد الذي كتبناه قبل سطرين مملوكٌ لهذا الحساب بالبناء، لكن ترك الاستعلام
    # بلا قيدٍ يجعله سابقةً تُنسَخ إلى موضعٍ لا يكون فيه المالك معروفاً.
    # Scoped even though we just wrote it — an unscoped read is a copyable precedent.
    row = conn.execute(
        "SELECT * FROM ledger_entries WHERE id = ? AND account_id = ?",
        (eid, account_id)).fetchone()
    return dict(row), True
