"""واجهة REST للمنصّة — FastAPI router for auth + tenancy (PR-1).

يُحمَّل fastapi/pydantic بكسل داخل المصنع كي يستورد الوحدة دون اعتمادية (نفس
نمط api.py الجذر). كل نقطة مُستأجَرة تشتقّ account_id من سياق الجلسة حصراً —
لا تقرأ owner من الطلب أو الاستعلام أبداً، فلا يمكن العبور بتلاعب المعاملات.

Lazy-imports FastAPI. Tenant scope always comes from the session context, never
from request/query params — query-param manipulation cannot cross tenants.
Cross-tenant reads return 404 (no existence leak); role walls return 403.
"""
# ملاحظة: لا `from __future__ import annotations` هنا عمداً — FastAPI يحلّ
# التلميحات النصّية مقابل globals الوحدة، وأنواع fastapi (Request) مستورَدة
# محلّياً داخل mount() كي تبقى الوحدة قابلة للاستيراد دون fastapi؛ فالتقييم
# الفوري (كائنات حقيقية) هو ما يجعل FastAPI يميّز Request عن معامل استعلام.
import logging
import os
import uuid

from . import (auth, audit, crypto, email_queue, quota, repository, seed as
               seed_mod, settings, wallet)
from .db import connect, init_db
from .models import (AuthContext, Operation, Role, projected_email_cost_cents,
                     tier_limits, Tier, PRICE_EMAIL_SENT_CENTS)

log = logging.getLogger(__name__)

COOKIE_NAME = "silk_session"
_PREFIX = "/platform"


# ── أدوات · helpers (fastapi-free so they stay unit-testable) ────────────────
def _open():
    """افتح اتصالاً وهيّئ القاعدة عند اللزوم — per-request connection."""
    init_db()
    return connect()


def _bearer(headers) -> str:
    raw = headers.get("authorization") or headers.get("Authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _client_ip(request) -> str | None:
    return request.client.host if request.client else None


def create_platform_app():
    """أنشئ تطبيق المنصّة المستقلّ — standalone FastAPI app for tests/dev."""
    from fastapi import FastAPI
    app = FastAPI(title="Silk Platform (PR-1: auth + tenancy)")
    mount(app)
    return app


def mount(app) -> bool:
    """ركّب كل نقاط المنصّة على `app` تحت /platform — returns True on success."""
    try:
        from fastapi import Request, Response
        from fastapi.responses import JSONResponse
    except Exception:  # noqa: BLE001 — بلا fastapi لا تركيب (استيراد بلا انهيار)
        log.warning("fastapi unavailable — platform router not mounted")
        return False

    from fastapi import HTTPException

    # ── وسيط تحميل السياق · context-loading middleware ───────────────────────
    @app.middleware("http")
    async def _load_auth(request: Request, call_next):
        """حمّل current_user/current_account/current_role في سياق الطلب.

        أفضل جهد: لا يرفع أبداً؛ الرمز الغائب/المنتهي => state.auth = None،
        والنقاط المحميّة هي من تفرض 401/403. Best-effort; guards enforce.
        """
        request.state.auth = None
        if request.url.path.startswith(_PREFIX):
            token = _bearer(request.headers) or request.cookies.get(COOKIE_NAME, "")
            if token:
                conn = _open()
                try:
                    request.state.auth = auth.resolve_session(conn, token)
                finally:
                    conn.close()
        return await call_next(request)

    # ── حرّاس الأدوار · role guards ──────────────────────────────────────────
    def _ctx(request: Request) -> AuthContext:
        ctx = getattr(request.state, "auth", None)
        if ctx is None:
            raise HTTPException(status_code=401, detail="authentication required")
        return ctx

    def _require(request: Request, *roles: Role) -> AuthContext:
        ctx = _ctx(request)
        if ctx.role not in roles:
            raise HTTPException(status_code=403, detail="forbidden for this role")
        return ctx

    def _json_body(data: dict | None) -> dict:
        return data if isinstance(data, dict) else {}

    # ── نقطة عزل عامّة · shared tenant-scoped fetch with 404/403 semantics ────
    def _tenant_detail(request: Request, repo_factory, row_id: int,
                       resource_type: str):
        """اجلب صفّاً مُستأجَراً بدلالة الدور — enforce the isolation matrix.

        - analyst: 403 (مجمّعات فقط).
        - admin: يرى دراسات حساب سِلك فقط؛ محتوى مصنع => 403.
        - factory: حسابه فقط؛ صفّ حساب آخر => 404 (+ تدقيق محاولة عبور).
        """
        ctx = _ctx(request)
        conn = _open()
        try:
            repo = repo_factory(conn)
            if ctx.is_silk_analyst:
                raise HTTPException(status_code=403, detail="analysts see aggregates only")
            row = repo.get(ctx.account_id, row_id)
            if row is not None:
                return ctx, conn, row  # caller closes conn
            # غير مملوك للمنادي · not owned by the caller.
            foreign = repo.exists_anywhere(row_id)
            if ctx.is_silk_admin:
                if foreign:
                    audit.record_denied(conn, action="admin_pii_wall",
                                        user_id=ctx.user_id, account_id=ctx.account_id,
                                        resource_type=resource_type, resource_id=row_id,
                                        ip_address=_client_ip(request))
                    raise HTTPException(status_code=403,
                                        detail="admins cannot access factory content/PII")
                raise HTTPException(status_code=404, detail="not found")
            # factory
            if foreign:
                audit.record_denied(conn, action="cross_tenant_read",
                                    user_id=ctx.user_id, account_id=ctx.account_id,
                                    resource_type=resource_type, resource_id=row_id,
                                    ip_address=_client_ip(request))
            raise HTTPException(status_code=404, detail="not found")
        except HTTPException:
            conn.close()
            raise
        except Exception:
            conn.close()
            raise

    # ══════════════════════════ AUTH ════════════════════════════════════════
    @app.post(_PREFIX + "/auth/login")
    async def login(request: Request):
        body = _json_body(await _safe_json(request))
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        conn = _open()
        try:
            user = auth.authenticate(conn, email, password)
            if not user:
                # لا تعداد مستخدمين: نفس الرسالة والتوقيت للمجهول والخطأ.
                raise HTTPException(status_code=401, detail="invalid credentials")
            raw = auth.create_session(
                conn, user["id"], ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"))
            audit.record(conn, action="login", user_id=user["id"],
                         account_id=user["account_id"], resource_type="session",
                         ip_address=_client_ip(request))
            conn.commit()
            payload = {"token": raw, "user": {
                "id": user["id"], "email": user["email"], "role": user["role"],
                "account_id": user["account_id"],
                "language_preference": user["language_preference"]}}
        finally:
            conn.close()
        resp = JSONResponse(payload)
        # secure عبر البيئة: HTTPS في الإنتاج، http في التطوير المحلي.
        resp.set_cookie(COOKIE_NAME, raw, httponly=True, samesite="lax",
                        secure=os.environ.get("SILK_PLATFORM_SECURE_COOKIES") == "1")
        return resp

    @app.post(_PREFIX + "/auth/logout")
    async def logout(request: Request):
        ctx = _ctx(request)
        conn = _open()
        try:
            if ctx.session_id:
                auth.destroy_session(conn, ctx.session_id)
        finally:
            conn.close()
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE_NAME)
        return resp

    @app.get(_PREFIX + "/me")
    async def me(request: Request):
        ctx = _ctx(request)
        return {"user_id": ctx.user_id, "account_id": ctx.account_id,
                "role": ctx.role.value, "email": ctx.email,
                "language_preference": ctx.language_preference}

    @app.post(_PREFIX + "/auth/password-reset/request")
    async def reset_request(request: Request):
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            raw = auth.issue_reset_token(conn, body.get("email") or "")
        finally:
            conn.close()
        # لا تفصح عن وجود المستخدم · never reveal whether the user exists.
        # أمنيّاً حرج: الرمز الخام لا يُعاد في الردّ إطلاقاً في الإنتاج — وإلا
        # لأمكن أي مهاجم طلب إعادة تعيين لأي بريد والاستيلاء على الحساب فوراً.
        # يُرسَل بالبريد (PR-5). يُكشف في الردّ فقط خلف علم بيئة صريح للاختبار.
        # SECURITY: the raw token is emailed, never returned in the response —
        # exposing it would allow trivial account takeover. Test-only env gate.
        out = {"ok": True}
        if raw is not None and os.environ.get("SILK_PLATFORM_EXPOSE_RESET_TOKEN") == "1":
            out["reset_token"] = raw
        return out

    @app.post(_PREFIX + "/auth/password-reset/confirm")
    async def reset_confirm(request: Request):
        from .passwords import PasswordError
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            try:
                ok = auth.consume_reset_token(conn, body.get("token") or "",
                                              body.get("new_password") or "")
            except PasswordError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        finally:
            conn.close()
        if not ok:
            raise HTTPException(status_code=400, detail="invalid or used token")
        return {"ok": True}

    # ══════════════════════════ STUDIES ═════════════════════════════════════
    def _validate_smtp_binding(conn, ctx: AuthContext, smtp_config_id):
        """ارفض ربط SMTP عابراً للمستأجر — reject foreign smtp_config binding."""
        if smtp_config_id in (None, "", 0):
            return None
        row = conn.execute("SELECT * FROM smtp_configs WHERE id = ?",
                           (smtp_config_id,)).fetchone()
        if not row or row["owner_id"] != ctx.account_id:
            raise HTTPException(status_code=422,
                                detail="smtp_config_id not owned by this account")
        return dict(row)

    @app.get(_PREFIX + "/studies")
    async def list_studies(request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        conn = _open()
        try:
            rows = repository.studies(conn).list(ctx.account_id)
        finally:
            conn.close()
        return {"studies": rows}

    @app.post(_PREFIX + "/studies")
    async def create_study(request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            _validate_smtp_binding(conn, ctx, body.get("smtp_config_id"))
            fields = {k: body.get(k) for k in
                      ("title_en", "title_ar", "description_en", "description_ar",
                       "target_count", "smtp_config_id")}
            fields["created_by_user_id"] = ctx.user_id
            row = repository.studies(conn).create(ctx.account_id, fields)
            audit.record(conn, action="study_created", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="study",
                         resource_id=row["id"], ip_address=_client_ip(request))
            conn.commit()
        finally:
            conn.close()
        return row

    @app.get(_PREFIX + "/studies/{study_id}")
    async def get_study(study_id: int, request: Request):
        ctx, conn, row = _tenant_detail(request, repository.studies, study_id, "study")
        conn.close()
        return row

    @app.patch(_PREFIX + "/studies/{study_id}")
    async def patch_study(study_id: int, request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            if body.get("smtp_config_id") is not None:
                _validate_smtp_binding(conn, ctx, body.get("smtp_config_id"))
            repo = repository.studies(conn)
            updated = repo.update(ctx.account_id, study_id,
                                  {k: body[k] for k in body if k in
                                   ("title_en", "title_ar", "description_en",
                                    "description_ar", "target_count", "smtp_config_id")})
            if updated is None:
                if repo.exists_anywhere(study_id):
                    audit.record_denied(conn, action="cross_tenant_write",
                                        user_id=ctx.user_id, account_id=ctx.account_id,
                                        resource_type="study", resource_id=study_id,
                                        ip_address=_client_ip(request))
                raise HTTPException(status_code=404, detail="not found")
            audit.record(conn, action="study_updated", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="study",
                         resource_id=study_id)
            conn.commit()
        finally:
            conn.close()
        return updated

    @app.delete(_PREFIX + "/studies/{study_id}")
    async def delete_study(study_id: int, request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        conn = _open()
        try:
            repo = repository.studies(conn)
            ok = repo.delete(ctx.account_id, study_id)
            if not ok:
                if repo.exists_anywhere(study_id):
                    audit.record_denied(conn, action="cross_tenant_delete",
                                        user_id=ctx.user_id, account_id=ctx.account_id,
                                        resource_type="study", resource_id=study_id,
                                        ip_address=_client_ip(request))
                raise HTTPException(status_code=404, detail="not found")
            audit.record(conn, action="study_deleted", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="study",
                         resource_id=study_id)
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    @app.post(_PREFIX + "/studies/{study_id}/launch")
    async def launch_study(study_id: int, request: Request):
        """أطلق دراسة — smtp validation → wallet sufficiency → quota → in_progress.

        العدّاد يزيد هنا فقط (أوّل بريد). مفتاح القتل لا يمنع الإطلاق لكنه يوقف
        الإرسال في العامل. The quota counter increments only here.
        """
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            study = repository.studies(conn).get(ctx.account_id, study_id)
            if study is None:
                if repository.studies(conn).exists_anywhere(study_id):
                    audit.record_denied(conn, action="cross_tenant_launch",
                                        user_id=ctx.user_id, account_id=ctx.account_id,
                                        resource_type="study", resource_id=study_id,
                                        ip_address=_client_ip(request))
                raise HTTPException(status_code=404, detail="not found")
            if study["state"] != "draft":
                raise HTTPException(status_code=409,
                                    detail=f"study is {study['state']}, not draft")
            # (1) تهيئة SMTP: مملوكة ونشطة · smtp owned by this account + active.
            cfg = _validate_smtp_binding(conn, ctx, study["smtp_config_id"])
            if cfg is None:
                raise HTTPException(status_code=422, detail="study has no smtp_config")
            if not cfg["is_active"]:
                raise HTTPException(status_code=422, detail="smtp_config is inactive")
            # (2) رصيد كافٍ للتكلفة المتوقّعة · wallet >= projected email cost.
            projected = projected_email_cost_cents(study["target_count"])
            w = wallet.ensure_wallet(conn, ctx.account_id)
            if int(w["balance"]) < projected:
                audit.record(conn, action="launch_blocked_insufficient_funds",
                             user_id=ctx.user_id, account_id=ctx.account_id,
                             resource_type="study", resource_id=study_id,
                             changes={"projected": projected,
                                      "balance": int(w["balance"])})
                conn.commit()
                raise HTTPException(status_code=402,
                                    detail={"error": "insufficient_balance",
                                            "projected_cents": projected,
                                            "balance_cents": int(w["balance"])})
            # (3) الحصّة: احجز (يزيد العدّاد) · quota reserve (increments counter).
            decision = quota.reserve_launch(conn, ctx.account_id,
                                            actor_user_id=ctx.user_id)
            if not decision.allowed:
                raise HTTPException(status_code=403,
                                    detail={"error": "quota_exceeded",
                                            "reason": decision.reason,
                                            "tier": decision.tier,
                                            "limit": decision.limit,
                                            "used": decision.used,
                                            "upgrade": True})
            # (4) انتقال draft→in_progress + صفّ البريد (إن مُرّرت قائمة) .
            conn.execute("UPDATE studies SET state = 'in_progress', launched_at = ?, "
                         "updated_at = ? WHERE id = ? AND owner_id = ?",
                         (auth.now_iso(), auth.now_iso(), study_id, ctx.account_id))
            queued = 0
            draft_id = body.get("draft_id")
            prospect_ids = body.get("prospect_ids") or []
            if draft_id and prospect_ids:
                draft = repository.drafts(conn).get(ctx.account_id, draft_id)
                if draft:
                    for pid in prospect_ids:
                        pr = repository.prospects(conn).get(ctx.account_id, pid)
                        if pr:
                            email_queue.enqueue(
                                conn, account_id=ctx.account_id, study_id=study_id,
                                prospect=pr, draft=draft,
                                smtp_config_id=study["smtp_config_id"],
                                actor_user_id=ctx.user_id)
                            queued += 1
            audit.record(conn, action="study_launched", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="study",
                         resource_id=study_id, changes={"queued": queued})
            conn.commit()
            out = {"ok": True, "state": "in_progress", "queued": queued,
                   "quota_used": decision.used, "quota_limit": decision.limit}
        finally:
            conn.close()
        return out

    # ══════════════════════════ PROSPECTS ═══════════════════════════════════
    @app.get(_PREFIX + "/prospects")
    async def list_prospects(request: Request):
        ctx = _require(request, Role.FACTORY)  # PII — factory own only
        conn = _open()
        try:
            rows = repository.prospects(conn).list(ctx.account_id)
        finally:
            conn.close()
        return {"prospects": rows}

    @app.post(_PREFIX + "/prospects")
    async def create_prospect(request: Request):
        ctx = _require(request, Role.FACTORY)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            fields = {k: body.get(k) for k in
                      ("email", "first_name", "last_name", "company", "industry",
                       "language_preference", "tags")}
            try:
                row = repository.prospects(conn).create(ctx.account_id, fields)
            except Exception as exc:  # unique (owner_id,email) etc.
                raise HTTPException(status_code=422, detail=str(exc))
            conn.commit()
        finally:
            conn.close()
        return row

    @app.get(_PREFIX + "/prospects/{prospect_id}")
    async def get_prospect(prospect_id: int, request: Request):
        ctx, conn, row = _tenant_detail(request, repository.prospects,
                                        prospect_id, "prospect")
        conn.close()
        return row

    @app.patch(_PREFIX + "/prospects/{prospect_id}")
    async def patch_prospect(prospect_id: int, request: Request):
        ctx = _require(request, Role.FACTORY)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            repo = repository.prospects(conn)
            try:
                updated = repo.update(ctx.account_id, prospect_id, body)
            except Exception as exc:  # تصادم UNIQUE(owner_id,email) => 422 لا 500
                raise HTTPException(status_code=422, detail=str(exc))
            if updated is None:
                if repo.exists_anywhere(prospect_id):
                    audit.record_denied(conn, action="cross_tenant_write",
                                        user_id=ctx.user_id, account_id=ctx.account_id,
                                        resource_type="prospect", resource_id=prospect_id,
                                        ip_address=_client_ip(request))
                raise HTTPException(status_code=404, detail="not found")
            conn.commit()
        finally:
            conn.close()
        return updated

    @app.delete(_PREFIX + "/prospects/{prospect_id}")
    async def delete_prospect(prospect_id: int, request: Request):
        ctx = _require(request, Role.FACTORY)
        conn = _open()
        try:
            repo = repository.prospects(conn)
            ok = repo.delete(ctx.account_id, prospect_id)
            if not ok:
                if repo.exists_anywhere(prospect_id):
                    audit.record_denied(conn, action="cross_tenant_delete",
                                        user_id=ctx.user_id, account_id=ctx.account_id,
                                        resource_type="prospect", resource_id=prospect_id,
                                        ip_address=_client_ip(request))
                raise HTTPException(status_code=404, detail="not found")
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    # ══════════════════════════ SMTP CONFIGS ════════════════════════════════
    @app.get(_PREFIX + "/smtp-configs")
    async def list_smtp(request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        conn = _open()
        try:
            rows = repository.smtp_configs(conn).list(ctx.account_id)
            for r in rows:  # لا تُعِد بيانات الاعتماد أبداً · never return creds
                r.pop("username_enc", None)
                r.pop("password_enc", None)
        finally:
            conn.close()
        return {"smtp_configs": rows}

    @app.post(_PREFIX + "/smtp-configs")
    async def create_smtp(request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            fields = {k: body.get(k) for k in
                      ("label", "host", "port", "from_email", "from_name",
                       "use_tls", "is_active")}
            # تشفير بيانات الاعتماد عند التخزين · encrypt credentials at rest.
            if body.get("username"):
                fields["username_enc"] = crypto.encrypt(body["username"])
            if body.get("password"):
                fields["password_enc"] = crypto.encrypt(body["password"])
            row = repository.smtp_configs(conn).create(ctx.account_id, fields)
            row.pop("username_enc", None)
            row.pop("password_enc", None)
            conn.commit()
        finally:
            conn.close()
        return row

    # ══════════════════════════ IMAGES ══════════════════════════════════════
    @app.post(_PREFIX + "/images")
    async def create_image(request: Request):
        ctx = _require(request, Role.FACTORY)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            ext = (body.get("ext") or "bin").lstrip(".")
            key = f"{ctx.account_id}/{uuid.uuid4().hex}.{ext}"
            fields = {"filename": body.get("filename"), "storage_key": key,
                      "mime_type": body.get("mime_type"),
                      "size_bytes": int(body.get("size_bytes") or 0),
                      "uploaded_by_user_id": ctx.user_id,
                      "alt_text_en": body.get("alt_text_en"),
                      "alt_text_ar": body.get("alt_text_ar")}
            row = repository.images(conn).create(ctx.account_id, fields)
            conn.commit()
        finally:
            conn.close()
        return row

    @app.get(_PREFIX + "/images/{image_id}/signed-url")
    async def image_signed_url(image_id: int, request: Request):
        """رابط موقّع للصورة — verify owner_id BEFORE signing; foreign ⇒ 404/403."""
        ctx, conn, row = _tenant_detail(request, repository.images, image_id, "image")
        try:
            from . import tokens
            import time
            # التوقيع يحدث فقط بعد إثبات الملكية · signed only after ownership proven.
            expiry = int(time.time()) + 900
            payload = f"{row['storage_key']}:{expiry}"
            sig = tokens.sign(payload)
            url = f"/files/{row['storage_key']}?expires={expiry}&sig={sig}"
        finally:
            conn.close()
        return {"signed_url": url, "expires": expiry}

    # ══════════════════════════ WALLET / LEDGER ═════════════════════════════
    @app.get(_PREFIX + "/wallet")
    async def get_wallet_ep(request: Request):
        # analyst: لا بيانات حساب فردية · no individual account data.
        ctx = _ctx(request)
        if ctx.is_silk_analyst:
            raise HTTPException(status_code=403, detail="analysts see aggregates only")
        conn = _open()
        try:
            w = wallet.ensure_wallet(conn, ctx.account_id)  # own account only
        finally:
            conn.close()
        return w

    @app.get(_PREFIX + "/wallet/ledger")
    async def get_ledger_ep(request: Request, limit: int = 20):
        ctx = _ctx(request)
        if ctx.is_silk_analyst:
            raise HTTPException(status_code=403, detail="analysts see aggregates only")
        conn = _open()
        try:
            # النطاق دائماً حساب المنادي — أي account_id في الاستعلام يُتجاهَل.
            entries = wallet.list_ledger(conn, ctx.account_id, limit=limit)
        finally:
            conn.close()
        return {"account_id": ctx.account_id, "entries": entries}

    # ══════════════════════════ AUDIT (factory own) ═════════════════════════
    @app.get(_PREFIX + "/audit")
    async def factory_audit(request: Request, limit: int = 50):
        ctx = _require(request, Role.FACTORY)
        conn = _open()
        try:  # own account only — cannot see other accounts' logs
            rows = audit.search(conn, account_id=ctx.account_id, limit=limit)
        finally:
            conn.close()
        return {"account_id": ctx.account_id, "audit": rows}

    # ══════════════════════════ ADMIN ═══════════════════════════════════════
    @app.get(_PREFIX + "/admin/metrics")
    async def admin_metrics(request: Request):
        ctx = _require(request, Role.SILK_ADMIN)
        conn = _open()
        try:
            by_tier = {r["tier"]: r["c"] for r in conn.execute(
                "SELECT tier, COUNT(*) AS c FROM accounts WHERE is_vault = 0 "
                "GROUP BY tier").fetchall()}
            active_studies = conn.execute(
                "SELECT COUNT(*) AS c FROM studies WHERE state = 'in_progress'"
            ).fetchone()["c"]
            vault_id = seed_mod.vault_account_id(conn)
            vw = wallet.get_wallet(conn, vault_id) if vault_id else None
            out = {"accounts_by_tier": by_tier, "active_studies": active_studies,
                   "vault_balance_cents": int(vw["balance"]) if vw else 0,
                   "kill_switch": settings.kill_switch_on(conn)}
        finally:
            conn.close()
        return out

    @app.post(_PREFIX + "/admin/fund")
    async def admin_fund(request: Request):
        ctx = _require(request, Role.SILK_ADMIN)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            factory_id = int(body.get("account_id") or 0)
            amount = int(body.get("amount_cents") or 0)
            acc = conn.execute("SELECT * FROM accounts WHERE id = ?",
                               (factory_id,)).fetchone()
            if not acc or acc["kind"] != "factory":
                raise HTTPException(status_code=404, detail="factory account not found")
            vault_id = seed_mod.vault_account_id(conn)
            if vault_id is None:
                raise HTTPException(status_code=500, detail="no vault account")
            try:
                vault_eid, factory_eid = wallet.fund_wallet(
                    conn, admin_user_id=ctx.user_id, factory_account_id=factory_id,
                    amount_cents=amount, vault_account_id=vault_id,
                    description=body.get("description") or "admin funding")
            except wallet.InsufficientFunds:
                raise HTTPException(status_code=402, detail="vault has insufficient funds")
            except wallet.WalletError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            out = {"ok": True, "vault_entry_id": vault_eid,
                   "factory_entry_id": factory_eid, "amount_cents": amount}
        finally:
            conn.close()
        return out

    @app.get(_PREFIX + "/admin/kill-switch")
    async def get_kill_switch(request: Request):
        ctx = _require(request, Role.SILK_ADMIN)
        conn = _open()
        try:
            state = settings.kill_switch_on(conn)
        finally:
            conn.close()
        return {"on": state}

    @app.post(_PREFIX + "/admin/kill-switch")
    async def set_kill_switch_ep(request: Request):
        ctx = _require(request, Role.SILK_ADMIN)
        body = _json_body(await _safe_json(request))
        conn = _open()
        try:
            settings.set_kill_switch(conn, bool(body.get("on")),
                                     admin_user_id=ctx.user_id)
            state = settings.kill_switch_on(conn)
        finally:
            conn.close()
        return {"on": state}

    @app.get(_PREFIX + "/admin/audit")
    async def admin_audit(request: Request, limit: int = 50,
                          account_id: int | None = None, action: str | None = None):
        ctx = _require(request, Role.SILK_ADMIN)
        conn = _open()
        try:  # global search (admin only)
            rows = audit.search(conn, account_id=account_id, action=action, limit=limit)
        finally:
            conn.close()
        return {"audit": rows}

    # ══════════════════════════ ANALYST ═════════════════════════════════════
    @app.get(_PREFIX + "/analyst/aggregates")
    async def analyst_aggregates(request: Request):
        # مجمّعات مجهّلة للقراءة فقط — no account-level data, no PII.
        ctx = _require(request, Role.SILK_ANALYST, Role.SILK_ADMIN)
        conn = _open()
        try:
            tiers = {r["tier"]: r["c"] for r in conn.execute(
                "SELECT tier, COUNT(*) AS c FROM accounts WHERE is_vault = 0 "
                "GROUP BY tier").fetchall()}
            studies_by_state = {r["state"]: r["c"] for r in conn.execute(
                "SELECT state, COUNT(*) AS c FROM studies GROUP BY state").fetchall()}
            resp_by_industry = [dict(r) for r in conn.execute(
                "SELECT industry, COUNT(*) AS prospects FROM prospects "
                "WHERE industry IS NOT NULL GROUP BY industry").fetchall()]
            out = {"tier_adoption": tiers, "studies_by_state": studies_by_state,
                   "response_rates_by_industry": resp_by_industry}
        finally:
            conn.close()
        return out

    return True


async def _safe_json(request) -> dict:
    """اقرأ جسم JSON بلا انهيار — parse body; {} on empty/invalid."""
    try:
        return await request.json()
    except Exception:  # noqa: BLE001
        return {}
