"""حُرّاس تأسيس المنصّة — البذر المشروط عند الإقلاع + جهوزيّة /health.

**البلاغ الحيّ الذي أنتج هذا الملف:** الشاشة شُحنت وعملت، ثم «ما يعمل». والسبب
أن `init_db()` يُنشئ الجداول ولا شيء يبذر عند الإقلاع، فالإنتاج يُقلِع بجداولَ
سليمة و**صفر مستخدمين** — فكل دخول يُرفَض بـ«invalid credentials» **لا تُميَّز
عن كلمة مرور خاطئة**، فيستحيل التشخيص عن بُعد.

الاختبار الأوّل هنا **يُعيد إنتاج الأعراض بالضبط**، والبقيّة تُثبِت أن البوّابة
تفتح بلا أن تُفتَح صمتاً.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading

import pytest

from tests.platform_helpers import client, hdr, setup_env


def _readiness() -> dict:
    from silk_platform import bootstrap, db as pdb
    conn = pdb.connect()
    try:
        return bootstrap.readiness(conn)
    finally:
        conn.close()


def _clear_gate(monkeypatch) -> None:
    from silk_platform import bootstrap
    for env in bootstrap._IDENTITY_ENV.values():
        monkeypatch.delenv(env, raising=False)


# ═══════════ إعادة إنتاج البلاغ · reproduce the live report ══════════════════
def test_without_the_gate_the_db_has_tables_but_zero_users(monkeypatch):
    """**أعراض البلاغ حرفياً**: جداول سليمة، صفر مستخدمين، وكل دخول 401.

    و401 هنا نصّه «invalid credentials» — لا يقول «لا حساب أصلاً»، وهذا بعينه
    ما جعل «ما يعمل» غير قابل للتشخيص عن بُعد.
    """
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    cl = client()                     # الإقلاع يمرّ ببوّابة التأسيس ولا يبذر
    r = cl.post("/platform/auth/login",
                json={"email": "admin@silk.local", "password": "anything"})
    assert r.status_code == 401
    ready = _readiness()
    assert ready["users"] == 0 and ready["accounts"] == 0
    assert ready["seeded"] is False
    assert ready["seed_gate_set"] is False      # ⇐ يشرح **لماذا**


def test_readiness_explains_why_login_fails(monkeypatch):
    """الجهوزيّة تحمل سبب الفشل لا مجرّد أنه فشل — أعدادٌ + حالة البوّابة."""
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    client()
    ready = _readiness()
    assert set(ready) == {"seeded", "users", "accounts", "seed_gate_set"}
    # لا بريد ولا كلمة مرور — `/health` عامّة.
    assert not any(isinstance(v, str) and "@" in v for v in ready.values())


# ═══════════════════ البوّابة تفتح · the gate opens ═══════════════════════════
def test_with_the_gate_set_boot_seeds_and_login_works(monkeypatch):
    """المتغيّر مضبوط ⇒ الإقلاع يبذر ⇒ الدخول ينجح فعلاً بالكلمة المختارة."""
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    monkeypatch.setenv("SILK_SEED_ADMIN_PASSWORD", "AdminChosen1234")
    monkeypatch.setenv("SILK_SEED_FACTORY_A_PASSWORD", "FactoryChosen1234")
    cl = client()
    ready = _readiness()
    assert ready["seeded"] is True and ready["users"] > 0
    for email, pw in (("admin@silk.local", "AdminChosen1234"),
                      ("owner@factory-a.local", "FactoryChosen1234")):
        r = cl.post("/platform/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 200, f"{email}: {r.text}"
        tok = r.json()["token"]
        assert cl.get("/platform/me", headers=hdr(tok)).status_code == 200


def test_seeding_is_idempotent_across_boots(monkeypatch):
    """إقلاعٌ ثانٍ لا يُعيد البذر ولا يغيّر كلمة مرور قائمة.

    لو أعاد الضبط لكان كل نشرٍ يُبطل كلمات المرور المستعملة.
    """
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    monkeypatch.setenv("SILK_SEED_ADMIN_PASSWORD", "AdminChosen1234")
    client()
    first = _readiness()
    # «أعِد النشر» بكلمة مرور مختلفة في البيئة — لا يجوز أن تُطبَّق.
    monkeypatch.setenv("SILK_SEED_ADMIN_PASSWORD", "DifferentPw9999")
    cl = client()
    assert _readiness()["users"] == first["users"]      # لا مستخدمين جدد
    assert cl.post("/platform/auth/login",
                   json={"email": "admin@silk.local",
                         "password": "DifferentPw9999"}).status_code == 401
    assert cl.post("/platform/auth/login",
                   json={"email": "admin@silk.local",
                         "password": "AdminChosen1234"}).status_code == 200


def test_concurrent_boots_seed_exactly_once(monkeypatch):
    """عمّالٌ متعدّدون يُقلِعون معاً ⇒ بذرٌ واحد، بلا استثناء يُسقِط أحدهم.

    **ما يُثبِته هذا الاختبار بدقّة، ولا أكثر:** الناتج النهائي صحيح (أدمِن
    واحد، خزنة واحدة، `seeded=True` مرّة واحدة) ولا عاملَ يسقط باستثناء.

    **وما لا يُثبِته — وأصرّح به بدل أن أدّعيه:** `BEGIN IMMEDIATE` ومعالجُ
    `IntegrityError` في `maybe_seed` **دفاعٌ زائد** (defense-in-depth) لا يعزله
    هذا الاختبار. فحصتُ ذلك عملياً: حذفُ أيٍّ منهما **يُبقي الاختبار أخضر**، لأن
    `seed()` نفسه خامل التكرار (يفحص وجود `silk_admin` ويعود مبكراً)، ولأن
    القسم الحرج أقصر من ميلي ثانية فتتسلسل الخيوط بحكم GIL.

    فقيمة الطبقتين احتياطية: لو تغيّر `seed()` يوماً وفقد فحصه الداخلي، أو
    استُدعي بـ`reset=True`، يبقى الناتج سليماً. لا أزعم أن هذا الاختبار يحرسهما.
    Proves the outcome, NOT the lock: removing either layer keeps this green
    because seed() is independently idempotent. Stated, not implied.
    """
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    monkeypatch.setenv("SILK_SEED_ADMIN_PASSWORD", "AdminChosen1234")
    from silk_platform import bootstrap, db as pdb
    pdb.init_db(force=False)
    results: list[dict] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def boot(_i):
        try:
            barrier.wait()
            conn = pdb.connect()          # اتصال لكل خيط
            try:
                results.append(bootstrap.maybe_seed(conn))
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=boot, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"إقلاعٌ فشل بلا داعٍ: {errors}"
    assert sum(1 for r in results if r.get("seeded")) == 1, results
    conn = pdb.connect()
    try:
        admins = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE role = 'silk_admin'").fetchone()["c"]
        vaults = conn.execute(
            "SELECT COUNT(*) c FROM accounts WHERE is_vault = 1").fetchone()["c"]
    finally:
        conn.close()
    assert admins == 1 and vaults == 1, f"admins={admins} vaults={vaults}"


# ═══════════════════ لا تسريب سرّ · no secret ever logged ════════════════════
def test_no_password_is_ever_written_to_the_log(monkeypatch, caplog):
    """السجلّ يذكر **أسماء الهويّات** لا كلمات المرور — ولو كانت صريحة في البيئة.

    سجلّات Railway محفوظة ومرئية؛ طبعُ كلمة مرور فيها يساوي تسريبها.
    """
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    secret_pw = "SuperSecretAdmin1234"
    monkeypatch.setenv("SILK_SEED_ADMIN_PASSWORD", secret_pw)
    monkeypatch.setenv("SILK_SEED_FACTORY_A_PASSWORD", "FactorySecret1234")
    with caplog.at_level(logging.DEBUG):
        client()
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_pw not in blob, "كلمة مرور الأدمِن ظهرت في السجلّ!"
    assert "FactorySecret1234" not in blob, "كلمة مرور المصنع ظهرت في السجلّ!"


def test_the_unset_gate_logs_actionable_guidance(monkeypatch, caplog):
    """بلا البوّابة يُسجَّل **ما يلزم فعله** لا صمتاً — التشخيص من السجلّ وحده."""
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    with caplog.at_level(logging.INFO):
        client()
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "SILK_SEED_ADMIN_PASSWORD" in blob
    assert "no users" in blob or "every login will be rejected" in blob


# ═══════════════════ التكامل مع /health · surfaced remotely ══════════════════
def test_health_exposes_platform_readiness(monkeypatch):
    """`/health` يحمل `storage.platform_ready` — التشخيص عن بُعد بلا وسيط."""
    setup_env(monkeypatch)
    _clear_gate(monkeypatch)
    import api as root_api
    ready = root_api._platform_readiness()
    assert ready is not None
    assert ready["users"] == 0 and ready["seed_gate_set"] is False


def test_readiness_survives_an_unmigrated_database(monkeypatch, tmp_path):
    """قاعدة بلا جداول ⇒ الجهوزيّة ترجع أعداداً سالبة لا تنهار.

    `/health` يجب أن يظلّ يعمل حتى لو تعذّرت الترحيلات — وإلا فقدنا أداة
    التشخيص في الحالة التي نحتاجها فيها أكثر ما نحتاج.
    """
    from silk_platform import bootstrap
    path = tmp_path / "empty.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        ready = bootstrap.readiness(conn)
    finally:
        conn.close()
    assert ready["users"] == -1 and ready["accounts"] == -1
    assert ready["seeded"] is False
