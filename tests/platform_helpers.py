"""أدوات اختبار المنصّة — shared helpers for silk_platform tests (not collected).

كل اختبار يحصل على قاعدة منصّة معزولة (SILK_PLATFORM_DB في مجلّد مؤقّت) +
بيانات مبذورة + TestClient. لا شبكة، لا مفاتيح. Hermetic; no network, no keys.
"""
from __future__ import annotations

import os
import tempfile


def setup_env(monkeypatch) -> str:
    """قاعدة منصّة معزولة لكل اختبار — isolated platform DB; returns its path.

    يعزل أيضاً جذر تخزين الملفات (PR-8) في نفس المجلّد المؤقّت — بلا هذا كان
    رفع صورة في الاختبارات يكتب فعلياً داخل `data/` الحقيقي للريبو.
    Also isolates the file-storage root in the same temp dir — otherwise an
    uploaded-image test would write real files into the repo's actual data/.
    """
    d = tempfile.mkdtemp()
    path = os.path.join(d, "platform.db")
    monkeypatch.setenv("SILK_PLATFORM_DB", path)
    monkeypatch.setenv("SILK_PLATFORM_SECRET", "fixed-test-secret-do-not-use-in-prod")
    monkeypatch.setenv("SILK_PLATFORM_STORAGE_DIR", os.path.join(d, "files"))
    from silk_platform import db as pdb
    pdb.init_db(path, force=True)
    return path


def seed(monkeypatch) -> dict:
    """هيّئ + ابذر — set up an isolated DB and seed the standard fixture."""
    setup_env(monkeypatch)
    from silk_platform import db as pdb, seed as pseed
    conn = pdb.connect()
    try:
        info = pseed.seed(conn)
    finally:
        conn.close()
    return info


def client():
    """TestClient على تطبيق منصّة جديد — a TestClient over a fresh platform app."""
    from fastapi.testclient import TestClient
    from silk_platform.api import create_platform_app
    return TestClient(create_platform_app())


def login(cl, email: str, password: str) -> str:
    """سجّل الدخول وأعِد الرمز — POST /platform/auth/login → raw token."""
    r = cl.post("/platform/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def hdr(token: str) -> dict:
    """ترويسة المصادقة — Authorization: Bearer header."""
    return {"Authorization": f"Bearer {token}"}


def make_factory(tier: str, email: str, password: str = "Factory1234",
                 *, fund_cents: int = 0):
    """أنشئ حساب مصنع + مستخدم (+ تمويل) — a factory account/user with a wallet.

    مباشرةً في القاعدة (أسرع وأدقّ من مسار الأدمِن للحالات الحديّة). يرجّع dict.
    """
    from silk_platform import db as pdb, passwords, wallet
    from silk_platform.db import now_iso
    from silk_platform.models import Operation
    conn = pdb.connect()
    try:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO accounts (name, kind, is_vault, tier, created_at, "
            "updated_at) VALUES (?, 'factory', 0, ?, ?, ?)",
            (f"Factory-{tier}", tier, now, now))
        aid = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO users (account_id, email, password_hash, role, "
            "first_name, last_name, language_preference, created_at, updated_at) "
            "VALUES (?,?,?, 'factory', 'F', 'Owner', 'en', ?, ?)",
            (aid, email.lower(), passwords.hash_password(password), now, now))
        uid = int(cur.lastrowid)
        wallet.ensure_wallet(conn, aid)
        conn.commit()
        if fund_cents:
            # التمويل يمرّ بالدفتر لا بكتابة رصيد خام: التثبيت نفسه يجب أن يُنمذج
            # المسار المسموح، وإلا نُطبِّع الانحراف الذي وُجد الدفتر غير القابل
            # للتعديل لمنعه (رصيد بلا قيد = لا مُكتشِف له).
            # Fund through the ledger — never raw-UPDATE a balance, even in tests.
            wallet.post_entry(conn, account_id=aid, actor_user_id=uid,
                              operation=Operation.WALLET_FUNDED, amount=fund_cents,
                              description="test fixture funding")
    finally:
        conn.close()
    return {"account_id": aid, "user_id": uid, "email": email, "password": password,
            "tier": tier}


def add_active_smtp(account_id: int) -> int:
    """تهيئة SMTP نشطة للحساب — an active smtp_config owned by the account."""
    from silk_platform import db as pdb, crypto
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO smtp_configs (owner_id, label, host, port, username_enc, "
            "password_enc, from_email, from_name, use_tls, is_active, created_at, "
            "updated_at) VALUES (?, 'main', 'smtp.example.com', 587, ?, ?, "
            "'from@example.com', 'From', 1, 1, ?, ?)",
            (account_id, crypto.encrypt("user"), crypto.encrypt("pass"), now, now))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def make_draft_study(account_id: int, user_id: int, smtp_config_id: int,
                     target_count: int = 1) -> tuple[int, int]:
    """دراسة مسودّة + مسودّة رسالة — a draft study + a draft email; returns ids."""
    from silk_platform import db as pdb
    from silk_platform.db import now_iso
    conn = pdb.connect()
    try:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO studies (owner_id, title_en, state, target_count, "
            "smtp_config_id, created_by_user_id, created_at, updated_at) "
            "VALUES (?, 'S', 'draft', ?, ?, ?, ?, ?)",
            (account_id, target_count, smtp_config_id, user_id, now, now))
        sid = int(cur.lastrowid)
        cur = conn.execute(
            "INSERT INTO drafts (owner_id, study_id, subject_en, body_en, version, "
            "created_at, updated_at) VALUES (?,?, 'Hi {{first_name}}', "
            "'Body {{first_name}}', 'A', ?, ?)", (account_id, sid, now, now))
        did = int(cur.lastrowid)
        conn.commit()
        return sid, did
    finally:
        conn.close()
