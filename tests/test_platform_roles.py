"""اختبارات الأدوار (القسم ١٣: ROLES) — Section 13 role acceptance.

Analyst: 200 على المجمّعات، 403 على أي تفصيل/إنشاء/إرسال/مفتاح قتل/دفتر.
Admin: 200 على المجمّعات/الخزنة، 403 على محتوى المصنع/PII؛ التمويل قيدان
ذرّيان بختم الأدمِن؛ مفتاح القتل يضبط العلم + قيد تدقيق.
Factory: 403 على كل نقاط الأدمِن؛ لا يرى تدقيق حساب آخر.
"""
from platform_helpers import client, hdr, login, seed
from silk_platform import db as pdb


def _tokens(monkeypatch):
    info = seed(monkeypatch)
    cl = client()
    return cl, info, {
        "admin": login(cl, info["admin"]["email"], info["admin"]["password"]),
        "analyst": login(cl, info["analyst"]["email"], info["analyst"]["password"]),
        "fa": login(cl, info["factory_a"]["email"], info["factory_a"]["password"]),
        "fb": login(cl, info["factory_b"]["email"], info["factory_b"]["password"]),
    }


# ── ANALYST ──────────────────────────────────────────────────────────────────
def test_analyst_aggregates_200_details_403(monkeypatch):
    cl, info, tk = _tokens(monkeypatch)
    # دراسة مصنع موجودة — a factory study exists.
    study = cl.post("/platform/studies", headers=hdr(tk["fa"]),
                    json={"title_en": "S"}).json()
    assert cl.get("/platform/analyst/aggregates", headers=hdr(tk["analyst"])
                  ).status_code == 200
    # 403 على كل تفصيل/إنشاء/إرسال/مفتاح قتل/دفتر.
    assert cl.get(f"/platform/studies/{study['id']}", headers=hdr(tk["analyst"])
                  ).status_code == 403
    assert cl.post("/platform/studies", headers=hdr(tk["analyst"]),
                   json={"title_en": "x"}).status_code == 403
    assert cl.get("/platform/wallet/ledger", headers=hdr(tk["analyst"])
                  ).status_code == 403
    assert cl.post(f"/platform/studies/{study['id']}/launch",
                   headers=hdr(tk["analyst"]), json={}).status_code == 403
    assert cl.post("/platform/admin/kill-switch", headers=hdr(tk["analyst"]),
                   json={"on": True}).status_code == 403


# ── ADMIN ────────────────────────────────────────────────────────────────────
def test_admin_aggregates_and_vault_200(monkeypatch):
    cl, info, tk = _tokens(monkeypatch)
    m = cl.get("/platform/admin/metrics", headers=hdr(tk["admin"]))
    assert m.status_code == 200
    assert m.json()["vault_balance_cents"] > 0  # seeded vault capitalization
    assert "accounts_by_tier" in m.json()


def test_admin_403_on_factory_content_and_pii(monkeypatch):
    cl, info, tk = _tokens(monkeypatch)
    study = cl.post("/platform/studies", headers=hdr(tk["fa"]),
                    json={"title_en": "S"}).json()
    prospect = cl.post("/platform/prospects", headers=hdr(tk["fa"]),
                       json={"email": "p@a.local"}).json()
    assert cl.get(f"/platform/studies/{study['id']}", headers=hdr(tk["admin"])
                  ).status_code == 403
    assert cl.get(f"/platform/prospects/{prospect['id']}", headers=hdr(tk["admin"])
                  ).status_code == 403


def test_admin_funding_two_atomic_entries_with_admin_actor(monkeypatch):
    cl, info, tk = _tokens(monkeypatch)
    fa = info["factory_a"]["account_id"]
    vault = info["vault_account_id"]
    r = cl.post("/platform/admin/fund", headers=hdr(tk["admin"]),
                json={"account_id": fa, "amount_cents": 2500})
    assert r.status_code == 200
    conn = pdb.connect()
    try:
        rows = [dict(x) for x in conn.execute(
            "SELECT * FROM ledger_entries WHERE operation_type = 'wallet_funded' "
            "AND actor_user_id = ? ORDER BY id", (info["admin"]["id"],)).fetchall()]
        # قيد افتتاح الخزنة + خصم الخزنة + إيداع المصنع = ثلاثة بختم الأدمِن.
        debit = [x for x in rows if x["account_id"] == vault and x["amount"] == -2500]
        credit = [x for x in rows if x["account_id"] == fa and x["amount"] == 2500]
        assert len(debit) == 1 and len(credit) == 1
        assert debit[0]["actor_user_id"] == credit[0]["actor_user_id"] == info["admin"]["id"]
    finally:
        conn.close()


def test_admin_kill_switch_sets_flag_and_audits(monkeypatch):
    cl, info, tk = _tokens(monkeypatch)
    r = cl.post("/platform/admin/kill-switch", headers=hdr(tk["admin"]),
                json={"on": True})
    assert r.status_code == 200 and r.json()["on"] is True
    conn = pdb.connect()
    try:
        assert conn.execute("SELECT value FROM system_settings WHERE key='kill_switch'"
                            ).fetchone()["value"] == "1"
        audit = conn.execute(
            "SELECT * FROM audit_log WHERE action='kill_switch_toggled' "
            "AND user_id = ?", (info["admin"]["id"],)).fetchone()
        assert audit is not None
    finally:
        conn.close()


# ── FACTORY ──────────────────────────────────────────────────────────────────
def test_factory_403_on_all_admin_endpoints(monkeypatch):
    cl, info, tk = _tokens(monkeypatch)
    fa = tk["fa"]
    assert cl.get("/platform/admin/metrics", headers=hdr(fa)).status_code == 403
    assert cl.post("/platform/admin/fund", headers=hdr(fa),
                   json={"account_id": info["factory_b"]["account_id"],
                         "amount_cents": 100}).status_code == 403
    assert cl.post("/platform/admin/kill-switch", headers=hdr(fa),
                   json={"on": True}).status_code == 403
    assert cl.get("/platform/admin/audit", headers=hdr(fa)).status_code == 403


def test_factory_cannot_see_other_accounts_audit(monkeypatch):
    cl, info, tk = _tokens(monkeypatch)
    # A ينشئ دراسة (يولّد قيد تدقيق لحسابه) — A's action logs to A's account.
    cl.post("/platform/studies", headers=hdr(tk["fa"]), json={"title_en": "A"})
    b_audit = cl.get("/platform/audit", headers=hdr(tk["fb"])).json()
    assert b_audit["account_id"] == info["factory_b"]["account_id"]
    assert all(row["account_id"] == info["factory_b"]["account_id"]
               for row in b_audit["audit"])
