"""تقرير الحملة — the charged campaign report for a study (PR-3).

يُجمَّع من بيانات المستأجر الحقيقية فقط: حالة الدراسة، عدّادات طابور البريد،
سجلّ الموافقة وإلغاءات الاشتراك، والإنفاق المشتَقّ من الرسائل التي **خرجت فعلاً**.

**لا رقم مختلَق ولا حقل تقديري**: ما لا تحمله القاعدة لا يظهر. عدد الردود يُقرأ
من عمود الدراسة كما هو (يُحدَّث بمسار الردّ حين يُبنى) ويُوسَم مصدره صراحةً كي
لا يُقرأ كقياسٍ محسوب. Same no-fabrication contract as the engine's DataPoint.

كل استعلام هنا مقيَّد بعمود المالك (`account_id`/`sending_account_id`) فلا يقرأ
تقريرُ حسابٍ أرقامَ غيره — يفرضه حارس العزل بـAST بنيوياً.
"""
from __future__ import annotations

import sqlite3

from . import billing, repository
from .db import now_iso
from .models import Operation, PRICE_EMAIL_SENT_CENTS, PRICE_REPORT_CENTS

# حالات الطابور المعروضة — الترتيب مقصود (نجاح ← فشل ← مكبوت ← معلّق).
_QUEUE_STATES = ("sent", "failed", "suppressed", "queued", "sending")


def _queue_counts(conn: sqlite3.Connection, account_id: int,
                  study_id: int) -> dict[str, int]:
    """عدّادات الطابور لهذه الدراسة — per-status counts, this account only."""
    rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM email_queue "
        "WHERE account_id = ? AND study_id = ? GROUP BY status",
        (account_id, study_id)).fetchall()
    found = {r["status"]: int(r["c"]) for r in rows}
    return {s: found.get(s, 0) for s in _QUEUE_STATES}


def _consent_counts(conn: sqlite3.Connection, account_id: int,
                    study_id: int) -> dict[str, int]:
    """قيود الموافقة وإلغاءات الاشتراك — the compliance side of the campaign."""
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN unsubscribed_at IS NOT NULL THEN 1 ELSE 0 END) AS unsubs "
        "FROM consent_registry WHERE sending_account_id = ? AND study_id = ?",
        (account_id, study_id)).fetchone()
    return {"consent_records": int(row["total"] or 0),
            "unsubscribed": int(row["unsubs"] or 0)}


def build_campaign_report(conn: sqlite3.Connection, account_id: int,
                          study_id: int) -> dict | None:
    """ابنِ تقرير حملة لدراسة — None if the study is another tenant's/absent.

    يمرّ عبر `repository.studies` فيرث قيد المالك بنيوياً (لا استعلام مُستأجَر
    خارج طبقة العزل). Routed through the tenant repository by construction.
    """
    study = repository.studies(conn).get(account_id, study_id)
    if study is None:
        return None
    queue = _queue_counts(conn, account_id, study_id)
    consent = _consent_counts(conn, account_id, study_id)
    # الإنفاق مشتَقّ من الرسائل التي خرجت فعلاً — كل رسالة مُرسَلة خُصِمت بسعرها
    # الثابت، فالحاصل مطابقٌ للدفتر بلا استعلام JSON على البيانات الوصفية.
    email_spend = queue["sent"] * PRICE_EMAIL_SENT_CENTS
    delivered = queue["sent"]
    return {
        "study": {"id": study["id"], "title_en": study.get("title_en"),
                  "title_ar": study.get("title_ar"), "state": study["state"],
                  "target_count": int(study.get("target_count") or 0),
                  "launched_at": study.get("launched_at"),
                  "completed_at": study.get("completed_at")},
        "outreach": {**queue, "delivered": delivered},
        "compliance": consent,
        "spend": {"emails_cents": email_spend,
                  "report_cents": PRICE_REPORT_CENTS,
                  "price_per_email_cents": PRICE_EMAIL_SENT_CENTS},
        # مصادر الأرقام معلَنة صراحةً — أيّ رقم بلا مصدر لا يُعرَض.
        "provenance": {
            "outreach": "email_queue rows for this study (this account only)",
            "compliance": "consent_registry rows (sending_account_id scoped)",
            "emails_cents": "sent count × PRICE_EMAIL_SENT_CENTS (ledger-equivalent)",
            "response_count": "studies.response_count column as stored — not a "
                              "computed metric; no response path writes it yet",
        },
        "response_count_as_stored": int(study.get("response_count") or 0),
        "generated_at": now_iso(),
    }


def default_report_key(study_id: int, at: str | None = None) -> str:
    """مفتاح الخمول الافتراضي — one charged report per study per UTC day.

    **قرار معلَن، لا قاعدة من المواصفة:** المواصفة تسعّر «التقرير» بدولار ولا
    تحدّد متى يصير التقرير الثاني تقريراً جديداً. اليومُ اختيارٌ محافظ يمنع
    الخصم المزدوج من نقرةٍ مزدوجة أو إعادة محاولة شبكة، ويسمح بتقرير جديد
    مدفوع في يوم تالٍ. من يريد تقريراً مدفوعاً ثانياً في نفس اليوم يمرّر
    `idempotency_key` صريحاً. A declared policy choice, not a spec rule.
    """
    stamp = (at or now_iso())[:10]          # YYYY-MM-DD (UTC)
    return f"study:{study_id}:{stamp}"


def generate_charged_report(conn: sqlite3.Connection, *, account_id: int,
                            actor_user_id: int | None, study_id: int,
                            idempotency_key: str | None = None) -> dict | None:
    """أنشئ تقرير حملة واخصم سعره مرّة واحدة — None if the study isn't the caller's.

    الترتيب مقصود: **يُبنى التقرير أولاً** ثم يُخصَم. لو خُصِم أولاً ثم فشل
    البناء لكان العميل قد دفع مقابل لا شيء — والدفتر غير قابل للتعديل فلا يُردّ
    القيد. البناء قراءةٌ خالصة فلا يترك أثراً إن رُفض الخصم.
    Build first, charge second: a failed build must never leave a paid entry.
    """
    report = build_campaign_report(conn, account_id, study_id)
    if report is None:
        return None
    key = idempotency_key or default_report_key(study_id)
    entry, charged = billing.charge_metered(
        conn, account_id=account_id, actor_user_id=actor_user_id,
        operation=Operation.REPORT_GENERATED,
        amount_cents=PRICE_REPORT_CENTS, key=key,
        metadata={"study_id": study_id})
    return {**report,
            "billing": {"charged": charged, "ledger_entry_id": entry["id"],
                        "amount_cents": abs(int(entry["amount"])),
                        "balance_after": int(entry["balance_after"]),
                        "idempotency_key": key}}
