"""اختبارات المصادقة (القسم ١٣: AUTH) — Section 13 auth acceptance.

login صحيح ينشئ جلسة (رمز مجزّأ، الخام مرّة واحدة)؛ الخاطئ 401 بلا تعداد؛
الرموز المنتهية/المزوّرة 401؛ الجلسات المتزامنة مستقلّة؛ last_activity يتحدّث؛
تخزين bcrypt/scrypt فقط؛ رموز إعادة تعيين أحادية الاستخدام.
"""
import datetime

import pytest

from platform_helpers import client, hdr, login, seed
from silk_platform import db as pdb


def _sessions(token_hash_like=None):
    conn = pdb.connect()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM sessions").fetchall()]
    finally:
        conn.close()


def test_login_success_creates_hashed_session_token_returned_once(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    r = cl.post("/platform/auth/login",
                json={"email": info["admin"]["email"],
                      "password": info["admin"]["password"]})
    assert r.status_code == 200, r.text
    raw = r.json()["token"]
    assert raw and len(raw) > 20
    # الرمز مُخزَّن مجزّأً فقط — the raw token never appears in the DB.
    sessions = _sessions()
    assert len(sessions) == 1
    assert raw not in [s["token_hash"] for s in sessions]
    import hashlib
    assert sessions[0]["token_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    # httpOnly cookie set.
    assert "silk_session" in r.cookies or "set-cookie" in {k.lower() for k in r.headers}


def test_invalid_login_401_no_user_enumeration(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    unknown = cl.post("/platform/auth/login",
                      json={"email": "nobody@nowhere.local", "password": "Whatever12"})
    wrongpw = cl.post("/platform/auth/login",
                      json={"email": info["admin"]["email"], "password": "WrongPass9"})
    assert unknown.status_code == 401 and wrongpw.status_code == 401
    # نفس الرسالة للحالتين — identical response shape (no enumeration signal).
    assert unknown.json() == wrongpw.json()


def test_passwords_stored_hashed_never_plaintext(monkeypatch):
    info = seed(monkeypatch)
    conn = pdb.connect()
    try:
        rows = conn.execute("SELECT email, password_hash FROM users").fetchall()
    finally:
        conn.close()
    assert rows
    for row in rows:
        h = row["password_hash"]
        assert h and (h.startswith("$2") or h.startswith("$scrypt$"))
        assert "Admin1234" not in h and "Factory1234" not in h


def test_expired_token_401(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    token = login(cl, info["admin"]["email"], info["admin"]["password"])
    # زوّر انتهاءً في الماضي — force expiry into the past.
    conn = pdb.connect()
    try:
        conn.execute("UPDATE sessions SET expires_at = ?",
                     ("2000-01-01T00:00:00Z",))
        conn.commit()
    finally:
        conn.close()
    r = cl.get("/platform/me", headers=hdr(token))
    assert r.status_code == 401


def test_tampered_token_401(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    token = login(cl, info["admin"]["email"], info["admin"]["password"])
    r = cl.get("/platform/me", headers=hdr(token + "TAMPER"))
    assert r.status_code == 401


def test_concurrent_sessions_independent(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    t1 = login(cl, info["admin"]["email"], info["admin"]["password"])
    t2 = login(cl, info["admin"]["email"], info["admin"]["password"])
    assert t1 != t2
    assert cl.get("/platform/me", headers=hdr(t1)).status_code == 200
    assert cl.get("/platform/me", headers=hdr(t2)).status_code == 200
    # تسجيل خروج جلسة لا يمسّ الأخرى — logout of one leaves the other live.
    assert cl.post("/platform/auth/logout", headers=hdr(t1)).status_code == 200
    assert cl.get("/platform/me", headers=hdr(t1)).status_code == 401
    assert cl.get("/platform/me", headers=hdr(t2)).status_code == 200


def test_last_activity_updates_and_window_slides(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    token = login(cl, info["admin"]["email"], info["admin"]["password"])
    old = (datetime.datetime.now(datetime.timezone.utc)
           - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = pdb.connect()
    try:
        conn.execute("UPDATE sessions SET last_activity_at = ?, expires_at = ?",
                     (old, "2999-01-01T00:00:00Z"))
        conn.commit()
    finally:
        conn.close()
    assert cl.get("/platform/me", headers=hdr(token)).status_code == 200
    conn = pdb.connect()
    try:
        s = conn.execute("SELECT last_activity_at FROM sessions").fetchone()
    finally:
        conn.close()
    assert s["last_activity_at"] != old  # slid forward on the request


def test_reset_token_single_use_invalidates_sessions(monkeypatch):
    info = seed(monkeypatch)
    # علم اختبار صريح لكشف الرمز في الردّ — production never exposes it.
    monkeypatch.setenv("SILK_PLATFORM_EXPOSE_RESET_TOKEN", "1")
    cl = client()
    email = info["admin"]["email"]
    old_token = login(cl, email, info["admin"]["password"])
    # اطلب رمز إعادة تعيين — issue a reset token.
    rr = cl.post("/platform/auth/password-reset/request", json={"email": email})
    assert rr.status_code == 200
    reset = rr.json()["reset_token"]
    # استهلكه مرّة — consume once with a compliant password.
    ok = cl.post("/platform/auth/password-reset/confirm",
                 json={"token": reset, "new_password": "NewPass123"})
    assert ok.status_code == 200
    # كلمة المرور القديمة بطلت، الجديدة تعمل — old fails, new works.
    assert cl.post("/platform/auth/login",
                   json={"email": email, "password": info["admin"]["password"]}
                   ).status_code == 401
    assert cl.post("/platform/auth/login",
                   json={"email": email, "password": "NewPass123"}).status_code == 200
    # الرمز أحادي الاستخدام — reusing the token fails.
    assert cl.post("/platform/auth/password-reset/confirm",
                   json={"token": reset, "new_password": "Another12"}
                   ).status_code == 400
    # الجلسة القديمة أُبطلت بعد التغيير — old session invalidated.
    assert cl.get("/platform/me", headers=hdr(old_token)).status_code == 401


def test_reset_password_policy_enforced(monkeypatch):
    info = seed(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_EXPOSE_RESET_TOKEN", "1")
    cl = client()
    email = info["factory_a"]["email"]
    reset = cl.post("/platform/auth/password-reset/request",
                    json={"email": email}).json()["reset_token"]
    # كلمة ضعيفة (لا رقم، قصيرة) — weak password rejected with 422.
    weak = cl.post("/platform/auth/password-reset/confirm",
                   json={"token": reset, "new_password": "short"})
    assert weak.status_code == 422


def test_unknown_email_reset_does_not_reveal_absence(monkeypatch):
    info = seed(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_EXPOSE_RESET_TOKEN", "1")
    cl = client()
    known = cl.post("/platform/auth/password-reset/request",
                    json={"email": info["admin"]["email"]})
    ghost = cl.post("/platform/auth/password-reset/request",
                    json={"email": "ghost@nowhere.local"})
    # كلاهما 200 (لا تعداد)، والمجهول لا يُنتج رمزاً — both 200, no enumeration.
    assert known.status_code == 200 and ghost.status_code == 200
    assert "reset_token" in known.json() and "reset_token" not in ghost.json()


def test_deactivated_account_session_rejected(monkeypatch):
    """جلسة مستخدم في حساب معطّل تُرفض — a deactivated account's sessions die."""
    info = seed(monkeypatch)
    cl = client()
    token = login(cl, info["factory_a"]["email"], info["factory_a"]["password"])
    assert cl.get("/platform/me", headers=hdr(token)).status_code == 200
    conn = pdb.connect()
    try:
        conn.execute("UPDATE accounts SET is_active = 0 WHERE id = ?",
                     (info["factory_a"]["account_id"],))
        conn.commit()
    finally:
        conn.close()
    assert cl.get("/platform/me", headers=hdr(token)).status_code == 401


def test_reset_token_never_exposed_without_flag(monkeypatch):
    """أمنيّاً: الرمز الخام لا يُعاد في الردّ افتراضياً — no takeover vector."""
    info = seed(monkeypatch)  # flag NOT set → production default
    cl = client()
    r = cl.post("/platform/auth/password-reset/request",
                json={"email": info["admin"]["email"]})
    assert r.status_code == 200 and "reset_token" not in r.json()
