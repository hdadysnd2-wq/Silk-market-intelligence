"""بيانات البذر — seed data: 1 vault/Silk account, 1 admin, 1 analyst, 2 factories.

قابلة لإعادة النداء (idempotent): إن وُجد الأدمِن لا تُعاد الكتابة. كلمات المرور
الافتراضية للتطوير فقط وتُستبدَل بالبيئة في الإنتاج (SILK_SEED_*_PASSWORD).
Dev credentials only — override via env for any real deployment.
"""
from __future__ import annotations

import os
import sqlite3

from . import passwords, wallet
from .db import connect, init_db, now_iso
from .models import Operation

# رأس مال الخزنة الافتتاحي · vault opening capitalization ($1,000,000).
VAULT_OPENING_CENTS = 100_000_000

_DEFAULTS = {
    "admin": ("admin@silk.local", "Admin1234"),
    "analyst": ("analyst@silk.local", "Analyst1234"),
    "factory_a": ("owner@factory-a.local", "Factory1234"),
    "factory_b": ("owner@factory-b.local", "Factory1234"),
}


def _pw(kind: str) -> str:
    return os.environ.get(f"SILK_SEED_{kind.upper()}_PASSWORD", "").strip() \
        or _DEFAULTS[kind][1]


def _account(conn: sqlite3.Connection, *, name: str, kind: str,
             is_vault: int, tier: str) -> int:
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO accounts (name, kind, is_vault, tier, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?)", (name, kind, is_vault, tier, now, now))
    aid = int(cur.lastrowid)
    wallet.ensure_wallet(conn, aid)
    return aid


def _user(conn: sqlite3.Connection, *, account_id: int, email: str, password: str,
          role: str, first: str, last: str, lang: str = "en") -> int:
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO users (account_id, email, password_hash, role, first_name, "
        "last_name, language_preference, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (account_id, email.lower(), passwords.hash_password(password), role,
         first, last, lang, now, now))
    return int(cur.lastrowid)


def seed(conn: sqlite3.Connection, *, reset: bool = False) -> dict:
    """ابذر البيانات — create the standard fixture; returns created identities.

    idempotent: يتخطّى إن وُجد أدمِن (ما لم يُطلب reset). يرجّع القواميس مع
    كلمات المرور المستخدَمة كي يطبعها سكربت البذر (لا تُخزَّن نصّاً في القاعدة).
    """
    existing = conn.execute(
        "SELECT id FROM users WHERE role = 'silk_admin' LIMIT 1").fetchone()
    if existing and not reset:
        return {"seeded": False, "reason": "already seeded"}

    # 1) حساب سِلك = الخزنة · Silk operator account is the vault.
    vault_id = _account(conn, name="Silk (operator/vault)", kind="silk",
                        is_vault=1, tier="platinum")
    admin_id = _user(conn, account_id=vault_id, email=_DEFAULTS["admin"][0],
                     password=_pw("admin"), role="silk_admin",
                     first="Silk", last="Admin", lang="ar")
    analyst_id = _user(conn, account_id=vault_id, email=_DEFAULTS["analyst"][0],
                       password=_pw("analyst"), role="silk_analyst",
                       first="Silk", last="Analyst")

    # رأس المال الافتتاحي للخزنة · vault opening capitalization (consistent ledger).
    wallet.post_entry(conn, account_id=vault_id, actor_user_id=admin_id,
                      operation=Operation.WALLET_FUNDED,
                      amount=VAULT_OPENING_CENTS, description="vault opening balance",
                      metadata={"opening": True})

    # 2) حسابا مصنع · two factory accounts (Silver + Gold) with owners.
    fa_id = _account(conn, name="Factory A", kind="factory", is_vault=0, tier="silver")
    fa_user = _user(conn, account_id=fa_id, email=_DEFAULTS["factory_a"][0],
                    password=_pw("factory_a"), role="factory",
                    first="Factory", last="A-Owner")
    fb_id = _account(conn, name="Factory B", kind="factory", is_vault=0, tier="gold")
    fb_user = _user(conn, account_id=fb_id, email=_DEFAULTS["factory_b"][0],
                    password=_pw("factory_b"), role="factory",
                    first="Factory", last="B-Owner", lang="ar")
    conn.commit()

    return {
        "seeded": True,
        "vault_account_id": vault_id,
        "admin": {"id": admin_id, "email": _DEFAULTS["admin"][0],
                  "password": _pw("admin")},
        "analyst": {"id": analyst_id, "email": _DEFAULTS["analyst"][0],
                    "password": _pw("analyst")},
        "factory_a": {"account_id": fa_id, "user_id": fa_user,
                      "email": _DEFAULTS["factory_a"][0], "password": _pw("factory_a"),
                      "tier": "silver"},
        "factory_b": {"account_id": fb_id, "user_id": fb_user,
                      "email": _DEFAULTS["factory_b"][0], "password": _pw("factory_b"),
                      "tier": "gold"},
    }


def vault_account_id(conn: sqlite3.Connection) -> int | None:
    """معرّف حساب الخزنة — the single vault account id (or None if unseeded)."""
    row = conn.execute("SELECT id FROM accounts WHERE is_vault = 1 LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def main() -> None:  # pragma: no cover — سكربت CLI للبذر
    """python3 -m silk_platform.seed — initialize + seed the platform DB."""
    init_db()
    conn = connect()
    try:
        result = seed(conn)
    finally:
        conn.close()
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
