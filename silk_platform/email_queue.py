"""طابور البريد وعامل الإرسال — email queue + worker (kill-switch aware).

خط الإرسال: اختر العملاء ← اختر المسودّة بلغة العميل ← عبّئ العناصر النائبة
({{first_name}}) ← صُفّ برسالة مع تهيئة SMTP للدراسة. العامل يفحص مفتاح القتل
**لكل بريد** وقت الإرسال؛ حين يكون مُفعَّلاً يتوقّف ويترك المصفوف (لا يُفقَد)،
ويُستأنف بالترتيب عند التعطيل. كل إرسال ناجح: قيد سجلّ موافقة + خصم دفتر واحد.

Naql (transport) is injected — PR-1 wires none, so tests pass a fake sender and
production wires a real SMTP sender in PR-5. Never sends silently.
"""
from __future__ import annotations

import re
import sqlite3

from . import settings, wallet
from .db import now_iso
from .models import Operation, PRICE_EMAIL_SENT_CENTS

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def interpolate(template: str, prospect: dict) -> str:
    """عبّئ العناصر النائبة — replace {{first_name}} etc. from prospect fields.

    مفتاح غير معروف يُترك كما هو (لا اختلاق قيمة). Unknown keys stay literal.
    """
    if not template:
        return template or ""

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        val = prospect.get(key)
        return str(val) if val not in (None, "") else m.group(0)

    return _PLACEHOLDER.sub(_sub, template)


def pick_content(draft: dict, language: str) -> tuple[str, str]:
    """اختر (الموضوع، النصّ) بلغة العميل — subject/body by prospect language.

    يرجع للإنجليزية عند غياب المحتوى العربي والعكس (لا حقل فارغ يُرسَل).
    """
    lang = "ar" if language == "ar" else "en"
    subject = draft.get(f"subject_{lang}") or draft.get(
        "subject_en" if lang == "ar" else "subject_ar") or ""
    body = draft.get(f"body_{lang}") or draft.get(
        "body_en" if lang == "ar" else "body_ar") or ""
    return subject, body


def enqueue(conn: sqlite3.Connection, *, account_id: int, study_id: int,
            prospect: dict, draft: dict, smtp_config_id: int | None,
            actor_user_id: int | None, commit: bool = True) -> int:
    """صُفّ بريداً واحداً لعميل — interpolate + queue one email; return its id.

    `commit=False` للصفّ الجماعي داخل معاملة المُنادي (إطلاق دراسة بآلاف
    المستلمين): التزامٌ لكل صفّ كان يعني fsync لكل مستلم.
    Pass commit=False to batch many rows inside the caller's transaction.
    """
    subject, body = pick_content(draft, prospect.get("language_preference", "en"))
    subject = interpolate(subject, prospect)
    body = interpolate(body, prospect)
    cur = conn.execute(
        "INSERT INTO email_queue (account_id, study_id, prospect_id, "
        "prospect_email, draft_id, smtp_config_id, subject, body, status, "
        "actor_user_id, queued_at) VALUES (?,?,?,?,?,?,?,?, 'queued', ?, ?)",
        (account_id, study_id, prospect.get("id"), prospect.get("email"),
         draft.get("id"), smtp_config_id, subject, body, actor_user_id, now_iso()))
    if commit:
        conn.commit()
    return int(cur.lastrowid)


def _null_sender(smtp_config: dict, email_row: dict) -> None:
    """ناقل غير مُهيَّأ — PR-1 wires no real SMTP transport (PR-5 does)."""
    raise RuntimeError("no SMTP transport configured (wired in PR-5)")


def _safe_error(exc: Exception) -> str:
    """نصّ خطأ مُنقّى للتخزين — redact secrets before persisting transport errors.

    `last_error` قد يُعرَض في لوحة المصنع/الأدمِن، وأخطاء SMTP الحقيقية (PR-5)
    تحمل المضيف واسم المستخدم وردّ الخادم. نمرّ بمُنقّي المشروع القائم كي لا
    يتسرّب سرّ في حقل تشخيصي. Reuses the repo's existing secret redactor.
    """
    text = str(exc)[:300]
    try:
        import silk_diagnostics
        return silk_diagnostics._redact(text)
    except Exception:  # noqa: BLE001 — المُنقّي أفضل جهد؛ لا يمنع تسجيل الخطأ
        return text


def _suppressed(conn: sqlite3.Connection, account_id: int, email: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM suppression_list WHERE account_id = ? AND email = ?",
        (account_id, email)).fetchone()
    return row is not None


def process_queue(conn: sqlite3.Connection, *, sender=None,
                  limit: int | None = None) -> dict:
    """عامل الطابور — process queued emails in id order (kill-switch per email).

    السلوك:
    - مفتاح القتل مُفعَّل → توقّف فوراً، اترك المصفوف كما هو (يُستأنف لاحقاً).
    - عميل مقموع (suppression) → علّمه suppressed، بلا إرسال ولا خصم.
    - رصيد غير كافٍ → علّمه failed، بلا إرسال.
    - إرسال ناجح → سجلّ موافقة + خصم دفتر واحد + status='sent'.

    يرجّع ملخّصاً {sent, suppressed, failed, halted_by_kill_switch, remaining}.
    """
    sender = sender or _null_sender
    # `LIMIT` في SQL لا في بايثون: بلا هذا يُحمَّل كل صفٍّ مصفوف (بنصّ الرسالة
    # كاملاً) إلى الذاكرة ثم يُعالَج جزء منه. Bound the fetch in SQL.
    sql = "SELECT * FROM email_queue WHERE status = 'queued' ORDER BY id ASC"
    args: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        args = (int(limit),)
    rows = conn.execute(sql, args).fetchall()
    summary = {"sent": [], "suppressed": [], "failed": [],
               "halted_by_kill_switch": False, "remaining": 0}
    cfg_memo: dict[int, dict | None] = {}   # تهيئة SMTP لكل دراسة تُقرأ مرّة
    processed = 0
    for row in rows:
        if limit is not None and processed >= limit:
            break
        # فحص مفتاح القتل لكل بريد وقت الإرسال · per-email kill-switch check.
        if settings.kill_switch_on(conn):
            summary["halted_by_kill_switch"] = True
            break  # اترك هذا وما بعده مصفوفاً · leave this and the rest queued
        email = dict(row)
        eid = email["id"]
        if _suppressed(conn, email["account_id"], email["prospect_email"]):
            conn.execute("UPDATE email_queue SET status = 'suppressed' WHERE id = ?",
                         (eid,))
            conn.commit()
            summary["suppressed"].append(eid)
            processed += 1
            continue
        # رصيد كافٍ قبل الإرسال · ensure funds before sending (per-email debit).
        w = wallet.get_wallet(conn, email["account_id"])
        if not w or int(w["balance"]) < PRICE_EMAIL_SENT_CENTS:
            conn.execute("UPDATE email_queue SET status = 'failed', attempts = "
                         "attempts + 1, last_error = ? WHERE id = ?",
                         ("insufficient_balance", eid))
            conn.commit()
            summary["failed"].append(eid)
            processed += 1
            continue
        cfg = None
        if email["smtp_config_id"]:
            sid = int(email["smtp_config_id"])
            if sid not in cfg_memo:   # تُقرأ مرّة لكل تهيئة في المرور الواحد
                crow = conn.execute("SELECT * FROM smtp_configs WHERE id = ?",
                                    (sid,)).fetchone()
                cfg_memo[sid] = dict(crow) if crow else None
            cfg = cfg_memo[sid]
        # طالِب بالصفّ **قبل** الإرسال — claim the row before sending. بلا هذا،
        # مرورَان متزاملان (مجدول + نداء يدوي) يقرأان نفس الصفّ «queued» فيُرسَل
        # البريد مرّتين ويُخصَم مرّتين. المطالبة UPDATE محروس + فحص rowcount،
        # فيخسر الثاني ويتخطّى. الحالة الوسيطة 'sending' تجعل المطالبة مرئية.
        claim = conn.execute(
            "UPDATE email_queue SET status = 'sending', attempts = attempts + 1 "
            "WHERE id = ? AND status = 'queued'", (eid,))
        if claim.rowcount == 0:
            conn.commit()
            continue   # مرورٌ آخر طالب به · another pass owns this row
        conn.commit()
        try:
            sender(cfg or {}, email)
        except Exception as exc:  # noqa: BLE001 — فشل الإرسال يُسجَّل لا يُخفى
            conn.execute("UPDATE email_queue SET status = 'failed', "
                         "last_error = ? WHERE id = ?", (_safe_error(exc), eid))
            conn.commit()
            summary["failed"].append(eid)
            processed += 1
            continue
        # نجح الإرسال — الموافقة + الخصم + الحالة في **معاملة واحدة** كي لا يقع
        # خصم مزدوج عند تعطّل بين التزامين. الالتزام الذرّي: إمّا تُثبَت الثلاثة
        # أو لا شيء. Atomic: consent + debit + status='sent' commit together.
        #
        # `allow_negative=True` مقصود هنا وليس تسامحاً: البريد **خرج فعلاً**.
        # فحص الرصيد قبل الإرسال هو البوّابة؛ أمّا بعد الخروج فرفضُ الخصم كان
        # سيتراجع بسجلّ الموافقة أيضاً (خصمٌ مفقود + بريدٌ مُرسَل بلا قيد موافقة
        # = خرق امتثال). الدَّين يُسجَّل ويُسوّى، ولا يُمحى أثر رسالة أُرسِلت.
        # The message physically left: never roll back its consent record — book
        # the debit even if a concurrent charge pushed the balance below zero.
        verbatim = f"Subject: {email['subject']}\n\n{email['body']}"
        try:
            conn.commit()                       # اطوِ المعلّق قبل BEGIN الصريح
            conn.execute("BEGIN IMMEDIATE")     # قفل كتابة فوري للقسم الحرج
            conn.execute(
                "INSERT INTO consent_registry (prospect_email, study_id, "
                "sending_account_id, approving_user_id, message_verbatim, sent_at, "
                "consent_granted_at) VALUES (?,?,?,?,?,?,?)",
                (email["prospect_email"], email["study_id"], email["account_id"],
                 email["actor_user_id"], verbatim, now_iso(), now_iso()))
            wallet.apply_entry(conn, account_id=email["account_id"],
                               actor_user_id=email["actor_user_id"],
                               operation=Operation.EMAIL_SENT,
                               amount=-PRICE_EMAIL_SENT_CENTS,
                               description="email sent",
                               metadata={"study_id": email["study_id"],
                                         "prospect_id": email["prospect_id"],
                                         "email_queue_id": eid},
                               allow_negative=True)
            # محروس بحالة المطالبة — guarded by the claim we own.
            conn.execute("UPDATE email_queue SET status = 'sent', sent_at = ? "
                         "WHERE id = ? AND status = 'sending'", (now_iso(), eid))
            conn.commit()
        except Exception as exc:  # noqa: BLE001 — الفشل يُسجَّل، لا خصم جزئي
            conn.rollback()
            conn.execute("UPDATE email_queue SET status = 'failed', "
                         "last_error = ? WHERE id = ?", (_safe_error(exc), eid))
            conn.commit()
            summary["failed"].append(eid)
            processed += 1
            continue
        summary["sent"].append(eid)
        processed += 1
    summary["remaining"] = int(conn.execute(
        "SELECT COUNT(*) AS c FROM email_queue WHERE status = 'queued'"
    ).fetchone()["c"])
    return summary
