"""انتقالات دراسة صريحة: completed/archived — PR-4 remaining item.

`docs/PLATFORM_ROADMAP.md` وسم هذين كـ«ناقص»: «حالتا completed/archived
كانتقالات صريحة بنقاط خاصّة». المسارات المسموحة: draft→in_progress (launch،
مُقفَل سابقاً) → completed (فقط من in_progress) → archived (من draft أو
completed، لا من in_progress نشطة). كل انتقال غير مسموح 409، وكل انتقال ذرّي
بنفس نمط launch_study (rowcount على شرط الحالة).
"""
import pytest

from platform_helpers import (add_active_smtp, client, hdr, login,
                              make_draft_study, make_factory, seed)
from silk_platform import db as pdb


def _state(account_id: int, study_id: int) -> str:
    conn = pdb.connect()
    try:
        return conn.execute("SELECT state FROM studies WHERE id = ?",
                            (study_id,)).fetchone()["state"]
    finally:
        conn.close()


# ════════════════════════════ complete ═══════════════════════════════════════
def test_complete_from_in_progress_sets_completed_at(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "complete1@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={}
                   ).status_code == 200
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["state"] == "completed"
    assert body["completed_at"]
    assert _state(f["account_id"], sid) == "completed"


def test_complete_from_draft_is_409(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "complete2@f.local")
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r.status_code == 409
    assert _state(f["account_id"], sid) == "draft"


def test_complete_twice_second_call_is_409(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "complete3@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={})
    assert cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok)
                   ).status_code == 200
    r2 = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    assert r2.status_code == 409


def test_complete_cross_tenant_is_404_not_403(monkeypatch):
    seed(monkeypatch)
    f1 = make_factory("gold", "own1@f.local", fund_cents=100_000)
    f2 = make_factory("gold", "own2@f.local")
    smtp = add_active_smtp(f1["account_id"])
    sid, _ = make_draft_study(f1["account_id"], f1["user_id"], smtp)
    cl = client()
    tok1 = login(cl, f1["email"], f1["password"])
    cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok1), json={})
    tok2 = login(cl, f2["email"], f2["password"])
    r = cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok2))
    assert r.status_code == 404  # no existence leak across tenants


# ════════════════════════════ archive ═════════════════════════════════════════
def test_archive_from_draft_ok(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "archive1@f.local")
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r.status_code == 200 and r.json()["state"] == "archived"
    assert _state(f["account_id"], sid) == "archived"


def test_archive_from_completed_ok(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "archive2@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={})
    cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r.status_code == 200 and r.json()["state"] == "archived"


def test_archive_from_in_progress_is_409(monkeypatch):
    """لا أرشفة لدراسة نشطة — an in-flight send cannot be archived out from under it."""
    seed(monkeypatch)
    f = make_factory("gold", "archive3@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={})
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r.status_code == 409
    assert _state(f["account_id"], sid) == "in_progress"


def test_archive_twice_second_call_is_409(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "archive4@f.local")
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    assert cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok)
                   ).status_code == 200
    r2 = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    assert r2.status_code == 409


def test_archive_cross_tenant_is_404_not_403(monkeypatch):
    seed(monkeypatch)
    f1 = make_factory("gold", "aown1@f.local")
    f2 = make_factory("gold", "aown2@f.local")
    smtp = add_active_smtp(f1["account_id"])
    sid, _ = make_draft_study(f1["account_id"], f1["user_id"], smtp)
    cl = client()
    tok2 = login(cl, f2["email"], f2["password"])
    r = cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok2))
    assert r.status_code == 404


# ════════════════════════════ audit trail ═════════════════════════════════════
def test_complete_and_archive_are_audited(monkeypatch):
    seed(monkeypatch)
    f = make_factory("gold", "audit1@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={})
    cl.post(f"/platform/studies/{sid}/complete", headers=hdr(tok))
    cl.post(f"/platform/studies/{sid}/archive", headers=hdr(tok))
    conn = pdb.connect()
    try:
        actions = {r["action"] for r in conn.execute(
            "SELECT action FROM audit_log WHERE resource_type = 'study' AND "
            "resource_id = ?", (str(sid),)).fetchall()}
    finally:
        conn.close()
    assert {"study_completed", "study_archived"} <= actions


def test_analyst_role_forbidden_from_both_transitions(monkeypatch):
    info = seed(monkeypatch)
    f = make_factory("gold", "roleguard@f.local", fund_cents=100_000)
    smtp = add_active_smtp(f["account_id"])
    sid, _ = make_draft_study(f["account_id"], f["user_id"], smtp)
    cl = client()
    tok = login(cl, f["email"], f["password"])
    cl.post(f"/platform/studies/{sid}/launch", headers=hdr(tok), json={})
    analyst_tok = login(cl, info["analyst"]["email"], info["analyst"]["password"])
    assert cl.post(f"/platform/studies/{sid}/complete",
                   headers=hdr(analyst_tok)).status_code == 403
    assert cl.post(f"/platform/studies/{sid}/archive",
                   headers=hdr(analyst_tok)).status_code == 403
