"""ناقل SMTP حقيقي — the real SMTP transport (PR-5; stdlib `smtplib` only).

`send()` لا تعرف شيئاً عن قاعدة المنصّة أو التعمية عمداً — تأخذ بيانات اعتماد
**نصّية صريحة** ومظروف رسالة، وتُرسِل. فكّ التعمية مسؤولية المُنادي
(`email_queue.sender`)، فيبقى هذا الملف قابلاً لاختبار بناء الرسالة وحده
بلا لمس `crypto.py` أو القاعدة. `smtp_cls` مُدخَل حقناً — الإنتاج يمرّر
`smtplib.SMTP` (الافتراضي)، والاختبارات الهرمتية تمرّر بديلاً مزيَّفاً فلا
تُفتَح أي مقبس شبكة حقيقي أثناء `pytest`.

`send()` knows nothing about the platform DB or encryption — it takes plain
credentials and an envelope, and sends. Decryption is the caller's job
(`email_queue.sender`). `smtp_cls` is injected: production passes the real
`smtplib.SMTP`; hermetic tests pass a fake, so no socket ever opens in CI.
"""
from __future__ import annotations

import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr


def operator_config_from_env() -> dict | None:
    """تهيئة SMTP التشغيلية من البيئة — env-configured transactional SMTP.

    منفصلة عمداً عن `smtp_configs` المستأجَرة المشفَّرة في القاعدة: بريد
    إعادة تعيين كلمة المرور يصدر **قبل** أي جلسة مُصادَقة، فلا تهيئة مستأجَر
    تصلح مصدراً له. `None` إن لم يُضبَط الحدّ الأدنى (مضيف + مرسل) — نصف
    تهيئة كان سيُرسِل بلا مضيف فيفشل بعطلٍ غامض بدل التصريح الواضح بالغياب.
    Deliberately separate from tenant-owned `smtp_configs`: a password-reset
    email fires before any authenticated session exists. `None` when the
    minimum (host + from-address) isn't set — declared absence, not a vague
    connection failure.
    """
    host = os.environ.get("SILK_PLATFORM_OPERATOR_SMTP_HOST", "").strip()
    from_email = os.environ.get("SILK_PLATFORM_OPERATOR_SMTP_FROM_EMAIL", "").strip()
    if not host or not from_email:
        return None
    try:
        port = int(os.environ.get("SILK_PLATFORM_OPERATOR_SMTP_PORT", "587") or 587)
    except ValueError:
        port = 587
    return {
        "host": host, "port": port,
        "use_tls": os.environ.get("SILK_PLATFORM_OPERATOR_SMTP_USE_TLS", "1") == "1",
        "username": os.environ.get("SILK_PLATFORM_OPERATOR_SMTP_USERNAME", ""),
        "password": os.environ.get("SILK_PLATFORM_OPERATOR_SMTP_PASSWORD", ""),
        "from_email": from_email,
        "from_name": os.environ.get("SILK_PLATFORM_OPERATOR_SMTP_FROM_NAME", "Silk"),
    }


def message_id(kind: str, row_id: int, domain: str = "silk-platform.local") -> str:
    """مُعرِّف رسالة حتمي — deterministic Message-ID, doubles as the resend key.

    مُشتقّ من (kind, row_id) لا عشوائي: إعادة معالجة نفس الصفّ (الحاصد بعد
    عطل) تنتج **نفس** المعرّف، فخوادم البريد التي تُميّز بـMessage-ID تعامل
    المحاولتين كرسالة واحدة بدل تكرارٍ فعلي — هذا هو مفتاح idempotency
    المطلوب؛ SMTP نفسه بلا آلية تسليم-مرّة-واحدة أصيلة.
    Deterministic, not random: a re-send of the same row (reaper after a
    crash) reproduces the identical Message-ID, so downstream MTAs that
    dedupe by it treat the retry as the same message — the idempotency key
    the roadmap calls for, since raw SMTP has no native exactly-once delivery.
    """
    return f"<platform-{kind}-{row_id}@{domain}>"


def _reject_header_injection(value: str, field: str) -> None:
    """ارفض حقن ترويسة — reject embedded CR/LF before it reaches a header.

    لا شيء في المنصّة يتحقّق من شكل بريد العميل (`prospects.email` يُقبَل كما
    هو منذ PR-1)؛ هذا الملف هو أوّل مكان يضع تلك القيمة في ترويسة SMTP حقيقية،
    فهو نقطة الفرض الصحيحة. بلا هذا، بريدٌ محفوظ كـ`"x@y.com\\r\\nBcc: z@evil"`
    يُدرِج مستلمين/ترويسات إضافية عبر `send_message`'s header-derived envelope.
    No layer above this ever validated email *shape*; this is the first place
    that value reaches a real SMTP header, so it's the correct enforcement
    point — otherwise a stored `"x@y.com\\r\\nBcc: z@evil"` injects extra
    recipients/headers via `send_message`'s header-derived envelope.
    """
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} contains illegal control characters")


def send(*, host: str, port: int, use_tls: bool, username: str, password: str,
         from_email: str, from_name: str, to_email: str, subject: str,
         body: str, msg_id: str, smtp_cls=None, timeout: float = 30.0) -> None:
    """أرسل رسالة نصّية واحدة — build a UTF-8 MIME message and send it.

    ترفع أي عطل نقل كما هو (لا تبتلعه) — `email_queue.process_queue` هو من
    يُسجِّل الفشل ويُعلمه؛ الابتلاع هنا كان سيُخفي عطلاً حقيقياً عن الطابور.
    Propagates transport failures — the caller (process_queue) is the single
    place that records/declares them; swallowing here would hide a real fault.
    """
    for value, field in ((to_email, "to_email"), (from_email, "from_email"),
                        (from_name or "", "from_name"), (subject or "", "subject")):
        _reject_header_injection(value, field)
    smtp_cls = smtp_cls or smtplib.SMTP
    msg = MIMEText(body or "", "plain", "utf-8")
    msg["Subject"] = str(Header(subject or "", "utf-8"))
    msg["From"] = formataddr((str(Header(from_name or "", "utf-8")), from_email))
    msg["To"] = to_email
    msg["Message-ID"] = msg_id

    client = smtp_cls(host, int(port), timeout=timeout)
    try:
        client.ehlo()
        if use_tls:
            client.starttls()
            client.ehlo()
        if username:
            client.login(username, password)
        client.send_message(msg)
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001 — فشل الإغلاق لا يُخفي فشل الإرسال
            pass
