"""قمع المقارنة — the comparison funnel state machine (PR-7; Gold/Platinum).

`comparison_funnels` **غير مُدرَج** في `repository._WRITABLE` عمداً (انظر تعليقه
هناك): عمود `state` آلةُ حالاتٍ ببوّابة طبقة وقيود لكل انتقال، فإدراجه في CRUD
العامّ يفتح باباً ثانياً يتخطّى تلك البوّابات. هذه الوحدة هي المسار الوحيد.

## الدلالة المُتبنّاة · the adopted semantics (owner-approved, see docs/PLATFORM_PR7.md)

المواصفة تسمّي الحالات الخمس ولا تشرحها. القراءة المُشتقّة من المخطّط نفسه
(جدولا الوصل + بوّابة `funnel_max_studies`) والمُعتمَدة بقرار المالك:

    compared → selected → extracted → drafted → sent

- **`compared`** — القمع مفتوح وتُضاف إليه دراسات تُقارَن معاً (سقف الطبقة:
  ١٠ لـGold، بلا سقف لـPlatinum). هذا وحده ما يفسّر وجود `funnel_max_studies`.
- **`selected`** — اختيار الدراسة الفائزة (`selected_study_id`). الدراسات
  الأخرى **تبقى** في `funnel_studies`: سجلّ المقارنة هو قيمة القمع، فحذفها
  يُهلِك ما يُراجَع.
- **`extracted`** — استخراج العملاء المستهدَفين إلى `funnel_prospects`.
- **`drafted`** — ربط مسودّة الرسالة (`draft_id`).
- **`sent`** — صفّ البريد فعلياً لعملاء القمع ثم ختم `sent_at`.

## العزل · isolation (the part with no owner column)

`funnel_studies`/`funnel_prospects` **لا تحملان عمود مالك** (مفتاحهما مركَّب
فقط) — والخطر حقيقي: كتابةُ صفّ وصلٍ بمعرّف دراسةٍ لحسابٍ آخر تربط بيانات
مستأجرٍ بقمع مستأجر ثانٍ. لذلك **كل** معرّف يمرّ من هنا يُتحقَّق من ملكيته عبر
`repository` (الذي يحمل قيد المالك بنيوياً) **قبل** أي كتابة على جدول وصل. لا
استثناء. والجدولان مُدرَجان في قائمة حارس العزل بـAST رغم غياب عمود المالك، كي
يُلزِم كل جملة عليهما بسببٍ مكتوب يشرح كيف تُفرَض الملكية.

Join tables carry no owner column, so the AST guard cannot see them: every id
is ownership-verified through repository (which carries the owner predicate by
construction) BEFORE any join-table write.
"""
from __future__ import annotations

import sqlite3

from . import audit, email_queue, entitlements, repository, wallet
from .db import now_iso
from .models import Tier, projected_email_cost_cents

# الانتقالات المسموحة صراحةً · the explicit transition graph.
STATES = ("compared", "selected", "extracted", "drafted", "sent")
_NEXT = {"compared": "selected", "selected": "extracted",
         "extracted": "drafted", "drafted": "sent"}


class FunnelError(Exception):
    """عملية قمع مرفوضة — carries a machine-readable code for the endpoint."""

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.extra = extra
        super().__init__(message)

    def as_detail(self) -> dict:
        return {"error": self.code, "message": str(self), **self.extra}


# ── قراءة مقيَّدة بالمالك · owner-scoped reads ────────────────────────────────
def get(conn: sqlite3.Connection, account_id: int, funnel_id: int) -> dict | None:
    """اقرأ قمعاً ضمن الحساب — None if it belongs to another tenant."""
    row = conn.execute(
        "SELECT * FROM comparison_funnels WHERE id = ? AND owner_id = ?",
        (funnel_id, account_id)).fetchone()
    return dict(row) if row else None


def exists_anywhere(conn: sqlite3.Connection, funnel_id: int) -> bool:
    """هل المعرّف موجود لأي حساب؟ — boolean only, for the 404-vs-audit decision."""
    return conn.execute("SELECT 1 FROM comparison_funnels WHERE id = ?",
                        (funnel_id,)).fetchone() is not None


def list_funnels(conn: sqlite3.Connection, account_id: int,
                 limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM comparison_funnels WHERE owner_id = ? "
        "ORDER BY id DESC LIMIT ?", (account_id, int(limit))).fetchall()
    return [dict(r) for r in rows]


def study_ids(conn: sqlite3.Connection, funnel_id: int) -> list[int]:
    """دراسات القمع — safe unscoped: the caller already proved funnel ownership.

    القمع نفسه مُتحقَّقٌ من ملكيته قبل كل نداء لهذه الدالّة، وكل صفّ في
    `funnel_studies` لا يدخل إلا بعد تحقّق ملكية الدراسة — فالنطاق مضمون
    بالبناء لا بقيدٍ في هذه الجملة.
    """
    return [int(r["study_id"]) for r in conn.execute(
        "SELECT study_id FROM funnel_studies WHERE funnel_id = ? ORDER BY study_id",
        (funnel_id,)).fetchall()]


def prospect_ids(conn: sqlite3.Connection, funnel_id: int) -> list[int]:
    """عملاء القمع — same ownership-by-construction guarantee as study_ids."""
    return [int(r["prospect_id"]) for r in conn.execute(
        "SELECT prospect_id FROM funnel_prospects WHERE funnel_id = ? "
        "ORDER BY prospect_id", (funnel_id,)).fetchall()]


def detail(conn: sqlite3.Connection, account_id: int,
           funnel_id: int) -> dict | None:
    """القمع بدراساته وعملائه — the full view for one funnel."""
    row = get(conn, account_id, funnel_id)
    if row is None:
        return None
    return {**row, "study_ids": study_ids(conn, funnel_id),
            "prospect_ids": prospect_ids(conn, funnel_id)}


# ── بوّابة الطبقة · the tier gate (reuses entitlements, never re-implements) ──
def _account_tier(conn: sqlite3.Connection, account_id: int) -> Tier:
    row = conn.execute("SELECT tier FROM accounts WHERE id = ?",
                       (account_id,)).fetchone()
    if row is None:
        raise FunnelError("account_not_found", f"no account {account_id}")
    return Tier(row["tier"])


def require_funnel_feature(conn: sqlite3.Connection, account_id: int) -> Tier:
    """افرض أن الطبقة تمنح القمع — raises entitlements.TierGateError otherwise.

    البوّابة **واحدة** (`entitlements.require_feature`) لا مقارنة طبقات محلّية:
    نسخةٌ ثانية من القاعدة تنحرف عن `TIER_LIMITS` بلا أن يلاحظ أحد.
    """
    tier = _account_tier(conn, account_id)
    entitlements.require_feature(tier, "funnel")
    return tier


# ── إنشاء · create ───────────────────────────────────────────────────────────
def create(conn: sqlite3.Connection, *, account_id: int,
           actor_user_id: int | None) -> dict:
    """أنشئ قمعاً في حالة `compared` — tier-gated; audited."""
    require_funnel_feature(conn, account_id)
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO comparison_funnels (owner_id, state, created_at, updated_at) "
        "VALUES (?, 'compared', ?, ?)", (account_id, now, now))
    fid = int(cur.lastrowid)
    audit.record(conn, action="funnel_created", user_id=actor_user_id,
                 account_id=account_id, resource_type="funnel", resource_id=fid)
    conn.commit()
    return detail(conn, account_id, fid)   # type: ignore[return-value]


# ── ضمّ دراسة · attach a study (the funnel_max_studies cap lives here) ────────
def attach_study(conn: sqlite3.Connection, *, account_id: int, funnel_id: int,
                 study_id: int, actor_user_id: int | None) -> dict:
    """اضمم دراسةً إلى القمع — enforces the tier's funnel_max_studies cap.

    الضمّ مسموح في حالة `compared` وحدها: قمعٌ اختير فائزُه ثم أُضيفت إليه
    دراسةٌ جديدة يجعل الاختيار وصفاً لمقارنةٍ لم تحدث.
    """
    tier = require_funnel_feature(conn, account_id)
    funnel = get(conn, account_id, funnel_id)
    if funnel is None:
        raise FunnelError("funnel_not_found", "no such funnel for this account")
    if funnel["state"] != "compared":
        raise FunnelError(
            "not_comparing",
            f"studies can only be added while the funnel is 'compared' "
            f"(it is '{funnel['state']}')", state=funnel["state"])
    # ملكية الدراسة تُتحقَّق عبر طبقة العزل **قبل** الكتابة على جدول الوصل:
    # هذا هو الحارس الوحيد هناك (لا عمود مالك في funnel_studies).
    if repository.studies(conn).get(account_id, study_id) is None:
        raise FunnelError("study_not_found",
                          "no such study for this account", study_id=study_id)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")      # السقف يُقاس ويُكتب تحت قفل واحد
    try:
        current = study_ids(conn, funnel_id)
        if study_id in current:
            conn.rollback()
            raise FunnelError("already_attached",
                              "study is already in this funnel", study_id=study_id)
        cap = entitlements.funnel_max_studies(tier)
        if cap != entitlements.UNLIMITED and len(current) >= cap:
            conn.rollback()
            raise FunnelError(
                "funnel_study_cap",
                f"the {tier.value} tier allows at most {cap} studies per funnel",
                tier=tier.value, limit=cap, used=len(current), upgrade=True)
        conn.execute("INSERT INTO funnel_studies (funnel_id, study_id) VALUES (?,?)",
                     (funnel_id, study_id))
        conn.execute("UPDATE comparison_funnels SET updated_at = ? "
                     "WHERE id = ? AND owner_id = ?", (now_iso(), funnel_id, account_id))
        audit.record(conn, action="funnel_study_attached", user_id=actor_user_id,
                     account_id=account_id, resource_type="funnel",
                     resource_id=funnel_id, changes={"study_id": study_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return detail(conn, account_id, funnel_id)   # type: ignore[return-value]


def detach_study(conn: sqlite3.Connection, *, account_id: int, funnel_id: int,
                 study_id: int, actor_user_id: int | None) -> dict:
    """انزع دراسةً من القمع — only while 'compared'."""
    require_funnel_feature(conn, account_id)
    funnel = get(conn, account_id, funnel_id)
    if funnel is None:
        raise FunnelError("funnel_not_found", "no such funnel for this account")
    if funnel["state"] != "compared":
        raise FunnelError(
            "not_comparing",
            f"studies can only be removed while the funnel is 'compared' "
            f"(it is '{funnel['state']}')", state=funnel["state"])
    cur = conn.execute(
        "DELETE FROM funnel_studies WHERE funnel_id = ? AND study_id = ?",
        (funnel_id, study_id))
    if cur.rowcount == 0:
        conn.rollback()
        raise FunnelError("not_attached", "study is not in this funnel",
                          study_id=study_id)
    conn.execute("UPDATE comparison_funnels SET updated_at = ? "
                 "WHERE id = ? AND owner_id = ?", (now_iso(), funnel_id, account_id))
    audit.record(conn, action="funnel_study_detached", user_id=actor_user_id,
                 account_id=account_id, resource_type="funnel",
                 resource_id=funnel_id, changes={"study_id": study_id})
    conn.commit()
    return detail(conn, account_id, funnel_id)   # type: ignore[return-value]


# ── الانتقالات · the transitions ─────────────────────────────────────────────
def _begin_transition(conn: sqlite3.Connection, account_id: int, funnel_id: int,
                      expected: str) -> dict:
    """افتح قفلاً فورياً وأعِد قراءة الحالة داخله — atomic, re-read under lock.

    نفس نمط `lifecycle.py`: الفحص خارج القفل قابل للتعتيق، فتنجح نقرتان
    متزامنتان معاً. Re-reading inside BEGIN IMMEDIATE is what makes the guarded
    UPDATE meaningful.
    """
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    fresh = get(conn, account_id, funnel_id)
    if fresh is None:
        conn.rollback()
        raise FunnelError("funnel_not_found", "no such funnel for this account")
    if fresh["state"] != expected:
        conn.rollback()
        raise FunnelError(
            "invalid_transition",
            f"expected state '{expected}' but the funnel is '{fresh['state']}'",
            state=fresh["state"], expected=expected)
    return fresh


def select_study(conn: sqlite3.Connection, *, account_id: int, funnel_id: int,
                 study_id: int, actor_user_id: int | None) -> dict:
    """compared → selected — اختر الدراسة الفائزة من دراسات القمع.

    الفائز **يجب** أن يكون من دراسات القمع نفسه: اختيار دراسةٍ لم تُقارَن
    يجعل الحالة ادعاءً. والباقي يبقى مضموماً (سجلّ المقارنة).
    """
    require_funnel_feature(conn, account_id)
    if repository.studies(conn).get(account_id, study_id) is None:
        raise FunnelError("study_not_found", "no such study for this account",
                          study_id=study_id)
    _begin_transition(conn, account_id, funnel_id, "compared")
    try:
        if study_id not in study_ids(conn, funnel_id):
            conn.rollback()
            raise FunnelError(
                "study_not_in_funnel",
                "the selected study must be one of the funnel's compared studies",
                study_id=study_id)
        now = now_iso()
        cur = conn.execute(
            "UPDATE comparison_funnels SET state = 'selected', "
            "selected_study_id = ?, updated_at = ? "
            "WHERE id = ? AND owner_id = ? AND state = 'compared'",
            (study_id, now, funnel_id, account_id))
        if cur.rowcount == 0:
            conn.rollback()
            raise FunnelError("invalid_transition", "funnel state changed concurrently")
        audit.record(conn, action="funnel_study_selected", user_id=actor_user_id,
                     account_id=account_id, resource_type="funnel",
                     resource_id=funnel_id, changes={"selected_study_id": study_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return detail(conn, account_id, funnel_id)   # type: ignore[return-value]


def extract_prospects(conn: sqlite3.Connection, *, account_id: int, funnel_id: int,
                      prospect_ids_in: list[int],
                      actor_user_id: int | None) -> dict:
    """selected → extracted — اربط العملاء المستهدَفين بالقمع.

    كل معرّف عميل يُتحقَّق من ملكيته عبر طبقة العزل **قبل** الكتابة (لا عمود
    مالك في `funnel_prospects`)، وقائمة فارغة تُرفَض: قمعٌ «استُخرج» بلا عميل
    واحد حالةٌ بلا معنى وستفشل عند الإرسال.
    """
    require_funnel_feature(conn, account_id)
    ids = [int(p) for p in (prospect_ids_in or [])]
    if not ids:
        raise FunnelError("no_prospects",
                          "extracting requires at least one prospect")
    repo = repository.prospects(conn)
    for pid in ids:
        if repo.get(account_id, pid) is None:
            raise FunnelError("prospect_not_found",
                              "no such prospect for this account", prospect_id=pid)
    _begin_transition(conn, account_id, funnel_id, "selected")
    try:
        for pid in ids:
            conn.execute("INSERT OR IGNORE INTO funnel_prospects "
                         "(funnel_id, prospect_id) VALUES (?,?)", (funnel_id, pid))
        now = now_iso()
        cur = conn.execute(
            "UPDATE comparison_funnels SET state = 'extracted', updated_at = ? "
            "WHERE id = ? AND owner_id = ? AND state = 'selected'",
            (now, funnel_id, account_id))
        if cur.rowcount == 0:
            conn.rollback()
            raise FunnelError("invalid_transition", "funnel state changed concurrently")
        audit.record(conn, action="funnel_prospects_extracted",
                     user_id=actor_user_id, account_id=account_id,
                     resource_type="funnel", resource_id=funnel_id,
                     changes={"prospect_count": len(ids)})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return detail(conn, account_id, funnel_id)   # type: ignore[return-value]


def attach_draft(conn: sqlite3.Connection, *, account_id: int, funnel_id: int,
                 draft_id: int, actor_user_id: int | None) -> dict:
    """extracted → drafted — اربط مسودّة الرسالة التي ستُرسَل."""
    require_funnel_feature(conn, account_id)
    if repository.drafts(conn).get(account_id, draft_id) is None:
        raise FunnelError("draft_not_found", "no such draft for this account",
                          draft_id=draft_id)
    _begin_transition(conn, account_id, funnel_id, "extracted")
    try:
        now = now_iso()
        cur = conn.execute(
            "UPDATE comparison_funnels SET state = 'drafted', draft_id = ?, "
            "updated_at = ? WHERE id = ? AND owner_id = ? AND state = 'extracted'",
            (draft_id, now, funnel_id, account_id))
        if cur.rowcount == 0:
            conn.rollback()
            raise FunnelError("invalid_transition", "funnel state changed concurrently")
        audit.record(conn, action="funnel_draft_attached", user_id=actor_user_id,
                     account_id=account_id, resource_type="funnel",
                     resource_id=funnel_id, changes={"draft_id": draft_id})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return detail(conn, account_id, funnel_id)   # type: ignore[return-value]


def send(conn: sqlite3.Connection, *, account_id: int, funnel_id: int,
         actor_user_id: int | None) -> dict:
    """drafted → sent — صُفّ البريد فعلياً لعملاء القمع ثم اختم `sent_at`.

    **حالةٌ تغيّر الواقع لا العمود** (نفس درس `lifecycle.archive_study`): لو
    كان `sent` تبديل عمودٍ فقط لكان القمع «مُرسَلاً» بلا رسالة واحدة في الطابور.

    بوّابتا المال قبل الصفّ، بنفس ترتيب `launch_study` وسببه: المديونية
    تُصرَّح صريحةً، والرصيد يُقاس مقابل التكلفة المتوقّعة — فلا يُصفّ ألف بريد
    لحسابٍ لا يملك ثمن أوّلها. (الخصم الفعلي لكل بريد يقع في العامل عند خروج
    الرسالة، لا هنا.)
    """
    require_funnel_feature(conn, account_id)
    funnel = get(conn, account_id, funnel_id)
    if funnel is None:
        raise FunnelError("funnel_not_found", "no such funnel for this account")
    if funnel["state"] != "drafted":
        raise FunnelError(
            "invalid_transition",
            f"expected state 'drafted' but the funnel is '{funnel['state']}'",
            state=funnel["state"], expected="drafted")
    targets = prospect_ids(conn, funnel_id)
    if not targets:
        raise FunnelError("no_prospects", "this funnel has no extracted prospects")
    draft = repository.drafts(conn).get(account_id, int(funnel["draft_id"]))
    if draft is None:      # حُذفت المسودّة بين الربط والإرسال
        raise FunnelError("draft_not_found",
                          "the funnel's draft no longer exists",
                          draft_id=funnel["draft_id"])
    study = repository.studies(conn).get(account_id, int(funnel["selected_study_id"]))
    if study is None:
        raise FunnelError("study_not_found",
                          "the funnel's selected study no longer exists",
                          study_id=funnel["selected_study_id"])
    # ── بوّابتا المال · the two money gates (pre-flight, mirrors launch_study) ─
    projected = projected_email_cost_cents(len(targets))
    w = wallet.ensure_wallet(conn, account_id)
    if wallet.is_delinquent(conn, account_id):
        audit.record(conn, action="funnel_send_blocked_delinquent",
                     user_id=actor_user_id, account_id=account_id,
                     resource_type="funnel", resource_id=funnel_id,
                     changes={"balance": int(w["balance"])})
        conn.commit()
        raise FunnelError("account_delinquent",
                          "account is delinquent — settle up before sending",
                          balance_cents=int(w["balance"]), settle_up_required=True)
    if int(w["balance"]) < projected:
        audit.record(conn, action="funnel_send_blocked_insufficient_funds",
                     user_id=actor_user_id, account_id=account_id,
                     resource_type="funnel", resource_id=funnel_id,
                     changes={"projected": projected, "balance": int(w["balance"])})
        conn.commit()
        raise FunnelError("insufficient_balance",
                          "wallet balance is below the projected send cost",
                          projected_cents=projected, balance_cents=int(w["balance"]))
    # ── المطالبة بالانتقال ثم الصفّ في نفس المعاملة · claim + enqueue atomically ─
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = get(conn, account_id, funnel_id)
        if fresh is None or fresh["state"] != "drafted":
            conn.rollback()
            raise FunnelError("invalid_transition",
                              "funnel state changed concurrently")
        marks = ",".join("?" for _ in targets)
        rows = conn.execute(
            f"SELECT * FROM prospects WHERE owner_id = ? AND id IN ({marks})",
            [account_id, *targets]).fetchall()
        queued = 0
        for pr in rows:
            email_queue.enqueue(
                conn, account_id=account_id, study_id=int(study["id"]),
                prospect=dict(pr), draft=draft,
                smtp_config_id=study["smtp_config_id"],
                actor_user_id=actor_user_id, commit=False)
            queued += 1
        now = now_iso()
        cur = conn.execute(
            "UPDATE comparison_funnels SET state = 'sent', sent_at = ?, "
            "updated_at = ? WHERE id = ? AND owner_id = ? AND state = 'drafted'",
            (now, now, funnel_id, account_id))
        if cur.rowcount == 0:
            conn.rollback()
            raise FunnelError("invalid_transition", "funnel state changed concurrently")
        audit.record(conn, action="funnel_sent", user_id=actor_user_id,
                     account_id=account_id, resource_type="funnel",
                     resource_id=funnel_id,
                     changes={"queued": queued, "study_id": int(study["id"]),
                              "draft_id": int(draft["id"])})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    out = detail(conn, account_id, funnel_id)
    return {**out, "queued": queued}   # type: ignore[dict-item]
