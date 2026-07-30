"""إدارة المستخدمين الفرعيين — tenant-scoped sub-user management (PR-2).

المواصفة §1: حساب المصنع «يدير مستخدميه الفرعيين»، و§2 يضع سقفاً لكل طبقة
(1 / 3 / 10 / غير محدود). هذه الوحدة هي المسار الوحيد لذلك، وكل استعلام فيها
مقيَّد بـ`account_id` — فلا يقرأ حسابٌ مستخدمي غيره ولا يعدّلهم.

**ثلاثة قيود بنيويّة، لا تجميلية:**

1. **الدور مفروض `factory`** ولا يُقرأ من الطلب أبداً. لو قُرِئ لصار بإمكان
   مصنعٍ أن يُنشئ `silk_admin` فيملك المنصّة كلّها — أخطر تصعيد صلاحية ممكن هنا.
2. **`account_id` من سياق الجلسة** حصراً، فلا يُزرَع مستخدم في حساب آخر.
3. **الإزالة تعطيلٌ لا حذف**، كي تبقى مراجع التدقيق والدفتر سليمة؛ والمعطَّل
   يُخلي مقعده فيصير الاستبدال ممكناً.

Role is forced to 'factory' (never read from the request — that would be a
privilege-escalation path to silk_admin); account_id always comes from the
session; removal is deactivation so audit/ledger references stay intact.

**حدّ معلَن:** مخطّط PR-1 لا يحمل «مالك حساب» فرعياً، فكل مستخدم `factory` في
الحساب يستطيع إدارة المقاعد. تمييز مالك/عضو يحتاج عموداً جديداً وقراراً من
المالك. Declared limit: no per-account owner sub-role exists yet.
"""
from __future__ import annotations

import sqlite3

from . import entitlements, passwords
from .db import now_iso

# الأعمدة التي يجوز للعميل تعديلها في مستخدم — allowlist (لا role، لا is_active،
# لا password_hash، ولا account_id: كلّها مسارات صلاحية أو ملكية).
_PROFILE_FIELDS = {"first_name", "last_name", "language_preference"}

_PUBLIC_COLS = ("id", "account_id", "email", "role", "first_name", "last_name",
                "language_preference", "is_active", "created_at", "updated_at")


class UserError(ValueError):
    """خطأ إدارة مستخدم قابل للعرض — a client-visible user-management fault."""


class DuplicateEmail(UserError):
    """البريد مستخدَم سلفاً — the login identity is globally unique."""


def _public(row) -> dict:
    """صفّ مستخدم بلا `password_hash` — never leak the hash, even to the owner."""
    d = dict(row)
    return {k: d.get(k) for k in _PUBLIC_COLS if k in d}


def _as_text(value, field: str) -> str | None:
    """اقبل نصّاً أو لا شيء، وارفض غيرهما — coerce a body value to text or fail 4xx.

    JSON يسمح بأي نوع، فـ`{"first_name": {"a": 1}}` كان يصل إلى رابط المعاملات
    فيرفع `InterfaceError` ⇒ **500 على خطأ عميل**. القاعدة في هذا الريبو صريحة:
    عيب العميل 4xx لا 5xx (نفس سبب وجود `api._as_int`). الأرقام والمنطقيات
    تُحوَّل نصّاً (مقبولة كاسم)، والحاويات تُرفَض.
    A dict/list body value must become a 422, never an unhandled 500.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return str(value)
    raise UserError(f"{field} must be a string")


def list_users(conn: sqlite3.Connection, account_id: int) -> list[dict]:
    """اسرد مستخدمي الحساب — this account's users only (hash never included)."""
    rows = conn.execute(
        "SELECT * FROM users WHERE account_id = ? ORDER BY id",
        (account_id,)).fetchall()
    return [_public(r) for r in rows]


def get_user(conn: sqlite3.Connection, account_id: int,
             user_id: int) -> dict | None:
    """اقرأ مستخدماً ضمن الحساب — None if it belongs to another account."""
    row = conn.execute(
        "SELECT * FROM users WHERE id = ? AND account_id = ?",
        (user_id, account_id)).fetchone()
    return _public(row) if row else None


def exists_anywhere(conn: sqlite3.Connection, user_id: int) -> bool:
    """هل المعرّف موجود لأي حساب؟ — lets the endpoint audit a cross-tenant try.

    الردّ للعميل يبقى 404 في الحالتين (لا تسريب وجود) — نفس دلالة
    `repository.TenantRepository.exists_anywhere`.
    """
    row = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
    return row is not None


def account_tier(conn: sqlite3.Connection, account_id: int) -> str:
    """طبقة الحساب — read the tier the seat gate is measured against."""
    row = conn.execute("SELECT tier FROM accounts WHERE id = ?",
                       (account_id,)).fetchone()
    if row is None:
        raise UserError(f"no account {account_id}")
    return str(row["tier"])


def create_sub_user(conn: sqlite3.Connection, account_id: int, *,
                    email: str, password: str, first_name: str | None = None,
                    last_name: str | None = None,
                    language_preference: str = "en") -> dict:
    """أنشئ مستخدماً فرعياً في الحساب — seat-gated; role is forced to 'factory'.

    يرفع `entitlements.TierGateError` عند امتلاء المقاعد (تترجمها النقطة إلى 403
    مع دعوة ترقية)، و`passwords.PasswordError` عند كلمة مرور ضعيفة (422)،
    و`DuplicateEmail` عند بريد مستخدَم (409).

    الحجز والإدراج داخل `BEGIN IMMEDIATE` واحد: فحصُ المقعد ثم الإدراج على
    اتصالين متزامنين كان يسمح لطلبين متوازيين بأخذ المقعد الأخير كليهما
    (TOCTOU) — نفس عائلة الخلل التي أُغلقت في `quota.reserve_launch`.
    The seat check and the insert share one immediate transaction, so two
    concurrent creates cannot both take the last seat.
    """
    email_norm = (_as_text(email, "email") or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise UserError("a valid email is required")
    first_name = _as_text(first_name, "first_name")
    last_name = _as_text(last_name, "last_name")
    if not isinstance(password, str):
        raise UserError("password must be a string")
    if (_as_text(language_preference, "language_preference") or "en") not in ("ar", "en"):
        raise UserError("language_preference must be 'ar' or 'en'")
    # السياسة والتجزئة **قبل** المعاملة: bcrypt بطيء بقصد، وحملُه داخل معاملة
    # كتابة فورية يُقفل الكاتب الوحيد ~250ms لكل إنشاء.
    passwords.validate_policy(password or "")     # raises PasswordError
    pw_hash = passwords.hash_password(password, enforce_policy=False)
    tier = account_tier(conn, account_id)

    conn.commit()                 # اطوِ أي معلّق قبل BEGIN الصريح
    conn.execute("BEGIN IMMEDIATE")
    try:
        entitlements.require_seat(conn, account_id, tier)   # raises TierGateError
        now = now_iso()
        try:
            cur = conn.execute(
                "INSERT INTO users (account_id, email, password_hash, role, "
                "first_name, last_name, language_preference, is_active, "
                "created_at, updated_at) VALUES (?,?,?,'factory',?,?,?,1,?,?)",
                (account_id, email_norm, pw_hash, first_name, last_name,
                 language_preference or "en", now, now))
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateEmail("email is already registered") from exc
        new_id = int(cur.lastrowid)
        conn.commit()
    except Exception:
        # `conn.rollback()` آمنٌ حتى لو كانت المعاملة قد أُنهيت سلفاً.
        conn.rollback()
        raise
    return get_user(conn, account_id, new_id)   # type: ignore[return-value]


def update_profile(conn: sqlite3.Connection, account_id: int, user_id: int,
                   fields: dict) -> dict | None:
    """حدّث حقول العرض لمستخدم ضمن الحساب — None if the row is another tenant's.

    الحقول المسموحة ثلاثة فقط (`_PROFILE_FIELDS`): الدور والتنشيط والبريد
    وكلمة المرور مسارات صلاحية/هوية لها نقاطها الخاصّة، فإدراجها في تحديث عامّ
    يفتح باباً ثانياً يتخطّى فحوصها. Profile-only; privilege fields excluded.
    """
    clean = {k: _as_text(v, k) for k, v in fields.items() if k in _PROFILE_FIELDS}
    if "language_preference" in clean and clean["language_preference"] not in ("ar", "en"):
        raise UserError("language_preference must be 'ar' or 'en'")
    if not clean:
        return get_user(conn, account_id, user_id)
    clean["updated_at"] = now_iso()
    sets = ", ".join(f"{c} = ?" for c in clean)
    cur = conn.execute(
        f"UPDATE users SET {sets} WHERE id = ? AND account_id = ?",
        [*clean.values(), user_id, account_id])
    conn.commit()
    if cur.rowcount == 0:
        return None
    return get_user(conn, account_id, user_id)


def set_active(conn: sqlite3.Connection, account_id: int, user_id: int,
               active: bool, *, acting_user_id: int | None = None) -> dict | None:
    """عطّل أو أعِد تنشيط مستخدماً ضمن الحساب — seat-gated on activation.

    - التعطيل يُخلي مقعداً فوراً.
    - إعادة التنشيط تستهلك مقعداً، فتمرّ من نفس بوّابة `require_seat`؛ وإلا لكان
      التعطيل ثم التنشيط طريقاً لتجاوز السقف.
    - **لا يعطّل المرء نفسه**: القفل الذاتي الفوري، وقد يترك الحساب بلا مستخدم
      نشط واحد فيصير غير قابل للدخول أبداً (الجلسة تُرفَض لمستخدم معطَّل).
      Self-deactivation is refused — it locks the actor out instantly and can
      leave the account with zero active users.
    """
    if not active and acting_user_id is not None and int(acting_user_id) == int(user_id):
        raise UserError("you cannot deactivate your own account")
    if get_user(conn, account_id, user_id) is None:
        return None

    # التنشيط يستهلك مقعداً، فيجب أن يكون **ذرّياً** كالإنشاء: فحصُ المقعد ثم
    # التحديث على اتصالين متزامنين كان يسمح لتنشيطَين متوازيين بأخذ المقعد
    # الأخير كليهما فيتجاوز الحساب سقفه — وهو بالضبط التجاوز الذي وُجدت بوّابة
    # إعادة التنشيط لمنعه (نفس عائلة TOCTOU المُغلقة في `quota.reserve_launch`
    # و`create_sub_user`). القفل الفوري + شرط `is_active = 0` في التحديث يجعلان
    # التنشيط الثاني يخسر بلا أثر.
    # The activation path must be atomic — a concurrent pair would otherwise both
    # claim the last seat, defeating the very gate that re-activation exists for.
    conn.commit()                          # اطوِ المعلّق قبل BEGIN الصريح
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = get_user(conn, account_id, user_id)
        if current is None:                # حُذف بيننا · vanished mid-transaction
            conn.rollback()
            return None
        if bool(current["is_active"]) == bool(active):
            conn.rollback()
            return current                 # لا تغيير · already in that state
        if active:
            entitlements.require_seat(conn, account_id,
                                      account_tier(conn, account_id))
        cur = conn.execute(
            "UPDATE users SET is_active = ?, updated_at = ? "
            "WHERE id = ? AND account_id = ? AND is_active = ?",
            (1 if active else 0, now_iso(), user_id, account_id,
             0 if active else 1))
        if cur.rowcount == 0:              # سبقنا غيرُنا · another writer won
            conn.rollback()
            return get_user(conn, account_id, user_id)
        if not active:
            # جلسات المستخدم المعطَّل تُحذف فوراً: `resolve_session` يرفض المعطَّل
            # أصلاً، لكن ترك الصفوف يبقي رموزاً صالحة الشكل حتى انتهائها.
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return get_user(conn, account_id, user_id)
