"""الرموز والتواقيع — opaque tokens + HMAC-signed payloads (stdlib only).

- رموز الجلسة/إعادة التعيين: عشوائية آمنة (`secrets`)، تُخزَّن مجزّأة (sha256)،
  ويُرجَّع الخام مرّة واحدة فقط.
- توقيع HMAC: لروابط إلغاء الاشتراك الموقّعة (PR-5) — يُبنى الآن كي تنزلق
  الموجة اللاحقة بلا إعادة عمل.

Secret comes from `SILK_PLATFORM_SECRET` (env). In dev, an ephemeral per-process
secret is generated so nothing breaks offline — signatures simply don't persist
across restarts, which is fine for hermetic tests.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

_EPHEMERAL_SECRET = secrets.token_hex(32)


def _secret() -> bytes:
    """السرّ الخادمي للتوقيع/التجزئة — server signing secret (env or ephemeral)."""
    return (os.environ.get("SILK_PLATFORM_SECRET", "").strip()
            or _EPHEMERAL_SECRET).encode("utf-8")


def new_token(nbytes: int = 32) -> str:
    """رمز خام آمن يُعرض مرّة واحدة — a fresh URL-safe random token."""
    return secrets.token_urlsafe(nbytes)


def hash_token(raw: str) -> str:
    """جزّئ رمزاً للتخزين — sha256 hex; the raw token is never persisted."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    """مقارنة زمنية ثابتة — timing-safe string compare."""
    return hmac.compare_digest(a, b)


def sign(payload: str) -> str:
    """وقّع حمولة نصّية — HMAC-SHA256 hex signature over `payload`."""
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(payload: str, signature: str) -> bool:
    """تحقّق من توقيع حمولة — constant-time HMAC verification."""
    return hmac.compare_digest(sign(payload), signature or "")
