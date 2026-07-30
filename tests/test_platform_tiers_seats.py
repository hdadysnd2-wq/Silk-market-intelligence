"""اختبارات PR-2 — المقاعد، بوّابة الاستحقاقات، إدارة الطبقات، المجدول.

هرمتية بالكامل: قاعدة منصّة معزولة لكل اختبار، بلا شبكة وبلا مفاتيح.
Hermetic: isolated platform DB per test, no network, no keys.

التغطية مقصودة **في الاتجاهين** لكل بوّابة: تُمنع حين يجب، وتَسمح حين يجب —
بوّابة ترفض دائماً تجتاز اختبار «رفَضَت» وهي معطّلة فعلياً.
"""
from __future__ import annotations

import datetime

import pytest

from tests.platform_helpers import (add_active_smtp, client, hdr, login,
                                    make_draft_study, make_factory, seed)


# ══════════════════════ بوّابة الميزات · feature gate ═══════════════════════
def test_feature_gate_allows_and_denies_per_tier():
    """البوّابة تفتح للطبقة المستحقّة وتُغلق لغيرها — both directions."""
    from silk_platform import entitlements
    # القمع: Gold وPlatinum فقط.
    assert entitlements.has_feature("gold", "funnel") is True
    assert entitlements.has_feature("platinum", "funnel") is True
    assert entitlements.has_feature("basic", "funnel") is False
    assert entitlements.has_feature("silver", "funnel") is False
    # التصدير/العلامة البيضاء/الـAPI: Platinum فقط.
    for feat in ("api_access", "white_label", "export"):
        assert entitlements.has_feature("platinum", feat) is True
        assert entitlements.has_feature("gold", feat) is False


def test_require_feature_raises_with_upgrade_prompt():
    """المنع يحمل دعوة ترقية قابلة للقراءة آلياً — §2 'show upgrade prompt'."""
    from silk_platform import entitlements
    with pytest.raises(entitlements.TierGateError) as exc:
        entitlements.require_feature("silver", "funnel")
    detail = exc.value.as_detail()
    assert detail["error"] == "tier_gate"
    assert detail["upgrade"] is True
    assert detail["feature"] == "funnel"
    assert detail["tier"] == "silver"
    # الطبقات التي تمنحها فعلاً — لا رسالة عامّة.
    assert set(detail["required_tiers"]) == {"gold", "platinum"}


def test_require_feature_passes_silently_when_granted():
    """الطبقة المستحقّة لا تُمنَع — the gate is not a blanket denial."""
    from silk_platform import entitlements
    entitlements.require_feature("platinum", "export")      # must not raise
    entitlements.require_feature("gold", "funnel")


def test_unknown_feature_raises_instead_of_silently_denying():
    """ميزة مجهولة خطأٌ صريح — a typo must not silently gate everyone out."""
    from silk_platform import entitlements
    with pytest.raises(ValueError):
        entitlements.has_feature("platinum", "exports")     # typo


# ══════════════════════════ المقاعد · seats ═════════════════════════════════
def test_seat_limits_match_the_spec_per_tier():
    from silk_platform import entitlements
    assert entitlements.seat_limit("basic") == 1
    assert entitlements.seat_limit("silver") == 3
    assert entitlements.seat_limit("gold") == 10
    assert entitlements.seat_limit("platinum") == entitlements.UNLIMITED


def test_basic_account_cannot_add_a_second_user(monkeypatch):
    """Basic مقعد واحد: المستخدم الثاني يُمنَع 403 بدعوة ترقية + قيد تدقيق."""
    seed(monkeypatch)
    f = make_factory("basic", "basic-seat@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "second@example.com", "password": "Second1234"})
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "tier_gate" and detail["feature"] == "users"
    assert detail["upgrade"] is True
    assert detail["limit"] == 1 and detail["used"] == 1
    # قيد التدقيق مطلوب صراحةً في §2 · the audit entry is part of the contract.
    from silk_platform import audit, db as pdb
    conn = pdb.connect()
    try:
        rows = audit.search(conn, account_id=f["account_id"],
                            action="seat_limit_exceeded")
    finally:
        conn.close()
    assert len(rows) == 1


def test_silver_fills_three_seats_then_blocks_the_fourth(monkeypatch):
    """Silver ثلاثة مقاعد: الثاني والثالث ينجحان والرابع يُمنَع."""
    seed(monkeypatch)
    f = make_factory("silver", "silver-seat@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    # المالك يشغل المقعد الأوّل؛ اثنان إضافيان مسموحان.
    for i in (2, 3):
        r = cl.post("/platform/users", headers=hdr(tok),
                    json={"email": f"u{i}@example.com", "password": "Member1234"})
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "factory"
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "u4@example.com", "password": "Member1234"})
    assert r.status_code == 403
    assert r.json()["detail"]["used"] == 3 and r.json()["detail"]["limit"] == 3


def test_platinum_seats_are_unlimited(monkeypatch):
    """Platinum بلا سقف مقاعد — the unlimited sentinel must not block."""
    seed(monkeypatch)
    f = make_factory("platinum", "plat-seat@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    for i in range(5):
        r = cl.post("/platform/users", headers=hdr(tok),
                    json={"email": f"p{i}@example.com", "password": "Member1234"})
        assert r.status_code == 200, r.text
    assert len(cl.get("/platform/users", headers=hdr(tok)).json()["users"]) == 6


def test_deactivating_a_user_frees_a_seat_and_reactivating_re_gates(monkeypatch):
    """التعطيل يُخلي مقعداً، وإعادة التنشيط تمرّ من البوّابة نفسها.

    لو لم تُبَوَّب إعادة التنشيط لكان «عطّل ثم نشّط» طريقاً لتجاوز السقف.
    """
    seed(monkeypatch)
    f = make_factory("basic", "basic-free@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    # Basic ممتلئ بالمالك ⇒ عطّل المالك؟ لا — لا يعطّل المرء نفسه. نرقّي مؤقّتاً.
    from silk_platform import db as pdb, entitlements
    conn = pdb.connect()
    try:
        conn.execute("UPDATE accounts SET tier = 'silver' WHERE id = ?",
                     (f["account_id"],))
        conn.commit()
    finally:
        conn.close()
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "member@example.com", "password": "Member1234"})
    assert r.status_code == 200
    member_id = r.json()["id"]
    # عُد إلى Basic (مقعد واحد) — المقاعد المستهلكة ٢ > ١، فالتخفيض يُرفَض لاحقاً،
    # لكن هنا نضبط الطبقة مباشرةً لنقيس بوّابة التنشيط وحدها.
    conn = pdb.connect()
    try:
        conn.execute("UPDATE accounts SET tier = 'basic' WHERE id = ?",
                     (f["account_id"],))
        conn.commit()
        assert entitlements.seats_used(conn, f["account_id"]) == 2
    finally:
        conn.close()
    # عطّل العضو ⇒ المقعد يُخلى.
    r = cl.post(f"/platform/users/{member_id}/deactivate", headers=hdr(tok))
    assert r.status_code == 200 and r.json()["is_active"] == 0
    conn = pdb.connect()
    try:
        assert entitlements.seats_used(conn, f["account_id"]) == 1
    finally:
        conn.close()
    # إعادة التنشيط تتخطّى سقف Basic (١) ⇒ 403 لا 200.
    r = cl.post(f"/platform/users/{member_id}/activate", headers=hdr(tok))
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["feature"] == "users"


def test_deactivation_kills_the_users_live_sessions(monkeypatch):
    """تعطيل مستخدم يُبطل جلساته فوراً — a deactivated user cannot keep browsing."""
    seed(monkeypatch)
    f = make_factory("gold", "gold-sessions@example.com")
    cl = client()
    owner = login(cl, f["email"], f["password"])
    r = cl.post("/platform/users", headers=hdr(owner),
                json={"email": "victim@example.com", "password": "Victim1234"})
    member_id = r.json()["id"]
    member_tok = login(cl, "victim@example.com", "Victim1234")
    assert cl.get("/platform/me", headers=hdr(member_tok)).status_code == 200
    cl.post(f"/platform/users/{member_id}/deactivate", headers=hdr(owner))
    assert cl.get("/platform/me", headers=hdr(member_tok)).status_code == 401


def test_a_user_cannot_deactivate_themselves(monkeypatch):
    """لا يعطّل المرء نفسه — self-lockout (and a zero-active-user account) refused."""
    seed(monkeypatch)
    f = make_factory("gold", "self-deact@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/users/{f['user_id']}/deactivate", headers=hdr(tok))
    assert r.status_code == 422
    assert "your own account" in r.json()["detail"]
    # ولا يزال قادراً على الدخول · still able to authenticate.
    assert cl.get("/platform/me", headers=hdr(tok)).status_code == 200


# ═════════════ تصعيد الصلاحية · privilege escalation (the critical wall) ═════
def test_factory_cannot_create_a_silk_admin_even_when_asking_for_it(monkeypatch):
    """الدور مفروض `factory` — أخطر تصعيد ممكن هنا، ويجب أن يكون مستحيلاً بنيوياً.

    لو قُرِئ `role` من الجسم لصار بإمكان أي مصنع أن يصنع `silk_admin` فيملك
    المنصّة: تمويلاً، مفتاح قتل، وتدقيقاً عالمياً.
    """
    seed(monkeypatch)
    f = make_factory("gold", "escalate@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "wannabe@example.com", "password": "Wannabe1234",
                      "role": "silk_admin"})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "factory"        # الطلب تُجوهل، لا يُطاع
    # وفعلياً: لا يستطيع الوصول لنقطة أدمِن.
    tok2 = login(cl, "wannabe@example.com", "Wannabe1234")
    assert cl.get("/platform/admin/metrics", headers=hdr(tok2)).status_code == 403


def test_factory_cannot_plant_a_user_in_another_account(monkeypatch):
    """`account_id` من الجلسة حصراً — a body-supplied account_id is ignored."""
    seed(monkeypatch)
    a = make_factory("gold", "acct-a@example.com")
    b = make_factory("gold", "acct-b@example.com")
    cl = client()
    tok = login(cl, a["email"], a["password"])
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "planted@example.com", "password": "Planted1234",
                      "account_id": b["account_id"]})
    assert r.status_code == 200
    assert r.json()["account_id"] == a["account_id"]     # not b


def test_profile_patch_cannot_change_role_or_activation(monkeypatch):
    """التحديث العامّ لا يمسّ الدور ولا التنشيط — privilege fields are excluded."""
    seed(monkeypatch)
    f = make_factory("gold", "patch-priv@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "member2@example.com", "password": "Member1234"})
    uid = r.json()["id"]
    r = cl.patch(f"/platform/users/{uid}", headers=hdr(tok),
                 json={"first_name": "New", "role": "silk_admin", "is_active": 0,
                       "email": "hijack@example.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_name"] == "New"            # المسموح طُبِّق
    assert body["role"] == "factory"              # الدور لم يتغيّر
    assert body["is_active"] == 1                 # التنشيط لم يتغيّر
    assert body["email"] == "member2@example.com"  # البريد لم يتغيّر


def test_user_rows_never_expose_the_password_hash(monkeypatch):
    """لا تسريب `password_hash` ولو لمالك الحساب — never in any response."""
    seed(monkeypatch)
    f = make_factory("gold", "no-hash@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    listing = cl.get("/platform/users", headers=hdr(tok))
    assert "password_hash" not in listing.text
    one = cl.get(f"/platform/users/{f['user_id']}", headers=hdr(tok))
    assert "password_hash" not in one.text


# ═══════════════ العزل بين المستأجرين · cross-tenant isolation ══════════════
def test_users_of_another_account_are_invisible_and_immutable(monkeypatch):
    """مستخدم حسابٍ آخر: 404 عند القراءة والكتابة + قيد تدقيق للمحاولة."""
    seed(monkeypatch)
    a = make_factory("gold", "iso-a@example.com")
    b = make_factory("gold", "iso-b@example.com")
    cl = client()
    tok = login(cl, a["email"], a["password"])
    victim = b["user_id"]
    assert cl.get(f"/platform/users/{victim}", headers=hdr(tok)).status_code == 404
    assert cl.patch(f"/platform/users/{victim}", headers=hdr(tok),
                    json={"first_name": "X"}).status_code == 404
    assert cl.post(f"/platform/users/{victim}/deactivate",
                   headers=hdr(tok)).status_code == 404
    # الضحيّة لم تتغيّر · the victim row is untouched, and still active.
    from silk_platform import audit, db as pdb, users as users_mod
    conn = pdb.connect()
    try:
        row = users_mod.get_user(conn, b["account_id"], victim)
        assert row["first_name"] == "F" and row["is_active"] == 1
        denied = audit.search(conn, account_id=a["account_id"],
                              action="cross_tenant_read")
        denied += audit.search(conn, account_id=a["account_id"],
                               action="cross_tenant_write")
    finally:
        conn.close()
    assert denied, "cross-tenant attempts must be audit-logged"


def test_listing_users_returns_only_own_account(monkeypatch):
    seed(monkeypatch)
    a = make_factory("gold", "list-a@example.com")
    make_factory("gold", "list-b@example.com")
    cl = client()
    tok = login(cl, a["email"], a["password"])
    rows = cl.get("/platform/users", headers=hdr(tok)).json()["users"]
    assert {r["account_id"] for r in rows} == {a["account_id"]}


def test_duplicate_email_is_409_not_500(monkeypatch):
    """بريد مستخدَم يرجع 409 — a client fault, never an unhandled IntegrityError."""
    seed(monkeypatch)
    a = make_factory("gold", "dup-a@example.com")
    cl = client()
    tok = login(cl, a["email"], a["password"])
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "dup-a@example.com", "password": "Another1234"})
    assert r.status_code == 409, r.text


def test_weak_password_is_422_and_creates_nothing(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "weak-pw@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    before = len(cl.get("/platform/users", headers=hdr(tok)).json()["users"])
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 422
    after = len(cl.get("/platform/users", headers=hdr(tok)).json()["users"])
    assert after == before




def test_non_string_body_values_are_422_not_500(monkeypatch):
    """قيمة JSON من نوع حاوية ترجع 422 — عيب العميل 4xx لا 5xx (قاعدة الريبو)."""
    seed(monkeypatch)
    f = make_factory("gold", "types@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": {"nested": 1}, "password": "Member1234"})
    assert r.status_code == 422, r.text
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "t@example.com", "password": "Member1234",
                      "first_name": {"nested": 1}})
    assert r.status_code == 422, r.text
    r = cl.post("/platform/users", headers=hdr(tok),
                json={"email": "t2@example.com", "password": ["not", "a", "string"]})
    assert r.status_code == 422, r.text
    # وفي التحديث كذلك · and on the patch path.
    r = cl.patch(f"/platform/users/{f['user_id']}", headers=hdr(tok),
                 json={"first_name": ["x"]})
    assert r.status_code == 422, r.text


# ═══════════════════ لقطة الاستحقاقات · entitlements snapshot ═══════════════
def test_entitlements_endpoint_reports_limits_and_usage(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "ent-gold@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    body = cl.get("/platform/entitlements", headers=hdr(tok)).json()
    assert body["tier"] == "gold"
    assert body["studies_limit"] == 6 and body["studies_period"] == "month"
    assert body["seats_limit"] == 10 and body["seats_used"] == 1
    assert body["dashboard"] == "full" and body["funnel"] is True
    assert body["export"] is False and body["api_access"] is False


def test_basic_entitlements_are_measured_on_the_lifetime_counter(monkeypatch):
    """Basic تُقاس على عدّاد مدى الحياة لا الشهري — no contradictory pair."""
    seed(monkeypatch)
    f = make_factory("basic", "ent-basic@example.com", fund_cents=10_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp, target_count=0)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    body = cl.get("/platform/entitlements", headers=hdr(tok)).json()
    assert body["studies_period"] == "lifetime"
    assert body["studies_limit"] == 1 and body["studies_used"] == 0
    cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok))
    body = cl.get("/platform/entitlements", headers=hdr(tok)).json()
    assert body["studies_used"] == 1


def test_stale_month_counter_reads_as_zero_not_as_exhausted(monkeypatch):
    """عدّاد شهرٍ مضى يُقرأ صفراً — لا «2/2 ممنوع» بينما الإطلاق سينجح.

    `reserve_launch` يصفّر الفترة **كسولاً**، فقراءةٌ خام لعدّاد يونيو في يوليو
    كانت ستعرض على العميل حدّاً مستهلَكاً يخالف ما يسمح به النظام فعلاً —
    رقمان متعارضان من نفس القاعدة. القراءة هنا تطبّق قاعدة الفترة بلا كتابة.
    """
    seed(monkeypatch)
    f = make_factory("silver", "stale-period@example.com")
    from silk_platform import db as pdb, entitlements
    conn = pdb.connect()
    try:
        # عدّاد ممتلئ موسوم بفترة قديمة · a full counter stamped with an old period.
        conn.execute("UPDATE accounts SET current_month_study_count = 2, "
                     "quota_period = '2020-01' WHERE id = ?", (f["account_id"],))
        conn.commit()
        snap = entitlements.snapshot(conn, f["account_id"])
    finally:
        conn.close()
    assert snap.studies_used == 0, "a stale period must not read as exhausted"
    assert snap.studies_limit == 2


def test_current_period_counter_is_reported_as_is(monkeypatch):
    """عدّاد الفترة الجارية يُعرَض كما هو — the stale-fix must not zero live usage."""
    seed(monkeypatch)
    f = make_factory("silver", "live-period@example.com")
    from silk_platform import db as pdb, entitlements, quota
    conn = pdb.connect()
    try:
        conn.execute("UPDATE accounts SET current_month_study_count = 1, "
                     "quota_period = ? WHERE id = ?",
                     (quota.current_period(), f["account_id"]))
        conn.commit()
        snap = entitlements.snapshot(conn, f["account_id"])
    finally:
        conn.close()
    assert snap.studies_used == 1


# ══════════════════ إدارة الطبقات · admin tier management ═══════════════════
def test_admin_can_upgrade_a_factory_tier_and_it_is_audited(monkeypatch):
    info = seed(monkeypatch)
    f = make_factory("silver", "tier-up@example.com")
    cl = client()
    tok = login(cl, info["admin"]["email"], info["admin"]["password"])
    r = cl.post(f"/platform/admin/accounts/{f['account_id']}/tier",
                headers=hdr(tok), json={"tier": "gold"})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "gold" and r.json()["previous_tier"] == "silver"
    from silk_platform import audit, db as pdb
    conn = pdb.connect()
    try:
        rows = audit.search(conn, account_id=f["account_id"],
                            action="account_tier_changed")
    finally:
        conn.close()
    assert len(rows) == 1


def test_downgrade_that_would_orphan_seats_is_refused(monkeypatch):
    """تخفيضٌ يترك مقاعد زائدة يُرفَض 409 — never a silent impossible state.

    لو مرّ لصار `seats_used > seats_limit` ولا شيء يعيده لحالة صحيحة: البوّابة
    تمنع الإضافة فقط، ولا شيفرة تختار أيّ مستخدم يُعطَّل.
    """
    info = seed(monkeypatch)
    f = make_factory("gold", "tier-down@example.com")
    cl = client()
    owner = login(cl, f["email"], f["password"])
    for i in range(3):     # المجموع ٤ مستخدمين نشطين
        assert cl.post("/platform/users", headers=hdr(owner),
                       json={"email": f"d{i}@example.com",
                             "password": "Member1234"}).status_code == 200
    admin = login(cl, info["admin"]["email"], info["admin"]["password"])
    r = cl.post(f"/platform/admin/accounts/{f['account_id']}/tier",
                headers=hdr(admin), json={"tier": "silver"})   # سقف ٣ < ٤
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "seats_exceed_target_tier"
    assert detail["seats_used"] == 4 and detail["seats_limit"] == 3
    # الطبقة لم تتغيّر · the tier is unchanged.
    assert cl.get("/platform/entitlements",
                  headers=hdr(owner)).json()["tier"] == "gold"


def test_downgrade_succeeds_once_the_excess_seats_are_freed(monkeypatch):
    """التخفيض يمرّ بعد تعطيل الزائد — the refusal is a gate, not a dead end."""
    info = seed(monkeypatch)
    f = make_factory("gold", "tier-down2@example.com")
    cl = client()
    owner = login(cl, f["email"], f["password"])
    ids = []
    for i in range(3):
        ids.append(cl.post("/platform/users", headers=hdr(owner),
                           json={"email": f"e{i}@example.com",
                                 "password": "Member1234"}).json()["id"])
    admin = login(cl, info["admin"]["email"], info["admin"]["password"])
    assert cl.post(f"/platform/admin/accounts/{f['account_id']}/tier",
                   headers=hdr(admin), json={"tier": "silver"}).status_code == 409
    cl.post(f"/platform/users/{ids[0]}/deactivate", headers=hdr(owner))
    r = cl.post(f"/platform/admin/accounts/{f['account_id']}/tier",
                headers=hdr(admin), json={"tier": "silver"})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "silver"


def test_tier_change_rejects_unknown_tier_and_non_factory_accounts(monkeypatch):
    info = seed(monkeypatch)
    f = make_factory("silver", "tier-bad@example.com")
    cl = client()
    admin = login(cl, info["admin"]["email"], info["admin"]["password"])
    r = cl.post(f"/platform/admin/accounts/{f['account_id']}/tier",
                headers=hdr(admin), json={"tier": "diamond"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "unknown_tier"
    # حساب سِلك نفسه ليس مشتركاً · the silk account has no subscription.
    r = cl.post(f"/platform/admin/accounts/{info['vault_account_id']}/tier",
                headers=hdr(admin), json={"tier": "gold"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "not_a_factory_account"
    r = cl.post("/platform/admin/accounts/999999/tier",
                headers=hdr(admin), json={"tier": "gold"})
    assert r.status_code == 404


def test_factory_cannot_change_its_own_tier(monkeypatch):
    """المصنع لا يرقّي نفسه — the tier is an admin-only, billing-bearing field."""
    seed(monkeypatch)
    f = make_factory("basic", "self-upgrade@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/admin/accounts/{f['account_id']}/tier",
                headers=hdr(tok), json={"tier": "platinum"})
    assert r.status_code == 403
    assert cl.get("/platform/entitlements", headers=hdr(tok)).json()["tier"] == "basic"


def test_tier_change_does_not_reset_usage_counters(monkeypatch):
    """الترقية ترفع السقف لا الاستهلاك — counters measure what was actually used."""
    info = seed(monkeypatch)
    f = make_factory("silver", "tier-counter@example.com")
    from silk_platform import db as pdb, quota
    conn = pdb.connect()
    try:
        conn.execute("UPDATE accounts SET current_month_study_count = 2, "
                     "quota_period = ? WHERE id = ?",
                     (quota.current_period(), f["account_id"]))
        conn.commit()
    finally:
        conn.close()
    cl = client()
    admin = login(cl, info["admin"]["email"], info["admin"]["password"])
    cl.post(f"/platform/admin/accounts/{f['account_id']}/tier",
            headers=hdr(admin), json={"tier": "gold"})
    owner = login(cl, f["email"], f["password"])
    body = cl.get("/platform/entitlements", headers=hdr(owner)).json()
    assert body["studies_limit"] == 6 and body["studies_used"] == 2


def test_admin_accounts_listing_shows_tier_and_seat_usage(monkeypatch):
    info = seed(monkeypatch)
    f = make_factory("gold", "admin-list@example.com")
    cl = client()
    admin = login(cl, info["admin"]["email"], info["admin"]["password"])
    rows = cl.get("/platform/admin/accounts", headers=hdr(admin)).json()["accounts"]
    mine = [r for r in rows if r["id"] == f["account_id"]]
    assert len(mine) == 1
    assert mine[0]["tier"] == "gold" and mine[0]["seats_limit"] == 10
    assert mine[0]["seats_used"] == 1
    # لا حسابات سِلك/الخزنة في القائمة · factories only.
    assert all(r["id"] != info["vault_account_id"] for r in rows)


def test_factory_cannot_list_accounts(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "no-list@example.com")
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.get("/platform/admin/accounts", headers=hdr(tok)).status_code == 403


# ══════════════════════════ المجدول · scheduler ═════════════════════════════
def _utc(y, m, d, h):
    return datetime.datetime(y, m, d, h, tzinfo=datetime.timezone.utc)


def test_due_jobs_respects_day_and_hour():
    from silk_platform import scheduler
    # أوّل الشهر 00:xx: التصفير مستحقّ، والفوترة (01:00) لم تحن بعد.
    assert scheduler.due_jobs(_utc(2026, 8, 1, 0), {}) == ["monthly_quota_reset"]
    due = scheduler.due_jobs(_utc(2026, 8, 1, 1), {})
    assert set(due) == {"monthly_quota_reset", "storage_billing"}
    # يومية الجلسات تستحقّ من 02:00 · daily job due from its hour onward.
    assert "session_cleanup" in scheduler.due_jobs(_utc(2026, 8, 2, 2), {})
    assert "session_cleanup" not in scheduler.due_jobs(_utc(2026, 8, 2, 1), {})


def test_due_jobs_is_idempotent_within_a_slot():
    """المهمّة المنفَّذة في فتحتها لا تُعاد — the slot id is the dedupe key."""
    from silk_platform import scheduler
    now = _utc(2026, 8, 1, 3)
    last = {"monthly_quota_reset": "2026-08", "storage_billing": "2026-08",
            "session_cleanup": "2026-08-01"}
    assert scheduler.due_jobs(now, last) == []
    # شهر جديد ⇒ الشهريّتان تستحقّان مرّة أخرى.
    assert set(scheduler.due_jobs(_utc(2026, 9, 1, 3), last)) == {
        "monthly_quota_reset", "storage_billing", "session_cleanup"}


def test_monthly_jobs_catch_up_after_downtime():
    """مهمّة شهرية فاتت لأن العملية كانت متوقّفة تُنفَّذ لاحقاً في نفس الشهر.

    بلا اللحاق تُسقَط فوترة تخزين شهرٍ كامل صمتاً لو كانت الحاوية متوقّفة يوم ١
    — والدفتر غير قابل للتعديل فلا تُصحَّح لاحقاً.
    """
    from silk_platform import scheduler
    due = scheduler.due_jobs(_utc(2026, 8, 17, 9), {})    # منتصف الشهر
    assert "monthly_quota_reset" in due and "storage_billing" in due


def test_scheduler_does_not_start_unless_explicitly_enabled(monkeypatch):
    """بلا `SILK_PLATFORM_SCHEDULER=1` لا خيط — tests never bill or reset silently."""
    from silk_platform import scheduler
    monkeypatch.delenv("SILK_PLATFORM_SCHEDULER", raising=False)
    assert scheduler.start() is None


def test_run_due_executes_real_jobs_and_marks_the_slot(monkeypatch):
    """تشغيلة فعلية: تنظيف الجلسات يعمل ويُوسَم فلا يُعاد في نفس الفتحة."""
    seed(monkeypatch)
    from silk_platform import scheduler
    last: dict[str, str] = {}
    now = _utc(2026, 8, 5, 3)          # يومية فقط (ليس أوّل الشهر)
    out = scheduler.run_due(now=now, last_run=last)
    assert "session_cleanup" in out
    assert isinstance(out["session_cleanup"], dict)
    assert last["session_cleanup"] == "2026-08-05"
    assert scheduler.run_due(now=now, last_run=last) == {}   # لا تكرار


def test_a_failing_job_is_recorded_and_retried_next_pass(monkeypatch):
    """فشل مهمّة لا يوسمها منفَّذة — so the next pass retries instead of skipping."""
    seed(monkeypatch)
    from silk_platform import scheduler
    monkeypatch.setattr(scheduler, "run_job",
                        lambda name: (_ for _ in ()).throw(RuntimeError("boom")))
    last: dict[str, str] = {}
    out = scheduler.run_due(now=_utc(2026, 8, 5, 3), last_run=last)
    assert "failed: boom" in str(out["session_cleanup"])
    assert "session_cleanup" not in last          # لم تُوسَم ⇒ ستُعاد


def test_unknown_job_name_raises():
    from silk_platform import scheduler
    with pytest.raises(ValueError):
        scheduler.run_job("no_such_job")
