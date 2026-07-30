"""تشفير الحقول الحسّاسة عند التخزين — field encryption at rest (stdlib).

بيانات اعتماد SMTP لا تُخزَّن نصّاً صريحاً أبداً. حين تتوفّر مكتبة
`cryptography` نستخدم Fernet؛ حين تغيب (CI الهرمتي) نرجع إلى تعمية مُصادَقة
مبنيّة على HMAC-SHA256 كتيّار مفاتيح (encrypt-then-MAC) — سرّية حقيقية مفتاحها
`SILK_PLATFORM_SECRET`، لا شيفرة XOR ساذجة. المفتاح يبقى في بيئة الخادم (KMS/Vault
في الإنتاج)، لا في القاعدة.

Prefer cryptography.Fernet; fall back to a stdlib HMAC-CTR authenticated stream
cipher. The key never lives in the database. Never stores plaintext.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct

from . import tokens

_PREFIX_FERNET = "f1:"
_PREFIX_STDLIB = "s1:"

try:
    from cryptography.fernet import Fernet as _Fernet  # type: ignore
except BaseException:  # noqa: BLE001 — غائبة في CI، وقد تكون مكسورة الخلفية (PanicException)
    # نلتقط BaseException عمداً: خلفية cryptography الصدئة قد ترفع
    # pyo3_runtime.PanicException المشتقّة من BaseException لا Exception، فلا
    # يكفي except Exception — والبديل القياسي يعمل بلا هذه المكتبة أصلاً.
    _Fernet = None


def _fernet_key() -> bytes:
    """اشتقّ مفتاح Fernet من السرّ — 32-byte urlsafe-base64 key from the secret.

    السرّ يُحلّ عبر `tokens.secret()` حصراً (مصدر واحد للحقيقة) — لا إعادة تنفيذ
    لقاعدة البيئة/الاحتياط هنا. Resolved through the single secret provider.
    """
    digest = hashlib.sha256(tokens.secret()).digest()
    return base64.urlsafe_b64encode(digest)


def _keystream(nonce: bytes, length: int) -> bytes:
    """تيّار مفاتيح HMAC في وضع العدّاد — HMAC-SHA256 keystream (CTR mode)."""
    key = tokens.secret()
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _mac(nonce: bytes, ciphertext: bytes) -> bytes:
    """وسم مُصادَقة على النصّ المعمّى — encrypt-then-MAC authentication tag."""
    return hmac.new(tokens.secret(), b"mac" + nonce + ciphertext,
                    hashlib.sha256).digest()


def encrypt(plaintext: str) -> str:
    """عمِّ نصّاً للتخزين — return an opaque, prefixed ciphertext string.

    فارغ يبقى فارغاً (لا سرّ لتعميته). يُفضّل Fernet حين يتوفّر.
    """
    if plaintext is None or plaintext == "":
        return ""
    data = plaintext.encode("utf-8")
    if _Fernet is not None:
        token = _Fernet(_fernet_key()).encrypt(data)
        return _PREFIX_FERNET + token.decode("ascii")
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _keystream(nonce, len(data))))
    tag = _mac(nonce, ct)
    blob = base64.b64encode(nonce + tag + ct).decode("ascii")
    return _PREFIX_STDLIB + blob


def decrypt(blob: str) -> str:
    """فكّ التعمية — recover plaintext; raises ValueError on tamper/format."""
    if blob is None or blob == "":
        return ""
    if blob.startswith(_PREFIX_FERNET):
        if _Fernet is None:
            raise ValueError("fernet ciphertext but cryptography is unavailable")
        return _Fernet(_fernet_key()).decrypt(
            blob[len(_PREFIX_FERNET):].encode("ascii")).decode("utf-8")
    if blob.startswith(_PREFIX_STDLIB):
        raw = base64.b64decode(blob[len(_PREFIX_STDLIB):])
        nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
        if not hmac.compare_digest(tag, _mac(nonce, ct)):
            raise ValueError("ciphertext authentication failed (tampered)")
        return bytes(a ^ b for a, b in zip(ct, _keystream(nonce, len(ct)))).decode("utf-8")
    raise ValueError("unrecognized ciphertext format")
