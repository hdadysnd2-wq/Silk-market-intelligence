"""المحفظة ودفتر الأستاذ — wallets, immutable ledger, atomic vault funding.

كل خصم/إيداع يُنتِج قيداً واحداً بالضبط مع لقطة `balance_after`. التمويل من
الخزنة معاملة ذرّية تنشئ قيدين (خصم الخزنة + إيداع المصنع) مختومَين بمعرّف
الأدمِن؛ فشلٌ في المنتصف يتراجع كلياً. المال بالسنتات الصحيحة.

Every debit/credit posts exactly one ledger entry with a balance_after snapshot.
Funding is one atomic transaction (vault debit + factory credit); a mid-flow
failure rolls the whole thing back. Money is integer cents.
"""
from __future__ import annotations

import json
import os
import sqlite3

from . import audit
from .db import now_iso
from .models import Operation


class WalletError(Exception):
    """خطأ محفظة — base wallet error."""


class InsufficientFunds(WalletError):
    """رصيد غير كافٍ — balance would go negative."""


def ensure_wallet(conn: sqlite3.Connection, account_id: int) -> dict:
    """اضمن وجود محفظة للحساب — get-or-create; returns the wallet row.

    `INSERT OR IGNORE` يجعل الإنشاء ذرّياً: النداءان المتزامنان الأوّلان لا
    يتسابقان على UNIQUE(account_id) فيسقط الخاسر بـIntegrityError غير ملتقط
    (كان يُظهر 500 على أوّل عرض للمحفظة). Atomic get-or-create; no race.
    """
    row = conn.execute("SELECT * FROM wallets WHERE account_id = ?",
                       (account_id,)).fetchone()
    if row:
        return dict(row)
    now = now_iso()
    conn.execute("INSERT OR IGNORE INTO wallets (account_id, balance, "
                 "lifetime_funded, lifetime_spent, created_at, updated_at) "
                 "VALUES (?,0,0,0,?,?)", (account_id, now, now))
    conn.commit()
    return dict(conn.execute("SELECT * FROM wallets WHERE account_id = ?",
                             (account_id,)).fetchone())


def get_wallet(conn: sqlite3.Connection, account_id: int) -> dict | None:
    """اقرأ محفظة حساب واحد — own account only (endpoint enforces scope)."""
    row = conn.execute("SELECT * FROM wallets WHERE account_id = ?",
                       (account_id,)).fetchone()
    return dict(row) if row else None


def is_delinquent(conn: sqlite3.Connection, account_id: int) -> bool:
    """حسابٌ مدين (رصيد سالب) — an overdrawn account owes a settle-up.

    السالب يحدث من مسارين مقصودين فقط: (١) خصم بريدٍ **خرج فعلاً** (٥ سنتات
    كحدّ أقصى لكل سباق)، و(٢) فوترة تخزين شهرية (دَينٌ حقيقي يجب أن يُقيَّد).
    ما دام سالباً: لا إطلاق دراسة جديدة ولا إرسال بريد — يُفكّ بتمويل الأدمِن.
    While negative: no launches, no sends. Cleared by admin funding.
    """
    w = get_wallet(conn, account_id)
    return bool(w) and int(w["balance"]) < 0


def overdraft_floor_cents() -> int:
    """أقصى مديونية مسموحة (سنتات موجبة) — the overdraft floor.

    حدٌّ **تشغيلي** يمنع انزلاق المديونية بلا سقف (فاتورة تخزين ضخمة لحساب
    غير ممول مثلاً). عند تجاوزه يُرفَض الخصم الاختياري ويُسجَّل إنذار بدل
    الاستمرار صامتاً. صفر = لا مديونية إطلاقاً فوق ما خرج فعلاً.
    Operational cap so debt cannot slide unbounded; env-tunable.
    """
    raw = os.environ.get("SILK_PLATFORM_MAX_OVERDRAFT_CENTS", "").strip()
    try:
        return max(0, int(raw)) if raw else 10_000   # افتراضي $100
    except ValueError:
        return 10_000


def list_ledger(conn: sqlite3.Connection, account_id: int,
                limit: int = 20) -> list[dict]:
    """اسرد قيود دفتر حساب واحد — this account's entries only, newest first."""
    rows = conn.execute(
        "SELECT * FROM ledger_entries WHERE account_id = ? ORDER BY id DESC LIMIT ?",
        (account_id, int(limit))).fetchall()
    return [dict(r) for r in rows]


def _apply(conn: sqlite3.Connection, account_id: int, actor_user_id: int | None,
           operation: Operation, amount: int, description: str,
           metadata: dict | None, *, allow_negative: bool) -> int:
    """طبّق حركة واحدة بلا commit — mutate the wallet + insert one ledger entry.

    لا يلتزم (ليُركَّب داخل معاملة أكبر). يرفع InsufficientFunds قبل أي كتابة
    حين يخالف الخصم الرصيد. Does NOT commit; raises before writing on overdraft.

    `allow_negative=True` ليس «بلا حدّ»: المديونية مسقوفة بـ`overdraft_floor_cents()`
    فلا تنزلق فاتورةٌ كبيرة بحسابٍ إلى سالبٍ غير محدود صامتةً — تجاوز السقف يرفع
    InsufficientFunds ليعالجه المُنادي (تسجيل/إنذار) لا ليمرّ بهدوء.
    Even allow_negative debits are bounded by the overdraft floor.
    """
    row = conn.execute("SELECT balance, lifetime_funded, lifetime_spent "
                       "FROM wallets WHERE account_id = ?", (account_id,)).fetchone()
    if row is None:
        raise WalletError(f"no wallet for account {account_id}")
    balance = int(row["balance"])
    new_balance = balance + int(amount)
    if new_balance < 0 and not allow_negative:
        raise InsufficientFunds(
            f"balance {balance} insufficient for {amount}")
    if new_balance < -overdraft_floor_cents():
        raise InsufficientFunds(
            f"debit would exceed the overdraft floor "
            f"({new_balance} < -{overdraft_floor_cents()})")
    funded = int(row["lifetime_funded"]) + (amount if amount > 0 else 0)
    spent = int(row["lifetime_spent"]) + (-amount if amount < 0 else 0)
    conn.execute("UPDATE wallets SET balance = ?, lifetime_funded = ?, "
                 "lifetime_spent = ?, updated_at = ? WHERE account_id = ?",
                 (new_balance, funded, spent, now_iso(), account_id))
    cur = conn.execute(
        "INSERT INTO ledger_entries (account_id, actor_user_id, operation_type, "
        "amount, balance_after, description, metadata, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (account_id, actor_user_id, operation.value, int(amount), new_balance,
         description, json.dumps(metadata, ensure_ascii=False) if metadata else None,
         now_iso()))
    return int(cur.lastrowid)


def apply_entry(conn: sqlite3.Connection, *, account_id: int,
                actor_user_id: int | None, operation: Operation, amount: int,
                description: str = "", metadata: dict | None = None,
                allow_negative: bool = False) -> int:
    """طبّق حركة ضمن معاملة المُنادي **بلا** commit — for multi-step atomic ops.

    يستعمله عامل البريد كي يلتزم الموافقة + الخصم + الحالة معاً (لا نافذة خصم
    مزدوج). المُنادي مسؤول عن commit/rollback. Post one entry without committing.
    """
    return _apply(conn, account_id, actor_user_id, operation, amount,
                  description, metadata, allow_negative=allow_negative)


def post_entry(conn: sqlite3.Connection, *, account_id: int,
               actor_user_id: int | None, operation: Operation, amount: int,
               description: str = "", metadata: dict | None = None,
               allow_negative: bool = False) -> int:
    """اكتب قيداً واحداً والتزم — post one debit/credit atomically; return id.

    الاستخدام العام لكل العمليات المدفوعة (إرسال بريد، تقرير، …). Exactly
    one ledger row per call.

    يلفّ القراءة-التعديل-الكتابة بـ`BEGIN IMMEDIATE` كي تتسلسل الخصومات
    المتزامنة على محفظة واحدة فلا تُفقَد تحديثات (قفل كتابة فوري لا مؤجَّل).
    Wraps the read-modify-write in BEGIN IMMEDIATE so concurrent debits on one
    wallet can't lose an update.
    """
    conn.commit()  # اطوِ أي معاملة معلّقة قبل BEGIN الصريح · clear pending txn
    conn.execute("BEGIN IMMEDIATE")
    try:
        eid = _apply(conn, account_id, actor_user_id, operation, amount,
                     description, metadata, allow_negative=allow_negative)
        conn.commit()
        return eid
    except Exception:
        conn.rollback()
        raise


def fund_wallet(conn: sqlite3.Connection, *, admin_user_id: int,
                factory_account_id: int, amount_cents: int,
                vault_account_id: int, description: str = "",
                _fault=None) -> tuple[int, int]:
    """موّل محفظة مصنع من الخزنة ذرّياً — vault debit + factory credit, one txn.

    القيدان مختومان بمعرّف الأدمِن (actor_user_id). أي استثناء (بما فيه
    `_fault` المحقون للاختبار) بين القيدين يتراجع بالكامل: لا محفظة تتغيّر ولا
    قيد يُكتب. يرجّع (vault_entry_id, factory_entry_id).

    Atomic: both entries stamped with the admin's id; injected mid-flow failure
    fully rolls back. Returns the two ledger entry ids.
    """
    if amount_cents <= 0:
        raise WalletError("funding amount must be positive")
    ensure_wallet(conn, vault_account_id)
    ensure_wallet(conn, factory_account_id)
    conn.commit()  # اطوِ أي معاملة معلّقة قبل BEGIN الصريح · clear pending txn
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 1) خصم الخزنة · vault debit (fails here if the vault is underfunded)
        vault_eid = _apply(conn, vault_account_id, admin_user_id,
                           Operation.WALLET_FUNDED, -amount_cents,
                           description or "vault → factory funding",
                           {"factory_account_id": factory_account_id,
                            "direction": "vault_debit"}, allow_negative=False)
        # نقطة حقن الفشل — mid-flow fault injection (rollback proof)
        if _fault is not None:
            _fault()
        # 2) إيداع المصنع · factory credit
        factory_eid = _apply(conn, factory_account_id, admin_user_id,
                            Operation.WALLET_FUNDED, amount_cents,
                            description or "vault → factory funding",
                            {"vault_account_id": vault_account_id,
                             "direction": "factory_credit"}, allow_negative=True)
        audit.record(conn, action="wallet_funded", user_id=admin_user_id,
                     account_id=factory_account_id, resource_type="wallet",
                     resource_id=factory_account_id,
                     changes={"amount_cents": amount_cents})
        conn.commit()
        return vault_eid, factory_eid
    except Exception:
        conn.rollback()
        raise
