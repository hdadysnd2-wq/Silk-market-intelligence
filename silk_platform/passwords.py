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

_BCRYPT_ROUNDS = 12
_SCRYPT_N = 2 ** 14   # عامل تكلفة scrypt (16384) — CPU/memory hardness
_SCRYPT_R = 8
_SCRYPT_P = 1

try:  # اختياري: bcrypt الحقيقي حين يتوفّر · optional real bcrypt
    import bcrypt as _bcrypt
except Exception:  # noqa: BLE001 — الغياب متوقّع في CI الهرمتي
    _bcrypt = None


class PasswordError(ValueError):
    """كلمة مرور تخالف السياسة — password violates the policy."""


def validate_policy(password: str) -> None:
    """افرض سياسة كلمة المرور — min 8 chars, mixed case + a digit.

    يرفع `PasswordError` بسبب واضح؛ لا يُعيد شيئاً عند النجاح.
    """
    if not isinstance(password, str) or len(password) < 8:
        raise PasswordError("password must be at least 8 characters")
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
        salt = _bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
        return _bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")
    # بديل قياسي · stdlib scrypt fallback — "$scrypt$N$r$p$salt$dk"
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode("ascii")  # noqa: E731
    return f"$scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(dk)}"


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
            return _bcrypt.checkpw(password.encode("utf-8"), stored.encode("ascii"))
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
