"""طبقة العزل بين المستأجرين — the tenant-scoping data layer.

القلب الأمني: **كل** عملية على كيان مُستأجَر تمرّ من هنا وتُقيَّد بعمود المالك
(`owner_id`/`account_id`). قراءة بمعرّف لحساب آخر تُعيد None (فتترجمها النقطة
النهائية إلى 404 لا 403 — لا تُسرَّب معلومة الوجود)، والكتابة/الحذف عبر
المستأجر لا يمسّان صفّاً واحداً. لا استعلام مُستأجَر يُكتب خارج هذه الطبقة.

Every tenant-scoped read/write is filtered by the owner column here. A get for
another account's row returns None; a cross-tenant update/delete touches zero
rows. The DB is the enforcement point — never the UI.
"""
from __future__ import annotations

import sqlite3

from .db import now_iso

# الأعمدة المسموح الكتابة عليها لكل جدول (allowlist) — يمنع حقن owner_id أو
# أعمدة النظام عبر جسم الطلب. Per-table writable-column allowlist.
_WRITABLE: dict[str, set[str]] = {
    "studies": {"title_en", "title_ar", "description_en", "description_ar",
                "target_count", "smtp_config_id", "created_by_user_id"},
    "prospects": {"email", "first_name", "last_name", "company", "industry",
                  "language_preference", "tags"},
    "smtp_configs": {"label", "host", "port", "username_enc", "password_enc",
                     "from_email", "from_name", "use_tls", "is_active"},
    "drafts": {"study_id", "subject_en", "subject_ar", "body_en", "body_ar", "version"},
    "images": {"filename", "storage_key", "mime_type", "size_bytes",
               "uploaded_by_user_id", "alt_text_en", "alt_text_ar"},
    # `comparison_funnels` غير مُدرَج عمداً: عمود `state` آلةُ حالاتٍ ببوّابة
    # طبقة (Gold/Platinum) وقيود لكل انتقال، فإدراجه في CRUD العامّ يفتح باباً
    # ثانياً يتخطّى تلك البوّابات. موجة القمع (PR-7) وصلت و**أبقته خارج القائمة**
    # عن قصد: مسارها الوحيد `silk_platform/funnels.py` بنفس نمط `lifecycle.py`.
    # Deliberately absent: a gated state machine must not be generic-CRUD
    # writable. PR-7 shipped the funnel and kept it out — see funnels.py.
}

# جداول بلا عمود `updated_at` — الطابع الزمني عندها هو عمود الإنشاء وحده.
# Tables whose only timestamp is their creation column (no updated_at).
_NO_UPDATED_AT = {"images"}
_CREATED_COL = {"images": "uploaded_at"}


class TenantRepository:
    """مستودع مقيّد بالمالك — a repository bound to one owner column.

    يُنشأ لكل جدول مُستأجَر؛ توقيعات الدوال تُلزِم تمرير account_id دائماً
    فلا يمكن نسيان النطاق. The account_id argument is mandatory by construction.
    """

    def __init__(self, conn: sqlite3.Connection, table: str,
                 owner_col: str = "owner_id"):
        if table not in _WRITABLE:
            raise ValueError(f"unknown tenant table: {table}")
        self.conn = conn
        self.table = table
        self.owner_col = owner_col

    # ── قراءة · reads ────────────────────────────────────────────────────────
    def list(self, account_id: int, *, where: str = "",
             params: tuple = (), order: str = "id DESC",
             limit: int | None = None) -> list[dict]:
        """اسرد صفوف الحساب فقط — rows for this account only, never others."""
        sql = f"SELECT * FROM {self.table} WHERE {self.owner_col} = ?"
        args: list = [account_id]
        if where:
            sql += f" AND ({where})"
            args.extend(params)
        sql += f" ORDER BY {order}"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(int(limit))
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def get(self, account_id: int, row_id: int) -> dict | None:
        """اقرأ صفّاً بمعرّفه ضمن الحساب — returns None if it belongs to another."""
        row = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE id = ? AND {self.owner_col} = ?",
            (row_id, account_id)).fetchone()
        return dict(row) if row else None

    def exists_anywhere(self, row_id: int) -> bool:
        """هل المعرّف موجود لأي حساب؟ — internal: distinguishes missing vs foreign.

        تستعمله النقطة النهائية لتقرّر وسم محاولة عبور المستأجر في التدقيق؛
        الردّ للعميل يبقى 404 في الحالتين (لا تسريب وجود).
        """
        row = self.conn.execute(
            f"SELECT 1 FROM {self.table} WHERE id = ?", (row_id,)).fetchone()
        return row is not None

    # ── كتابة · writes (always stamped with the caller's account) ────────────
    def create(self, account_id: int, fields: dict) -> dict:
        """أنشئ صفّاً مملوكاً للحساب — insert; owner column is forced, never trusted.

        القيم None تُسقَط كي تُطبَّق افتراضيات الأعمدة (لا نكتب None فوق DEFAULT
        NOT NULL). Omitted (None) values are dropped so column defaults apply.
        """
        clean = {k: v for k, v in fields.items()
                 if k in _WRITABLE[self.table] and v is not None}
        clean[self.owner_col] = account_id
        now = now_iso()
        clean[_CREATED_COL.get(self.table, "created_at")] = now
        if self.table not in _NO_UPDATED_AT:
            clean["updated_at"] = now
        cols = list(clean.keys())
        placeholders = ", ".join("?" for _ in cols)
        cur = self.conn.execute(
            f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES ({placeholders})",
            [clean[c] for c in cols])
        self.conn.commit()
        return self.get(account_id, int(cur.lastrowid))  # type: ignore[return-value]

    def update(self, account_id: int, row_id: int, fields: dict) -> dict | None:
        """حدّث صفّاً ضمن الحساب — no-op + None if the row is another tenant's.

        الشرط `AND owner_col = ?` يضمن أن تحديث عبر المستأجر يمسّ صفر صفوف؛
        القاعدة تبقى دون تغيير. Cross-tenant update changes zero rows.
        """
        clean = {k: v for k, v in fields.items() if k in _WRITABLE[self.table]}
        if not clean:
            return self.get(account_id, row_id)
        # لا تختم `updated_at` على جدول لا يملكه (images) — كان يفشل كل تحديث
        # لصورة بـ«no such column». Only stamp updated_at where the column exists.
        if self.table not in _NO_UPDATED_AT:
            clean["updated_at"] = now_iso()
        sets = ", ".join(f"{c} = ?" for c in clean)
        cur = self.conn.execute(
            f"UPDATE {self.table} SET {sets} WHERE id = ? AND {self.owner_col} = ?",
            [*clean.values(), row_id, account_id])
        self.conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get(account_id, row_id)

    def delete(self, account_id: int, row_id: int) -> bool:
        """احذف صفّاً ضمن الحساب — returns False (no-op) for a foreign row."""
        cur = self.conn.execute(
            f"DELETE FROM {self.table} WHERE id = ? AND {self.owner_col} = ?",
            (row_id, account_id))
        self.conn.commit()
        return cur.rowcount > 0


# ── مصانع مريحة · convenience factories ──────────────────────────────────────
def studies(conn: sqlite3.Connection) -> TenantRepository:
    return TenantRepository(conn, "studies")


def prospects(conn: sqlite3.Connection) -> TenantRepository:
    return TenantRepository(conn, "prospects")


def smtp_configs(conn: sqlite3.Connection) -> TenantRepository:
    return TenantRepository(conn, "smtp_configs")


def drafts(conn: sqlite3.Connection) -> TenantRepository:
    return TenantRepository(conn, "drafts")


def images(conn: sqlite3.Connection) -> TenantRepository:
    return TenantRepository(conn, "images")

# لا مصنع `funnels()` هنا **بقصدٍ دائم** لا انتظاراً: القمع (Gold/Platinum) يحتاج
# بوّابة طبقة وسقف دراسات وتحقّق ملكية على جدولَي وصلٍ بلا عمود مالك، وكلّها لا
# يعبّر عنها مستودعٌ عامّ. مساره الوحيد `silk_platform/funnels.py` (PR-7).
# No funnels() factory by permanent design (not "not yet") — the tier gate, the
# study cap, and the ownerless join tables can't be expressed by generic CRUD.
