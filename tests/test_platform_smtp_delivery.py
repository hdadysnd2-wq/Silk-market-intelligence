"""PR-5: ناقل SMTP حقيقي + مفتاح idempotency + حاصد + إلغاء اشتراك + بريد إعادة تعيين.

كل عناصر PR-5 الناقصة في docs/PLATFORM_ROADMAP.md: ناقل SMTP حقيقي (حُقن الآن
بمُدخَل `smtp_cls` فلا يفتح اختبارٌ مقبساً حقيقياً)، Message-ID حتمي كمفتاح
idempotency، حاصد الصفوف العالقة في `sending`، رابط إلغاء اشتراك موقَّع
+صفحته الثنائية اللغة، وتوصيل بريد إعادة تعيين كلمة المرور عبر SMTP تشغيلي
مضبوط بالبيئة (منفصل عن smtp_configs المستأجَرة).
"""
from email.header import decode_header
from urllib.parse import urlparse

import pytest

from platform_helpers import add_active_smtp, client, make_factory, seed, setup_env
from silk_platform import api as api_mod
from silk_platform import crypto, db as pdb, email_queue, scheduler, smtp_transport, unsubscribe


class _FakeSMTP:
    """ناقل SMTP مزيَّف — captures calls; never touches a real socket."""
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=30.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.ehlo_calls = 0
        self.starttls_called = False
        self.login_args = None
        self.sent = None
        self.quit_called = False
        _FakeSMTP.instances.append(self)

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent = msg

    def quit(self):
        self.quit_called = True


class _FailingSMTP(_FakeSMTP):
    def login(self, user, password):
        raise RuntimeError("auth failed: password=hunter2")


# ════════════════════════════ smtp_transport ═════════════════════════════════
def test_send_builds_utf8_message_uses_tls_and_login():
    _FakeSMTP.instances.clear()
    smtp_transport.send(host="smtp.example.com", port=587, use_tls=True,
                        username="user@example.com", password="secret",
                        from_email="from@example.com", from_name="سِلك",
                        to_email="to@example.com", subject="مرحباً Hello",
                        body="نص Body", msg_id="<abc@x>", smtp_cls=_FakeSMTP)
    inst = _FakeSMTP.instances[-1]
    assert inst.starttls_called
    assert inst.login_args == ("user@example.com", "secret")
    assert inst.quit_called
    msg = inst.sent
    assert msg["Message-ID"] == "<abc@x>"
    assert msg["To"] == "to@example.com"
    parts = decode_header(msg["Subject"])
    text = "".join(t.decode(enc or "utf-8") if isinstance(t, bytes) else t
                   for t, enc in parts)
    assert text == "مرحباً Hello"


def test_send_skips_tls_and_login_when_unset():
    _FakeSMTP.instances.clear()
    smtp_transport.send(host="h", port=25, use_tls=False, username="", password="",
                        from_email="f@x.com", from_name="F", to_email="t@x.com",
                        subject="S", body="B", msg_id="<x@y>", smtp_cls=_FakeSMTP)
    inst = _FakeSMTP.instances[-1]
    assert not inst.starttls_called
    assert inst.login_args is None
    assert inst.quit_called  # still closed even without auth


def test_send_propagates_transport_errors_and_still_closes():
    _FakeSMTP.instances.clear()
    with pytest.raises(RuntimeError):
        smtp_transport.send(host="h", port=25, use_tls=False, username="u",
                            password="p", from_email="f@x.com", from_name="F",
                            to_email="t@x.com", subject="S", body="B",
                            msg_id="<x@y>", smtp_cls=_FailingSMTP)
    assert _FakeSMTP.instances[-1].quit_called  # finally-block cleanup ran


def test_send_rejects_header_injection_in_recipient():
    """بريدٌ محفوظ بلا تحقّق شكل (`prospects.email` منذ PR-1) لا يُدرِج ترويسات.

    لا شيء أعلى هذا الملف يتحقّق من شكل البريد؛ هنا أوّل نقطة تلمس ترويسة
    SMTP حقيقية فهي نقطة الفرض الصحيحة — CRLF مُضمَّن يُرفَض بدل أن يُدرِج
    `Bcc:` أو مستلماً إضافياً عبر envelope المُشتقّ من الترويسات.
    """
    _FakeSMTP.instances.clear()
    with pytest.raises(ValueError):
        smtp_transport.send(host="h", port=25, use_tls=False, username="",
                            password="", from_email="f@x.com", from_name="F",
                            to_email="victim@x.com\r\nBcc: attacker@evil.com",
                            subject="S", body="B", msg_id="<x@y>", smtp_cls=_FakeSMTP)
    assert _FakeSMTP.instances == []  # rejected before any connection opened


def test_message_id_is_deterministic_per_row():
    """حتميّة المعرّف = مفتاح idempotency التسليم — same row ⇒ same Message-ID."""
    assert smtp_transport.message_id("email", 42) == smtp_transport.message_id("email", 42)
    assert smtp_transport.message_id("email", 42) != smtp_transport.message_id("email", 43)


def test_operator_config_none_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SILK_PLATFORM_OPERATOR_SMTP_HOST", raising=False)
    monkeypatch.delenv("SILK_PLATFORM_OPERATOR_SMTP_FROM_EMAIL", raising=False)
    assert smtp_transport.operator_config_from_env() is None


def test_operator_config_present_when_configured(monkeypatch):
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_HOST", "smtp.op.local")
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_FROM_EMAIL", "noreply@silk.local")
    cfg = smtp_transport.operator_config_from_env()
    assert cfg["host"] == "smtp.op.local"
    assert cfg["port"] == 587 and cfg["use_tls"] is True
    assert cfg["from_email"] == "noreply@silk.local"


# ════════════════════════════ email_queue.sender adapter ═════════════════════
def test_sender_decrypts_stored_creds_before_transport(monkeypatch):
    captured = {}
    monkeypatch.setattr(email_queue.smtp_transport, "send",
                        lambda **kw: captured.update(kw))
    smtp_config = {"host": "h", "port": 587, "use_tls": 1,
                  "username_enc": crypto.encrypt("user"),
                  "password_enc": crypto.encrypt("pass"),
                  "from_email": "f@x.com", "from_name": "F"}
    email_row = {"id": 7, "prospect_email": "to@x.com", "subject": "S", "body": "B"}
    email_queue.sender(smtp_config, email_row)
    assert captured["username"] == "user" and captured["password"] == "pass"
    assert captured["msg_id"] == smtp_transport.message_id("email", 7)
    assert captured["to_email"] == "to@x.com"


# ════════════════════════════ unsubscribe link injection ═════════════════════
def test_enqueue_injects_valid_unsubscribe_link(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "linktest@f.local")
    conn = pdb.connect()
    draft = {"subject_en": "Hi", "body_en": "Bye {{unsubscribe_link}}"}
    prospect = {"email": "p@x.com", "language_preference": "en"}
    eid = email_queue.enqueue(conn, account_id=f["account_id"], study_id=None,
                              prospect=prospect, draft=draft, smtp_config_id=None,
                              actor_user_id=f["user_id"])
    row = conn.execute("SELECT body FROM email_queue WHERE id = ?", (eid,)).fetchone()
    parsed = urlparse(row["body"].split("Bye ", 1)[1].strip())
    qs = dict(p.split("=", 1) for p in parsed.query.split("&"))
    from urllib.parse import unquote
    assert unsubscribe.verify(int(qs["a"]), unquote(qs["e"]), qs["s"])


# ════════════════════════════ reaper ══════════════════════════════════════════
def test_reap_stuck_requeues_recent_and_fails_exhausted(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "reap1@f.local")
    conn = pdb.connect()
    old = "2020-01-01T00:00:00Z"
    conn.execute("INSERT INTO email_queue (account_id, prospect_email, status, "
                "attempts, claimed_at, queued_at) VALUES (?,?, 'sending', 1, ?, ?)",
                (f["account_id"], "a@x.com", old, old))
    conn.execute("INSERT INTO email_queue (account_id, prospect_email, status, "
                "attempts, claimed_at, queued_at) VALUES (?,?, 'sending', 5, ?, ?)",
                (f["account_id"], "b@x.com", old, old))
    conn.commit()
    result = email_queue.reap_stuck(conn, stale_after_seconds=1, max_attempts=5)
    rows = {r["prospect_email"]: r["status"] for r in
            conn.execute("SELECT prospect_email, status FROM email_queue").fetchall()}
    assert rows["a@x.com"] == "queued"
    assert rows["b@x.com"] == "failed"
    assert len(result["requeued"]) == 1 and len(result["failed"]) == 1


def test_reap_stuck_ignores_fresh_claims(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "reap2@f.local")
    conn = pdb.connect()
    now = pdb.now_iso()
    conn.execute("INSERT INTO email_queue (account_id, prospect_email, status, "
                "attempts, claimed_at, queued_at) VALUES (?,?, 'sending', 1, ?, ?)",
                (f["account_id"], "c@x.com", now, now))
    conn.commit()
    email_queue.reap_stuck(conn, stale_after_seconds=900, max_attempts=5)
    status = conn.execute("SELECT status FROM email_queue WHERE prospect_email = ?",
                          ("c@x.com",)).fetchone()["status"]
    assert status == "sending"


# ════════════════════════════ scheduler wiring ════════════════════════════════
def test_run_email_pass_reaps_then_processes_with_injected_sender(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "sched1@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    conn = pdb.connect()
    draft = {"subject_en": "S", "body_en": "B"}
    prospect = {"email": "p2@x.com", "language_preference": "en"}
    eid = email_queue.enqueue(conn, account_id=f["account_id"], study_id=None,
                              prospect=prospect, draft=draft, smtp_config_id=smtp,
                              actor_user_id=f["user_id"])
    sent = []
    result = scheduler.run_email_pass(conn, sender=lambda cfg, row: sent.append(row["id"]))
    assert eid in sent
    status = conn.execute("SELECT status FROM email_queue WHERE id = ?",
                          (eid,)).fetchone()["status"]
    assert status == "sent"
    assert result["reaped"] == {"requeued": [], "failed": []}


def test_run_email_pass_defaults_to_real_sender(monkeypatch):
    setup_env(monkeypatch)
    f = make_factory("gold", "sched2@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    conn = pdb.connect()
    calls = []
    monkeypatch.setattr(email_queue, "sender", lambda cfg, row: calls.append(row["id"]))
    draft = {"subject_en": "S", "body_en": "B"}
    prospect = {"email": "p3@x.com", "language_preference": "en"}
    eid = email_queue.enqueue(conn, account_id=f["account_id"], study_id=None,
                              prospect=prospect, draft=draft, smtp_config_id=smtp,
                              actor_user_id=f["user_id"])
    scheduler.run_email_pass(conn)   # sender omitted ⇒ falls back to email_queue.sender
    assert eid in calls


# ════════════════════════════ unsubscribe endpoint ════════════════════════════
def _path_and_query(url: str) -> str:
    p = urlparse(url)
    return p.path + "?" + p.query


def test_unsubscribe_valid_link_updates_suppression_and_consent(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "unsub1@f.local")
    conn = pdb.connect()
    now = pdb.now_iso()
    conn.execute(
        "INSERT INTO consent_registry (prospect_email, study_id, sending_account_id, "
        "approving_user_id, message_verbatim, sent_at, consent_granted_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("p3@x.com", None, f["account_id"], f["user_id"], "Subject: S\n\nB", now, now))
    conn.commit()
    url = unsubscribe.build_url(f["account_id"], "p3@x.com")
    cl = client()
    r = cl.get(_path_and_query(url))
    assert r.status_code == 200
    assert "unsubscribe" in r.text.lower() and "إلغاء" in r.text
    supp = conn.execute("SELECT 1 FROM suppression_list WHERE account_id = ? AND "
                        "email = ?", (f["account_id"], "p3@x.com")).fetchone()
    assert supp is not None
    consent = conn.execute("SELECT unsubscribed_at FROM consent_registry WHERE "
                           "sending_account_id = ? AND prospect_email = ?",
                           (f["account_id"], "p3@x.com")).fetchone()
    assert consent["unsubscribed_at"] is not None


def test_unsubscribe_invalid_signature_is_400_and_writes_nothing(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "unsub2@f.local")
    cl = client()
    r = cl.get(f"/platform/unsubscribe?a={f['account_id']}&e=x@y.com&s=deadbeef")
    assert r.status_code == 400
    assert "إلغاء" in r.text or "invalid" in r.text.lower()
    conn = pdb.connect()
    supp = conn.execute("SELECT 1 FROM suppression_list WHERE account_id = ? AND "
                        "email = ?", (f["account_id"], "x@y.com")).fetchone()
    assert supp is None


def test_unsubscribe_double_click_is_idempotent(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "unsub3@f.local")
    url = unsubscribe.build_url(f["account_id"], "p4@x.com")
    cl = client()
    r1 = cl.get(_path_and_query(url))
    r2 = cl.get(_path_and_query(url))
    assert r1.status_code == 200 and r2.status_code == 200


def test_unsubscribe_then_future_send_is_suppressed(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "unsub4@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    conn = pdb.connect()
    url = unsubscribe.build_url(f["account_id"], "p5@x.com")
    cl = client()
    cl.get(_path_and_query(url))
    draft = {"subject_en": "S", "body_en": "B"}
    prospect = {"email": "p5@x.com", "language_preference": "en"}
    eid = email_queue.enqueue(conn, account_id=f["account_id"], study_id=None,
                              prospect=prospect, draft=draft, smtp_config_id=smtp,
                              actor_user_id=f["user_id"])
    summary = email_queue.process_queue(conn, sender=lambda cfg, row: None)
    assert eid in summary["suppressed"]


# ════════════════════════════ password-reset delivery ═════════════════════════
def test_password_reset_sends_email_when_operator_smtp_configured(monkeypatch):
    info = seed(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_HOST", "smtp.op.local")
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_FROM_EMAIL", "noreply@silk.local")
    captured = {}
    monkeypatch.setattr(api_mod.smtp_transport, "send", lambda **kw: captured.update(kw))
    cl = client()
    r = cl.post("/platform/auth/password-reset/request",
               json={"email": info["factory_a"]["email"]})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert captured["to_email"] == info["factory_a"]["email"]
    assert "reset-password?token=" in captured["body"]


def test_password_reset_no_crash_when_operator_smtp_unconfigured(monkeypatch):
    info = seed(monkeypatch)
    monkeypatch.delenv("SILK_PLATFORM_OPERATOR_SMTP_HOST", raising=False)
    cl = client()
    r = cl.post("/platform/auth/password-reset/request",
               json={"email": info["factory_a"]["email"]})
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_password_reset_email_failure_is_audited_not_raised(monkeypatch):
    info = seed(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_HOST", "smtp.op.local")
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_FROM_EMAIL", "noreply@silk.local")

    def boom(**kw):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(api_mod.smtp_transport, "send", boom)
    cl = client()
    r = cl.post("/platform/auth/password-reset/request",
               json={"email": info["factory_a"]["email"]})
    assert r.status_code == 200 and r.json() == {"ok": True}
    conn = pdb.connect()
    row = conn.execute("SELECT 1 FROM audit_log WHERE "
                       "action = 'password_reset_email_failed'").fetchone()
    assert row is not None


def test_password_reset_unknown_email_never_attempts_send(monkeypatch):
    seed(monkeypatch)
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_HOST", "smtp.op.local")
    monkeypatch.setenv("SILK_PLATFORM_OPERATOR_SMTP_FROM_EMAIL", "noreply@silk.local")
    called = []
    monkeypatch.setattr(api_mod.smtp_transport, "send", lambda **kw: called.append(1))
    cl = client()
    r = cl.post("/platform/auth/password-reset/request",
               json={"email": "nobody@nowhere.local"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert called == []
