"""رابط إلغاء اشتراك موقَّع — signed unsubscribe links (PR-5; stdlib only).

الحمولة الموقَّعة `account_id:email` — لا رمز عشوائي مخزَّن، فلا حاجة لجدول
أو انتهاء صلاحية: أي رابط أُرسِل قط يبقى صالحاً وقابلاً للتحقّق بلا حالة على
الخادم، والتوقيع (`tokens.sign`) يمنع تزوير رابطٍ لحساب/بريد لم يُرسَل إليه.
The signed payload needs no server-side state or expiry — verification is a
pure HMAC check; the signature alone prevents forging a link for an
account/email pair that was never actually sent one.
"""
from __future__ import annotations

import os
import urllib.parse

from . import tokens


def _payload(account_id: int, email: str) -> str:
    return f"{int(account_id)}:{(email or '').strip().lower()}"


def base_url() -> str:
    """الأصل العام للمنصّة — public origin used to build absolute links.

    مصدر واحد لهذا المتغيّر البيئي (روابط إلغاء الاشتراك وروابط بريد إعادة
    تعيين كلمة المرور كلاهما يستعمله) كي لا يختلف الافتراض بين مكانين.
    Single resolver — reused by both unsubscribe links and password-reset
    email links, so the default can't drift between the two call sites.
    """
    return os.environ.get("SILK_PLATFORM_BASE_URL", "http://localhost:8000").rstrip("/")


def build_url(account_id: int, email: str) -> str:
    """ابنِ رابط إلغاء الاشتراك لهذا العميل — an absolute, signed unsubscribe URL."""
    sig = tokens.sign(_payload(account_id, email))
    return (f"{base_url()}/platform/unsubscribe?a={int(account_id)}"
            f"&e={urllib.parse.quote(email or '')}&s={sig}")


def verify(account_id: int, email: str, signature: str) -> bool:
    """تحقّق من توقيع رابط — constant-time; True only for an unaltered link."""
    return tokens.verify_signature(_payload(account_id, email), signature)
