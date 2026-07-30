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
import sqlite3
import threading
import time
import uuid

from . import (auth, audit, billing, crypto, email_queue, entitlements,
               lifecycle, passwords, quota, reporting, repository,
               scheduler, seed as seed_mod, settings, throttle,
               users as users_mod, wallet)
from .db import connect, init_db
from .models import (AuthContext, PRICE_REPORT_CENTS, Role,
                     projected_email_cost_cents)

log = logging.getLogger(__name__)

COOKIE_NAME = "silk_session"
_PREFIX = "/platform"

# خنق الدخول يعيش في قاعدة المنصّة لا في ذاكرة العملية (`silk_platform/throttle.py`):
# الحالة على مستوى الوحدة تنفصل لكل worker فيصير السقف ١٠×عددها، وتُمحى عند كل
# إعادة نشر. Throttle state is DB-backed ⇒ shared across workers, restart-durable.


# ── تحقّق من المدخلات · input coercion (client faults must be 4xx, never 500) ─
def _as_int(value, field: str, *, minimum: int | None = None,
            maximum: int | None = None, default: int | None = None):
    """حوّل مدخلاً إلى صحيح أو ارفعه 422 — coerce to int or raise a 422.

    يرفض النصّ غير الرقمي والعائم (المال بالسنتات الصحيحة: 250.75 لا تُقتطَع
    صمتاً إلى 250) والمنطقي. Rejects non-numeric, float, and bool inputs.
    """
    from fastapi import HTTPException
    if value is None or value == "":
        if default is not None:
            return default
        raise HTTPException(status_code=422, detail=f"{field} is required")
    if isinstance(value, bool) or isinstance(value, float):
        raise HTTPException(status_code=422,
                            detail=f"{field} must be an integer, got {value!r}")
    try:
        out = int(str(value).strip())
    except (TypeError, ValueError):
        raise HTTPException(status_code=422,
                            detail=f"{field} must be an integer, got {value!r}")
    if minimum is not None and out < minimum:
        raise HTTPException(status_code=422, detail=f"{field} must be >= {minimum}")
    if maximum is not None and out > maximum:
        raise HTTPException(status_code=422, detail=f"{field} must be <= {maximum}")
    return out


def _require_fields(body: dict, *names: str) -> None:
    """اطلب حقولاً غير فارغة — 422 for a missing NOT NULL field (never a 500)."""
    from fastapi import HTTPException
    missing = [n for n in names if body.get(n) in (None, "")]
    if missing:
        raise HTTPException(status_code=422,
                            detail=f"missing required field(s): {', '.join(missing)}")


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


def boot_config_guard() -> None:
    """حارس إقلاع — fail fast if the signing secret is missing in production.

    غير مضبوط في التطوير = وضع مفتوح (سرّ عابر لكل عملية). لكن مع أي إشارة
    إنتاج (`SILK_PLATFORM_REQUIRE_SECRET=1` أو `SILK_PLATFORM_SECURE_COOKIES=1`)
    يجب أن يكون `SILK_PLATFORM_SECRET` مضبوطاً — وإلا نرفض الإقلاع بصوت عالٍ بدل
    الخدمة بسرّ عابر يجعل اعتماد SMTP غير قابل للفكّ بعد إعادة التشغيل ويُبطل
    الروابط الموقّعة. Fail-fast in prod; never silently serve with an ephemeral
    secret. (Mirrors the engine's SILK_REQUIRE_PERSISTENT_DATA_DIR pattern.)
    """
    secret = os.environ.get("SILK_PLATFORM_SECRET", "").strip()
    prod_signal = (os.environ.get("SILK_PLATFORM_REQUIRE_SECRET") == "1"
                   or os.environ.get("SILK_PLATFORM_SECURE_COOKIES") == "1")
    if not secret and prod_signal:
        raise RuntimeError(
            "SILK_PLATFORM_SECRET must be set when a production signal is active "
            "(SILK_PLATFORM_REQUIRE_SECRET=1 or SILK_PLATFORM_SECURE_COOKIES=1); "
            "refusing to boot with an ephemeral per-process secret.")
    # عامل عمل مُخفَّض لا يصل الإنتاج أبداً: التخفيض أداةُ اختبارات، فلو تسرّب
    # `SILK_PLATFORM_BCRYPT_ROUNDS` إلى بيئة إنتاجية نرفض الإقلاع بصوت عالٍ بدل
    # تجزئة كلمات مرور حقيقية بعاملٍ ضعيف صامتاً.
    # A reduced work factor can never reach production — it refuses to boot.
    from .passwords import bcrypt_rounds, _BCRYPT_MIN_PRODUCTION
    if prod_signal and bcrypt_rounds() < _BCRYPT_MIN_PRODUCTION:
        raise RuntimeError(
            f"SILK_PLATFORM_BCRYPT_ROUNDS={bcrypt_rounds()} is below the "
            f"production minimum ({_BCRYPT_MIN_PRODUCTION}); it is a test-only "
            "knob. Refusing to boot with a weakened password work factor.")


def create_platform_app():
    """أنشئ تطبيق المنصّة المستقلّ — standalone FastAPI app for tests/dev."""
    from fastapi import FastAPI
    app = FastAPI(title="Silk Platform (PR-1: auth + tenancy)")
    mount(app)
    return app


def mount(app) -> bool:
    """ركّب كل نقاط المنصّة على `app` تحت /platform — returns True on success."""
    boot_config_guard()   # افشل بصوت عالٍ على سوء تهيئة الإنتاج · fail fast
    try:
        from fastapi import Request, Response
        from fastapi.responses import JSONResponse
    except Exception:  # noqa: BLE001 — بلا fastapi لا تركيب (استيراد بلا انهيار)
        log.warning("fastapi unavailable — platform router not mounted")
        return False

    from fastapi import Body, HTTPException
    from starlette.concurrency import run_in_threadpool

    def _resolve_token(token: str):
        """حُلّ الرمز في اتصال خاص — blocking; runs in the threadpool."""
        conn = _open()
        try:
            return auth.resolve_session(conn, token)
        finally:
            conn.close()

    # ── وسيط تحميل السياق · context-loading middleware ───────────────────────
    @app.middleware("http")
    async def _load_auth(request: Request, call_next):
        """حمّل current_user/current_account/current_role في سياق الطلب.

        أفضل جهد: لا يرفع أبداً؛ الرمز الغائب/المنتهي => state.auth = None،
        والنقاط المحميّة هي من تفرض 401/403. Best-effort; guards enforce.

        عمل SQLite الحاجب يُنفَّذ في مجمّع خيوط لا على حلقة الأحداث — الوسيط
        يعمل لكل طلب، وحجبُ الحلقة هنا كان يعطّل خدمة المحرّك المُركَّبة معه.
        The blocking DB read runs off the event loop (shared app!).
        """
        request.state.auth = None
        if request.url.path.startswith(_PREFIX):
            token = _bearer(request.headers) or request.cookies.get(COOKIE_NAME, "")
            if token:
                request.state.auth = await run_in_threadpool(_resolve_token, token)
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
                       resource_type: str) -> dict:
        """اجلب صفّاً مُستأجَراً بدلالة الدور — enforce the isolation matrix.

        - analyst: 403 (مجمّعات فقط).
        - admin: يرى دراسات حساب سِلك فقط؛ محتوى مصنع => 403.
        - factory: حسابه فقط؛ صفّ حساب آخر => 404 (+ تدقيق محاولة عبور).

        يُغلق اتصاله بنفسه ويرجّع الصفّ فقط — تسليم الاتصال للمُنادي كان عقداً
        قابلاً للتسريب بلا فائدة. Owns and closes its connection; returns the row.
        """
        ctx = _ctx(request)
        conn = _open()
        try:
            repo = repo_factory(conn)
            if ctx.is_silk_analyst:
                raise HTTPException(status_code=403, detail="analysts see aggregates only")
            row = repo.get(ctx.account_id, row_id)
            if row is not None:
                return row
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
        finally:
            conn.close()

    def _deny_write_404(conn, request: Request, ctx: AuthContext, repo,
                        row_id: int, resource_type: str, action: str):
        """دلالة رفض الكتابة عبر المستأجر — one place for the write-denial rule.

        صفر صفوف متأثّرة يعني: إمّا الصفّ غير موجود أو لحسابٍ آخر. الحالتان 404
        (لا تسريب وجود)، ومحاولة العبور تُسجَّل تدقيقاً. كانت هذه الكتلة منسوخة
        في خمسة مواضع، فتُنسى في السادس. Single definition of the denial semantics.
        """
        if repo.exists_anywhere(row_id):
            audit.record_denied(conn, action=action, user_id=ctx.user_id,
                                account_id=ctx.account_id,
                                resource_type=resource_type, resource_id=row_id,
                                ip_address=_client_ip(request))
        raise HTTPException(status_code=404, detail="not found")

    # ══════════════════════════ AUTH ════════════════════════════════════════
    # ملاحظة على `def` بلا `async` في كل ما يلي: هذه المعالجات تُنفِّذ عملاً
    # حاجباً (bcrypt عامل ١٢ ≈ ٢٥٠ms، وSQLite بمهلة انتظار قفل). FastAPI يشغّل
    # المعالجات المتزامنة في مجمّع خيوط تلقائياً، فلا تُحجَب حلقة الأحداث —
    # وهي حلقة **مشتركة** مع خدمة المحرّك المُركَّبة على نفس التطبيق.
    # Sync handlers ⇒ FastAPI runs them in its threadpool, off the shared loop.
    @app.post(_PREFIX + "/auth/login")
    def login(request: Request, body: dict = Body(default=None)):
        body = _json_body(body)
        email = (body.get("email") or "").strip()
        password = body.get("password") or ""
        ident = throttle.identity(email, _client_ip(request))
        conn = _open()
        try:
            # الخنق يُقرأ من القاعدة فيراه كل worker فوراً (لا حالة في الذاكرة).
            if throttle.is_throttled(conn, ident):
                raise HTTPException(status_code=429,
                                    detail="too many failed attempts; try again later")
            user = auth.authenticate(conn, email, password)
            if not user:
                throttle.record_failure(conn, ident)
                # لا تعداد مستخدمين: نفس الرسالة والتوقيت للمجهول والخطأ.
                raise HTTPException(status_code=401, detail="invalid credentials")
            throttle.clear(conn, ident)
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
    def logout(request: Request):
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
    def me(request: Request):
        ctx = _ctx(request)
        return {"user_id": ctx.user_id, "account_id": ctx.account_id,
                "role": ctx.role.value, "email": ctx.email,
                "language_preference": ctx.language_preference}

    @app.post(_PREFIX + "/auth/password-reset/request")
    def reset_request(request: Request, body: dict = Body(default=None)):
        body = _json_body(body)
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
    def reset_confirm(request: Request, body: dict = Body(default=None)):
        from .passwords import PasswordError
        body = _json_body(body)
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
        """ارفض ربط SMTP عابراً للمستأجر — reject foreign smtp_config binding.

        `None` وحدها تعني «غير مضبوط»؛ أمّا 0 أو "" فقيمة **غير صالحة** تُرفَض
        422 — كانت تعبر الفحص ثم تُكتب في عمود بمفتاح أجنبي مُفعَّل فتُنتج 500.
        Only None means unset: 0/"" are invalid (they used to reach the FK).
        """
        if smtp_config_id is None:
            return None
        sid = _as_int(smtp_config_id, "smtp_config_id", minimum=1)
        row = conn.execute("SELECT * FROM smtp_configs WHERE id = ?",
                           (sid,)).fetchone()
        if not row or row["owner_id"] != ctx.account_id:
            raise HTTPException(status_code=422,
                                detail="smtp_config_id not owned by this account")
        return dict(row)

    @app.get(_PREFIX + "/studies")
    def list_studies(request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        conn = _open()
        try:
            rows = repository.studies(conn).list(ctx.account_id)
        finally:
            conn.close()
        return {"studies": rows}

    @app.post(_PREFIX + "/studies")
    def create_study(request: Request, body: dict = Body(default=None)):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            _validate_smtp_binding(conn, ctx, body.get("smtp_config_id"))
            fields = {k: body.get(k) for k in
                      ("title_en", "title_ar", "description_en", "description_ar",
                       "smtp_config_id")}
            # عدد مستهدف صحيح غير سالب — otherwise launch's int() would 500 later.
            fields["target_count"] = _as_int(body.get("target_count"),
                                             "target_count", minimum=0, default=0)
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
    def get_study(study_id: int, request: Request):
        return _tenant_detail(request, repository.studies, study_id, "study")

    @app.patch(_PREFIX + "/studies/{study_id}")
    def patch_study(study_id: int, request: Request, body: dict = Body(default=None)):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            if body.get("smtp_config_id") is not None:
                _validate_smtp_binding(conn, ctx, body.get("smtp_config_id"))
            fields = {k: body[k] for k in body if k in
                      ("title_en", "title_ar", "description_en",
                       "description_ar", "smtp_config_id")}
            if "target_count" in body:   # صحيح غير سالب أو 422 (لا None لعمود NOT NULL)
                fields["target_count"] = _as_int(body.get("target_count"),
                                                 "target_count", minimum=0)
            repo = repository.studies(conn)
            updated = repo.update(ctx.account_id, study_id, fields)
            if updated is None:
                _deny_write_404(conn, request, ctx, repo, study_id, "study",
                                "cross_tenant_write")
            audit.record(conn, action="study_updated", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="study",
                         resource_id=study_id)
            conn.commit()
        finally:
            conn.close()
        return updated

    @app.delete(_PREFIX + "/studies/{study_id}")
    def delete_study(study_id: int, request: Request):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        conn = _open()
        try:
            repo = repository.studies(conn)
            ok = repo.delete(ctx.account_id, study_id)
            if not ok:
                _deny_write_404(conn, request, ctx, repo, study_id, "study",
                                "cross_tenant_delete")
            audit.record(conn, action="study_deleted", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="study",
                         resource_id=study_id)
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    @app.post(_PREFIX + "/studies/{study_id}/launch")
    def launch_study(study_id: int, request: Request, body: dict = Body(default=None)):
        """أطلق دراسة — smtp validation → wallet sufficiency → claim → quota → queue.

        العدّاد يزيد هنا فقط (أوّل بريد). مفتاح القتل لا يمنع الإطلاق لكنه يوقف
        الإرسال في العامل. The quota counter increments only here.

        **ترتيب مقصود**: الانتقال draft→in_progress يُطالَب به ذرّياً (`AND
        state='draft'` + فحص rowcount) **قبل** حجز الحصّة، فنقرتان متزامنتان
        لا تُنتجان إلا رابحاً واحداً — سابقاً كانتا تستهلكان حصّتين وتصفّان كل
        عميل مرّتين. وإن رُفضت الحصّة يُعاد الانتقال إلى draft (تعويض) فلا
        تُحرَق حصّة بدراسة لم تنطلق. Claim-then-reserve, with compensation.
        """
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            repo = repository.studies(conn)
            study = repo.get(ctx.account_id, study_id)
            if study is None:
                _deny_write_404(conn, request, ctx, repo, study_id, "study",
                                "cross_tenant_launch")
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
            # المديونية تُصرَّح صريحةً لا تُستنتَج من حسابٍ عددي: حساب سالب
            # الرصيد محجوب عن أي إطلاق جديد حتى يُسدَّد بتمويل الأدمِن.
            # Delinquency is an explicit, declared gate — not emergent arithmetic.
            if wallet.is_delinquent(conn, ctx.account_id):
                audit.record(conn, action="launch_blocked_delinquent",
                             user_id=ctx.user_id, account_id=ctx.account_id,
                             resource_type="study", resource_id=study_id,
                             changes={"balance": int(w["balance"])})
                conn.commit()
                raise HTTPException(status_code=402,
                                    detail={"error": "account_delinquent",
                                            "balance_cents": int(w["balance"]),
                                            "settle_up_required": True})
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
            # (3) طالِب بالانتقال ذرّياً · atomically claim draft→in_progress.
            now = auth.now_iso()
            claim = conn.execute(
                "UPDATE studies SET state = 'in_progress', launched_at = ?, "
                "updated_at = ? WHERE id = ? AND owner_id = ? AND state = 'draft'",
                (now, now, study_id, ctx.account_id))
            conn.commit()
            if claim.rowcount == 0:   # سبقنا طلبٌ متزامن · a concurrent launch won
                raise HTTPException(status_code=409,
                                    detail="study is no longer draft (already launching)")
            # (4) الحصّة: احجز (يزيد العدّاد) · quota reserve (increments counter).
            decision = quota.reserve_launch(conn, ctx.account_id,
                                            actor_user_id=ctx.user_id)
            if not decision.allowed:
                # تعويض: أعِد الدراسة مسودّةً فلا تُحرَق حصّة بلا إطلاق.
                conn.execute("UPDATE studies SET state = 'draft', launched_at = NULL, "
                             "updated_at = ? WHERE id = ? AND owner_id = ?",
                             (auth.now_iso(), study_id, ctx.account_id))
                conn.commit()
                raise HTTPException(status_code=403,
                                    detail={"error": "quota_exceeded",
                                            "reason": decision.reason,
                                            "tier": decision.tier,
                                            "limit": decision.limit,
                                            "used": decision.used,
                                            "upgrade": True})
            # (5) صفّ البريد (إن مُرّرت قائمة) — استعلام واحد لكل العملاء والتزام
            # واحد في النهاية، بدل SELECT وcommit لكل مستلم (5000 مستلم = 5000
            # جولة + 5000 fsync). One batched SELECT, one commit.
            queued = 0
            draft_id = body.get("draft_id")
            prospect_ids = body.get("prospect_ids") or []
            if draft_id and prospect_ids:
                draft = repository.drafts(conn).get(ctx.account_id, draft_id)
                if draft:
                    ids = [_as_int(p, "prospect_ids[]", minimum=1)
                           for p in prospect_ids]
                    marks = ",".join("?" for _ in ids)
                    rows = conn.execute(
                        f"SELECT * FROM prospects WHERE owner_id = ? AND id IN ({marks})",
                        [ctx.account_id, *ids]).fetchall()
                    for pr in rows:
                        email_queue.enqueue(
                            conn, account_id=ctx.account_id, study_id=study_id,
                            prospect=dict(pr), draft=draft,
                            smtp_config_id=study["smtp_config_id"],
                            actor_user_id=ctx.user_id, commit=False)
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
    def list_prospects(request: Request):
        ctx = _require(request, Role.FACTORY)  # PII — factory own only
        conn = _open()
        try:
            rows = repository.prospects(conn).list(ctx.account_id)
        finally:
            conn.close()
        return {"prospects": rows}

    @app.post(_PREFIX + "/prospects")
    def create_prospect(request: Request, body: dict = Body(default=None)):
        ctx = _require(request, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            _require_fields(body, "email")
            fields = {k: body.get(k) for k in
                      ("email", "first_name", "last_name", "company", "industry",
                       "language_preference", "tags")}
            try:
                row = repository.prospects(conn).create(ctx.account_id, fields)
            except sqlite3.IntegrityError as exc:
                # خطأ عميل (بريد مكرّر/قيد عمود) => 422. أمّا أخطاء التشغيل
                # (قفل/قرص) فتُترك تصعد 5xx كي لا تُقنَّع كخطأ مدخلات.
                raise HTTPException(status_code=422, detail=str(exc))
            conn.commit()
        finally:
            conn.close()
        return row

    @app.get(_PREFIX + "/prospects/{prospect_id}")
    def get_prospect(prospect_id: int, request: Request):
        return _tenant_detail(request, repository.prospects, prospect_id, "prospect")

    @app.patch(_PREFIX + "/prospects/{prospect_id}")
    def patch_prospect(prospect_id: int, request: Request,
                       body: dict = Body(default=None)):
        ctx = _require(request, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            repo = repository.prospects(conn)
            try:
                updated = repo.update(ctx.account_id, prospect_id, body)
            except sqlite3.IntegrityError as exc:  # UNIQUE(owner_id,email) => 422
                raise HTTPException(status_code=422, detail=str(exc))
            if updated is None:
                _deny_write_404(conn, request, ctx, repo, prospect_id, "prospect",
                                "cross_tenant_write")
            audit.record(conn, action="prospect_updated", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="prospect",
                         resource_id=prospect_id)
            conn.commit()
        finally:
            conn.close()
        return updated

    @app.delete(_PREFIX + "/prospects/{prospect_id}")
    def delete_prospect(prospect_id: int, request: Request):
        ctx = _require(request, Role.FACTORY)
        conn = _open()
        try:
            repo = repository.prospects(conn)
            ok = repo.delete(ctx.account_id, prospect_id)
            if not ok:
                _deny_write_404(conn, request, ctx, repo, prospect_id, "prospect",
                                "cross_tenant_delete")
            audit.record(conn, action="prospect_deleted", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="prospect",
                         resource_id=prospect_id)
            conn.commit()
        finally:
            conn.close()
        return {"ok": True}

    # ══════════════════════════ SMTP CONFIGS ════════════════════════════════
    @app.get(_PREFIX + "/smtp-configs")
    def list_smtp(request: Request):
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
    def create_smtp(request: Request, body: dict = Body(default=None)):
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            # أعمدة NOT NULL تُتحقَّق هنا فيصير النقص 422 لا 500 (IntegrityError).
            _require_fields(body, "host", "port", "from_email")
            fields = {k: body.get(k) for k in
                      ("label", "host", "from_email", "from_name",
                       "use_tls", "is_active")}
            fields["port"] = _as_int(body.get("port"), "port",
                                     minimum=1, maximum=65535)
            # تشفير بيانات الاعتماد عند التخزين · encrypt credentials at rest.
            if body.get("username"):
                fields["username_enc"] = crypto.encrypt(body["username"])
            if body.get("password"):
                fields["password_enc"] = crypto.encrypt(body["password"])
            try:
                row = repository.smtp_configs(conn).create(ctx.account_id, fields)
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            row.pop("username_enc", None)
            row.pop("password_enc", None)
            conn.commit()
        finally:
            conn.close()
        return row

    # ══════════════════════════ IMAGES ══════════════════════════════════════
    @app.post(_PREFIX + "/images")
    def create_image(request: Request, body: dict = Body(default=None)):
        ctx = _require(request, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            ext = (body.get("ext") or "bin").lstrip(".")
            key = f"{ctx.account_id}/{uuid.uuid4().hex}.{ext}"
            fields = {"filename": body.get("filename"), "storage_key": key,
                      "mime_type": body.get("mime_type"),
                      # حجم غير سالب حتماً: قيمة سالبة كانت تُنقص مجموع الحساب
                      # فيتخطّاه `HAVING bytes > 0` وتصير فاتورة تخزينه صفراً.
                      # Non-negative: a negative row could zero the storage bill.
                      "size_bytes": _as_int(body.get("size_bytes"), "size_bytes",
                                            minimum=0, default=0),
                      "uploaded_by_user_id": ctx.user_id,
                      "alt_text_en": body.get("alt_text_en"),
                      "alt_text_ar": body.get("alt_text_ar")}
            row = repository.images(conn).create(ctx.account_id, fields)
            conn.commit()
        finally:
            conn.close()
        return row

    @app.get(_PREFIX + "/images/{image_id}/signed-url")
    def image_signed_url(image_id: int, request: Request):
        """رابط موقّع للصورة — verify owner_id BEFORE signing; foreign ⇒ 404/403.

        **حدّ معلَن**: مسار الخدمة `/files/...` نفسه يأتي مع خزن البايتات في
        موجة الصور (PR-8) — لا شيء يُرفَع فعلياً الآن (هذه النقطة تسجّل بيانات
        وصفية فقط)، فالرابط موقّع وصحيح البنية لكنه لا يُخدَم بعد. البوّابة التي
        يفرضها القسم ١٣ (لا توقيع لمالك أجنبي) نافذة هنا.
        Declared limit: the /files serving route lands with PR-8 storage.
        """
        row = _tenant_detail(request, repository.images, image_id, "image")
        from . import tokens
        # التوقيع يحدث فقط بعد إثبات الملكية · signed only after ownership proven.
        expiry = int(time.time()) + 900
        sig = tokens.sign(f"{row['storage_key']}:{expiry}")
        return {"signed_url": f"/files/{row['storage_key']}?expires={expiry}&sig={sig}",
                "expires": expiry, "storage_key": row["storage_key"],
                "serving_available": False,
                "note": "URL signing is enforced now; /files serving lands in PR-8"}

    # ══════════════════════════ WALLET / LEDGER ═════════════════════════════
    @app.get(_PREFIX + "/wallet")
    def get_wallet_ep(request: Request):
        # analyst: لا بيانات حساب فردية · no individual account data.
        ctx = _ctx(request)
        if ctx.is_silk_analyst:
            raise HTTPException(status_code=403, detail="analysts see aggregates only")
        conn = _open()
        try:
            w = wallet.ensure_wallet(conn, ctx.account_id)  # own account only
            # المديونية وحدّها مرئيان للعميل صراحةً (لماذا تُحجَب الإطلاقات).
            w["delinquent"] = wallet.is_delinquent(conn, ctx.account_id)
            w["overdraft_floor_cents"] = wallet.overdraft_floor_cents()
        finally:
            conn.close()
        return w

    @app.get(_PREFIX + "/wallet/ledger")
    def get_ledger_ep(request: Request, limit: int = 20):
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
    def factory_audit(request: Request, limit: int = 50):
        ctx = _require(request, Role.FACTORY)
        conn = _open()
        try:  # own account only — cannot see other accounts' logs
            rows = audit.search(conn, account_id=ctx.account_id, limit=limit)
        finally:
            conn.close()
        return {"account_id": ctx.account_id, "audit": rows}

    # ═══════════════ دورة حياة الدراسة · study lifecycle transitions ═════════
    def _lifecycle(fn, study_id: int, request: Request, action: str):
        """جسم مشترك لانتقالَي الإنهاء والأرشفة — one body so they cannot drift."""
        ctx = _require(request, Role.SILK_ADMIN, Role.FACTORY)
        conn = _open()
        try:
            try:
                out = fn(conn, account_id=ctx.account_id, study_id=study_id,
                         actor_user_id=ctx.user_id)
            except lifecycle.LifecycleError as exc:
                raise HTTPException(status_code=409, detail=exc.as_detail())
            if out is None:
                repo = repository.studies(conn)
                _deny_write_404(conn, request, ctx, repo, study_id, "study", action)
        finally:
            conn.close()
        return out

    @app.post(_PREFIX + "/studies/{study_id}/complete")
    def complete_study_ep(study_id: int, request: Request):
        """أنهِ دراسة — in_progress → completed؛ 409 ما دام في الطابور معلّق.

        «مكتملة» ورسائلها ستخرج بعد دقائق **ادعاءٌ كاذب** عن الواقع — والعامل لا
        يفحص حالة الدراسة، فلا شيء يوقفها. ينتظر العميل النفاد أو يؤرشف.
        """
        return _lifecycle(lifecycle.complete_study, study_id, request,
                          "cross_tenant_complete")

    @app.post(_PREFIX + "/studies/{study_id}/archive")
    def archive_study_ep(study_id: int, request: Request):
        """أرشِف دراسة — **سحبٌ حقيقي**: يُلغي البريد المعلّق ثم يُغلق الحالة.

        يرجّع `cancelled_queued_emails`. صفٌّ مُرسَل مِن قبل لا يُمَسّ (له قيد
        موافقة وقيد دفتر)، وصفٌّ في الطريق (`sending`) يرفع 409 عابراً.
        """
        return _lifecycle(lifecycle.archive_study, study_id, request,
                          "cross_tenant_archive")

    # ═══════════════ التقرير المدفوع · the charged campaign report ═══════════
    @app.post(_PREFIX + "/studies/{study_id}/report")
    def generate_study_report(study_id: int, request: Request,
                              body: dict = Body(default=None)):
        """أنشئ تقرير حملة واخصم سعره — $1.00، مرّة واحدة لكل مفتاح خمول.

        نقرةٌ مزدوجة أو إعادة محاولة ترجع **نفس** القيد بـ`charged: false` ولا
        تخصم ثانية (المفتاح مخزَّن في وصف قيد الدفتر). الحساب المدين يُرفَض 402،
        والرصيد غير الكافي 402 كذلك — لا يُسجَّل دَينٌ مقابل شيء لم يحدث.
        """
        ctx = _require(request, Role.FACTORY)
        body = _json_body(body)
        key = body.get("idempotency_key")
        if key is not None and not isinstance(key, str):
            raise HTTPException(status_code=422,
                                detail="idempotency_key must be a string")
        conn = _open()
        try:
            try:
                out = reporting.generate_charged_report(
                    conn, account_id=ctx.account_id, actor_user_id=ctx.user_id,
                    study_id=study_id, idempotency_key=key)
            except billing.Delinquent as exc:
                raise HTTPException(status_code=402,
                                    detail={"error": "delinquent",
                                            "message": str(exc)})
            except wallet.InsufficientFunds as exc:
                raise HTTPException(
                    status_code=402,
                    detail={"error": "insufficient_funds", "message": str(exc),
                            "price_cents": PRICE_REPORT_CENTS})
            except billing.BillingError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if out is None:
                # غير مملوكة للمنادي — 404 بلا تسريب وجود + تدقيق للعبور.
                repo = repository.studies(conn)
                if repo.exists_anywhere(study_id):
                    audit.record_denied(conn, action="cross_tenant_read",
                                        user_id=ctx.user_id,
                                        account_id=ctx.account_id,
                                        resource_type="study",
                                        resource_id=study_id,
                                        ip_address=_client_ip(request))
                raise HTTPException(status_code=404, detail="not found")
            if out["billing"]["charged"]:
                audit.record(conn, action="campaign_report_generated",
                             user_id=ctx.user_id, account_id=ctx.account_id,
                             resource_type="study", resource_id=study_id,
                             ip_address=_client_ip(request))
                conn.commit()
        finally:
            conn.close()
        return out

    # ═══════════════ الاستحقاقات والمقاعد · entitlements + seats ═════════════
    class _UsersRepoShim:
        """مُهايئ يعرض `exists_anywhere` بتوقيع المستودع — reuse the denial rule.

        `users` ليس في `repository._WRITABLE` عمداً (أعمدته صلاحيات وهويّة، لا
        حقول CRUD عامّة)، لكن **دلالة رفض الكتابة يجب أن تبقى واحدة**: 404 بلا
        تسريب وجود + قيد تدقيق لمحاولة العبور. المُهايئ يعيد استخدام
        `_deny_write_404` بدل نسخ الكتلة سادسةً. Keeps one denial semantics.
        """

        def __init__(self, conn):
            self.conn = conn

        def exists_anywhere(self, row_id: int) -> bool:
            return users_mod.exists_anywhere(self.conn, row_id)

    def _tier_gate_403(exc: entitlements.TierGateError):
        """ترجم بوّابة الطبقة إلى 403 بدعوة ترقية — the ONE translation point.

        كل مسار يفرض طبقةً يرفع `TierGateError` ويمرّ من هنا، فتخرج دعوة الترقية
        بشكل واحد للواجهة بدل رسائل متناثرة. One shape for every tier denial.
        """
        return HTTPException(status_code=403, detail=exc.as_detail())

    @app.get(_PREFIX + "/entitlements")
    def get_entitlements(request: Request):
        """استحقاقات الحساب + استخدامه — what this tier grants and what's used.

        المصنع يقرأ حسابه؛ الأدمِن يقرأ حسابه هو (بيانات المصانع تمرّ من نقاط
        الأدمِن المخصّصة كي يبقى جدار PII واحداً).
        """
        ctx = _require(request, Role.FACTORY, Role.SILK_ADMIN)
        conn = _open()
        try:
            snap = entitlements.snapshot(conn, ctx.account_id)
        finally:
            conn.close()
        return snap.as_dict()

    @app.get(_PREFIX + "/users")
    def list_account_users(request: Request):
        ctx = _require(request, Role.FACTORY)
        conn = _open()
        try:
            rows = users_mod.list_users(conn, ctx.account_id)
            seats = {"limit": entitlements.seat_limit(
                         users_mod.account_tier(conn, ctx.account_id)),
                     "used": entitlements.seats_used(conn, ctx.account_id)}
        finally:
            conn.close()
        return {"users": rows, "seats": seats}

    @app.post(_PREFIX + "/users")
    def create_account_user(request: Request, body: dict = Body(default=None)):
        """أنشئ مستخدماً فرعياً — seat-gated; the new user's role is always factory.

        الدور و`account_id` **لا يُقرآن من الجسم** إطلاقاً (`users.create_sub_user`
        يفرضهما): قراءتهما كانت ستسمح لمصنع بإنشاء `silk_admin` أو بزرع مستخدم
        في حساب آخر.
        """
        ctx = _require(request, Role.FACTORY)
        body = _json_body(body)
        _require_fields(body, "email", "password")
        conn = _open()
        try:
            try:
                row = users_mod.create_sub_user(
                    conn, ctx.account_id, email=body.get("email"),
                    password=body.get("password"),
                    first_name=body.get("first_name"),
                    last_name=body.get("last_name"),
                    language_preference=body.get("language_preference") or "en")
            except entitlements.TierGateError as exc:
                # قيد تدقيق للمنع (§2: «block + upgrade prompt + audit entry»).
                audit.record(conn, action="seat_limit_exceeded",
                             user_id=ctx.user_id, account_id=ctx.account_id,
                             resource_type="user",
                             changes={"limit": exc.limit, "used": exc.used,
                                      "tier": exc.tier},
                             ip_address=_client_ip(request))
                conn.commit()
                raise _tier_gate_403(exc)
            except users_mod.DuplicateEmail as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except passwords.PasswordError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            except users_mod.UserError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            audit.record(conn, action="sub_user_created", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="user",
                         resource_id=row["id"], ip_address=_client_ip(request))
            conn.commit()
        finally:
            conn.close()
        return row

    @app.get(_PREFIX + "/users/{user_id}")
    def get_account_user(user_id: int, request: Request):
        ctx = _require(request, Role.FACTORY)
        conn = _open()
        try:
            row = users_mod.get_user(conn, ctx.account_id, user_id)
            if row is None:
                if users_mod.exists_anywhere(conn, user_id):
                    audit.record_denied(conn, action="cross_tenant_read",
                                        user_id=ctx.user_id,
                                        account_id=ctx.account_id,
                                        resource_type="user", resource_id=user_id,
                                        ip_address=_client_ip(request))
                raise HTTPException(status_code=404, detail="not found")
        finally:
            conn.close()
        return row

    @app.patch(_PREFIX + "/users/{user_id}")
    def patch_account_user(user_id: int, request: Request,
                           body: dict = Body(default=None)):
        """حدّث حقول عرض مستخدم — profile fields only (no role, no activation)."""
        ctx = _require(request, Role.FACTORY)
        body = _json_body(body)
        conn = _open()
        try:
            try:
                row = users_mod.update_profile(conn, ctx.account_id, user_id, body)
            except users_mod.UserError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if row is None:
                _deny_write_404(conn, request, ctx, _UsersRepoShim(conn), user_id,
                                "user", "cross_tenant_write")
            audit.record(conn, action="sub_user_updated", user_id=ctx.user_id,
                         account_id=ctx.account_id, resource_type="user",
                         resource_id=user_id, ip_address=_client_ip(request))
            conn.commit()
        finally:
            conn.close()
        return row

    def _set_user_active(user_id: int, request: Request, active: bool):
        """جسم مشترك للتنشيط/التعطيل — one body so the two paths cannot drift."""
        ctx = _require(request, Role.FACTORY)
        conn = _open()
        try:
            try:
                row = users_mod.set_active(conn, ctx.account_id, user_id, active,
                                           acting_user_id=ctx.user_id)
            except entitlements.TierGateError as exc:
                audit.record(conn, action="seat_limit_exceeded",
                             user_id=ctx.user_id, account_id=ctx.account_id,
                             resource_type="user", resource_id=user_id,
                             changes={"limit": exc.limit, "used": exc.used,
                                      "tier": exc.tier, "on": "activate"},
                             ip_address=_client_ip(request))
                conn.commit()
                raise _tier_gate_403(exc)
            except users_mod.UserError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if row is None:
                _deny_write_404(conn, request, ctx, _UsersRepoShim(conn), user_id,
                                "user", "cross_tenant_write")
            audit.record(conn,
                         action="sub_user_activated" if active
                         else "sub_user_deactivated",
                         user_id=ctx.user_id, account_id=ctx.account_id,
                         resource_type="user", resource_id=user_id,
                         ip_address=_client_ip(request))
            conn.commit()
        finally:
            conn.close()
        return row

    @app.post(_PREFIX + "/users/{user_id}/deactivate")
    def deactivate_account_user(user_id: int, request: Request):
        return _set_user_active(user_id, request, False)

    @app.post(_PREFIX + "/users/{user_id}/activate")
    def activate_account_user(user_id: int, request: Request):
        return _set_user_active(user_id, request, True)

    # ══════════════════════════ ADMIN ═══════════════════════════════════════
    @app.get(_PREFIX + "/admin/metrics")
    def admin_metrics(request: Request):
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
    def admin_fund(request: Request, body: dict = Body(default=None)):
        ctx = _require(request, Role.SILK_ADMIN)
        body = _json_body(body)
        conn = _open()
        try:
            # صحيحان أو 422: `int("1,000")` كان 500، و250.75 كانت تُقتطَع صمتاً
            # إلى 250 فيُقيَّد مبلغ يخالف ما أرسله الأدمِن (المال سنتات صحيحة).
            factory_id = _as_int(body.get("account_id"), "account_id", minimum=1)
            amount = _as_int(body.get("amount_cents"), "amount_cents", minimum=1)
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
    def get_kill_switch(request: Request):
        ctx = _require(request, Role.SILK_ADMIN)
        conn = _open()
        try:
            state = settings.kill_switch_on(conn)
        finally:
            conn.close()
        return {"on": state}

    @app.post(_PREFIX + "/admin/kill-switch")
    def set_kill_switch_ep(request: Request, body: dict = Body(default=None)):
        ctx = _require(request, Role.SILK_ADMIN)
        body = _json_body(body)
        conn = _open()
        try:
            settings.set_kill_switch(conn, bool(body.get("on")),
                                     admin_user_id=ctx.user_id)
            state = settings.kill_switch_on(conn)
        finally:
            conn.close()
        return {"on": state}

    @app.get(_PREFIX + "/admin/audit")
    def admin_audit(request: Request, limit: int = 50,
                    account_id: int | None = None, action: str | None = None):
        ctx = _require(request, Role.SILK_ADMIN)
        conn = _open()
        try:  # global search (admin only)
            rows = audit.search(conn, account_id=account_id, action=action, limit=limit)
        finally:
            conn.close()
        return {"audit": rows}

    @app.get(_PREFIX + "/admin/accounts")
    def admin_list_accounts(request: Request, limit: int = 100):
        """اسرد حسابات المصانع بطبقاتها ومقاعدها — tier management needs this view.

        مقاييس فقط (طبقة، مقاعد، حالة) بلا محتوى مصنع ولا PII — جدار الأدمِن
        القائم يمنع محتوى المصانع، وهذه النقطة لا تخرقه. No factory content/PII.
        """
        ctx = _require(request, Role.SILK_ADMIN)
        limit = _as_int(limit, "limit", minimum=1, maximum=500, default=100)
        conn = _open()
        try:
            rows = conn.execute(
                "SELECT id, name, tier, is_active, created_at FROM accounts "
                "WHERE kind = 'factory' ORDER BY id LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                out.append({**dict(r),
                            "seats_limit": entitlements.seat_limit(r["tier"]),
                            "seats_used": entitlements.seats_used(conn, r["id"])})
        finally:
            conn.close()
        return {"accounts": out}

    @app.post(_PREFIX + "/admin/accounts/{account_id}/tier")
    def admin_set_tier(account_id: int, request: Request,
                       body: dict = Body(default=None)):
        """غيّر طبقة حساب مصنع — audited; a seat-breaking downgrade is refused.

        رمز `seats_exceed_target_tier` يرجع 409: على الأدمِن تعطيل الزائد أولاً
        ثم التخفيض؛ لا شيفرة تختار **أيّ** مستخدم يُعطَّل.
        """
        ctx = _require(request, Role.SILK_ADMIN)
        body = _json_body(body)
        _require_fields(body, "tier")
        conn = _open()
        try:
            try:
                out = entitlements.set_account_tier(
                    conn, account_id, body.get("tier"), admin_user_id=ctx.user_id)
            except entitlements.TierChangeError as exc:
                code = {"account_not_found": 404,
                        "not_a_factory_account": 422,
                        "unknown_tier": 422,
                        "seats_exceed_target_tier": 409}.get(exc.code, 422)
                raise HTTPException(status_code=code, detail=exc.as_detail())
        finally:
            conn.close()
        return out

    @app.post(_PREFIX + "/admin/users/{user_id}/reset")
    def admin_issue_reset(user_id: int, request: Request):
        """إعادة تعيين مساعدة من الأدمِن — PR-5 stopgap until email delivery lands.

        يُصدر رمزاً أحادي الاستخدام (٣٠ دقيقة) لمستخدم، يوصله الأدمِن للمستخدم
        عبر قناة دعم؛ ثم يُستهلَك بنقطة confirm العادية. مدقَّق (من أعاد تعيين مَن).
        Admin-only, audit-logged; the raw token goes to the authenticated admin.
        """
        ctx = _require(request, Role.SILK_ADMIN)
        conn = _open()
        try:
            raw = auth.issue_reset_token_for_user(conn, user_id)
            if raw is None:
                raise HTTPException(status_code=404, detail="user not found")
            audit.record(conn, action="admin_password_reset_issued",
                         user_id=ctx.user_id, account_id=ctx.account_id,
                         resource_type="user", resource_id=user_id,
                         ip_address=_client_ip(request))
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "user_id": user_id, "reset_token": raw,
                "note": "single-use, 30-min expiry; convey to the user via support"}

    # ══════════════════════════ ANALYST ═════════════════════════════════════
    @app.get(_PREFIX + "/analyst/aggregates")
    def analyst_aggregates(request: Request):
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

    # مجدول المهام — opt-in بـSILK_PLATFORM_SCHEDULER=1؛ بلا الضبط لا خيط أصلاً،
    # فتشغيلة اختبار أو تطوير لا تصفّر حصّة ولا تكتب فاتورة. يُبدأ بعد نجاح
    # التركيب كي لا يبقى خيطٌ يعمل لتطبيق لم يُركَّب.
    # Started only after a successful mount; no thread unless explicitly enabled.
    scheduler.start()
    return True
