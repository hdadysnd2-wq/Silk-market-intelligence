"""المصادقة والجلسات — login, sessions, password reset, role resolution.

- تسجيل الدخول: تحقّق ثابت الزمن (لا تعداد مستخدمين بالتوقيت) → جلسة برمز خام
  يُعرض مرّة واحدة، مُخزَّن مجزّأً.
- الجلسة: انتهاء بعدم النشاط 24 ساعة (نافذة منزلقة)؛ جلسات متزامنة مستقلّة.
- إعادة التعيين: رمز أحادي الاستخدام محدود الزمن.

Constant-time login (no user enumeration via timing); hashed session tokens;
sliding 24h inactivity expiry; single-use reset tokens.
"""
from __future__ import annotations

import datetime
import sqlite3

from . import passwords, tokens
from .db import now_iso
from .models import AuthContext, Role

SESSION_TTL_HOURS = 24
RESET_TTL_MINUTES = 30
# لا تكتب نافذة النشاط لكل طلب — الكتابة fsync على كل GET تتنافس مع الكاتب
# الوحيد في SQLite. نُحدّثها فقط بعد مضيّ هذه المدّة، ودلالة الـ24 ساعة سليمة
# لأن أي طلب داخل النافذة يمدّها. Coarse sliding: same 24h semantics, far fewer writes.
ACTIVITY_WRITE_GRANULARITY_S = 300

# هاش وهمي صالح لتشغيل تحقّق حين لا يوجد مستخدم — يوحّد زمن الردّ فيمنع تعداد
# المستخدمين عبر التوقيت. يُحسَب كسولاً مرّة واحدة: حسابه وقت الاستيراد كان
# يدفع ثمن bcrypt عامل ١٢ (~٢٥٠ms) في كل إقلاع وكل جلسة اختبار بلا فائدة.
# A valid dummy hash burned when the user is absent — computed lazily once.
_DUMMY_HASH_CACHE: str | None = None


def _dummy_hash() -> str:
    """هاش وهمي مُذكَّر — memoized dummy hash (no bcrypt cost at import time)."""
    global _DUMMY_HASH_CACHE
    if _DUMMY_HASH_CACHE is None:
        _DUMMY_HASH_CACHE = passwords.hash_password("Dummy-Password-0",
                                                    enforce_policy=False)
    return _DUMMY_HASH_CACHE


def _parse(ts: str) -> datetime.datetime:
    """حوّل طابعاً زمنياً مخزّناً إلى datetime واعٍ بالمنطقة — parse a stored stamp."""
    raw = ts.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ── تسجيل الدخول · login ─────────────────────────────────────────────────────
def authenticate(conn: sqlite3.Connection, email: str, password: str) -> dict | None:
    """تحقّق من بيانات الاعتماد بزمن ثابت — returns the user row or None.

    يُشغّل تحقّق تجزئة دائماً (حتى لو غاب المستخدم) فلا يتسرّب وجوده عبر التوقيت.
    Runs a hash verification unconditionally to equalize timing.
    """
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)
    ).fetchone()
    stored = row["password_hash"] if row else _dummy_hash()
    ok = passwords.verify_password(password or "", stored)
    if not row or not ok:
        return None
    if not row["is_active"]:
        return None
    return dict(row)


def create_session(conn: sqlite3.Connection, user_id: int, *,
                   ip_address: str | None = None,
                   user_agent: str | None = None) -> str:
    """أنشئ جلسة وأعِد الرمز الخام مرّة واحدة — create a session; return raw token.

    الخام يُعرض هنا فقط ولا يُخزَّن؛ القاعدة تحمل sha256 منه.
    """
    raw = tokens.new_token()
    now = _now()
    expires = now + datetime.timedelta(hours=SESSION_TTL_HOURS)
    conn.execute(
        "INSERT INTO sessions (user_id, token_hash, ip_address, user_agent, "
        "created_at, expires_at, last_activity_at) VALUES (?,?,?,?,?,?,?)",
        (user_id, tokens.hash_token(raw), ip_address, user_agent,
         _fmt(now), _fmt(expires), _fmt(now)))
    conn.commit()
    return raw


def _fmt(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_session(conn: sqlite3.Connection, raw_token: str) -> AuthContext | None:
    """حُلّ الجلسة إلى سياق مصادقة — validate a raw token into an AuthContext.

    يرفض الرمز الغائب/المزوَّر/المنتهي (401 في النقطة النهائية)، ويُجدّد نافذة
    عدم النشاط (last_activity_at + expires_at) عند كل طلب صالح.
    Rejects missing/tampered/expired tokens; slides the inactivity window.
    """
    if not raw_token:
        return None
    row = conn.execute(
        "SELECT s.*, u.account_id AS account_id, u.role AS role, u.email AS email, "
        "u.language_preference AS lang, u.is_active AS user_active, "
        "a.is_active AS account_active "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "JOIN accounts a ON a.id = u.account_id WHERE s.token_hash = ?",
        (tokens.hash_token(raw_token),)).fetchone()
    # ارفض جلسة مستخدم أو **حساب** معطّل — reject deactivated user OR account.
    if not row or not row["user_active"] or not row["account_active"]:
        return None
    now = _now()
    if _parse(row["expires_at"]) <= now:
        return None  # منتهية بعدم النشاط · expired
    # نافذة منزلقة خشِنة: كل طلب صالح يُمدّد الانتهاء ٢٤ ساعة، لكن الكتابة تحدث
    # فقط بعد مضيّ ACTIVITY_WRITE_GRANULARITY_S على آخر تحديث — فلا معاملة كتابة
    # (fsync) على كل GET تتنافس مع كاتب SQLite الوحيد. الدلالة محفوظة: أي طلب
    # داخل النافذة يمدّها، والفارق الأقصى بين المخزَّن والفعلي هو هذه الحبيبية.
    try:
        elapsed = (now - _parse(row["last_activity_at"])).total_seconds()
    except (TypeError, ValueError):
        elapsed = ACTIVITY_WRITE_GRANULARITY_S + 1  # طابع تالف ⇒ حدّثه
    if elapsed >= ACTIVITY_WRITE_GRANULARITY_S:
        conn.execute(
            "UPDATE sessions SET last_activity_at = ?, expires_at = ? WHERE id = ?",
            (_fmt(now), _fmt(now + datetime.timedelta(hours=SESSION_TTL_HOURS)),
             row["id"]))
        conn.commit()
    return AuthContext(
        user_id=row["user_id"], account_id=row["account_id"],
        role=Role(row["role"]), email=row["email"],
        language_preference=row["lang"] or "en", session_id=row["id"])


def destroy_session(conn: sqlite3.Connection, session_id: int) -> None:
    """أنهِ جلسة واحدة — logout: delete this session only (others stay live)."""
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()


# ── إعادة تعيين كلمة المرور · password reset (single-use, time-limited) ───────
def issue_reset_token_for_user(conn: sqlite3.Connection, user_id: int) -> str | None:
    """أصدر رمز إعادة تعيين لمستخدم بمعرّفه — returns raw token, None if no user.

    يستعمله مسار الأدمِن المساعد (POST /admin/users/{id}/reset) قبل تجهيز
    توصيل البريد في PR-5. نفس دلالات الأحادية والزمن. Admin-assisted stopgap.
    """
    row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return None
    raw = tokens.new_token()
    now = _now()
    conn.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, created_at, "
        "expires_at) VALUES (?,?,?,?)",
        (row["id"], tokens.hash_token(raw), _fmt(now),
         _fmt(now + datetime.timedelta(minutes=RESET_TTL_MINUTES))))
    conn.commit()
    return raw


def user_language_by_email(conn: sqlite3.Connection, email: str) -> str:
    """لغة المستخدم ببريده — resolves before any account context exists.

    نفس منطق `issue_reset_token`: البريد هويّة عالمية على مستوى المنصّة، فلا
    قيد حساب مُمكن هنا — الحساب نتيجةٌ لاحقة لا مدخلاً. `en` احتياطاً لبريدٍ
    غير موجود (المُنادي لا يستدعيها إلا بعد نجاح `issue_reset_token` أصلاً).
    """
    row = conn.execute("SELECT language_preference FROM users WHERE email = ?",
                       ((email or "").strip().lower(),)).fetchone()
    return row["language_preference"] if row else "en"


def issue_reset_token(conn: sqlite3.Connection, email: str) -> str | None:
    """أصدر رمز إعادة تعيين بالبريد — returns raw token, or None if no such user.

    النقطة النهائية لا تفصح عن وجود المستخدم؛ ترجع 200 دائماً بصرف النظر.
    """
    row = conn.execute("SELECT id FROM users WHERE email = ?",
                       ((email or "").strip().lower(),)).fetchone()
    if not row:
        return None
    return issue_reset_token_for_user(conn, int(row["id"]))


def consume_reset_token(conn: sqlite3.Connection, raw_token: str,
                        new_password: str) -> bool:
    """استهلك الرمز وعيّن كلمة مرور جديدة — single-use; returns success.

    يرفض الرمز المستعمَل أو المنتهي؛ يفرض سياسة كلمة المرور (يرفع
    PasswordError عند المخالفة)؛ يبطل كل جلسات المستخدم بعد التغيير.
    """
    if not raw_token:
        return False
    token_hash = tokens.hash_token(raw_token)
    row = conn.execute(
        "SELECT * FROM password_reset_tokens WHERE token_hash = ?",
        (token_hash,)).fetchone()
    if not row or row["used_at"]:
        return False
    if _parse(row["expires_at"]) <= _now():
        return False
    # السياسة والتجزئة **قبل** المطالبة بالرمز، كي لا تحرق كلمةٌ ضعيفة الرمز.
    passwords.validate_policy(new_password)  # raises PasswordError on violation
    new_hash = passwords.hash_password(new_password)
    # الأحادية تُفرَض ذرّياً في القاعدة لا بفحص بايثون: `AND used_at IS NULL`
    # + فحص rowcount داخل معاملة كتابة فورية، فتخسر المطالبة الثانية المتزامنة
    # ولا يُعاد استعمال رمز واحد مرّتين. Atomic single-use claim (guarded UPDATE).
    conn.commit()                      # اطوِ المعلّق قبل BEGIN الصريح
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE password_reset_tokens SET used_at = ? "
            "WHERE id = ? AND used_at IS NULL", (now_iso(), row["id"]))
        if cur.rowcount == 0:
            conn.rollback()            # سبقنا غيرُنا · another confirm won the race
            return False
        conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                     (new_hash, now_iso(), row["user_id"]))
        # إبطال الجلسات القائمة بعد تغيير كلمة المرور · invalidate live sessions.
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def cleanup_expired_sessions(conn: sqlite3.Connection) -> int:
    """احذف الجلسات المنتهية — daily job; returns rows removed."""
    cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_fmt(_now()),))
    conn.commit()
    return cur.rowcount
