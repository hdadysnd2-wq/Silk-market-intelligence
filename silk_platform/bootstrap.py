"""تأسيس المنصّة عند الإقلاع — opt-in seeding + readiness for /health.

**الفجوة التي يُغلقها (بلاغ مالك حيّ):** الشاشة شُحنت وعملت، ثم «ما يعمل» —
والسبب أن `init_db()` يُنشئ **الجداول فقط**، و`seed()` لا يناديه شيءٌ عند
الإقلاع (فقط `python3 -m silk_platform.seed` يدوياً أو الاختبارات). فقاعدة
الإنتاج تُقلِع بجداولَ سليمة و**صفر مستخدمين** — فشاشة الدخول ترفض كل شيء، ولا
رسالةَ خطأٍ تقول «لا حساب أصلاً» بل «بيانات غير صحيحة». تشخيصٌ مستحيل عن بُعد.

**القرار:** لا بذر تلقائي بلا إذن صريح، ولا كلمة مرور مولَّدة تضيع.

- `SILK_SEED_ADMIN_PASSWORD` غير مضبوط ⇒ **لا بذر إطلاقاً**، وسطرُ سجلٍّ يشرح
  السبب وما يلزم. (بذرٌ صامت بكلمة عشوائية = حسابٌ لا يملك أحدٌ مفتاحه.)
- مضبوط والقاعدة غير مبذورة ⇒ يُبذَر مرّة، ويُسجَّل **مَن أُنشئ** و**أيُّ هويّة
  تملك كلمة صريحة** — بلا طبع أي كلمة مرور في السجلّ أبداً.
- مضبوط والقاعدة مبذورة ⇒ لا شيء (`seed()` خامل التكرار).

`readiness()` يجيب السؤال الذي كان يحتاج وسيطاً: هل هي مبذورة؟ كم مستخدماً؟
أين ملفّها؟ فيراه المالك في `/health` بلا أن يسأل أحداً.

Production booted with correct tables and ZERO users, so login rejected
everything with "invalid credentials" — indistinguishable from a wrong password.
Seeding is opt-in via an explicit password env var; never a generated one.
"""
from __future__ import annotations

import logging
import os
import sqlite3

log = logging.getLogger(__name__)

# الهويّات التي يُنشئها البذر ومتغيّر كلمة مرور كلٍّ منها.
_IDENTITY_ENV = {
    "admin": "SILK_SEED_ADMIN_PASSWORD",
    "analyst": "SILK_SEED_ANALYST_PASSWORD",
    "factory_a": "SILK_SEED_FACTORY_A_PASSWORD",
    "factory_b": "SILK_SEED_FACTORY_B_PASSWORD",
}
# البوّابة: بلا هذه لا بذر. الأدمِن هو الهويّة التي تستطيع إصدار إعادة تعيين
# لغيرها، فوجود كلمتها الصريحة يكفي لاستعادة الباقي.
GATE_ENV = _IDENTITY_ENV["admin"]


def _explicit(name: str) -> bool:
    return bool(os.environ.get(_IDENTITY_ENV[name], "").strip())


def seed_problem() -> str | None:
    """أوّل كلمة بذر مضبوطة تخالف السياسة — **باسم المتغيّر، لا قيمته**.

    **البلاغ الحيّ الذي أنتج هذه الدالّة:** المالك ضبط البوّابة ثم ظلّ الدخول
    يُرفَض بـ«invalid credentials». والسبب أن كلمته خالفت السياسة، فرفع
    `passwords.hash_password` استثناءً، فابتلعه الإقلاع (صواباً — لا يُسقِط
    الخدمة)، فبقيت القاعدة بصفر مستخدمين. و`readiness()` قال `seeded:false` مع
    `seed_gate_set:true` **بلا سبب**، فالسبب كان في سجلّ النشر وحده. لا يجوز
    إجبار المالك على قراءة السجلّات لمعرفة أنّ كلمته تحتاج حرفاً كبيراً.

    ملاحظتان على السلامة:
    - رسائل `validate_policy` نصوصٌ ثابتة (قواعد السياسة) لا تحوي كلمة المرور
      أبداً، فإظهارها في `/health` العامّة يسمّي **المتغيّر والقاعدة** فقط.
    - البوّابة غير المضبوطة تُبلَّغ بحقلها `seed_gate_set`، فلا نُزاحمها بسبب
      ثانٍ لا أثر له (لا بذر أصلاً بلا بوّابة).

    Names the offending VARIABLE and the violated rule — never the value.
    """
    if not os.environ.get(GATE_ENV, "").strip():
        return None
    from . import passwords
    for _kind, env in sorted(_IDENTITY_ENV.items()):
        raw = os.environ.get(env, "").strip()
        if not raw:
            continue                      # غير مضبوطة ⇒ تُولَّد عشوائياً
        try:
            passwords.validate_policy(raw)
        except passwords.PasswordError as exc:
            return f"{env}: {exc}"
    return None


def is_seeded(conn: sqlite3.Connection) -> bool:
    """هل توجد هويّة أدمِن؟ — same predicate `seed()` uses for idempotency."""
    row = conn.execute(
        "SELECT 1 FROM users WHERE role = 'silk_admin' LIMIT 1").fetchone()
    return row is not None


def readiness(conn: sqlite3.Connection) -> dict:
    """جهوزيّة المنصّة لِـ/health — أرقامٌ فقط، **بلا بريد ولا كلمة مرور**.

    عدد المستخدمين/الحسابات يكفي للتشخيص («صفر ⇒ لم تُبذَر») ولا يكشف هويّات:
    نقطةُ `/health` عامّة، وإدراج البُرد فيها تسريبٌ لا داعي له.
    Counts only — /health is public; identities would be a needless leak.
    """
    def _count(sql: str) -> int:
        try:
            return int(conn.execute(sql).fetchone()[0])
        except sqlite3.Error:
            return -1        # جدول غائب/قاعدة غير مهيّأة · schema not applied
    users = _count("SELECT COUNT(*) FROM users")
    seeded = users > 0 and is_seeded(conn)
    return {
        "seeded": seeded,
        "users": users,
        "accounts": _count("SELECT COUNT(*) FROM accounts"),
        # هل البوّابة مضبوطة؟ (لا قيمتها) — يفسّر «لماذا لم يُبذَر».
        "seed_gate_set": bool(os.environ.get(GATE_ENV, "").strip()),
        # ولماذا لم يُبذَر **والبوّابة مضبوطة**: اسم المتغيّر المخالف + القاعدة.
        # لا معنى له بعد نجاح البذر، فيُصفَّر حينها.
        "seed_error": None if seeded else seed_problem(),
    }


def maybe_seed(conn: sqlite3.Connection) -> dict:
    """ابذر مرّة إن أُذِن — returns a summary; never raises, never logs secrets.

    آمنٌ للتزامن: عمّالُ uvicorn المتعدّدون قد يُقلِعوا معاً، فالبذر داخل
    `BEGIN IMMEDIATE` وأي `IntegrityError` (الخاسر على فهرس الخزنة الفريد)
    يُقرأ «بُذِرت سلفاً» لا خطأً.

    **تصريح دقيق:** الطبقتان أعلاه **دفاعٌ زائد**، لا الحاجزُ الوحيد — `seed()`
    نفسه خاملُ التكرار (يفحص وجود `silk_admin` ويعود مبكراً). فحُذِفت كلٌّ منهما
    تجريبياً وبقي اختبارُ التزامن أخضر، فلا يعزلهما اختبار. تُبقيان لأن كلفتهما
    صفر ولأنهما تحفظان الناتج لو فقد `seed()` فحصه يوماً — لا لأنهما مُثبَتتان.
    Both layers are redundant defense over seed()'s own idempotency; no test
    isolates them, and this docstring says so rather than implying otherwise.
    """
    if not os.environ.get(GATE_ENV, "").strip():
        log.info(
            "silk_platform: not seeding — %s is unset. The platform DB has "
            "tables but no users, so every login will be rejected. Set %s "
            "(and optionally %s) then redeploy to create the accounts.",
            GATE_ENV, GATE_ENV, _IDENTITY_ENV["factory_a"])
        return {"seeded": False, "reason": "gate_unset", "gate": GATE_ENV}

    # ارفض **قبل** المحاولة، وسمِّ المتغيّر. `seed()` يُلبِّد الكلمات الأربع
    # مسبقاً قبل إدخال أيّ صفّ، فمخالفةٌ في هويّة اختياريّة تمنع إنشاء الأدمِن
    # أيضاً؛ أُبقي هذا الرفضَ الشامل (تجاهلُ ما ضبطه المالك صراحةً أسوأ) وأُصلِح
    # تشخيصه: «فشل» بلا اسم متغيّر كان يُلام فيه الأدمِن ظلماً.
    problem = seed_problem()
    if problem:
        log.error(
            "silk_platform: not seeding — %s. No account was created, so every "
            "login will be rejected as 'invalid credentials'. The policy is: at "
            "least 8 characters including a lowercase letter, an uppercase "
            "letter and a digit. Fix that variable and redeploy.", problem)
        return {"seeded": False, "reason": "password_policy", "detail": problem}

    try:
        if is_seeded(conn):
            return {"seeded": False, "reason": "already_seeded"}
    except sqlite3.Error as exc:      # الجداول غير مهيّأة بعد
        log.warning("silk_platform: readiness probe failed: %s", exc)
        return {"seeded": False, "reason": "schema_unavailable"}

    from . import seed as seed_mod
    try:
        conn.commit()                 # اطوِ المعلّق قبل BEGIN الصريح
        conn.execute("BEGIN IMMEDIATE")
        try:
            if is_seeded(conn):       # سبقنا عاملٌ آخر داخل القفل
                conn.rollback()
                return {"seeded": False, "reason": "already_seeded"}
            result = seed_mod.seed(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except sqlite3.IntegrityError:
        # الخاسر في السباق على `ux_accounts_vault` — ليس خطأً.
        return {"seeded": False, "reason": "already_seeded"}
    except Exception as exc:  # noqa: BLE001 — لا يُسقِط الإقلاع أبداً
        log.warning("silk_platform: bootstrap seeding failed: %s", exc)
        return {"seeded": False, "reason": f"failed: {exc}"}

    if not result.get("seeded"):
        return {"seeded": False, "reason": result.get("reason", "unknown")}

    explicit = sorted(k for k in _IDENTITY_ENV if _explicit(k))
    generated = sorted(k for k in _IDENTITY_ENV if not _explicit(k))
    # **لا كلمة مرور في السجلّ** — الأسماء والحالة فقط.
    log.info("silk_platform: seeded accounts. Explicit-password identities: %s. "
             "Random (unrecoverable) passwords: %s — an admin can issue a reset "
             "for those via POST /platform/admin/users/{id}/reset.",
             explicit or "none", generated or "none")
    emails = {k: v.get("email") for k, v in result.items()
              if isinstance(v, dict) and v.get("email")}
    return {"seeded": True, "explicit_password": explicit,
            "generated_password": generated, "emails": emails}
