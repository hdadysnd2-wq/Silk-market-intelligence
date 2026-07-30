"""تجزئة كلمات المرور — password hashing (bcrypt-preferred, scrypt fallback).

المواصفة تطلب bcrypt بعامل عمل 12. نستخدمه حين يكون مثبَّتاً، ونرجع إلى
`hashlib.scrypt` (مكتبة قياسية، صعب الذاكرة) حين يغيب — فتبقى الحزمة الهرمتية
خضراء بلا اعتمادية غير مثبّتة. الهاش يحمل بادئته المميِّزة (`$2b$` أو `$scrypt$`)
فيوجّه التحقّق نفسه. لا نصّ صريح يُخزَّن أو يُسجَّل أبداً.

Prefer bcrypt(cost=12) per spec; fall back to stdlib scrypt so the hermetic
suite needs no unpinned dependency. Never store or log plaintext.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

_BCRYPT_ROUNDS = 12          # الإنتاج · production work factor (spec §11)
_BCRYPT_MIN_PRODUCTION = 12  # أقلّ عامل مقبول في الإنتاج · prod floor
_SCRYPT_N = 2 ** 14   # عامل تكلفة scrypt (16384) — CPU/memory hardness
_SCRYPT_R = 8
_SCRYPT_P = 1


def bcrypt_rounds() -> int:
    """عامل عمل bcrypt — 12 افتراضاً، ويُخفَّض **للاختبارات فقط** بالبيئة.

    السبب المقيس: تجزئة واحدة بعامل ١٢ تكلف ~٢٧٧ms وتحقّقٌ واحد ~٢٧٣ms، فحزمة
    الاختبارات (مئات العمليات) قفزت من ~٢٥ث إلى ~١٣٣ث — اتجاه غير مستدام مع نمو
    الاختبارات. التخفيض في الاختبارات لا يُضعف الإنتاج بثلاث حمايات:
      ١) غياب المتغيّر ⇒ ١٢ (الافتراضي هو الإنتاج، لا العكس).
      ٢) قيمة تالفة/خارج المدى ⇒ ١٢ (خطأ مطبعي لا يُخفّض العامل صمتاً).
      ٣) `boot_config_guard` **يرفض الإقلاع** بعاملٍ أقلّ من ١٢ مع أي إشارة إنتاج.
    Test-only reduction; production cannot be weakened (default, clamp, boot guard).
    """
    raw = os.environ.get("SILK_PLATFORM_BCRYPT_ROUNDS", "").strip()
    if not raw:
        return _BCRYPT_ROUNDS
    try:
        rounds = int(raw)
    except ValueError:
        return _BCRYPT_ROUNDS
    if not 4 <= rounds <= 31:          # مدى bcrypt الصالح · valid bcrypt range
        return _BCRYPT_ROUNDS
    return rounds


def scrypt_n() -> int:
    """عامل تكلفة scrypt (المسار الاحتياطي) — env-tunable for tests; default 2^14.

    يجب أن يكون قوّةً للعدد ٢؛ أي قيمة أخرى تُتجاهَل ويُستخدَم الافتراضي.
    """
    raw = os.environ.get("SILK_PLATFORM_SCRYPT_N", "").strip()
    if not raw:
        return _SCRYPT_N
    try:
        n = int(raw)
    except ValueError:
        return _SCRYPT_N
    if n < 2 or (n & (n - 1)) != 0:     # ليست قوّةً للعدد ٢ · not a power of two
        return _SCRYPT_N
    return n

try:  # اختياري: bcrypt الحقيقي حين يتوفّر · optional real bcrypt
    import bcrypt as _bcrypt
except BaseException:  # noqa: BLE001 — الغياب متوقّع؛ وBaseException كي لا ينهار
    # الاستيراد لو كانت خلفية bcrypt الـcffi مكسورة (PanicException كـcryptography).
    _bcrypt = None


class PasswordError(ValueError):
    """كلمة مرور تخالف السياسة — password violates the policy."""


MAX_PASSWORD_BYTES = 4096   # حدّ عِلوي للمدخل (منع إنفاق CPU على مدخل ضخم)


def _bcrypt_input(password: str) -> bytes:
    """مدخل bcrypt بعد تلخيص SHA-256 — pre-hash so nothing is silently truncated.

    bcrypt يتجاهل ما بعد ٧٢ بايت **صمتاً**: بلا هذا التلخيص تُصادِق عبارةُ مرور
    طويلة على أوّل ٧٢ بايت منها فقط، وتختلف مساحة كلمات المرور الفعلية بين
    الخلفيتين (scrypt لا يقتطع). التلخيص يعطي ٤٤ بايت base64 ثابتة فتمرّ كامل
    الإنتروبيا. Pre-hash to base64(sha256) — no silent 72-byte truncation.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def validate_policy(password: str) -> None:
    """افرض سياسة كلمة المرور — min 8 chars, mixed case + a digit.

    يرفع `PasswordError` بسبب واضح؛ لا يُعيد شيئاً عند النجاح.
    """
    if not isinstance(password, str) or len(password) < 8:
        raise PasswordError("password must be at least 8 characters")
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise PasswordError(
            f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    if not re.search(r"[a-z]", password):
        raise PasswordError("password must contain a lowercase letter")
    if not re.search(r"[A-Z]", password):
        raise PasswordError("password must contain an uppercase letter")
    if not re.search(r"[0-9]", password):
        raise PasswordError("password must contain a digit")


def hash_password(password: str, *, enforce_policy: bool = True) -> str:
    """جزّئ كلمة المرور — return a self-identifying hash string.

    الافتراضي يفرض السياسة (يُعطَّل فقط لبذر ثابت داخلي). يُفضّل bcrypt.
    """
    if enforce_policy:
        validate_policy(password)
    if _bcrypt is not None:
        salt = _bcrypt.gensalt(rounds=bcrypt_rounds())
        return _bcrypt.hashpw(_bcrypt_input(password), salt).decode("ascii")
    # بديل قياسي · stdlib scrypt fallback — "$scrypt$N$r$p$salt$dk"
    # العامل مضمَّن في السلسلة فيبقى كل هاش قديم قابلاً للتحقّق بعد أي تغيير.
    n = scrypt_n()
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=n, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"$scrypt${n}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """تحقّق بأمان زمني ثابت — constant-time verify against a stored hash.

    يوجّه بحسب بادئة الهاش المخزَّن؛ لا يرمي أبداً (مدخل تالف => False).
    """
    if not stored:
        return False
    try:
        if stored.startswith("$2"):   # bcrypt ($2a$/$2b$/$2y$)
            if _bcrypt is None:
                return False
            return _bcrypt.checkpw(_bcrypt_input(password), stored.encode("ascii"))
        if stored.startswith("$scrypt$"):
            _, _tag, n, r, p, salt_b64, dk_b64 = stored.split("$")
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(dk_b64)
            dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
            return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001 — أي تلف في الهاش = فشل تحقّق، لا انهيار
        return False
    return False
