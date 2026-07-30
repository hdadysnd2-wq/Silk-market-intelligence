"""واجهة REST لمنصّة سِلك — FastAPI service exposing the Silk engine.

Lazy-imports FastAPI/pydantic inside create_app() so that `import api` works
offline and even when fastapi is absent (founding principle: graceful degrade,
never crash, never fabricate). Module-level `app` is None when fastapi is missing.

Run:  python3 api.py   (needs `pip install fastapi uvicorn`).
"""
import dataclasses
import hmac
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_PIP_HINT = "FastAPI/uvicorn not installed — run: pip install fastapi uvicorn"


def _to_jsonable(obj: object) -> object:
    """حوّل DataPoint وغيره إلى JSON — make DataPoints/dataclasses JSON-safe."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _rmtree_bg(tmpdir: str):
    """BackgroundTask تحذف مجلّد تصدير مؤقّت **بعد** إرسال الردّ (البند #8،
    تدقيق v2 الموجة ٣) — FileResponse يبثّ الملف لا مجلّده، فبلا هذا يتراكم
    كل mkdtemp على قرص النشر الفاني حتى إعادة النشر. حذفٌ صامت (ignore_errors)."""
    import shutil
    from starlette.background import BackgroundTask
    return BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True)


def _index_search(q: str = "", limit: int = 20) -> list[dict]:
    """فهرس بحث المنتجات للوحة المعلومات — product search index for the dashboard
    combobox. Returns [{"name", "hs", "analyzed"}]; empty q -> a small default
    list. Pure/offline (the HS CSV load is lru_cached). Never fabricates.
    """
    import silk_hs_resolver as resolver

    def _row(r: dict) -> dict:
        return {"name": r.get("name_ar") or r.get("name_en"),
                "hs": r.get("hs_code"), "analyzed": False}

    rows = resolver.load_hs_codes()
    q = (q or "").strip().lower()
    if not q:
        return [_row(r) for r in rows[:12]]

    out: list[dict] = []
    for r in rows:
        hay = " ".join([
            (r.get("name_ar") or ""), (r.get("name_en") or ""),
            (r.get("keywords") or ""),
        ]).lower()
        if q in hay:
            out.append(_row(r))
        if len(out) >= max(1, limit):
            break
    return out


def _platform_db_path() -> str | None:
    """مسار قاعدة المنصّة المحلول — resolved platform DB path for /health.

    None حين لا تكون الحزمة متوفّرة (المنصّة إضافة اختيارية استيراداً).
    """
    try:
        import silk_platform.db as _pdb
        return _pdb.db_path()
    except Exception:  # noqa: BLE001 — الكشف أفضل جهد؛ لا يُسقط /health
        return None


def _platform_readiness() -> dict | None:
    """جهوزيّة المنصّة لِـ/health — None حين لا تتوفّر الحزمة استيراداً.

    يفتح اتصالاً للقراءة فقط ويغلقه؛ أي فشل يعود None بدل إسقاط `/health`
    (نفس عقد `_platform_db_path`). Best-effort; never breaks /health.
    """
    try:
        from silk_platform import bootstrap as _pboot, db as _pdb
        conn = _pdb.connect()
        try:
            return _pboot.readiness(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return None


def _cors_origins() -> list[str]:
    """أصول CORS المسموحة — allowed origins from CORS_ORIGINS; [] = same-origin only.

    الموجة ٠: الافتراضي لم يعد "*" — بلا ضبط صريح لا يُركَّب CORS إطلاقاً
    (الواجهة تُقدَّم من نفس الأصل). Wildcard requires an explicit opt-in.
    """
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    if not raw:
        return []
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def _api_key_expected() -> str:
    """مفتاح الخدمة المتوقع — the API key required by /analyze ('' = auth off).

    الموجة ٠: يُضبط SILK_API_KEY في الإنتاج فيصير كل طلب /analyze بلا ترويسة
    X-API-Key مطابقة = 401 **قبل تشغيل أي وكيل**. غير مضبوط => وضع تطوير مفتوح.
    """
    return os.environ.get("SILK_API_KEY", "").strip()


# الطبقات المدفوعة الخاضعة للسقف — paid layers counted against the daily cap.
_PAID_FLAGS = ("with_localprice", "with_volza", "with_explee", "with_ai")

# مفاتيح البيئة المدفوعة — the paid-provider key env vars (503 guard below).
_PAID_KEY_ENVS = ("LOCALPRICE_API_KEY", "VOLZA_API_KEY",
                  "EXPLEE_API_KEY", "ANTHROPIC_API_KEY")


def _unprotected_paid_keys() -> list[str]:
    """مفاتيح مدفوعة بلا مصادقة — paid keys present while SILK_API_KEY is unset.

    الإغلاق المسبق قبل الموجة ٤: مفتاح مدفوع في البيئة + مصادقة معطّلة =
    خدمة عامة تصرف رصيداً مدفوعاً لأي مجهول. القاعدة: وضع التطوير المفتوح
    مشروع فقط عند غياب المفاتيح المدفوعة **كلها**؛ وجود أي منها يوجب ضبط
    SILK_API_KEY وإلا رُفض تشغيل الطبقات المدفوعة بـ503 وسبب واضح.
    """
    if _api_key_expected():
        return []
    return [k for k in _PAID_KEY_ENVS if os.environ.get(k, "").strip()]


def create_app():
    """أنشئ تطبيق FastAPI — build the FastAPI app, or raise if fastapi is absent."""
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse
        from fastapi.staticfiles import StaticFiles
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover - exercised only without dep
        raise RuntimeError(_PIP_HINT) from exc

    import silk_engine
    import silk_hs_resolver
    import silk_storage
    import silk_usage

    app = FastAPI(title="Silk Market Intelligence API",
                  description="Real public-data export-market analysis "
                              "(UN Comtrade + World Bank). Preliminary, never fabricated.")

    # Stage 2A: حمّل مفاتيح المصادر المحفوظة إلى بيئة العملية عند الإقلاع —
    # متغير بيئة النشر يفوز (overwrite=False). Best-effort: فشلها لا يمنع الإقلاع.
    try:
        import silk_store as _store
        _store.migrate()
        _store.load_settings_into_env()
    except Exception as _e:  # noqa: BLE001
        log.debug("settings bootstrap skipped: %s", _e)

    # القرص الدائم: هيّئ قاعدة التحليلات ومجلد ذاكرة الطلبات على مساراتهما
    # الموجَّهة (SILK_DB/SILK_CACHE_DIR أو SILK_DATA_DIR على Railway volume)
    # عند الإقلاع — إنشاء المجلدات إن غابت، فلا يفاجأ أول طلب بكتابة فاشلة.
    # Persistent volume: init the analyses DB and cache dir at startup so the
    # first request never trips over a missing directory. Best-effort.
    try:
        silk_storage.init_db()
        import silk_cache as _cache
        os.makedirs(_cache._cache_dir(), exist_ok=True)
    except Exception as _e:  # noqa: BLE001
        log.warning("storage bootstrap failed (continuing): %s", _e)

    # قوانين LESSONS.md البند ٤ (فقدان تحليلات مدفوعة على قرص Railway الفاني):
    # مصيدة إقلاع صريحة. حين يضبط المشغّل SILK_REQUIRE_PERSISTENT_DATA_DIR
    # (على النشر الإنتاجي) بلا توجيه أيّ مخزن دائم (SILK_DATA_DIR أو SILK_DB)،
    # ترفض الخدمة الإقلاع بصوت عالٍ بدل أن تكتب على قرص يُمحى عند إعادة النشر
    # التالية فتُفقَد كل التحليلات (وبيانات المخزن/الاستخدام/الذاكرة المؤقتة)
    # بصمت. مطفأة افتراضياً — نفس عقد المشروع «غير مضبوط = وضع تطوير مفتوح»
    # (كـSILK_API_KEY/SILK_PAID_DAILY_CAP)، فالمجموعة الهرمتية والتطوير بلا
    # مفاتيح لا يتأثران؛ تحذير /health الدائم يبقى قائماً في كلتا الحالتين.
    #
    # تقوية (بلاغ المالك الحيّ — «الدراسة تروح بعد كل دبلوي رغم ضبط المتغيّر»):
    # الفحص القديم اكتفى بأن المتغيّر **غير فارغ**، فمرّ سيناريو `SILK_DATA_DIR`
    # مضبوط بلا وحدة تخزين مركّبة على مساره فعلًا — الكتابة تذهب لجذر الحاوية
    # الفاني والحارس نائم. الآن نتحقّق من الحالة الفعلية: مركَّب (is_mount) +
    # قابل للكتابة (writable). المخرج الوحيد لغير-المركَّب هو SILK_ALLOW_NONMOUNT_
    # PERSIST=1 (لمضيفٍ قرصه الجذري دائم أصلًا) — قرار مشغّل صريح لا صمت.
    _require_persist = os.environ.get(
        "SILK_REQUIRE_PERSISTENT_DATA_DIR", "").strip().lower() \
        in ("1", "true", "yes", "on")
    if _require_persist:
        _pst = silk_storage.persistence_status()
        _allow_nonmount = os.environ.get(
            "SILK_ALLOW_NONMOUNT_PERSIST", "").strip().lower() \
            in ("1", "true", "yes", "on")
        if not _pst["configured"]:
            raise RuntimeError(
                "SILK_REQUIRE_PERSISTENT_DATA_DIR مضبوط لكن لا SILK_DATA_DIR ولا "
                "SILK_DB موجَّه إلى تخزين دائم — الحاوية ستفقد كل التحليلات "
                "(والمخزن/الاستخدام/الذاكرة المؤقتة) عند إعادة النشر التالية. "
                "اضبط SILK_DATA_DIR=/data (وحدة تخزين Railway) قبل الإقلاع، أو "
                "أزِل SILK_REQUIRE_PERSISTENT_DATA_DIR إن كان التخزين الفاني مقصوداً.")
        if not _pst["writable"]:
            raise RuntimeError(
                "SILK_REQUIRE_PERSISTENT_DATA_DIR مضبوط لكن مسار التخزين "
                f"'{_pst['path']}' غير قابل للكتابة — تعذّر إنشاء ملف مجسّ فيه. "
                "تأكّد أن وحدة التخزين مركّبة وصلاحياتها صحيحة قبل الإقلاع.")
        if not _pst["is_mount"] and not _allow_nonmount:
            raise RuntimeError(
                "SILK_REQUIRE_PERSISTENT_DATA_DIR مضبوط لكن مسار التخزين "
                f"'{_pst['path']}' ليس وحدة تخزين مركّبة (أقرب نقطة تركيب = "
                f"'{_pst['mountpoint']}' = جذر الحاوية الفاني) — بياناتك ستُمحى "
                "عند إعادة النشر رغم ضبط المتغيّر. تأكّد أن Mount Path لوحدة "
                "Railway يساوي SILK_DATA_DIR تمامًا، أو اضبط "
                "SILK_ALLOW_NONMOUNT_PERSIST=1 إن كان القرص الجذري للمضيف دائمًا.")

    # حاصد التشغيلات اليتيمة عند الإقلاع — إعادة النشر تقتل عمليةً منتصف
    # تشغيلة /research، فيبقى صفّها 'running' أبداً وحجزُ الدولار المسبق بلا
    # مصالحة يسدّ السقف اليومي. المكنَس يوسم العالق 'failed' ويصالح حجزه إلى
    # الفعلي-حتى-الآن. الإقلاع أهمّ نقطة تشغيل (يلتقط ما خلّفته العملية الميتة).
    try:
        import silk_storage
        _reaped = silk_storage.reap_orphan_research_runs()
        if _reaped:
            log.warning("startup orphan reaper marked %d stale runs failed: %s",
                        len(_reaped), _reaped)
    except Exception as _e:  # noqa: BLE001 — الحصاد لا يُسقِط الإقلاع أبداً
        log.warning("startup orphan reaper failed: %s", _e)

    # التحديث الدوري داخل العملية (SILK_REFRESH_HOURS) — قرص Railway يُركَّب
    # على خدمة واحدة، فالمُجدول خيط خلفي هنا لا خدمة cron منفصلة. معطّل بلا
    # المتغير — الاختبارات والتطوير لا تتأثر. In-process scheduled refresh.
    # (الحلقة الدورية تحصد اليتيمة أيضاً — راجع silk_collectors._loop).
    try:
        import silk_collectors
        silk_collectors.start_scheduler()
    except Exception as _e:  # noqa: BLE001
        log.warning("refresh scheduler not started: %s", _e)

    # CORS (الموجة ٠): الافتراضي صار **نفس الأصل فقط** (الواجهة تُقدَّم من نفس
    # الخدمة فلا تحتاج CORS). للواجهات المنفصلة (Netlify) اضبط CORS_ORIGINS
    # بقائمة أصول مفصولة بفواصل؛ "*" لم يعد افتراضياً ويتطلب ضبطاً صريحاً.
    allow = _cors_origins()
    if allow:
        app.add_middleware(CORSMiddleware, allow_origins=allow,
                           allow_methods=["*"], allow_headers=["*"])

    # ترويسات أمان على كل ردّ (L-2) — security headers on every response.
    # CSP خطّ أساس يسمح بأنماط/سكربتات الصفحة المضمّنة وخطوط Google (الواجهة
    # ملف واحد بأنماط وسكربت مضمّنين)؛ التشديد (nonces / خط ذاتي الاستضافة =
    # L-3) لاحقاً. nosniff يمنع تخمين نوع المحتوى؛ Referrer-Policy يحدّ التسريب.
    # الخطوط صارت مستضافة ذاتياً (task 12) — لا مضيف خطوط خارجي في السياسة.
    _CSP = ("default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; "
            "base-uri 'self'; frame-ancestors 'none'")

    @app.middleware("http")
    async def _security_headers(request, call_next):  # noqa: ANN001, ANN201
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers.setdefault("Content-Security-Policy", _CSP)
        return resp

    class ProductCard(BaseModel):
        """بطاقة المنتج (الموجة ٤، vision §2) — اختيارية، تفعّل محرّك التقاطع."""
        cost_per_unit: float
        unit: str | None = None
        tier: str | None = None            # premium|standard|economy
        monthly_capacity: float | None = None
        shipping_per_unit: float | None = None  # افتراض شحن معلَن قابل للتعديل
        certifications: list[str] | None = None  # مثال: HALAL, ISO22000, SFDA

    class AnalyzeRequest(BaseModel):
        """طلب تحليل منتج (المسار العادي) — analyze request body.

        الموجة ٢: حقول الطبقات المدفوعة أُزيلت نهائياً من هذا النموذج —
        إرسالها يُتجاهَل بنيوياً (pydantic يسقط الحقول الزائدة)، فالمسار
        العادي **يستحيل** أن يشغّل طبقة مدفوعة. التعميق عبر POST /deepen.
        """
        product: str
        year: int | None = None
        with_trends: bool = False
        with_tariffs: bool = False
        with_faostat: bool = False
        with_maps: bool = False
        with_websearch: bool = False
        with_competitors: bool = False
        with_channels: bool = False
        with_importers: bool = False
        with_requirements: bool = False
        with_trend: bool = False          # مدى السنوات — multi-year import trend
        trend_span: int = 5
        product_card: ProductCard | None = None
        hs_code: str | None = None
        markets: list[str] | None = None  # ISO3s لتضييق المرشّحين؛ فارغ = كل الأسواق
        # P3: توجيهات درج «إعدادات الوكلاء» — {agent_key: {on: bool, cmd: str}}.
        # تُطبَّع شكلياً في _clean_agent_prefs؛ الأمر يوجّه تركيز برومبتات
        # كلود حصراً داخل العزل — لا يغيّر رقماً ولا يصل وكيل بيانات.
        agent_prefs: dict | None = None
        persist: bool = False
        # hs_confirmed=true بعد مراجعة المستخدم يتجاوز بوّابة تأكيد HS
        # (الموجة ٢ — نفس عقد /research، silk_hs_confirm.preflight_block).
        hs_confirmed: bool = False
        # سماتُ بطاقة العبوة الرقمية من استخلاص الصورة (بلاغ المُشرِف):
        # [{name, value, unit}] — تحسم عتبةَ الترويسة (نسبة دهن/سعة/وزن) قبل
        # عرض أيّ حوارٍ يسأل التاجرَ عن رقمٍ يُجيب عنه المنتجُ نفسُه. غيابها
        # => المسارُ كما كان (ويبٌ ثم حوار).
        label_attributes: list[dict] | None = None

    class IntakeRequest(BaseModel):
        """طلب استقبال منتج متعدّد الوسائط — {name} أو {image_base64,kind}.

        محوّلٌ أماميّ (الميزة ب): لا يبدأ أيّ تحليل — يُعيد اسماً مؤكَّداً/قابلاً
        للتعديل يدخل بعده المسارَ القائم. لا حقول مدفوعة/تحليل هنا بنيوياً.
        """
        name: str | None = None
        image_base64: str | None = None
        kind: str = "product"                 # "product" | "ingredients_label"
        media_type: str = "image/jpeg"        # jpeg/png/webp

    class ClassifyRequest(BaseModel):
        """طلب تصنيف HS (Wave 1) — {product, ingredients?, category?}.

        خطوةٌ ما قبل التشغيل: تُعيد **اقتراح** HS6 (حتمي واثق، أو نداءُ كلود
        مقيسٌ مُرسًى على المرجع، أو منتقٍ يدوي) — لا تبدأ تحليلًا ولا تحجز شيئًا
        هنا؛ المستخدم يؤكّد الرمز ثم يدخل `/research`.
        """
        product: str | None = None
        ingredients: list[str] | None = None      # من استخلاص الصورة إن وُجد
        category: str | None = None

    class DeepenRequest(BaseModel):
        """طلب تعميق (المسار المدفوع) — the /deepen request body (wave 2).

        المسار الوحيد القادر على تفعيل الطبقات المدفوعة، ويعمل داخل
        silk_context.deepen_context() فيسمح حارس BaseAgent البنيوي بالتنفيذ.

        `hs_confirmed` (بلاغ «حليب نادك»): لم يكن هذا الحقل موجوداً هنا
        إطلاقاً — لأنّ `/deepen` لم تكن له بوّابةُ HS أصلاً، فلا مَخرجَ يحتاجه.
        أُضيف مع البوّابة كي يملك المستخدمُ نفسَ حقّ التأكيد الصريح المتاح على
        `/analyze` و`/research` (LAW: المالك آخِر مؤكِّد، لا طريقٌ مسدود).
        """
        product: str
        year: int | None = None
        # تأكيدُ المستخدم الصريح يتجاوز بوّابةَ HS — نفس عقد المسارين الآخرين.
        hs_confirmed: bool = False
        # قياساتُ بطاقة العبوة — نفس عقد `/analyze` و`/research` حرفياً
        # (اللائحة ٦٥): المسارُ المدفوع لا يُترَك خارج نقطة القياس.
        label_attributes: list[dict] | None = None
        with_trends: bool = False
        with_tariffs: bool = False
        with_faostat: bool = False
        with_maps: bool = False
        with_websearch: bool = False
        with_localprice: bool = False
        own_price: float | None = None
        with_volza: bool = False
        with_explee: bool = False
        with_ai: bool = False
        with_competitors: bool = False
        with_channels: bool = False
        with_importers: bool = False
        with_requirements: bool = False
        with_trend: bool = False          # مدى السنوات — multi-year import trend
        trend_span: int = 5
        product_card: ProductCard | None = None
        hs_code: str | None = None
        persist: bool = False

    def _json(payload: object):
        """رد JSON آمن للـ DataPoint — JSONResponse with DataPoint-safe payload."""
        return JSONResponse(content=_to_jsonable(payload))

    def _view(result: dict) -> dict:
        """القالب الموحّد (§10.1) — attach the canonical view (never crashes)."""
        try:
            from silk_render import build_view
            return build_view(result)
        except Exception as e:  # noqa: BLE001 — العرض لا يُسقط التحليل
            log.warning("view build failed: %s", e)
            return {"error": f"view error: {type(e).__name__}: {e}"}

    def _gmaps_health_status() -> str:
        """C1: حالة مكشطة الخرائط لـ/health — تعطيل نظيف إن غاب المتغيّر."""
        import silk_gmaps
        return silk_gmaps.health_status()

    @app.get("/health")
    def health():
        """فحص الصحّة — liveness plus optional-dep availability flags."""
        deps = {}
        for name in ("fastapi", "uvicorn", "pytrends", "streamlit", "requests"):
            try:
                __import__(name)
                deps[name] = True
            except Exception:  # noqa: BLE001
                deps[name] = False
        health = {"status": "ok", "deps": deps}
        # P5: شفافية المصادر — أي طبقة قوقل/كلود فعّالة الآن ولماذا لا.
        # وجود/غياب فقط، لا قيم مفاتيح ولا نداءات حية (التحقيق العميق في
        # /diagnostics المحروس).
        from silk_websearch_agent import search_key as _sk
        _claude_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
        if not _claude_key:
            _claude = "off — ANTHROPIC_API_KEY غير مضبوط"
        elif _unprotected_paid_keys():
            _claude = ("blocked — ANTHROPIC_API_KEY بلا SILK_API_KEY؛ "
                       "اضبط SILK_API_KEY لتفعيل حكم كلود وطبقاته")
        else:
            _claude = "on"
        health["sources"] = {
            "comtrade": ("key" if os.environ.get("COMTRADE_API_KEY", "").strip()
                         else "preview — بلا COMTRADE_API_KEY (حدّ معدل منخفض؛ "
                              "أضِف المفتاح المجاني)"),
            "world_bank": "on — بلا مفتاح",
            "google_trends": "on — pytrends بلا مفتاح (حصة قوقل محدودة)",
            "google_search_serper": ("on" if _sk() else
                                     "off — SEARCH_API_KEY/SERPER_API_KEY غائب"),
            "google_maps": ("on" if os.environ.get(
                "GOOGLE_MAPS_API_KEY", "").strip()
                else "off — GOOGLE_MAPS_API_KEY غائب"),
            # C1 (SPEC-v2): مكشطة الخرائط خدمة Railway ثانية — حالة إخبارية
            # فقط (تعطيل نظيف؛ لا تحجب research_ready ولا تتأثر بها المهام).
            "gmaps_scraper": _gmaps_health_status(),
            "claude": _claude,
        }
        # أيّ نموذج تناديه هذه النشرة فعلاً — طلب المالك: «SILK_AI_MODEL من
        # صدفة Railway». اسم النموذج ليس سرّاً (لا مفتاح ولا قيمة حسّاسة)، وهو
        # لازم للتشخيص عن بُعد: قرار إرسال/إهمال معاملات المعاينة مبنيّ عليه
        # حصراً، فبلا رؤيته يبقى «هل الإصلاح فعّال إنتاجياً؟» تخميناً. القيمة
        # المحلولة (الافتراضية إن كان المتغيّر غير مضبوط)، لا نصّ المتغيّر.
        try:
            import silk_ai_judge as _judge
            import silk_llm_provider as _llm
            health["ai_model"] = {
                "resolved": _judge._MODEL,
                "env_set": bool(os.environ.get("SILK_AI_MODEL", "").strip()),
                "sends_sampling_params":
                    _llm._supports_sampling_params(_judge._MODEL),
                "no_sampling_params_prefixes":
                    list(_llm._no_sampling_params_prefixes()),
            }
        except Exception as _exc:  # noqa: BLE001 — فحص صحّة لا ينهار أبداً
            health["ai_model"] = {"error": f"{type(_exc).__name__}: {_exc}"}
        # جهوزية البحث العميق (/research) — بلاغ حي: كلود شرط تشغيل هناك لا
        # تحسين اختياري؛ حقل صريح هنا كي يتحقق المشغّل قبل أي طلب حي، لا
        # بعد تسليم هيكل فارغ (نفس فحص _research_readiness دون حجز ميزانية).
        _rr_ready, _rr_reason = _research_readiness()
        health["research_ready"] = _rr_ready
        if not _rr_ready:
            health["research_ready_reason"] = _rr_reason
        # القرص الدائم: المسارات المحلولة فعلاً لكل مخزن — للتحقق بعد النشر أن
        # كل شيء يكتب للقرص (persistent=true عندما يقع المسار تحت SILK_DATA_DIR
        # أو وُجّه بمتغير صريح). Resolved storage paths for volume verification.
        _warnings: list[str] = []
        try:
            import silk_cache as _cache
            import silk_store as _fact_store
            import silk_usage as _usage
            _base = os.environ.get("SILK_DATA_DIR", "").strip()
            _pstatus = silk_storage.persistence_status()
            health["storage"] = {
                "data_dir": _base or None,
                "analyses_db": silk_storage._db_path(),
                "fact_store_db": _fact_store._db_path(),
                "usage_db": _usage._db_path(),
                "cache_dir": _cache._cache_dir(),
                # المخزن الخامس (منصّة المستأجرين/المصادقة/المحافظ) — بلا كشفه
                # لا يستطيع المالك التحقّق بعد النشر أن بيانات المستأجرين نزلت
                # على الوحدة المركَّبة لا على قرص الحاوية الفاني.
                # Fifth store: tenant/auth/wallet DB must be verifiable remotely.
                "platform_db": _platform_db_path(),
                # جهوزيّة المنصّة: جداولٌ سليمة و**صفر مستخدمين** كانت تجعل كل
                # دخول يُرفَض برسالة «بيانات غير صحيحة» لا تُميَّز عن كلمة مرور
                # خاطئة، فيستحيل التشخيص عن بُعد (بلاغ مالك حيّ: «ما يعمل»).
                # أعدادٌ فقط — بلا بريد ولا كلمة مرور؛ `/health` عامّة.
                # Tables-but-no-users was undiagnosable remotely: counts only.
                "platform_ready": _platform_readiness(),
                # PART E (أمر العمل الرئيس): حالة مصيدة الإقلاع مرئية من
                # /health — كانت غير قابلة للتفتيش عن بُعد فلا يعرف المالك
                # إن كان صمّام «رفض الإقلاع على قرص فانٍ» مفعّلاً فعلاً.
                "persist_guard": os.environ.get(
                    "SILK_REQUIRE_PERSISTENT_DATA_DIR", "").strip().lower()
                    in ("1", "true", "yes", "on"),
                # بلاغ المالك الحيّ: «المتغيّر مضبوط» لا يكفي — نكشف الحالة
                # الفعلية (قرص مركّب + قابل للكتابة) كي يرى المالك عن بُعد إن
                # كانت وحدة التخزين مركّبة حقًّا على مسار SILK_DATA_DIR.
                "is_mount": _pstatus["is_mount"],
                "writable": _pstatus["writable"],
                "mountpoint": _pstatus["mountpoint"],
            }
            # بلاغ حي (تدقيق تكلفة): تحليلات مكتملة مدفوعة الثمن كانت تختفي
            # بعد كل إعادة نشر — SILK_DATA_DIR فارغ يعني كل الأربعة مخازن
            # تقع تحت المسار النسبي الافتراضي داخل حاوية Railway الفانية (لا
            # وحدة تخزين ثابتة)، فتُمحى كل البيانات عند كل نشرة تالية. كان
            # هذا خطراً صامتاً (data_dir: null بلا أي تحذير مرئي) — الآن
            # تحذير صريح لا يفوّت مشغّلاً يفحص /health.
            if not (_base or os.environ.get("SILK_DB", "").strip()):
                _warnings.append(
                    "SILK_DATA_DIR غير مضبوط — التخزين على مسار نسبي داخل "
                    "حاوية Railway الفانية؛ كل التحليلات (والمخزن/الاستخدام/"
                    "الذاكرة المؤقتة) ستُفقَد عند إعادة النشر التالية ما لم "
                    "تُركَّب وحدة تخزين (Volume) وتُوجَّه إليها هذا المتغيّر")
            # بلاغ المالك الحيّ: المتغيّر مضبوط لكن لا وحدة مركّبة على مساره —
            # الكتابة على جذر الحاوية الفاني فتُمحى عند كل دبلوي رغم ضبط
            # المتغيّر. تحذير صريح حتى دون تفعيل مصيدة الإقلاع.
            elif _pstatus["configured"] and not _pstatus["is_mount"]:
                _warnings.append(
                    f"مسار التخزين '{_pstatus['path']}' مضبوط لكن ليس وحدة "
                    f"تخزين مركّبة (أقرب نقطة تركيب '{_pstatus['mountpoint']}' = "
                    "جذر الحاوية الفاني) — كل البيانات ستُمحى عند إعادة النشر. "
                    "اجعل Mount Path لوحدة Railway يساوي هذا المسار تمامًا")
        except Exception as _e:  # noqa: BLE001 — تشخيص لا شرط
            log.debug("storage health section skipped: %s", _e)
        # اللائحة ٤٣ (بلاغ حي متكرّر — رمز HS خاطئ رغم إصلاح المُصنِّف العام):
        # صمّام `SILK_HS_CLASSIFIER` نفسه لم يكن قابلاً للتفتيش عن بُعد، فلا
        # يعرف المالك أن الإصلاح المدموج فعلياً لا يعمل على النشر الفعلي —
        # نفس عائلة `persist_guard` أعلاه (سطح مراقبة، لا تخمين). فشل-آمنٌ
        # افتراضياً الآن، لكن يبقى قابلاً للتعطيل الصريح؛ هذا الحقل يُظهر
        # الحالة الفعلية الحيّة بدل انتظار بلاغٍ حيٍّ آخر لاكتشافها.
        try:
            import silk_hs_classifier as _hsc
            _hs_enabled = _hsc.enabled()
            health["hs_classifier"] = {"enabled": _hs_enabled}
            if not _hs_enabled and _claude_key:
                _warnings.append(
                    "SILK_HS_CLASSIFIER مُعطَّل صراحةً — المُصنِّف العام "
                    "(تصنيف HS الدقيق لمنتجات متعدّدة الصفات) لن يستدعي "
                    "كلود؛ يعتمد على جدول بحثٍ جزئي وحده وقد يُخطئ الفصل "
                    "(نفس عائلة بلاغ «زبدة الفول السوداني»)")
        except Exception as _e:  # noqa: BLE001 — تشخيص لا شرط
            log.debug("hs_classifier health section skipped: %s", _e)
        unprotected = _unprotected_paid_keys()
        if unprotected:
            _warnings.append(
                "paid keys present without SILK_API_KEY ("
                + ", ".join(unprotected)
                + ") — paid layers will refuse with 503 until SILK_API_KEY "
                  "is set (or the paid keys are removed)")
        if _warnings:
            health["warnings"] = _warnings
        return health

    @app.get("/resolve/{name}")
    def resolve(name: str, request: Request):
        """صنّف اسم منتج إلى HS6 — resolve a product name to an HS6 DataPoint."""
        _rate_limit(request)   # قراءة رخيصة لكنها ليست مجانية بلا حدود
        dp = silk_hs_resolver.resolve(name)
        return _json({"hs_code": dp.value, "confidence": dp.confidence,
                      "note": dp.note, "source": dp.source,
                      "retrieved_at": dp.retrieved_at})

    @app.get("/config")
    def config(request: Request):
        """أعلامُ الميزات العلنية للواجهة — public feature flags (لا أسرار).

        الواجهة تقرؤها مرّة عند الإقلاع لتفعيل تبويبات الصورة (الميزة ب) —
        القيَم أعلامٌ بيئية علنية لا مفاتيح، فآمنٌ كشفها بلا مصادقة."""
        _rate_limit(request)
        import silk_product_intake as intake
        import silk_hs_classifier as hsc
        from silk_market_ranker import _world_markets_enabled
        # أعلام Wave 1 العلنية — الواجهة تُفعّل خطوة التصنيف/الاستشارة بحسبها.
        return _json({"image_intake": intake.enabled(),
                      "world_markets": _world_markets_enabled(),
                      "hs_classifier": hsc.enabled(),
                      "producer_advisory": _producer_advisory_enabled(),
                      "require_hs6": _require_hs6(),
                      "prerun_advisories": __import__(
                          "silk_prerun").advisories_enabled()})

    def _intake_vision_allowed() -> tuple[bool, str]:
        """هل يُسمح نداء الرؤية الواحد؟ — (allowed, reason). يعكس منطق
        `_free_ai_extras_allowed`: نداء الرؤية مقيسٌ كأيّ نداء مدفوع (حجز
        تفعيلة واحدة ذرّياً من SILK_PAID_DAILY_CAP). الرفض يتدهور بصدق إلى
        «تعذّرت القراءة» — لا اختلاق منتج، ولا 429 على مسار مجاني أصلاً.

        بلا مفتاح كلود => لا رؤية ممكنة (تعذّر قراءة صادق، لا اختلاق). مفتاحٌ
        مدفوع بلا SILK_API_KEY => محجوب (حارس 503) بلا إنفاق. سقفٌ مستنفد =>
        محجوب. غير ذلك => تُحجَز تفعيلة واحدة قبل النداء."""
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return False, ("طبقة الرؤية تتطلّب ANTHROPIC_API_KEY — "
                           "تعذّرت قراءة الصورة، اكتب الاسم يدوياً.")
        if _unprotected_paid_keys():
            return False, ("ANTHROPIC_API_KEY مضبوط بلا SILK_API_KEY — "
                           "طبقة الرؤية محجوبة (حارس 503) حتى تُضبط المصادقة.")
        if not silk_usage.try_reserve_paid_calls(1):
            return False, ("سقف الاستهلاك اليومي (SILK_PAID_DAILY_CAP) "
                           "مستنفد — تعذّرت قراءة الصورة لهذا الطلب.")
        return True, ""

    @app.post("/products/intake")
    def products_intake(req: IntakeRequest, request: Request):
        """استقبال منتج متعدّد الوسائط — اسمٌ مكتوب أو صورة منتج/بطاقة مكوّنات.

        محوّلٌ أماميّ (الميزة ب): يُعيد اسماً مؤكَّداً/قابلاً للتعديل **بلا بدء
        تحليل**. مسار الصورة = نداء رؤية واحد مقيس؛ ثقةٌ منخفضة/غير مقروءة =>
        «تعذّرت القراءة — اكتب الاسم يدوياً» (لا اختلاق). الاسم المؤكَّد يدخل
        بعده `/resolve → /analyze|/research` القائم بلا تغيير.
        """
        _require_key(request)
        _rate_limit(request)
        import silk_product_intake as intake
        if not intake.enabled():
            raise HTTPException(status_code=404, detail={
                "error": "image_intake_disabled",
                "reason": "استقبال الصور مُعطَّل — اضبط SILK_IMAGE_INTAKE=1."})
        # مسار الاسم المكتوب: لا نداء كلود، لا حجز.
        if req.name and not req.image_base64:
            return _json(intake.intake_name(req.name))
        if not req.image_base64:
            raise HTTPException(status_code=422, detail={
                "error": "name_or_image_required",
                "reason": "مرّر name أو image_base64."})
        # مسار الصورة: تحقّق الحجم/النوع أولاً (لا حجز على إدخالٍ باطل)، ثم
        # احجز تفعيلة الرؤية الواحدة. الحجز يقع فقط حين نعتزم النداء فعلاً.
        raw, why = intake._decode_and_check(req.image_base64, req.media_type)
        if raw is None:
            return _json(intake.intake_image(req.image_base64, req.media_type,
                                             req.kind))
        allow, reason = _intake_vision_allowed()
        # البند #6 (تدقيق v2 الموجة ٢): نداء الرؤية كان مقيساً بالعدّاد فقط
        # (SILK_PAID_DAILY_CAP) لا بالدولار — يجري خارج أيّ `begin_data_counter`
        # فتُهمَل رموزه (`_record_usage` صامت)، فلا يظهر إنفاقه في السقف الدولاري
        # ولا في `?economics`. نفتح عدّاداً حول النداء، ثم نسجّل الكلفة الفعلية
        # في دفتر اليوم الدولاري (record_usd) — فيُحتسَب ضمن الحدّ اليومي المشترك
        # الذي يقرؤه /research، ويُصبح مرئياً. لا حجز مسبق (النداء الواحد الصغير
        # محكومٌ أصلاً بالعدّاد؛ التسجيل البعدي يجعله مرئياً/محسوباً بلا اختلاق).
        import silk_context
        if allow:
            silk_context.begin_data_counter()
        out = intake.intake_image(
            req.image_base64, req.media_type, req.kind,
            allow_vision=allow, blocked_reason=reason)
        if allow:
            try:
                from silk_pricing import estimate_cost_usd
                _c = silk_context.data_counter() or {}
                _cost = estimate_cost_usd(_c.get("llm_usage"))
                if _cost.get("total_usd"):
                    silk_usage.record_usd(_cost["total_usd"])
            except Exception as _e:  # noqa: BLE001 — القياس قناة جانبية لا تُسقط الردّ
                log.warning("intake vision cost metering failed: %s", _e)
        return _json(out)

    def _classify_general_allow_claude() -> bool:
        """هل نداءُ التصنيف العام (`silk_hs_classifier.classify_general`)
        مسموحٌ نظرياً؟ — فحصٌ رخيصٌ **بلا حجز**: يُستدعى على **كل** طلبٍ ذي
        رمزٍ مُعلَّم عند `preflight_block`، فحجزٌ هنا يُهدر تفعيلةً حتى حين
        يكفي المُحلِّل الحتمي (بذرة CSV) أو ذاكرة المنتج بلا أيّ نداء فعلي.
        الحجزُ الذرّي الحقيقي (count + dollar) يعيش **داخل**
        `silk_hs_classifier._reserve_llm_call` — نقطة اختناقٍ واحدة يستدعيها
        `classify_general` سواءً من `/classify_hs` أو من `preflight_block`
        (كلا مساري `/analyze`/`/research`)، فيُستدعى فقط حين يثبت فعلاً أن
        نداءً حياً لا مفرّ منه — لا ازدواج حجزٍ بين نقطتَي نهاية."""
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()) \
            and not _unprotected_paid_keys()

    @app.post("/classify_hs")
    def classify_hs(req: ClassifyRequest, request: Request):
        """صنّف منتجًا إلى HS6 — the general-purpose HS classifier step
        (الموجة ٣، systemic fix). لا يبدأ أيّ تحليل ولا يحجز ميزانيةَ بحث —
        الاقتراح فقط، لكنه **نفس عقد `classify_general`** الذي تستدعيه بوّابة
        `preflight_block` وقت الإرسال — نقطة اختناقٍ منطقية واحدة، لا نسخة
        مسبَقة أضيق تعطي نتيجةً مختلفة عمّا يراه المستخدم لاحقاً عند الحجب.

        الحجزُ الذرّي (count + dollar) يعيش الآن **داخل** `classify_general`
        نفسها (`_reserve_llm_call`) — لا حجزٌ استكشافيٌّ هنا؛ يُستدعى فقط حين
        يثبت فعلاً أن نداءً حياً لا مفرّ منه (لا إصابة ذاكرة ولا مُحلِّل حتمي
        كافٍ)."""
        _require_key(request)
        _rate_limit(request)
        import silk_hs_classifier as hsc
        product = (req.product or "").strip()
        if not product:
            raise HTTPException(status_code=422, detail={
                "error": "product_required", "reason": "مرّر اسم منتج."})
        out = hsc.classify_general(
            product, ingredients=req.ingredients, category=req.category,
            allow_claude=_classify_general_allow_claude())
        return _json(out)

    @app.get("/index")
    def index(request: Request, q: str = "", limit: int = 20):
        """فهرس المنتجات للبحث — product search index for the dashboard combobox.

        limit مُقيَّد إلى [1..100] (M0): قيمة ضخمة/سالبة لا تُمرَّر للبحث كما هي.
        """
        _rate_limit(request)
        return _json(_index_search(q, max(1, min(int(limit), 100))))

    @app.get("/markets")
    def markets_reference(request: Request):
        """مرجع الأسواق المرشَّحة — the candidate-market list for the target
        picker: {iso3, m49, name}. Same reference `rank_markets()` scores
        against — يُبنى مرة واحدة، لا نداء شبكة، ثابت لكل التشغيلات.
        """
        _rate_limit(request)
        from silk_market_ranker import (
            COUNTRIES, TIER2_LABEL, _world_markets_enabled)
        from silk_data_layer import partner_name
        from silk_narrative import COUNTRY_AR
        # P3 (بلاغ المالك): الاسم العربي إلى جانب الإنجليزي — الواجهة تعرض
        # العربية في وضعها العربي بدل أسماء إنجليزية خام.
        out = [{"iso3": c["iso3"], "m49": c["m49"],
                "name": partner_name(c["m49"]),
                "name_ar": COUNTRY_AR.get(c["iso3"], partner_name(c["m49"])),
                "tier": 1}
               for c in COUNTRIES]
        # تغطية العالم (SILK_WORLD_MARKETS): أضِف بقية دول العالم كفئة-٢ موسومة
        # «تغطية أساسية» ليجمعها منسدل الواجهة تحت «كل دول العالم». مُطفأ افتراضياً
        # => الرد نفسه حرفياً كاليوم (تغطية-١ فقط، بلا حقل tier مؤثِّر على العرض).
        if _world_markets_enabled():
            from silk_market_resolver import _load as _load_countries
            seen = {c["iso3"] for c in COUNTRIES}
            for row in _load_countries():
                iso3 = (row.get("iso3") or "").strip()
                if len(iso3) != 3 or iso3 in seen:
                    continue
                seen.add(iso3)
                out.append({
                    "iso3": iso3, "m49": row.get("m49", ""),
                    "name": row.get("name_en") or iso3,
                    "name_ar": row.get("name_ar") or row.get("name_en") or iso3,
                    "tier": 2, "coverage": TIER2_LABEL})
        return _json(out)

    def _require_key(request: Request) -> None:
        """حارس المصادقة — 401 when the key mismatches, constant-time (L-1).

        يُطبَّق على مسارات القراءة الحسّاسة أيضاً (C-1): التحليلات المحفوظة
        تحمل بطاقة المنتج الاقتصادية، ومعرّفاتها متسلسلة — بلا هذا الحارس
        يقرؤها أي مجهول بالتعداد. المقارنة عبر hmac.compare_digest لتفادي
        تسريب التوقيت. غير مضبوط SILK_API_KEY => وضع تطوير مفتوح (لا انحدار).
        """
        expected = _api_key_expected()
        if not expected:
            return
        got = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(got, expected):   # constant-time (L-1)
            raise HTTPException(status_code=401,
                                detail="missing or invalid API key "
                                       "(send X-API-Key header)")

    # تحديد معدّل بسيط بالذاكرة (M-1) — in-memory fixed-window rate limit.
    # نافذة ثابتة لكل هوية (X-API-Key إن وُجد وإلا IP)؛ التجاوز = 429.
    # يكفي أداةً داخلية (لا Redis)؛ الحالة لكل نسخة تطبيق (تُعاد تهيئتها في
    # الاختبارات). SILK_RATE_LIMIT=0 يعطّله؛ الافتراضي 120 طلباً/60 ثانية.
    _rl_max = int(os.environ.get("SILK_RATE_LIMIT", "120") or "120")
    _rl_window = max(1, int(os.environ.get("SILK_RATE_WINDOW", "60") or "60"))
    _rl_lock = threading.Lock()
    _rl_hits: dict[str, tuple[int, int]] = {}

    def _rate_limit(request: Request) -> None:
        """حدّ المعدّل — raise 429 when a client exceeds the window budget."""
        if _rl_max <= 0:
            return
        ident = (request.headers.get("x-api-key")
                 or (request.client.host if request.client else "anon"))
        win = int(time.time()) // _rl_window
        with _rl_lock:
            w, c = _rl_hits.get(ident, (win, 0))
            if w != win:
                w, c = win, 0
            c += 1
            _rl_hits[ident] = (w, c)
            if len(_rl_hits) > 4096:
                # سدّ النمو بلا تصفير شامل (مراجعة المشروع): .clear() كان
                # يمحو نوافذ كل العملاء، فيستطيع مهاجم إرسال 4096 هوية
                # زائفة ليصفّر عدّاده هو. الآن: تُقلَّم النوافذ المنتهية
                # فقط؛ وإن بقي الفيض (هجوم هويات في نافذة واحدة) تُطرد
                # هويات أخرى — عدّاد الهوية الحالية لا يُمسّ أبداً.
                stale = [k for k, (w0, _c0) in _rl_hits.items() if w0 != win]
                for k in stale:
                    del _rl_hits[k]
                if len(_rl_hits) > 4096:
                    for k in list(_rl_hits):
                        if k != ident:
                            del _rl_hits[k]
                        if len(_rl_hits) <= 4096:
                            break
        if c > _rl_max:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded ({_rl_max}/{_rl_window}s) — "
                       "slow down or raise SILK_RATE_LIMIT")

    def _guard_paid(req) -> None:
        """حارسا المدفوع — 503 لمفاتيح غير محمية، ثم 429 للسقف، ثم التسجيل."""
        paid_requested = sum(1 for f in _PAID_FLAGS if getattr(req, f, False))
        if paid_requested:
            unprotected = _unprotected_paid_keys()
            if unprotected:
                raise HTTPException(
                    status_code=503,
                    detail="paid provider keys are set ("
                           + ", ".join(unprotected)
                           + ") but SILK_API_KEY is not — refusing to run "
                             "paid layers unauthenticated. Set SILK_API_KEY "
                             "(and send X-API-Key) or unset the paid keys.")
        # فحص السقف والتسجيل في معاملة ذرّية واحدة — لا نافذة سباق بين
        # القراءة والكتابة (طلبان متزامنان لا يتجاوزان السقف معًا).
        # Atomic check-and-reserve: no TOCTOU window between read and write.
        if paid_requested and not silk_usage.try_reserve_paid_calls(
                paid_requested):
            # ITEM 5ب: رفض حجز بحالة السقف — نص خادمي بحت، لا محتوى كلود.
            import silk_ops_log
            silk_ops_log.record_error(
                "reservation_refused",
                "بلغ سقف التفعيلات المدفوعة اليومي (SILK_PAID_DAILY_CAP)",
                context={"requested": paid_requested,
                        "today_activations": silk_usage.paid_calls_today()})
            raise HTTPException(
                status_code=429,
                detail="daily paid-layer cap reached (SILK_PAID_DAILY_CAP) — "
                       "retry tomorrow or raise the cap")

    def _source_policy() -> dict:
        """سياسة المصادر الخادمية (Stage 2A) — server decides, never UI flags.

        القاعدة الصلبة: كل مصدر مجاني بلا مفتاح يُحاول دائماً؛ والمفتاحيّ المجاني
        يُحاول متى وُجد مفتاحه في بيئة الخادم. أعلام العميل لا تُعطّل مصدراً —
        كانت البوابة المشتقة من لوحة مفاتيح المتصفح سببَ إظلام 8/12 مصدراً
        (docs/SOURCE_AUDIT.md). المدفوع يبقى بنيوياً في /deepen فقط.

        قرار المالك (مراجعة التشغيل الحي، 2026-07-06): with_competitors/
        with_channels/with_importers (الموجة ٣) عُطِّلت هنا نهائياً — صارت
        زائدة عن حاجتها بعد `with_research` (المرحلة ٣، §4b): CompetitorAgent
        وSupplierAgent يبحثان نفس السؤال (منافسون/موزّعون بالاسم) عبر
        Serper/Maps، فتضاعف الاستهلاك بلا فائدة وتكرّر نفس المحتوى في قسمين
        مختلفين من التقرير الواحد (وquotas Serper/Trends محدودة — لاحظنا
        429 من Google Trends في التشغيل الحي). الوكيلان القائمان (silk_
        competitors_agent.py، silk_channels_agent.py، silk_importers_agent.py)
        يبقيان دون حذف — silk_engine.analyze لا يزال يقبل هذه الأعلام
        مباشرة (اختبارات test_wave3_agents.py)، فقط سياسة الخادم توقفت عن
        تفعيلها تلقائياً.
        """
        return {
            "with_trends": True, "with_tariffs": True, "with_faostat": True,
            "with_requirements": True, "with_trend": True,
            "with_competitors": False, "with_channels": False,
            "with_importers": False, "with_risk": True, "with_research": True,
            "with_dynamics": True,
            "with_websearch": bool(__import__("silk_websearch_agent")
                                   .search_key()),
            "with_maps": bool(os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()),
        }

    def _free_ai_extras_allowed() -> tuple[bool, str]:
        """هل تُسمح إضافات كلود على المسار المجاني؟ — (allowed, reason).

        مراجعة المشروع (H2): استخلاص الثقافة وفلترة الكيانات نداءاتُ كلود
        تجري داخل /analyze خارج وكلاء PAID الثلاثة، فكانت (١) تُصرف لمجهولين
        حين يوجد ANTHROPIC_API_KEY بلا SILK_API_KEY — خرقاً لقاعدة حارس 503،
        و(٢) لا تُحتسب على SILK_PAID_DAILY_CAP. الآن: النشر غير المحمي يحجبها،
        والمحمي يحجز تفعيلة واحدة ذرّياً من نفس عدّاد السقف قبل السماح.
        الرفض يتدهور (تحليل بلا إضافات كلود + ملاحظة معلنة) — لا 429 لمسارٍ
        مجاني في أصله.
        """
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return True, ""            # لا مفتاح => لا إنفاق ممكن أصلاً
        if _unprotected_paid_keys():
            return False, ("ANTHROPIC_API_KEY مضبوط بلا SILK_API_KEY — "
                           "إضافات كلود (ثقافة المستهلك، فلترة الكيانات) "
                           "حُجبت على المسار المجاني حتى تُضبط المصادقة.")
        if not silk_usage.try_reserve_paid_calls(1):
            return False, ("سقف الاستهلاك اليومي (SILK_PAID_DAILY_CAP) "
                           "مستنفد — إضافات كلود حُجبت لهذا الطلب؛ "
                           "التحليل المجاني اكتمل بدونها.")
        return True, ""

    def _research_readiness() -> tuple[bool, str]:
        """جهوزية البحث العميق — (ready, reason). فحص قراءة بلا حجز (M-2 نمط
        would_exceed_cap، لا try_reserve_paid_calls) — الحجز الذرّي الفعلي
        يبقى في _free_ai_extras_allowed أثناء التنفيذ؛ هذا فحص مسبق رخيص.

        بلاغ حي (أول تشغيلة إنتاجية): /research بلا كلود ينتج هيكلاً فارغاً
        لا تقريراً — الاثنتا عشرة بعثة + المحلل + التوليف مرحلة ٢ + الكاتب/
        المراجع كلها نداءات كلود، خلافاً لـ/analyze حيث كلود تحسين اختياري.
        الفرق: هنا كلود **شرط تشغيل**، فيُرفض غيابه صراحة (409) قبل تشغيل
        أي بعثة — لا يُسلَّم هيكل فارغ كأنه المنتج. `allow_degraded=true`
        فتحة هروب صريحة تطلبها الجهة المستهلكة، لا تدهوراً افتراضياً.
        """
        import silk_ai_judge
        if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
            return False, ("ANTHROPIC_API_KEY غير مضبوط — البحث العميق "
                           "يتطلب كلود لكل الاثنتي عشرة بعثة والمحلل "
                           "والكاتب؛ بلا مفتاح لا تقرير حقيقي ممكن.")
        unprotected = _unprotected_paid_keys()
        if unprotected:
            return False, ("ANTHROPIC_API_KEY مضبوط بلا SILK_API_KEY — "
                           "طبقة كلود محجوبة (حارس 503) حتى تُضبط "
                           "SILK_API_KEY في بيئة النشر.")
        if not silk_ai_judge.available():
            return False, ("طبقة كلود محجوبة سياقياً (block_ai_extras) في "
                           "هذا الطلب — راجع سياق التشغيل الحالي.")
        if silk_usage.would_exceed_cap(1):
            return False, ("سقف الاستهلاك اليومي (SILK_PAID_DAILY_CAP) "
                           "مستنفد — أعد المحاولة غداً أو ارفع السقف.")
        return True, ""

    def _research_ambiguity_gate_enabled() -> bool:
        """بوّابةُ الالتباس على `/research` (`SILK_RESEARCH_AMBIGUITY_GATE`).

        مفعّلةٌ افتراضياً (فشل-آمن، كبقية بوّابات HS): سؤالٌ واحدٌ قبل تشغيلةٍ
        تكلّف دولاراتٍ أرخص من تقريرٍ كاملٍ على رمزٍ خاطئ. تُطفأ صراحةً."""
        raw = os.environ.get("SILK_RESEARCH_AMBIGUITY_GATE", "").strip().lower()
        return raw not in ("0", "false", "no", "off")

    def _require_hs6() -> bool:
        """صمّام البوّابة الصلبة (Wave 1) — SILK_REQUIRE_HS6=1 يرفض بدءَ /research
        برمز HS فارغ (422). افتراضيًا مُطفأ => السلوك كاليوم (تُقبَل فجوة معلنة)."""
        return os.environ.get("SILK_REQUIRE_HS6", "0").strip() == "1"

    def _producer_advisory_enabled() -> bool:
        """صمّام استشارة بلد المنشأ (Wave 1) — SILK_PRODUCER_ADVISORY=1 يفعّل
        تحذيرَ «سوق منتِجة» (422 حتى موافقة). افتراضيًا مُطفأ => السلوك كاليوم."""
        return os.environ.get("SILK_PRODUCER_ADVISORY", "0").strip() == "1"

    def _market_in_coverage(hs_code, iso3: str) -> tuple[bool, bool]:
        """هل السوق ضمن التغطية لهذا الرمز؟ — (covered, determinable).

        اتفاق المالك: التغطية = Tier-1 المنسّقة **أو** الظهور ضمن مجموعة أكبر
        مستوردي هذا الرمز (Tier-1+Tier-2 الديناميكية من نداء العالم الواحد).
        سوقٌ خارجها => لا دراسة هزيلة بل رسالة صادقة. تعذّر تحديد المجموعة (بلا
        رمز/ميزانية كومتريد منفدة/شبكة) => (True, False): نفتح البوّابة (يعمل
        كاليوم، فجوات معلنة) بدل حجب سوقٍ مشروع على عطلٍ عابر — فشلٌ آمن.
        """
        from silk_market_ranker import (COUNTRIES, world_import_totals_resolved,
                                        _TIER1_N, _TIER2_MAX)
        if iso3 in {c["iso3"] for c in COUNTRIES}:
            return True, True               # Tier-1 منسّقة — مغطّاة دائماً
        if not hs_code:
            return True, False              # لا رمز => لا يمكن حساب المجموعة
        # تدقيق v2 (الموجة ١): استطلاع بسُلَّم fallback (سنة-١ → سنة-٢ → سنة-٣ →
        # سنة الدراسة الافتراضية) بدل سنة اليوم-١ وحدها — كومتريد يتأخّر فكانت
        # ٢٠٢٥ تعود فارغةً دوماً فتفشل البوّابة مفتوحةً (لا تُغلَق أبداً). الآن تشترك
        # البوّابة والدراسة في **أساس مستوردين واحد** (نفس السُّلَّم، نفس السنة).
        try:
            totals, _yr = world_import_totals_resolved(hs_code)
        except Exception as e:  # noqa: BLE001 — عطل قياس لا يحجب سوقاً
            log.warning("coverage probe failed: %s", e)
            totals = []
        if not totals:
            return True, False              # تعذّر التحديد => فتح البوّابة (آمن)
        covered = {t["iso3"] for t in totals[:_TIER1_N + _TIER2_MAX]}
        return iso3 in covered, True

    @app.post("/analyze")
    def analyze(req: AnalyzeRequest, request: Request):
        """حلّل منتجًا عبر الأسواق (المسار العادي، مجاني حصراً) — free-only path.

        الموجة ٢: لا حقول مدفوعة في النموذج أصلاً — الحصر بنيوي. التعميق عبر
        POST /deepen. Stage 2A: طبقات المصادر تُقرَّر بسياسة الخادم حصراً
        (_source_policy) — علم العميل لا يستطيع إطفاء مصدر مجاني.
        """
        _require_key(request)
        _rate_limit(request)
        # بوّابة تأكيد رمز HS (الموجة ٢، تدقيق المُشرِف 2026-07-21 — نفس
        # نقطة الاختناق المشتركة `silk_hs_confirm.preflight_block` التي
        # يستدعيها `/research`؛ إصلاحٌ سابقٌ اقتصر على `/research` وحده
        # فعاود الظهور — «إصلاحٌ على مسارٍ واحد نصفُ إصلاح» — LESSONS ٣٦).
        # رمزٌ صريح يُفحَص كما هو؛ رمزٌ غائب يُحسَم حتميّاً (بلا نداء كلود،
        # نفس مُحلِّل `/research`) قبل أيّ عملٍ فعليّ. تُتخطّى بتأكيد المستخدم
        # الصريح (hs_confirmed=True) — لا يُنفَق زمن/ميزانية كومتريد على
        # فئة مجاورة خاطئة دلالياً.
        _analyze_hs_code = req.hs_code
        if not _analyze_hs_code and req.product:
            from silk_hs_resolver import resolve as _resolve_hs_preflight
            _analyze_hs_code = _resolve_hs_preflight(req.product).value
        # بلاغ المُشرِف («حليب نادك كامل الدسم؟»): البنودُ داخل الترويسة
        # الواحدة تتمايز بعتبةٍ رقمية — تُقاس قبل أن يُسأل المستخدم عنها
        # (`silk_hs_confirm.preflight_resolve`: بطاقةُ العبوة ثم استعلامُ
        # ويبٍ واحد). حُسِمت => يُستعمَل الرمزُ المقيس ويُوسَم بمصدره؛ لم
        # تُحسَم => نفسُ الـ٤٢٢ السابق مضافاً إليه `attribute_probe`.
        from silk_hs_confirm import preflight_resolve
        _analyze_hs_code, _hs_prov, _blocked = preflight_resolve(
            req.product, _analyze_hs_code, req.hs_confirmed,
            allow_claude=_classify_general_allow_claude(),
            label_attributes=req.label_attributes)
        if _blocked is not None:
            import silk_ops_log
            silk_ops_log.record_error(
                "hs_confirmation_blocked",
                f"رُفض بدءُ تحليلٍ برمز HS غير مؤكَّد لمنتج {req.product!r}: "
                f"{_blocked['hs_confirmation'].get('reason')}",
                context={"product": req.product, "hs_code": _analyze_hs_code,
                         "attribute_probe": _blocked.get("attribute_probe"),
                         "missing_terms":
                             _blocked["hs_confirmation"].get("missing_terms")})
            raise HTTPException(status_code=422, detail=_blocked)
        if _hs_prov is not None:
            import silk_ops_log
            silk_ops_log.record_error(
                "hs_auto_resolved",
                f"حُسِم رمزُ HS آلياً لمنتج {req.product!r} => "
                f"{_analyze_hs_code} ({_hs_prov.get('resolved_from')})",
                context={"product": req.product, "hs_code": _analyze_hs_code,
                         "resolved_from": _hs_prov.get("resolved_from"),
                         "value": _hs_prov.get("value"),
                         "source_url": _hs_prov.get("source_url")})
        policy = _source_policy()
        ai_ok, ai_note = _free_ai_extras_allowed()
        # P5 (بلاغ المالك: «كلود العادي يتفوق على المنصة»): حكم كلود
        # (المرحلة ٢ من التوليف) وتقريره يعملان على المسار الرئيسي متى كان
        # المفتاح مضبوطاً ومحمياً — نفس بوابة H2 ونفس الحجز من السقف؛ لا
        # مسار حكم موازٍ (التوليف يبقى نقطة الدخول الوحيدة، §9.3).
        policy["with_ai"] = ai_ok and bool(
            os.environ.get("ANTHROPIC_API_KEY", "").strip())
        import contextlib
        import silk_context
        ctx = (contextlib.nullcontext() if ai_ok
               else silk_context.block_ai_extras())
        # لوحة إعدادات الوكلاء: طلبٌ بلا agent_prefs يرث الإعدادات المحفوظة
        # خادمياً — فتسري على الإدخال والدردشة معاً حتى من عميل لا يرسلها.
        prefs = _clean_agent_prefs(req.agent_prefs)
        if prefs is None:
            prefs = _saved_agent_settings()
        with ctx, silk_context.agent_prefs_context(prefs):
            result = silk_engine.analyze(
                req.product, year=req.year,
                countries=_target_countries(req.markets),
                trend_span=req.trend_span,
                product_card=(req.product_card.model_dump()
                              if req.product_card else None),
                # رمزٌ حُسِم آلياً بالسمة الرقمية يتقدّم على مُحلِّل المحرّك
                # (وإلا لَعاد المحرّكُ إلى نفس الرمز الملتبِس)؛ بلا حسمٍ آليّ
                # يبقى السلوكُ حرفياً كما كان.
                hs_code=(_analyze_hs_code if _hs_prov is not None
                         else req.hs_code),
                persist=req.persist, **policy)
        if not ai_ok:
            result["ai_extras_note"] = ai_note   # الغياب مُعلَن لا صامت
        if _hs_prov is not None:
            result["hs_provenance"] = _hs_prov   # يعرضه التقريرُ كمصدرٍ للرمز
        result["view"] = _view(result)
        _attach_watchdog(result, result.get("analysis_id"), "analyze")
        return _json(result)

    def _clean_agent_prefs(raw: dict | None) -> dict | None:
        """طبّع توجيهات الوكلاء شكلياً (P3) — {key: {on: bool, cmd: str<=500}}.

        أي شكل آخر يُتجاهل بصمت (إعداد عميل لا بيانات)؛ الأمر نص حر يذهب
        حصراً إلى برومبتات كلود داخل عزل _isolate — لا يمسّ وكلاء الأرقام.
        """
        if not isinstance(raw, dict):
            return None
        out = {}
        for k, v in list(raw.items())[:24]:
            if not isinstance(v, dict):
                continue
            out[str(k)[:40]] = {"on": bool(v.get("on", True)),
                                "cmd": str(v.get("cmd") or "")[:500]}
        return out or None

    def _target_countries(iso3s: list[str] | None):
        """ضيّق قائمة الأسواق المرشّحة — an explicit ISO3 subset of COUNTRIES,
        أو None (الافتراضي: كل الأسواق ‎— سلوك الاكتشاف القائم بلا تغيير).

        رموز غير معروفة تُتجاهَل بصمت (لا اختلاق سوق غير موجود في المرجع)؛
        قائمة فارغة بعد الترشيح => None (لا نُسقط التحليل إلى صفر أسواق).
        """
        if not iso3s:
            return None
        from silk_market_ranker import COUNTRIES
        wanted = {s.strip().upper() for s in iso3s if s and s.strip()}
        filtered = [c for c in COUNTRIES if c["iso3"] in wanted]
        return filtered or None

    def _saved_agent_settings() -> dict | None:
        """إعدادات الوكلاء المحفوظة خادمياً — sanitized, or None (لا حفظ)."""
        try:
            import silk_store
            return _clean_agent_prefs(silk_store.load_agent_settings())
        except Exception as e:  # noqa: BLE001 — الإعدادات تحسين لا شرط
            log.debug("saved agent settings unavailable: %s", e)
            return None

    class AgentSettingsBody(BaseModel):
        """جسم إعدادات الوكلاء — {agent_key: {on: bool, cmd: str}} فقط.

        **لا مفاتيح مصادر هنا** — pydantic يسقط أي حقل آخر، والقاموس يمرّ
        على _clean_agent_prefs (on/cmd حصراً) فلا يمكن تهريب مفتاح عبر
        هذه اللوحة؛ مفاتيح المصادر تُضبط في بيئة النشر (Railway env).
        """
        settings: dict | None = None

    @app.get("/settings/agents")
    def get_agent_settings(request: Request):
        """سجل الوكلاء + الإعدادات السارية — the catalog and effective settings.

        الواجهة تبني اللوحة من هذا الرد (سجل واحد قانوني في silk_agents —
        لا قائمة موازية في الواجهة تنحرف عنه).
        """
        _require_key(request)
        _rate_limit(request)
        import silk_missions  # noqa: F401 — يسجّل صفوف البعثات الاثنتي عشر
        from silk_agents import AGENT_CATALOG, default_agent_settings
        merged = default_agent_settings()
        saved = _saved_agent_settings() or {}
        for k, v in saved.items():
            if k in merged:
                merged[k] = v
        return {"agents": AGENT_CATALOG, "settings": merged,
                "saved": bool(saved)}

    @app.post("/settings/agents")
    def set_agent_settings(body: AgentSettingsBody, request: Request):
        """احفظ إعدادات الوكلاء خادمياً — persist per-agent {on, cmd}.

        جسم فارغ = استعادة الافتراضي (يمحو المحفوظ فتسري الافتراضيات).
        """
        _require_key(request)
        _rate_limit(request)
        clean = _clean_agent_prefs(body.settings) or {}
        import silk_store
        silk_store.migrate()
        silk_store.save_agent_settings(clean)
        return {"saved": True, "count": len(clean)}

    class KeysBody(BaseModel):
        """جسم حفظ مفاتيح المصادر — allow-listed server-side key settings."""
        keys: dict[str, str]

    @app.post("/settings/keys")
    def set_keys(body: KeysBody, request: Request):
        """احفظ مفاتيح المصادر في الخادم (Stage 2A) — the settings panel finally
        persists somewhere real: allow-listed keys go to the unified store AND the
        process env (agents pick them up immediately). القيم لا تُعاد أبداً —
        الاستجابة وجود/رفض فقط. متغير بيئة النشر يبقى الأعلى سلطة عند الإقلاع.
        """
        _require_key(request)
        _rate_limit(request)
        import silk_store
        silk_store.migrate()
        saved, rejected = [], []
        for k, v in (body.keys or {}).items():
            v = (v or "").strip()
            if v and silk_store.set_setting(k, v):
                os.environ[k] = v
                saved.append(k)
            elif v:
                rejected.append(k)
        return {"saved": saved, "rejected": rejected}

    @app.post("/deepen")
    def deepen(req: DeepenRequest, request: Request):
        """عمّق التحليل (المسار المدفوع الوحيد) — the only paid-layer path.

        يعمل داخل silk_context.deepen_context() فيسمح حارس BaseAgent البنيوي
        بتشغيل الوكلاء المدفوعين (localprice/volza/explee) — خارجه يستحيل
        تنفيذهم حتى مع مفاتيح مضبوطة. حارسا الموجة ٠ (401 المصادقة، 429
        السقف) يعملان قبل أي وكيل.
        """
        _require_key(request)
        _rate_limit(request)
        _guard_paid(req)
        # بوّابة HS الصلبة على المسار **المدفوع** (بلاغ «حليب نادك»، الثغرة
        # الأولى): كان `/deepen` يمرّر `req.hs_code` مباشرةً إلى المحرّك بلا
        # أيّ فحص — المسار الوحيد القادر على صرف رصيدٍ مدفوع، وهو الوحيد الذي
        # كان بلا بوّابة. والأسوأ: `DeepenRequest.hs_confirmed` كان مُعلَناً
        # بتعليقٍ يوحي بوجود بوّابة، و**لا يُقرأ إطلاقاً** — حقلٌ ميت يوحي
        # بأمانٍ غير قائم. الآن يُقرأ هنا كما في `/analyze` و`/research`
        # تماماً، فتصير نقطةُ الاختناق ثلاثةَ مسارات لا اثنين.
        _deepen_hs = req.hs_code
        _deepen_conf = 1.0 if req.hs_code else None
        if not _deepen_hs and req.product:
            from silk_hs_resolver import resolve as _resolve_deepen
            _dp = _resolve_deepen(req.product)
            _deepen_hs, _deepen_conf = _dp.value, (
                _dp.confidence if _dp.value is not None else None)
        from silk_hs_confirm import preflight_resolve as _preflight_deepen
        _deepen_hs, _deepen_prov, _blocked = _preflight_deepen(
            req.product, _deepen_hs, req.hs_confirmed,
            allow_claude=_classify_general_allow_claude(),
            hs_confidence=_deepen_conf,
            label_attributes=req.label_attributes)
        if _deepen_prov is not None:
            import silk_ops_log
            silk_ops_log.record_error(
                "hs_auto_resolved",
                f"حُسِم رمزُ HS آلياً على المسار المدفوع لمنتج {req.product!r} "
                f"=> {_deepen_hs} ({_deepen_prov.get('resolved_from')})",
                context={"product": req.product, "hs_code": _deepen_hs,
                         "path": "/deepen",
                         "resolved_from": _deepen_prov.get("resolved_from"),
                         "source_url": _deepen_prov.get("source_url")})
        if _blocked is not None:
            import silk_ops_log
            _cd = _blocked.get("hs_confirmation") or {}
            silk_ops_log.record_error(
                _blocked.get("error") or "hs_confirmation_blocked",
                f"رُفض تعميقٌ مدفوع برمز HS غير محسوم لمنتج {req.product!r}: "
                f"{_cd.get('reason') or _blocked.get('message')}",
                context={"product": req.product, "hs_code": _deepen_hs,
                         "path": "/deepen",
                         "hs_confidence": _blocked.get("hs_confidence"),
                         "missing_terms": _cd.get("missing_terms")})
            raise HTTPException(status_code=422, detail=_blocked)
        import silk_context
        with silk_context.deepen_context():
            result = silk_engine.analyze(
                req.product, year=req.year, with_trends=req.with_trends,
                with_tariffs=req.with_tariffs, with_faostat=req.with_faostat,
                with_maps=req.with_maps, with_websearch=req.with_websearch,
                with_localprice=req.with_localprice, own_price=req.own_price,
                with_volza=req.with_volza, with_explee=req.with_explee,
                with_ai=req.with_ai,
                with_competitors=req.with_competitors,
                with_channels=req.with_channels,
                with_importers=req.with_importers,
                with_requirements=req.with_requirements,
                with_trend=req.with_trend, trend_span=req.trend_span,
                product_card=(req.product_card.model_dump()
                              if req.product_card else None),
                hs_code=(_deepen_hs if _deepen_prov is not None
                         else req.hs_code),
                persist=req.persist)
        if _deepen_prov is not None:
            result["hs_provenance"] = _deepen_prov
        result["view"] = _view(result)
        return _json(result)

    class ResearchRequest(BaseModel):
        """طلب بحث عميق (الموجة ٤، V5) — ١٢ بعثة كلود + محلل شامل + تقرير.

        لا حقول مدفوعة (كـ AnalyzeRequest تماماً) — مسار مجاني حصراً. `market`
        نص حر (اسم عربي/إنجليزي أو ISO3) يُحلّ عبر silk_market_resolver؛
        مطابقة ضعيفة/غامضة = 422 مع اقتراحات، لا تخمين سوق.

        حادثة نقطة تفتيش/استئناف + تشغيل خلفي (P0): `resume` يستأنف تشغيلة
        سابقة بمعرّفها — `product`/`market` يصيران اختياريَين عندها (تُقرَآن
        من الطلب المخزَّن وقت الإنشاء)، وإلا إلزاميان كالسابق. `async_run`
        يُعيد `analysis_id` فوراً (202) ويكمل المعالجة في خيط خلفي — تجاوز
        مهلة بوّابة/وكيل عكسي لا يقتل التشغيلة أو يُيتّمها بعد اليوم.
        """
        product: str | None = None
        market: str | None = None
        hs_code: str | None = None
        product_card: ProductCard | None = None
        own_price: float | None = None  # سعرك المستهدف — كـAnalyzeRequest
        persist: bool = True
        agent_prefs: dict | None = None
        # بلاغ حي — بوابة ما قبل التشغيل (409) ترفض تشغيلة بلا كلود صراحة؛
        # هذا الحقل فتحة هروب صريحة تطلبها الجهة المستهلكة (لا تدهور
        # افتراضي): تسليم تقرير موسوم بوضوح "متدهور" بدل رفضه كلياً.
        allow_degraded: bool = False
        resume: int | None = None
        async_run: bool = False
        # استشارة بلد المنشأ (Wave 1): موافقةٌ صريحة على إكمال دراسةِ سوقٍ من
        # أكبر مصدّري هذا الرمز عالميًا. غيابها + سوقٌ منتِجة + الصمّام مُفعَّل
        # => 422 استشاري قبل أيّ حجز؛ إرسالها بعد موافقة المستخدم يُكمِل التشغيلة.
        producer_ack: bool = False
        # سماتُ بطاقة العبوة الرقمية من استخلاص الصورة — نفس عقد
        # `AnalyzeRequest` حرفياً (المسارَان معاً، لا إصلاحَ نصفيّ: الدرس ٣٥).
        label_attributes: list[dict] | None = None
        # موافقةٌ صريحة موحّدة على أشقّاء استشارات ما قبل التشغيل (Wave 1.5،
        # عائلة A): تصدير إلى بلد المنشأ / سوق تحت عقوبات / فصل مقيَّد قانونيًا.
        # لوحةُ «جاهزية الدراسة» تجمعها؛ زرّ التأكيد الواحد يرسلها true.
        advisories_ack: bool = False
        # تأكيدٌ صريح على رمز HS رغم تحذير التطابق (Wave 1.2): بوّابةُ تأكيد
        # الرمز تُرجِع 422 حين لا تشمل صفةُ الرمز صفةَ المنتج المميّزة؛ إرسالُ
        # hs_confirmed=true بعد مراجعة المستخدم يُكمِل التشغيلة على مسؤوليته.
        hs_confirmed: bool = False
        # نمط كتابة التقرير (طلب المالك 2026-07-23): "academic" يجعل الكاتب
        # يكتب بسجلٍّ بحثيٍّ علمي (نفس الأقسام/الحكم/قواعد الصدق، النثر وحده
        # يتغيّر). غيابه => الافتراضي من البيئة `SILK_REPORT_STYLE`
        # (المضبوط "academic"). قيمة صريحة في الطلب تتقدّم على البيئة.
        report_style: str | None = None
        # موافقةٌ صريحة على تحذير معقولية بلد المورّد (البند أ٢): حين يكشف
        # الفحص الاقتصادي تفكّكًا شبه تامٍّ بين موردي السوق وأكبر مصدّري الرمز
        # عالميًا => 422 استشاري حتى يؤكّد المستخدم الرمز أو يعيد تصنيفه.
        a2_ack: bool = False

    def _default_report_style() -> str:
        """نمط الكتابة الافتراضي للتوليد — `SILK_REPORT_STYLE` (طلب المالك:
        الافتراضي "academic"). أيّ قيمةٍ غير "academic" تعني السجل التجاري."""
        return (os.environ.get("SILK_REPORT_STYLE", "academic") or "").strip().lower()

    def _research_budget_status(economics: dict) -> dict:
        """حالة الميزانية على مستوى التشغيلة كاملة — P1، حادثة نفاد
        الاعتمادات: يسمّي **أي** سقف بلغ حدّه صراحة، لا يكتفي بملاحظة
        مدفونة داخل فجوات بعثة واحدة (كانت موجودة أصلاً — هذا تجميع
        علوي إضافي يجعلها مرئية فوراً للوحة/المستهلك)."""
        llm_cap = int(os.environ.get("SILK_RESEARCH_MAX_LLM_CALLS", "40"))
        tool_cap = int(os.environ.get("SILK_RESEARCH_MAX_TOOL_CALLS", "100"))
        llm_calls = economics.get("llm_calls", 0)
        tool_calls = economics.get("tool_calls", 0)
        hit = []
        if llm_calls >= llm_cap:
            hit.append(f"SILK_RESEARCH_MAX_LLM_CALLS={llm_cap}")
        if tool_calls >= tool_cap:
            hit.append(f"SILK_RESEARCH_MAX_TOOL_CALLS={tool_cap}")
        return {"exhausted": bool(hit), "caps_hit": hit,
               "llm_calls": llm_calls, "llm_cap": llm_cap,
               "tool_calls": tool_calls, "tool_cap": tool_cap,
               # H5: هل تدهور الذيل (تخطّى حَكَم التوليف + مراجعة الكاتب) لأن
               # البعثات استنفدت السقف؟ مُعلَن صراحةً، لا مدفون.
               "tail_degraded": bool(economics.get("tail_degraded"))}

    def _attach_quality_gate(result: dict, trace_id: str | None) -> None:
        """شغّل بوابة الجودة على القالب الموحّد وألحِق نتيجتها — الموجة ١٠،
        مستخرَجة كي يستدعيها كل من مسار /research الكامل ونقطة إعادة توليد
        التقرير وحدها (POST /analyses/{id}/report) بلا ازدواج منطق."""
        try:
            import silk_quality_gate
            gate_out = silk_quality_gate.run_quality_gate(result["view"])
            result["view"]["deep_research"]["quality_gate"] = gate_out
            if trace_id:
                import silk_trace
                silk_trace.append_event(
                    trace_id, event="quality_gate", verdict=gate_out["verdict"],
                    finding_count=len(gate_out["findings"]))
        except Exception as e:  # noqa: BLE001 — البوابة تحسين لا شرط تسليم
            log.warning("quality gate skipped: %s", e)

    def _gate_verdict_for_client_export(view: dict) -> tuple[str, dict]:
        """شغّل بوابة الجودة الحتمية على القالب المُصدَّر لجمهور العميل وأعد
        (verdict, gate_out) — الفكس الجذري §0: هذه البوابة **شرط تسليم** لا
        تحسيناً اختيارياً على مسار التصدير للعميل (خلافاً لـ`_attach_quality_
        gate` أعلاه التي تبقى «أفضل جهد» على مسار بناء التشغيلة نفسها).
        عطلٌ داخلي في البوابة نفسها (استثناء غير متوقَّع) يُعامَل كـFAIL —
        لا «تخطٍّ صامت» يسمح بتسريب تقرير لم يُفحَص فعلياً."""
        try:
            import silk_quality_gate
            gate_out = silk_quality_gate.run_quality_gate(view)
            return gate_out.get("verdict", silk_quality_gate.FAIL), gate_out
        except Exception as e:  # noqa: BLE001 — عطل البوابة = FAIL للعميل، ليس تخطّياً
            log.warning("quality gate crashed during client export: %s", e)
            return "FAIL", {
                "verdict": "FAIL",
                "findings": [{
                    "check": "gate_crash", "repairable": False,
                    "note": f"عطل داخلي في بوابة الجودة أثناء التصدير "
                            f"({type(e).__name__}) — عومل التقرير كأنه FAIL "
                            "لحماية العميل من محتوى لم يُفحَص فعلياً."}],
                "methodology_notes": [],
            }

    def _block_client_export_if_gate_failed(
            view: dict, analysis_id: int, found: dict, fmt: str,
            request: "Request") -> None:
        """يرفع 409 إن رصدت بوابة الجودة FAIL على قالب تصدير العميل — البند
        §0 (الفكس الجذري): البوابة كانت «تحسين لا شرط تسليم»
        (`_attach_quality_gate`)؛ تقرير FAIL كان يصل العميل بلا أي حجب.
        `?override=1` يتخطّى الحجب بسلطة المالك المنفصلة فقط (WP-7 §1:
        ترويسة `X-Owner-Key` المطابقة لـ`SILK_OWNER_KEY`؛ مفتاح API العادي
        وحده = 403). `internal=1` لا يمرّ من هنا إطلاقاً (يبقى متاحاً دوماً
        للمدقّق)."""
        # WP-2 §3: قبل البوابة — حضِّر نثر الصياغة التجارية للأقسام بلا سرد
        # كاتب (نداء مصغّر لكل قسم، temperature=0). نجاحه يملأ
        # dr["client_fallback_prose"] فيمرّ القسم من البوابة ويعرضه
        # `_client_body_or_fallback`؛ فشله يترك القسم خاوياً فتُفشِله
        # البوابة (409) — لا بنود `dp.value` خام تصل العميل بعد الآن.
        #
        # مراجعة شيفرة PR #147: (أ) هذا نداء كلود على مسارٍ مجاني — يمرّ
        # عبر بوابة إضافات كلود نفسها (`_free_ai_extras_allowed`: حجب النشر
        # غير المحمي + حجز ذرّي واحد من SILK_PAID_DAILY_CAP) كأي إضافة
        # مسار مجاني، لا نداء غير محكوم؛ (ب) النثر الناجح **يُخزَّن على
        # السجل** (save_analysis بمعرّفه = تحديث في المكان) فلا يُعاد دفع
        # نفس النداءات مع كل تصدير docx/pdf — build_view يعيد حمله من
        # المدوّنة عبر view["deep_research"]["client_fallback_prose"].
        dr = view.get("deep_research") or {}
        if dr and not dr.get("client_fallback_prose"):
            try:
                from silk_reports import _client_missing_narrative_heads
                needs = {h: items for h, items in
                         _client_missing_narrative_heads(dr).items() if items}
            except Exception:  # noqa: BLE001 — تعذّر الفحص = لا نداء
                needs = {}
            ai_ok = False
            if needs:
                ai_ok, _rephrase_note = _free_ai_extras_allowed()
            if needs and ai_ok:
                try:
                    from silk_ai_judge import rephrase_client_sections
                    prose = rephrase_client_sections(dr)
                    if prose:
                        dr["client_fallback_prose"] = prose
                        try:
                            stored_dr = (found or {}).get("deep_research")
                            if isinstance(stored_dr, dict) and analysis_id:
                                stored_dr["client_fallback_prose"] = prose
                                silk_storage.save_analysis(
                                    found, analysis_id=analysis_id)
                        except Exception as e:  # noqa: BLE001 — تخزين اختياري
                            log.warning("prose cache persist failed: %s", e)
                except Exception as e:  # noqa: BLE001 — فشل التحضير تحكمه البوابة
                    log.warning("client fallback rephrase failed: %s", e)
        verdict, gate_out = _gate_verdict_for_client_export(view)
        if verdict != "FAIL":
            return
        findings = gate_out.get("findings") or []
        digest = [{"check": f.get("check"), "note": f.get("note")}
                  for f in findings if not f.get("repairable", True)] or [
            {"check": f.get("check"), "note": f.get("note")} for f in findings]
        override = str(request.query_params.get("override") or "").lower() in (
            "1", "true", "yes")
        if override:
            # WP-7 §1: التجاوز يتطلّب سلطة مالكٍ منفصلة (SILK_OWNER_KEY عبر
            # ترويسة X-Owner-Key) — مفتاح API العادي لم يعد يكفي. كل تجاوز
            # ناجح يُسجَّل في الحارس (kind=export_override) فتُختَم النسخ
            # الداخلية اللاحقة «سُلِّم بتجاوز مالك — ملاحظات البوابة مرفقة».
            owner_key = os.environ.get("SILK_OWNER_KEY", "").strip()
            supplied = (request.headers.get("X-Owner-Key") or "").strip()
            if not owner_key or supplied != owner_key:
                raise HTTPException(status_code=403, detail={
                    "error": "owner_override_required",
                    "message": "تجاوز بوابة الجودة يتطلّب سلطة المالك "
                               "المنفصلة (ترويسة X-Owner-Key المطابقة لـ"
                               "SILK_OWNER_KEY على الخادم) — مفتاح API "
                               "العادي لا يكفي.",
                })
            try:
                import silk_watchdog
                silk_watchdog.record_override(
                    analysis_id, found.get("product"),
                    (found.get("market") or {}).get("name_en"), findings, fmt)
            except Exception as e:  # noqa: BLE001 — تسجيل التجاوز لا يُسقِطه
                log.warning("watchdog override record failed: %s", e)
            return
        try:
            import silk_watchdog
            silk_watchdog.record_blocked_export(
                analysis_id, found.get("product"),
                (found.get("market") or {}).get("name_en"), findings, fmt)
        except Exception as e:  # noqa: BLE001 — تسجيل الحجب لا يُسقِط الحجب نفسه
            log.warning("watchdog blocked-export record failed: %s", e)
        import silk_ops_log
        silk_ops_log.record_error(
            "quality_gate_blocked_export",
            f"تصدير العميل ({fmt}) مُنِع: بوابة الجودة أعادت FAIL",
            context={"analysis_id": analysis_id, "findings": digest})
        raise HTTPException(status_code=409, detail={
            "error": "quality_gate_fail",
            "message": "تعذّر تسليم هذا التقرير للعميل: بوابة الجودة رصدت "
                       "مشاكل حاجبة قبل التسليم. استخدم ?internal=1 للنسخة "
                       "التشغيلية الكاملة للمدقّق، أو ?override=1 لتخطّي "
                       "الحجب — التجاوز يتطلّب سلطة المالك المنفصلة "
                       "(ترويسة X-Owner-Key المطابقة لـ SILK_OWNER_KEY)؛ "
                       "مفتاح API العادي وحده لا يكفي.",
            "findings": digest,
        })

    def _attach_override_history(view: dict, analysis_id: int | None) -> None:
        """WP-7 §1: حمِّل سجلّات تجاوز المالك (إن وُجدت) على القالب قبل بناء
        النسخة الداخلية — يختمها `silk_reports._render_research_docx` بسطر
        «سُلِّمت نسخة عميل بتجاوز مالكٍ — ملاحظات البوابة مرفقة»."""
        try:
            import silk_watchdog
            ov = silk_watchdog.override_records_for(analysis_id)
            if ov:
                view["owner_override_history"] = ov[:3]
        except Exception as e:  # noqa: BLE001 — الختم توثيق، لا شرط تصدير
            log.warning("override history attach failed: %s", e)

    def _attach_watchdog(result: dict, analysis_id: int | None,
                         kind: str) -> None:
        """نقطةُ اختناقٍ مشتركةٌ واحدة يستدعيها **كلا** المسارين (/analyze
        و/research) — الحارس («كاميرا مراقبة»، طلب المُشرِف): سجلّ صحّةٍ
        حتميٌّ (صفر نداء كلود) يُخزَّن في مخزنه المستقل (`silk_watchdog.py`)
        لسطح مالكٍ منفصل تماماً («تقرير الحارس»). **لا يمسّ `result` إطلاقاً**
        (لا حقل يُضاف لنتيجة التحليل) — مبدأ عدم التلوّث: صفر سطر حارسٍ يصل
        أي سطح عميل. فشلها الداخلي مُعزولٌ بالفعل (`silk_watchdog.observe`
        لا ترفع أبداً)؛ هذا `try` طبقة حمايةٍ إضافية فقط."""
        try:
            import silk_watchdog
            silk_watchdog.observe(result, kind, analysis_id)
        except Exception as e:  # noqa: BLE001 — مراقبة لا تُسقِط تحليلاً أبداً
            log.warning("watchdog skipped: %s", e)

    def _collect_importer_leads(scrape_future, product: str, market_ref,
                                mission_reports: dict, scrape_t0: float,
                                mono) -> dict:
        """اجمع جهات اتصال المستوردين بسقف زمني كلّي — نفس مسار الروابط الوحيد
        (مكشطة → احتياط Places → فجوة معلنة). بلا نداء كلود. أمر المالك
        المُحدَّث ITEM 1: يُنادى تلقائياً في التشغيلة قبل الكاتب.

        السقف الكلّي `SILK_ENRICH_TIMEOUT_S` (٦٠ث افتراضياً) يحدّ زمن الجمع —
        الكشط راكَب البعثات أصلاً فيكون عادةً جاهزاً. مهلة/فشل = فجوة معلنة
        `{leads:[], path:"gap"}`، لا تشغيلة عالقة ولا رقم مخترَع."""
        import silk_gmaps
        try:
            _cap = float(os.environ.get("SILK_ENRICH_TIMEOUT_S", "60"))
            _web_cands = silk_gmaps.extract_web_candidates(mission_reports)
            leads = silk_gmaps.finalize_leads(
                scrape_future, product, market_ref, _web_cands, timeout_s=_cap)
            log.info("gmaps leads path=%s count=%d (%.1fs after submit)",
                     leads.get("path"), len(leads.get("leads") or []),
                     mono.monotonic() - scrape_t0)
            return leads
        except Exception as e:  # noqa: BLE001 — الروابط تحسين لا شرط؛ لا تُسقط التشغيلة
            log.warning("gmaps enrich failed: %s", e)
            return {"leads": [], "path": "gap",
                    "note": f"تعذّر جمع الروابط: {type(e).__name__}"}

    def _run_research_pipeline(market_ref, product: str, hs_code: str | None,
                               hs_note: str | None, product_card_dict: dict | None,
                               ai_ok: bool, ai_note: str, prefs: dict | None,
                               ready: bool, ready_reason: str,
                               analysis_id: int | None,
                               resume_reports: dict | None,
                               report_style: str | None = None,
                               hs_confidence: float | None = None,
                               hs_provenance: dict | None = None) -> dict:
        """جسم التشغيلة الثقيل — بعثات + محلل + توليف + كاتب/مراجع + بوابة
        جودة. مستخرَج من مسار /research المتزامن السابق **بلا تغيير سلوكي**
        كي يُستدعى إما مباشرة (وضع متزامن) أو من خيط خلفي (async_run=true)
        بلا ازدواج منطق. لا حفظ هنا — المستدعي يقرّر متى/كيف يُخزَّن."""
        import contextlib
        import silk_context
        ctx = (contextlib.nullcontext() if ai_ok
               else silk_context.block_ai_extras())
        with ctx, silk_context.agent_prefs_context(prefs):
            silk_context.begin_data_counter()
            from silk_missions import deep_research
            from silk_market_analyst import analyze_market, to_synthesis_input
            from silk_synthesis import synthesize
            from silk_ai_judge import write_reviewed_report

            # تقدّم حيّ (GET /research/{id}/status): لقطة أولى تضبط started_at
            # مرّة واحدة — كل لقطة لاحقة (لكل بعثة، ولكل مرحلة كاتب/مراجع)
            # تعيد استعمال نفس القناة (silk_context.snapshot_research_progress)
            # بلا عدّاد جديد، تُقرأ من نفس data_counter المستعمَل للتقرير النهائي.
            import datetime as _dt
            _started_at = _dt.datetime.now().isoformat(timespec="seconds")
            silk_context.snapshot_research_progress(
                analysis_id, "missions", started_at=_started_at)

            # C2/D-02 (Command #5b): قدّم مهمة كشط الخرائط **مبكراً** (قبل
            # البعثات) على خيط منفصل — مهلتها (٨ دقائق) تتراكب مع زمن البعثات
            # والذيل فلا تزيد زمن التشغيلة الكلي، والتشغيلة لا تنتظرها (تُجمَع
            # بمهلة قصيرة قبل بناء النتيجة، وإلا السلسلة الاحتياطية/فجوة).
            # تعطيل نظيف: إن كانت المكشطة غير مُهيَّأة يعود None بلا أي أثر.
            import time as _mono
            import silk_gmaps
            _scrape_future = silk_gmaps.submit_scrape_async(product, market_ref)
            _scrape_t0 = _mono.monotonic()
            # E3 (SPEC-v2): علامات زمن الجدار لكل مرحلة — تُحسَب منها المصارف
            # الثلاثة الكبرى (stage_top_sinks) وتُطبَع في data_economics.
            _stage_marks = {"missions": _scrape_t0}

            # deep_research() (لا run_all_missions مباشرة) — يفعّل التتبّع
            # الكامل دوماً (data/traces/{trace_id}.jsonl، الموجة ٦) فيبقى كل
            # تشغيل /research إنتاجي قابلاً للتدقيق، لا التشغيلات التجريبية فقط.
            research_run = deep_research(market_ref, product=product,
                                         hs_code=hs_code,
                                         product_card=product_card_dict,
                                         analysis_id=analysis_id,
                                         resume_reports=resume_reports)
            mission_reports = research_run["reports"]
            trace_id = research_run.get("trace_id")
            _stage_marks["analyst"] = _mono.monotonic()  # E3: نهاية البعثات
            silk_context.snapshot_research_progress(analysis_id, "analyst")
            # H5 (تدقيق): حارس إنفاق على مستوى التشغيلة للذيل. السقف الكلي
            # (SILK_RESEARCH_MAX_LLM_CALLS) كان يُستشار داخل حلقة البعثات
            # فقط؛ الذيل (محلل+توليف+كاتب+مراجع) يجري بلا حكم حتى لو استُنفد.
            # الآن: إن بلغت البعثاتُ السقف، يُنتِج الذيل التقرير الأساسي
            # (المحلل + مسوّدة الكاتب — لا يُلغى الكاتب فلا فقدان) لكن يتخطّى
            # المكلّف الاختياري: حَكَم التوليف مرحلة-٢ (تبقى جورية المرحلة ١)
            # ودورة مراجعة الكاتب (مسوّدة بلا تنقيح). تدهور رشيق مُعلَن.
            _llm_cap = int(os.environ.get("SILK_RESEARCH_MAX_LLM_CALLS", "40"))
            _ctr = silk_context.data_counter() or {}
            tail_over_budget = ai_ok and _ctr.get("llm_calls", 0) >= _llm_cap
            tail_with_ai = ai_ok and not tail_over_budget
            # PART C1: تجاوز الميزانية يفرض دورة واحدة؛ وإلا None = افتراض
            # البيئة (SILK_MAX_REVIEW_CYCLES، افتراضياً ١ — التنقيح الثاني
            # للمشاكل الحاجبة فقط حتى حين يُرفَع السقف إلى ٢).
            tail_max_cycles = 1 if tail_over_budget else None
            # بلاغ حي (تمور/هولندا، تشغيلة ثانية): trace_context البعثات
            # الاثنتي عشرة يُغلَق فور عودة deep_research() أعلاه — نداءا
            # المحلل الشامل والكاتب/المراجع كانا يجريان **بلا أي تتبّع**،
            # فحين فشل الكاتب لم يكن هناك أثر يوضّح هل بلغ مهلته الموسّعة
            # فعلاً أم فشل أسرع بخطأ آخر. إعادة فتح نفس ملف التتبّع (معرّف
            # واحد، إلحاق فقط — لا تصادم) للمحلل صراحة؛ الكاتب يُمرَّر
            # trace_id مباشرة (راجع silk_ai_judge._traced_call).
            import silk_trace
            with silk_trace.trace_context(trace_id):
                analyst_out = analyze_market(
                    market_ref, product, mission_reports, hs_code=hs_code,
                    product_card=product_card_dict)
            analyst_input = to_synthesis_input(analyst_out)
            _stage_marks["synthesis"] = _mono.monotonic()  # E3: نهاية المحلل
            verdict = synthesize(
                list(mission_reports.values()), product=product,
                market=market_ref.name_en, with_ai=tail_with_ai,
                analyst_assessment=analyst_input)
            _stage_marks["enrich"] = _mono.monotonic()  # E3: نهاية التوليف
            # إكمال بيانات المستوردين تلقائياً (أمر المالك المُحدَّث ITEM 1):
            # حين تكون المكشطة مُهيَّأة، تُجمَع جهات الاتصال (هاتف/إيميل/موقع)
            # **قبل الكاتب** فيشحن التقرير كاملاً من التشغيلة الأولى. الكشط
            # قُدِّم مبكراً (submit_scrape_async قبل البعثات) فتراكب زمنه مع
            # البعثات/المحلل؛ هنا يُجمَع بسقف زمني كلّي (SILK_ENRICH_TIMEOUT_S،
            # ٦٠ث افتراضياً). فشل/مهلة = فجوة معلنة «—»، لا تشغيلة عالقة، لا
            # نداء كلود. صندوق التقدّم يعرض المرحلة «إكمال بيانات المستوردين».
            silk_context.snapshot_research_progress(analysis_id, "enrich_leads")
            importer_leads = _collect_importer_leads(
                _scrape_future, product, market_ref, mission_reports,
                _scrape_t0, _mono)
            _stage_marks["writer"] = _mono.monotonic()  # E3: نهاية الإكمال
            # Wave 1.2/1.3 (تدقيق زبدة الفول السوداني/اليمن): عقد تأكيد رمز HS
            # — يُقاس تداخل صفات المنتج المميّزة مع وصف الرمز؛ رمز غير مؤكَّد
            # يُمرَّر للكاتب فيؤطّر أرقام كومتريد «مؤشر سياقي»، ويُخزَّن في
            # النتيجة فتعيد طبقة العرض تأطيرها + تسقف الثقة (silk_render).
            try:
                from silk_hs_confirm import (confirm_hs,
                                             cap_confidence_for_flagged_hs)
                hs_conf = confirm_hs(product, hs_code) if hs_code else None
            except Exception:
                hs_conf = None
            # PR A §A1: سقفُ ثقةِ الحكم عند تعليم الرمز يُطبَّق **قبل** الكاتب من
            # المصدر الواحد، فيرى الكاتب القيمة المسقوفة نفسها التي سيعرضها
            # الغلاف — لا §4 «73%» مقابل غلاف «50%». طبقة العرض تُبقيها idempotent.
            try:
                verdict = cap_confidence_for_flagged_hs(verdict, hs_conf)
            except Exception:  # noqa: BLE001 — تسقيفٌ تحسينيّ لا يُسقِط تشغيلة
                pass
            # نمط الكتابة (طلب المالك): يُمرَّر للكاتب فيبدّل عقد السجل اللغوي
            # وحده (الأكاديمي مقابل التجاري) — نفس الأقسام/الحكم/قواعد الصدق.
            eff_report_style = (report_style or _default_report_style())
            report_out = (write_reviewed_report(
                mission_reports, analyst_input.get("summary", ""), verdict,
                product, market_ref.name_en, max_cycles=tail_max_cycles,
                trace_id=trace_id, hs_code=hs_code, hs_confirmation=hs_conf,
                style=eff_report_style,
                on_stage=lambda s: silk_context.snapshot_research_progress(
                    analysis_id, s)) if ai_ok else
                {"report": None, "review_cycles": 0, "unresolved_notes": []})
            _stage_marks["end"] = _mono.monotonic()  # E3: نهاية الكاتب/المراجع
            economics = dict(silk_context.data_counter() or {})
            economics["tail_degraded"] = tail_over_budget
            # E3 (SPEC-v2): زمن الجدار لكل مرحلة + أكبر ثلاثة مصارف — يُطبَع في
            # data_economics كي يقيس المالك أين تذهب الدقائق (البعثات متوازية،
            # الذيل متسلسل)، وهدف < ١٠ دقائق يُقاس عليه.
            _order = ["missions", "analyst", "synthesis", "enrich", "writer",
                      "end"]
            _labels = {"missions": "البعثات (متوازية)", "analyst": "المحلل الشامل",
                       "synthesis": "التوليف/الحكم",
                       "enrich": "إكمال بيانات المستوردين",
                       "writer": "الكاتب+المراجع"}
            _ss = {}
            for _i in range(len(_order) - 1):
                a, b = _order[_i], _order[_i + 1]
                if a in _stage_marks and b in _stage_marks:
                    _ss[a] = round(_stage_marks[b] - _stage_marks[a], 1)
            economics["stage_seconds"] = _ss
            economics["stage_total_seconds"] = round(sum(_ss.values()), 1)
            economics["stage_top_sinks"] = [
                {"stage": _labels.get(k, k), "seconds": v}
                for k, v in sorted(_ss.items(), key=lambda kv: -kv[1])[:3]]
            if not report_out.get("report"):
                # ITEM 5ب: فشل الكاتب في التشغيلة الرئيسية (لا مسار regen) —
                # السبب مُطهَّر قبل التخزين (نفس مُطهِّر H1/H4 القائم).
                from silk_render import _strip_internal_plumbing
                import silk_ops_log
                silk_ops_log.record_error(
                    "writer_failure",
                    _strip_internal_plumbing(report_out.get("failure_reason") or "")
                    or "فشل الكاتب بلا سبب مسجَّل",
                    context={"analysis_id": analysis_id, "trace_id": trace_id})

        served = economics.get("store_hits", 0) + economics.get("cache_hits", 0)
        economics["note"] = (
            f"{economics.get('llm_calls', 0)} نداء كلود، "
            f"{economics.get('tool_calls', 0)} نداء أداة، {served} قراءة "
            "خُدمت من المخزن/ذاكرة الطلبات")
        from silk_pricing import estimate_cost_usd
        cost = estimate_cost_usd(economics.get("llm_usage"))
        economics["cost_usd_estimate"] = cost["total_usd"]
        economics["cost_usd_by_model"] = cost["by_model"]
        economics["cost_unpriced_models"] = cost["unpriced_models"]
        # إسناد التكلفة لكل بعثة (Part C، تحضير قياس حقيقي لتخفيض الكلفة):
        # mission_usage يُملأ فقط داخل silk_context.mission_context (وسم كل
        # نداء بمفتاح بعثته) — يُعاد استعمال estimate_cost_usd نفسه لكل بعثة
        # لا حساب تسعير موازٍ. تشغيلات سابقة لهذه الإضافة تعرض {} — فجوة
        # معلنة صريحة (تشغيلة قديمة بلا هذا الوسم)، لا اختلاق رقم.
        economics["cost_usd_by_mission"] = {
            mkey: estimate_cost_usd(mu)["total_usd"]
            for mkey, mu in (economics.get("mission_usage") or {}).items()}
        silk_context.snapshot_research_progress(analysis_id, "done")
        # H6: صالِح الحجز المسبق بالتكلفة الفعلية المُقدَّرة — المعالج حجز
        # التقدير (_expected) ذرّيًا قبل البدء؛ هنا نبدّله بالمُنفَق الحقيقي
        # المحسوب من رموز *كل* نداءات كلود في التشغيلة: بعثات + محلل + توليف +
        # كاتب بما فيه **كل محاولات تصعيد السقف** + **دورات المراجع** — العدّاد
        # يُقرأ بعد اكتمال الذيل كله (economics أعلاه)، وكل ردّ HTTP يسجّل رموزه
        # حتى المقتطع (silk_llm_provider._record_usage قبل فحص الاقتطاع). لكل
        # تشغيلة (متزامنة أو خلفية، كلاهما يمرّ من هنا).
        _reserved_usd = float(os.environ.get("SILK_RESEARCH_EXPECTED_USD", "3.0"))
        silk_usage.reconcile_usd(reserved=_reserved_usd, actual=cost["total_usd"])
        budget_status = _research_budget_status(economics)

        # روابط المستوردين جُمِعت تلقائياً **قبل الكاتب** (أمر المالك المُحدَّث
        # ITEM 1) في مرحلة «إكمال بيانات المستوردين» أعلاه — تُشحن مع التقرير
        # من التشغيلة الأولى. `importer_leads` جاهز هنا لبناء النتيجة.
        result: dict = {
            "product": product, "hs_code": hs_code,
            # ثقةُ التصنيف تصل النتيجةَ فالعرض (كانت تُهدَر => «ثقة التصنيف —»).
            "hs_confidence": hs_confidence,
            "year": None,
            "preliminary": True,
            "market": {"iso3": market_ref.iso3, "m49": market_ref.m49,
                      "iso2": market_ref.iso2, "name_en": market_ref.name_en,
                      "name_ar": market_ref.name_ar},
            "markets": [],  # لا ترتيب أسواق هنا — سوق واحد مُحلَّل بعمق
            "deep_research": {
                "missions": mission_reports, "analyst": analyst_out,
                "verdict": verdict, "report": report_out,
                # نمط الكتابة المستعمَل فعلاً — يُخزَّن كي تتّسق طبقة العرض
                # وإعادة التوليد/التصدير مع ما كُتب به التقرير أول مرّة.
                "report_style": eff_report_style,
                # C5 (Command #5b): قائمة مستوردين/موزعين قابلين للتواصل
                # (خرائط قوقل/Places + مرشّحو ويب) — تُعرَض في قسم الدخول.
                "importer_leads": importer_leads,
                "trace_id": research_run.get("trace_id"),
                # P1 (حادثة نفاد الاعتمادات): سقف بلغ حدّه = إنهاء رشيق
                # بفجوات معلنة، لا خطأ صلب — لكن يُذكَر صراحةً أيّ سقف.
                "budget_status": budget_status,
            },
            "data_economics": economics,
        }
        if hs_note:
            result["hs_resolution_note"] = hs_note
        # مصدرُ الرمز حين حُسِم آلياً بالسمة الرقمية (بلاغ المُشرِف) — يعرضه
        # التقريرُ صراحةً («الرمز محدَّد من صورة العبوة» / «من مصدر ويب: …»)
        # فلا يظهر رمزٌ حُسِم آلياً بلا إفصاحٍ عن دليله.
        if isinstance(hs_provenance, dict) and hs_provenance.get("hs6"):
            result["hs_provenance"] = hs_provenance
        # Wave 1.3: عقد تأكيد الرمز يُخزَّن في النتيجة — تعيد طبقة العرض تأطير
        # أرقام كومتريد وتسقف الثقة عند التعليم (silk_render._deep_research_view).
        if isinstance(hs_conf, dict):
            result["hs_confirmation"] = hs_conf
        # إعادةُ تحقّقٍ للرمز المُعاد من سجلّ (بلاغ «حليب نادك»، الثغرة الثانية):
        # `resume` يتخطّى بوّابةَ ما قبل التشغيل عمداً (البعثاتُ المخزَّنة تُعاد
        # بلا نداءٍ جديد، فحجبُها يُفقِد عملاً مدفوعاً اكتمل) — لكنّ المرورَ
        # الصامت يُعيد إنتاج تقريرٍ على رمزٍ ربّما صار خاطئاً. العقد: **يمرّ
        # ويُوسَم**، فتعرضه طبقةُ العرض في «حدود هذا التقرير».
        # **لا تُعاد المصالحة على رمزٍ حُسِم بقياس** (اللائحة ٦٥، منعاً لعائلة
        # اللائحة ١٢ «الحدود تناقض المتن»): `revalidate` يقارن الرمزَ بمُحلِّلٍ
        # **لفظيّ** — وهو بالضبط ما عجز عن التمييز بين بنود الترويسة فاستُدعي
        # القياسُ أصلاً. تركُه يعمل يُخرِج سطرَ حدٍّ يقول «المُحلِّل اليوم يعيد
        # رمزاً آخر» بجوار سطرِ إفصاحٍ يقول «الرمز محدَّد من صورة العبوة» —
        # تناقضٌ صريح، ومقارنةُ دليلٍ مقيسٍ بتطابقٍ حرفيّ. سطرُ الإفصاح وحده
        # يبقى، وهو يذكر القياسَ ومصدرَه وحدودَ الثقة.
        if not (isinstance(hs_provenance, dict) and hs_provenance.get("hs6")):
            try:
                from silk_hs_confirm import revalidate
                _reval = revalidate(product, hs_code, hs_confidence)
                if _reval:
                    result["hs_revalidation"] = _reval
            except Exception as e:  # noqa: BLE001 — وسمٌ تحسينيّ لا يُسقِط تشغيلة
                log.warning("hs revalidation skipped: %s", e)
        if not ai_ok:
            result["ai_extras_note"] = ai_note
        # التدهور الفعلي = عدم الجهوزية (ready=False، بلاغ حي: _free_ai_
        # extras_allowed تعيد (True, "") حين لا مفتاح إطلاقاً — "لا قيد على
        # إضافات كلود" لأن /analyze لا يحتاجها أصلاً؛ ذلك المنطق يُخفي هنا
        # حقيقة أن /research **لا يعمل بلا مفتاح** رغم ai_ok=True ظاهرياً)
        # أو فشل الحجز الذرّي المتأخر رغم اجتياز البوابة (سباق نادر).
        if not ready or not ai_ok:
            result["degraded"] = True
            result["degraded_reason"] = (ai_note if not ai_ok else "") or ready_reason
        result["view"] = _view(result)

        # بوابة الجودة قبل التسليم (الموجة ١٠) — تعمل على القالب الموحّد
        # النهائي **قبل** أي عرض docx، فتلحَق نتيجتها بالتتبّع وبقسم
        # "منهجية البحث ونطاقه" داخل التقرير (طبقة العرض، silk_reports.py).
        _attach_quality_gate(result, research_run.get("trace_id"))
        _attach_watchdog(result, analysis_id, "research")
        return result

    def _finish_research_run(analysis_id: int | None, result: dict) -> None:
        """خزّن النتيجة النهائية وحدّث حالة التشغيلة — نفس معرّف الإنشاء
        (P0: نتيجة الاستئناف تنتهي بنفس analysis_id الذي بدأت به).

        `analysis_id` يُضاف لِـ`result` **قبل** التخزين لا بعده — بلاغ حي
        (اختبار استئناف تشغيلة مكتملة): إضافته بعد `save_analysis` تعني
        أن النسخة المخزَّنة لا تحمله أبداً، فقراءتها لاحقاً عبر استئناف أو
        `GET /analyses/{id}` تفتقد الحقل رغم ظهوره في الرد الأصلي المباشر.
        """
        if analysis_id is None:
            return
        result["analysis_id"] = analysis_id
        from silk_storage import save_analysis
        save_analysis(result, analysis_id=analysis_id)

    def _research_background(market_ref, product, hs_code, hs_note,
                             product_card_dict, ai_ok, ai_note, prefs,
                             ready, ready_reason, analysis_id,
                             resume_reports, report_style=None,
                             hs_confidence=None, hs_provenance=None) -> None:
        """جسم الخيط الخلفي (async_run=true) — يُغلَّف باستثناء شامل عمداً:
        خيط بايثون غير المُمسوك يفشل صامتاً (لا كسر عملية، لا تحديث حالة)
        فتبقى التشغيلة عالقة على 'running' للأبد — بلاغ التحقيق (P0) يمنع
        هذا صراحة. نقاط تفتيش البعثات المكتملة فعلاً تبقى مخزَّنة بصرف
        النظر عن نتيجة هذه المحاولة — استئناف لاحق يقرأها."""
        try:
            result = _run_research_pipeline(
                market_ref, product, hs_code, hs_note, product_card_dict,
                ai_ok, ai_note, prefs, ready, ready_reason, analysis_id,
                resume_reports, report_style, hs_confidence=hs_confidence,
                hs_provenance=hs_provenance)
            _finish_research_run(analysis_id, result)
        except Exception as e:  # noqa: BLE001 — خيط خلفي: هذا آخر حزام أمان
            log.error("background /research run %s failed: %s", analysis_id, e)
            from silk_storage import mark_research_failed, reconcile_failed_run_usd
            mark_research_failed(analysis_id, f"{type(e).__name__}: {e}")
            # البند #3 (تدقيق v2 الموجة ٢): تشغيلةٌ تفشل رشيقاً تُصالِح حجزها
            # الدولاري للفعلي-حتى-الآن، فلا يبقى محجوزاً يسدّ السقف حتى الدوران.
            reconcile_failed_run_usd(analysis_id)

    def _readiness_checks(product: str, market_ref, hs_code) -> list[dict]:
        """لوحةُ «جاهزية الدراسة» (Wave 1.5، عائلة D) — كلُّ تدهورٍ معروفٍ **قبل
        الحجز** كسطرٍ ✓/⚠/✗، فلا يعرف المالكُ تدهورًا **بعد** الدفع أبدًا.

        كلُّ سطر `{key, label_ar, status, detail_ar, blocking}`:
        status ∈ {ok, advisory, blocked, info}؛ blocking=True => يمنع التشغيل
        (رمز HS/خارج التغطية)؛ advisory => يتطلّب موافقة؛ info => إخباري فقط.
        قراءةٌ فقط — لا حجز ولا إنفاق. يشارك api البوّابةَ نفسها (مصدر واحد).
        """
        import datetime as _dt
        checks: list[dict] = []
        iso3 = getattr(market_ref, "iso3", "") or ""
        year = _dt.date.today().year - 1
        # (١) رمز HS محسوم — حجرُ الأساس (بوّابة صلبة).
        checks.append({
            "key": "hs_resolved", "label_ar": "رمز HS محسوم",
            "status": "ok" if hs_code else "blocked",
            "detail_ar": (f"HS {hs_code}" if hs_code
                          else "لم يُحسَم رمز HS — صنِّف المنتج أو اختر يدويًا"),
            "blocking": not bool(hs_code)})
        # (١ب) رمز HS **مؤكَّد دلالياً** — البلاغ الحيّ 2026-07-21 (زبدة الفول
        # السوداني/040510): رمزٌ محسومٌ لكنه خاطئ دلالياً كان يمرّ كـ«ok» لأن
        # اللوحة تفحص الحسم لا المطابقة. البوّابة (فشل-آمن) تحجبه الآن؛ فتظهر
        # هنا **قبل** الحجز اتّساقاً مع بوّابة `/research` (لا تدهورٌ بعد الدفع).
        if hs_code:
            from silk_hs_confirm import confirm_hs, is_flagged, gate_enabled
            _c = confirm_hs(product or "", hs_code)
            if is_flagged(_c):
                checks.append({
                    "key": "hs_confirmed", "label_ar": "مطابقة رمز HS للمنتج",
                    "status": "blocked" if gate_enabled() else "advisory",
                    "detail_ar": (f"رمز HS {hs_code} («{_c.get('code_desc')}») "
                                  "قد لا يطابق المنتج — صفة مميّزة غير مشمولة: "
                                  + "، ".join(_c.get("missing_terms") or [])),
                    "blocking": gate_enabled()})
        # (٢) السوق ضمن التغطية.
        from silk_market_ranker import _world_markets_enabled
        if _world_markets_enabled() and hs_code and iso3:
            _cov, _det = _market_in_coverage(hs_code, iso3)
            checks.append({
                "key": "coverage", "label_ar": "السوق ضمن التغطية",
                "status": "ok" if (not _det or _cov) else "blocked",
                "detail_ar": ("ضمن التغطية" if (not _det or _cov)
                              else "خارج التغطية الحالية — تواصل معنا لإضافتها"),
                "blocking": bool(_det and not _cov)})
        # (٣) استشارةُ بلد المنشأ (Wave 1) — سوقٌ من أكبر المصدّرين.
        if _producer_advisory_enabled() and hs_code and len(iso3) == 3:
            from silk_market_ranker import is_top_world_exporter
            _is_top, _top = is_top_world_exporter(hs_code, iso3, year)
            checks.append({
                "key": "producer_country", "label_ar": "بلد المنشأ",
                "status": "advisory" if _is_top else "ok",
                "detail_ar": ("من أكبر مصدّري هذا الرمز عالميًا — دخول تنافسي جدًّا"
                              if _is_top else "ليست من كبار المصدّرين"),
                "blocking": False})
        # (٣ب) معقولية بلد المورّد (البند أ٢) — إشارةٌ اقتصادية: هل ملفُّ موردي
        # السوق يطابق الرمز؟ تفكّكٌ شبه تامٌّ => تحذيرٌ استشاري (لا حجب نهائي).
        from silk_market_ranker import _a2_plausibility_enabled
        if _a2_plausibility_enabled() and hs_code and len(iso3) == 3:
            from silk_market_ranker import supplier_plausibility
            _a2r = supplier_plausibility(
                hs_code, iso3, getattr(market_ref, "m49", ""), year)
            if _a2r is not None:
                _bad = bool(_a2r.get("implausible"))
                checks.append({
                    "key": "supplier_plausibility",
                    "label_ar": "معقولية بلد المورّد",
                    "status": "advisory" if _bad else "ok",
                    "detail_ar": (
                        "موردو السوق الفعليون لا يطابقون أكبر مصدّري الرمز "
                        "عالميًا — راجع التصنيف" if _bad
                        else "ملفّ الموردين متّسقٌ مع مصدّري الرمز عالميًا"),
                    "blocking": False})
        # (٤) أشقّاء عائلة A (Wave 1.5) — بلد المنشأ نفسه/عقوبات/فصل مقيَّد.
        import silk_prerun
        if silk_prerun.advisories_enabled() and len(iso3) == 3:
            for a in silk_prerun.sibling_advisories(hs_code, iso3):
                checks.append({
                    "key": a["kind"],
                    "label_ar": {"self_origin": "بلد المنشأ نفسه",
                                 "sanction": "عقوبات/حظر",
                                 "restricted_chapter": "فئة مقيَّدة قانونيًا"
                                 }.get(a["kind"], a["kind"]),
                    "status": "advisory",
                    "detail_ar": a.get("detail") or a.get("message") or "",
                    "blocking": False})
        # (٥) ميزانية كومتريد (إخباري — لا يمنع، لكن يُعلَن قبل الدفع).
        try:
            from silk_collectors import comtrade_budget_left
            _left = comtrade_budget_left()
            checks.append({
                "key": "comtrade_budget", "label_ar": "ميزانية كومتريد اليومية",
                "status": "ok" if _left > 0 else "advisory",
                "detail_ar": f"المتبقّي ~{_left} نداء",
                "blocking": False})
        except Exception:  # noqa: BLE001 — قياس اختياري
            pass
        # (٦) حالة مكشطة الخرائط (إخباري).
        _scraper = bool(os.environ.get("SILK_GMAPS_SCRAPER_URL", "").strip())
        checks.append({
            "key": "scraper_state", "label_ar": "مكشطة جهات الاتصال",
            "status": "ok" if _scraper else "info",
            "detail_ar": ("مُهيَّأة" if _scraper
                          else "غير مُهيَّأة — هواتف/عناوين قد تغيب"),
            "blocking": False})
        # (٧) حماية المفاتيح المدفوعة (إخباري/تحذيري).
        _unprot = _unprotected_paid_keys()
        checks.append({
            "key": "key_protection", "label_ar": "حماية المفاتيح",
            "status": "advisory" if _unprot else "ok",
            "detail_ar": ("مفاتيح مدفوعة بلا SILK_API_KEY — نداءات محجوبة"
                          if _unprot else "محميّة"),
            "blocking": False})
        return checks

    @app.get("/research/readiness")
    def research_readiness(request: Request, product: str = "",
                           market: str = "", hs_code: str = ""):
        """جاهزيةُ الدراسة قبل الحجز — the pre-reservation readiness panel (D).

        قراءةٌ فقط (لا حجز/إنفاق): تُعيد كلَّ تدهورٍ معروفٍ كسطرٍ ✓/⚠/✗ +
        `can_run`/`needs_ack` كي تعرضها الواجهة **قبل زرّ التأكيد**. رمزُ HS
        يُحلّ حتميًا إن غاب (بلا نداء كلود)."""
        _rate_limit(request)
        from silk_market_resolver import resolve_market
        market_ref, suggestions = resolve_market(market) if market else (None, [])
        hs = (hs_code or "").strip()
        if not hs and product:
            from silk_hs_resolver import resolve as _rhs
            hs = _rhs(product).value or ""
        if market_ref is None:
            return _json({"checks": [{
                "key": "market", "label_ar": "السوق المستهدفة",
                "status": "blocked",
                "detail_ar": "سوق غير معروفة/غامضة — اختر من القائمة",
                "blocking": True}], "can_run": False, "needs_ack": False,
                "suggestions": suggestions})
        checks = _readiness_checks(product, market_ref, hs)
        return _json({
            "checks": checks,
            "can_run": not any(c["blocking"] for c in checks),
            "needs_ack": any(c["status"] == "advisory" for c in checks)})

    @app.post("/research")
    def research(req: ResearchRequest, request: Request):
        """بحث عميق — ١٢ بعثة كلود بالأدوات + محلل شامل + حكم + تقرير مراجَع.

        مسار مجاني حصراً (نموذج بلا حقول مدفوعة، كـ/analyze) — إضافات كلود
        (البعثات + المحلل + توليف المرحلة ٢ + الكاتب/المراجع) تمرّ عبر نفس
        بوابة H2 (`_free_ai_extras_allowed`) وحجزها الذرّي الواحد من
        SILK_PAID_DAILY_CAP (كنداء AI إضافي واحد على /analyz تماماً) — لا
        مسار كلود موازٍ. الحجم الفعلي لعدد النداءات عبر التحليل بأكمله
        محكوم بسقف منفصل (`SILK_RESEARCH_MAX_LLM_CALLS`/`_MAX_TOOL_CALLS`،
        افتراضياً ٤٠/١٠٠) يُطبَّق حياً داخل `silk_llm_runtime._run_loop` —
        إنهاء رشيق لا كسر عند تجاوزه، ومُلخَّص علوياً في
        `deep_research.budget_status` (P1، حادثة نفاد الاعتمادات).

        بوابة ما قبل التشغيل (بلاغ حي): كلود هنا **شرط تشغيل** لا تحسين
        اختياري — بلا مفتاح فعّال كل الاثنتي عشرة بعثة تفشل بصفر نتائج
        والتقرير هيكل فارغ. `_research_readiness()` يرفض هذا صراحة بـ409
        قبل تشغيل أي بعثة، إلا أن يمرّر الطالب `allow_degraded=true` —
        عندها تُشغَّل التشغيلة وتُوسَم النتيجة `degraded=true` مع سبب واضح
        (`degraded_reason`)، فتحمل كل مشتقات التقرير (docx/مختصر/لوحة)
        لافتة تحذير حمراء بدل تسليم هيكل فارغ بصمت كأنه المنتج.

        نقطة تفتيش/استئناف + تشغيل خلفي (P0، حادثة نفاد الاعتمادات —
        `docs/DEEP_RESEARCH_DECISIONS.md`): `persist=true` (الافتراضي)
        يخصّص `analysis_id` **قبل** تشغيل أي بعثة، وكل بعثة تُخزَّن فور
        اكتمالها — عطل/إعادة نشر منتصف الطريق لا يخسر البعثات المكتملة.
        `resume=<analysis_id>` يعيد فتح تشغيلة سابقة (مكتملة/فاشلة/عالقة)
        ويُشغّل فقط البعثات الناقصة + المحلل/الكاتب من جديد؛ إن كانت
        مكتملة أصلاً يعيدها كما هي بلا أي نداء كلود جديد (لا حرق اعتمادات
        مضاعف). `async_run=true` يعيد `{analysis_id, status:"running"}`
        فوراً (202) ويكمل المعالجة في خيط خلفي — تجاوز مهلة بوّابة/وكيل
        عكسي يعود يقطع اتصال العميل فقط، لا التشغيلة نفسها، ولا يُيتّمها:
        استطلع `GET /research/{analysis_id}/status` حتى `status=="completed"`
        ثم `GET /analyses/{analysis_id}` للنتيجة الكاملة.
        """
        _require_key(request)
        _rate_limit(request)

        resume_reports: dict | None = None
        analysis_id: int | None = None
        stored_request: dict = {}

        if req.resume is not None:
            from silk_storage import get_analysis, get_research_run, \
                load_mission_checkpoints, mission_status_map
            run_row = get_research_run(req.resume)
            if run_row is None:
                raise HTTPException(status_code=404, detail={
                    "error": "resume_not_found",
                    "reason": f"research run {req.resume} not found"})
            if run_row.get("kind") != "research":
                raise HTTPException(status_code=400, detail={
                    "error": "not_a_research_run",
                    "reason": f"analysis {req.resume} is not a /research "
                              "run — resume only applies to /research"})
            stored_request = run_row.get("request") or {}
            # بوّابة نطاق السوق (البلاغ الحي — تسرّب اليمن↔الكويت،
            # 2026-07-21) — **تسبق** مسار «مكتملة => أعِدها كما هي» أدناه
            # عمداً: ذلك المسار كان يُعيد نتيجة اليمن المخزَّنة بصمتٍ متجاهلاً
            # `req.market="Kuwait"` (لا تسريب تسمية، لكن تجاهل صامت لطلب
            # المستخدم يخفي بالضبط الخطأ الذي أدّى للحادثة الحية عبر مسارٍ
            # آخر — تشغيلة غير مكتملة استؤنفت بسوقٍ مختلف). رفضٌ صريح هنا
            # أوضح من إرجاعٍ صامت لبيانات سوقٍ لم يُطلَب.
            _stored_iso3 = stored_request.get("market_iso3")
            if req.market and _stored_iso3:
                from silk_market_resolver import resolve_market as _rm_check
                _req_ref, _ = _rm_check(req.market)
                if (_req_ref is not None and _req_ref.iso3
                        and _req_ref.iso3 != _stored_iso3):
                    raise HTTPException(status_code=409, detail={
                        "error": "resume_market_mismatch",
                        "reason": (f"analysis {req.resume} was created for "
                                  f"market {_stored_iso3}, not "
                                  f"{_req_ref.iso3} — resuming under a "
                                  "different market would reuse that "
                                  "market's mission checkpoints. start a "
                                  "fresh /research run instead."),
                        "stored_market_iso3": _stored_iso3,
                        "requested_market_iso3": _req_ref.iso3})
            if run_row.get("status") == "completed":
                # مكتملة فعلاً — أعِدها كما هي، لا إعادة تشغيل ولا حرق
                # اعتمادات إضافي (استئناف مكتمل يجب أن يكون آمناً للتكرار)
                # — **إلا** إن حملت التشغيلة بعثةً نقطتها فاشلة (بلاغ
                # Nadec/اليمن #7): التشغيلة تُوسَم "completed" حتى لو فشلت
                # بعثة (429/مهلة)، فكان هذا المسار يُعيد النتيجة المخزَّنة
                # بصمتٍ ولا يبلغ `run_all_missions` حيث تُعاد الفاشلة (#180)
                # — فبدا `resume` بلا أثر (updated_at ثابت، صفر نداء، تكلفة
                # كما هي). الآن: وجود أيّ بعثة فاشلة يُسقط مسار الإعادة
                # الصامتة فتُستأنف التشغيلة فعلاً وتُعاد الفاشلة وحدها؛
                # التشغيلة الناجحة كاملةً تبقى إعادةَ تسليمٍ صرفةً (idempotent).
                _status_map = mission_status_map(req.resume)
                _has_failed = any(v == "failed" for v in _status_map.values())
                if not _has_failed:
                    existing = get_analysis(req.resume)
                    if existing is not None:
                        return _json(existing)
            analysis_id = req.resume
            # نقاط تفتيش البعثات تُحمَّل **بعد** حسم السوق أدناه (لا هنا) —
            # كي تُفلتَر بسوق التشغيلة المحسوم لا تُقرأ خاماً هنا. راجع
            # البوّابة أسفل حسم market_ref.

        product = req.product or stored_request.get("product")
        market_name = req.market or stored_request.get("market")
        if not product or not market_name:
            raise HTTPException(status_code=422, detail={
                "error": "product_and_market_required",
                "reason": "product/market are required unless resuming an "
                         "existing analysis_id that already has them"})

        from silk_market_resolver import resolve_market
        market_ref, suggestions = resolve_market(market_name)
        if market_ref is None:
            raise HTTPException(status_code=422, detail={
                "error": f"unknown or ambiguous market {market_name!r}",
                "suggestions": suggestions})

        if req.resume is not None:
            # نقاط تفتيش البعثات تُحمَّل هنا بعد حسم `market_ref` — مُفلترة
            # بسوقه (شبكة أمان بنيوية، `silk_storage.load_mission_checkpoints`)
            # فوق بوّابة الرفض الصريحة أعلاه (البلاغ الحي — تسرّب اليمن↔الكويت):
            # حتى لو مرّت بوّابة الرفض بطريقةٍ ما (تعارض غير مُكتشَف)، أيّ صفّ
            # مختوم بسوقٍ آخر لن يُعاد من المخزن أصلاً.
            from silk_storage import load_mission_checkpoints
            resume_reports = load_mission_checkpoints(
                req.resume, market_iso3=market_ref.iso3)

        # ── تحديد رمز HS مرّةً واحدة قبل أيّ بوّابة/حجز (Wave 1) ──────────────
        # نظامٌ عام: كلُّ البوّابات (التغطية، بلد المنشأ، الحجز الدولاري) تعمل
        # على رمزٍ محسوم — لا دراسةَ على HS مجهول. صريحٌ/مخزَّن أولًا، وإلا
        # المُحلِّل الحتمي (لا اختلاق رمز عند فشله — فجوة معلنة في hs_note).
        hs_code = req.hs_code or stored_request.get("hs_code")
        hs_note = None
        # ثقةُ التصنيف تُحمَل ولا تُهدَر (بلاغ «حليب نادك»): كان يُؤخَذ
        # `dp.value` وحده وتُرمى `dp.confidence`، فلا تصل النتيجةَ إطلاقاً —
        # ومن هناك جاء «ثقة التصنيف —» في جدول التقرير (silk_render.py:2046
        # يقرأ `result["hs_confidence"]` الغائب فيعرض شرطة الفجوة). رمزٌ
        # صريحٌ/مخزَّن = اختيارُ مستخدمٍ معلومُ المصدر (1.0)، كعقد المحرّك.
        hs_confidence = 1.0 if hs_code else None
        if not hs_code and product:
            from silk_hs_resolver import resolve as resolve_hs
            dp = resolve_hs(product)
            hs_code = dp.value
            hs_confidence = dp.confidence if dp.value is not None else None
            if hs_code is None:
                hs_note = dp.note  # فجوة معلنة — لا اختلاق رمز HS

        # بوّابة HS الصلبة (Wave 1، عائلة unresolved-hs-silent-spend): رفضُ
        # الحجز/الإنفاق ما دام hs6 فارغًا — 422 **قبل** أيّ تفعيلة أو دولار.
        # تُغلق حادثةَ الفيتوتشيني (أُنفِق $ والغلاف «—» والركيزة التجارية فجوة
        # حرجة): لا تعود ممكنة حين يُفعَّل الصمّام. المُصنِّف (`/classify_hs`)
        # يمنع الوصولَ لهنا فارغًا في التدفّق. مُطفأ افتراضيًا (SILK_REQUIRE_HS6)
        # => السلوك كاليوم. يُتخطّى عند الاستئناف (رمزُه محسومٌ وقت الإنشاء).
        if req.resume is None and not hs_code and _require_hs6():
            import silk_ops_log
            silk_ops_log.record_error(
                "unresolved_hs_blocked",
                f"رُفض بدءُ بحثٍ برمز HS غير محسوم لمنتج {product!r} "
                f"(السوق {market_ref.iso3})",
                context={"product": product, "market_iso3": market_ref.iso3,
                         "hs_note": hs_note})
            raise HTTPException(status_code=422, detail={
                "error": "unresolved_hs",
                "message": "تعذّر تحديد رمز HS لهذا المنتج — صنِّفه أولًا "
                           "(/classify_hs) أو اختر الرمز يدويًا قبل بدء البحث.",
                "hs_note": hs_note})

        # بوّابة تأكيد رمز HS (Wave 1.2، عائلة unresolved-hs-silent-spend
        # موسَّعةً — تدقيق زبدة الفول السوداني/اليمن؛ الموجة ٢ (2026-07-21):
        # المنطق يعيش الآن في `silk_hs_confirm.preflight_block` — نقطة
        # اختناق واحدة يستدعيها **كل** من `/research` و`/analyze` (أدناه)
        # بلا نسخ؛ إصلاحٌ سابق على `/research` وحده عاود الظهور فتوحّد
        # هنا). رمزٌ محسومٌ لكنه **خاطئ دلالياً** يُوقِف **قبل** أيّ حجز/دولار
        # ويطلب تأكيد المستخدم. فشل-آمن: مفعّلة افتراضياً (`gate_enabled`).
        # تُتخطّى عند الاستئناف وعند تأكيد المستخدم الصريح (hs_confirmed=True).
        hs_provenance: dict | None = None
        if req.resume is None:
            # بلاغ المُشرِف: تُقاس العتبةُ الرقمية (بطاقةُ العبوة ثم استعلامُ
            # ويبٍ واحد) قبل عرض أيّ حوار — نفسُ نقطة الاختناق التي يستدعيها
            # `/analyze` (لا إصلاحَ على مسارٍ واحد، الدرسان ٣٥/٣٧).
            from silk_hs_confirm import preflight_resolve
            _resolved_hs, hs_provenance, _blocked = preflight_resolve(
                product, hs_code, getattr(req, "hs_confirmed", False),
                allow_claude=_classify_general_allow_claude(),
                # بوّابة الثقة تُفحَص هنا أيضاً — **قبل** الحجز والدولار.
                hs_confidence=hs_confidence,
                label_attributes=req.label_attributes,
                gl=(market_ref.iso2 or None))
            if hs_provenance is not None:
                # رمزٌ مقيسٌ بدليلٍ موسوم يحلّ محلّ الملتبِس، وثقتُه من دليله
                # لا من مطابقةٍ لفظية (صورةُ عبوةٍ ١٫٠، رابطُ ويبٍ ثانويّ).
                hs_code = _resolved_hs
                hs_confidence = hs_provenance.get("confidence")
                import silk_ops_log
                silk_ops_log.record_error(
                    "hs_auto_resolved",
                    f"حُسِم رمزُ HS آلياً لمنتج {product!r} => {hs_code} "
                    f"({hs_provenance.get('resolved_from')})",
                    context={"product": product, "hs_code": hs_code,
                             "market_iso3": market_ref.iso3,
                             "resolved_from": hs_provenance.get("resolved_from"),
                             "value": hs_provenance.get("value"),
                             "source_url": hs_provenance.get("source_url")})
            if _blocked is not None:
                import silk_ops_log
                # البوّابة صارت بوّابتين (ثقة + تطابق دلالي) بشكلَي ردٍّ
                # مختلفَين — السجلّ يقرأ ما هو موجود فعلاً، لا مفتاحاً بعينه.
                _conf_detail = _blocked.get("hs_confirmation") or {}
                silk_ops_log.record_error(
                    _blocked.get("error") or "hs_confirmation_blocked",
                    f"رُفض بدءُ بحثٍ برمز HS غير محسوم لمنتج {product!r}: "
                    f"{_conf_detail.get('reason') or _blocked.get('message')}",
                    context={"product": product, "hs_code": hs_code,
                             "market_iso3": market_ref.iso3,
                             "hs_confidence": _blocked.get("hs_confidence"),
                             "min_confidence": _blocked.get("min_confidence"),
                             "attribute_probe": _blocked.get("attribute_probe"),
                             "missing_terms": _conf_detail.get("missing_terms")})
                raise HTTPException(status_code=422, detail=_blocked)

        # بوّابةُ الالتباس داخل الترويسة (البند ٣ — تحصينٌ وقائيّ لا إصلاحُ
        # عطلٍ مُثبَت). تصحيحُ المالك مُسجَّل: حادثةُ «نادك» **لم تكن** التباساً
        # — `resolve` أعاد 040120 الصحيح؛ الرمزُ الخاطئ دخل عبر مسارٍ بلا
        # بوّابة (أُغلِق في البند ١). هذه البوّابة تعالج خطراً **مختلفاً** لم
        # يقع بعد: 040110/040120/040150 تتمايز بنسبةِ دسمٍ رقمية لا بكلمة،
        # فالمُحلِّل الحتمي قد يحسم واحداً منها بثقةٍ عالية وهو مخطئ.
        # `silk_hs_classifier.classify_general` يملك القاعدةَ الصحيحة أصلاً
        # (`_clearly_auto`: لا حسمَ تلقائيّ حين يتقارب المرشّحان ضمن
        # `_AUTO_MARGIN`) — لكنّ المسارَين كانا يتجاوزانه إلى `resolve` المجرّد.
        # هنا يُوصَل على `/research` **وحده** (المسار الأغلى؛ سؤالٌ واحدٌ قبله
        # أرخص من تقريرٍ كاملٍ على رمزٍ خاطئ)، وبلا أيّ نداء كلود
        # (`allow_claude=False`) فيبقى مجانياً وحتمياً.
        # صمّام: SILK_RESEARCH_AMBIGUITY_GATE=0 يعطّله.
        if (req.resume is None and not req.hs_code
                and not getattr(req, "hs_confirmed", False)
                and _research_ambiguity_gate_enabled()):
            import silk_hs_classifier as _hsc
            _cls = _hsc.classify_general(product or "", allow_claude=False)
            if _cls.get("tier") != "auto" and (_cls.get("candidates") or []):
                import silk_ops_log
                # بلاغ المُشرِف — هذه هي البوّابةُ التي أنتجت السؤالَ حرفياً
                # («إن كان … كامل الدسم (أكثر من ٦٪)»). العتبةُ التي كانت
                # تُسأل تُقاس هنا أولاً: بطاقةُ العبوة ثم استعلامُ ويبٍ واحد.
                from silk_hs_confirm import resolve_or_probe, _probe_public
                _probe = resolve_or_probe(
                    product or "", _cls.get("candidates") or [],
                    label_attributes=req.label_attributes,
                    gl=(market_ref.iso2 or None))
                if _probe.get("hs6"):
                    hs_code = _probe["hs6"]
                    hs_confidence = _probe.get("confidence")
                    hs_provenance = _probe
                    silk_ops_log.record_error(
                        "hs_auto_resolved",
                        f"حُسِم التباسُ رمز HS آلياً لمنتج {product!r} => "
                        f"{hs_code} ({_probe.get('resolved_from')})",
                        context={"product": product, "hs_code": hs_code,
                                 "market_iso3": market_ref.iso3,
                                 "resolved_from": _probe.get("resolved_from"),
                                 "value": _probe.get("value"),
                                 "source_url": _probe.get("source_url")})
                else:
                    silk_ops_log.record_error(
                        "hs_ambiguous_blocked",
                        f"رُفض بدءُ بحثٍ لالتباسِ رمز HS لمنتج {product!r} — "
                        f"مرشّحون: "
                        f"{[c.get('hs6') for c in _cls['candidates']]}",
                        context={"product": product, "hs_code": hs_code,
                                 "market_iso3": market_ref.iso3,
                                 "attribute_probe": _probe_public(_probe),
                                 "tier": _cls.get("tier")})
                    raise HTTPException(status_code=422, detail={
                        "error": "hs_ambiguous",
                        "message": (
                            f"«{product}» يطابق أكثر من رمزٍ مقبول بفارقٍ غير "
                            "حاسم — والفارقُ بين البنود قياسٌ رقميّ حاولنا "
                            "قراءته من صورة العبوة ومن مصادر الويب ولم نجده. "
                            "أرفِق صورة العبوة أو اختر البند المطابق أدناه."),
                        "hs_code_deterministic": hs_code,
                        "candidates": _cls.get("candidates") or [],
                        "candidates_source": _cls.get("source"),
                        "attribute_probe": _probe_public(_probe),
                    })

        # بوابة «خارج التغطية» (اتفاق المالك) — تسبق الجهوزية/الحجز: مع تفعيل
        # تغطية العالم، سوقٌ ليس Tier-1 ولا ضمن مجموعة أكبر مستوردي هذا الرمز
        # (Tier-2 الديناميكية) يُعاد برسالةٍ صادقة «تواصل معنا لإضافتها» بدل
        # دراسةٍ هزيلة، ويُسجَّل إشارةَ طلبٍ في سجلّ العمليات (طلب فعلي غير مغطّى).
        # الصمّام مُطفأ => السلوك كاليوم (أيّ دولة تعمل، فجوات معلنة) بلا انحدار.
        from silk_market_ranker import _world_markets_enabled
        if _world_markets_enabled():
            _covered, _determinable = _market_in_coverage(
                hs_code, market_ref.iso3)
            if _determinable and not _covered:
                import silk_ops_log
                silk_ops_log.record_error(
                    "out_of_coverage_demand",
                    f"طلب بحث لسوقٍ خارج التغطية الحالية: "
                    f"{market_ref.name_en} ({market_ref.iso3}) "
                    f"لرمز HS {hs_code}",
                    context={"product": product,
                             "market_iso3": market_ref.iso3,
                             "hs_code": hs_code})
                raise HTTPException(status_code=422, detail={
                    "error": "out_of_coverage",
                    "message": "هذه السوق خارج التغطية الحالية — "
                               "تواصل معنا لإضافتها"})

        # استشارة بلد المنشأ (Wave 1، قاعدة عامّة مبنيّة على البيانات): سوقٌ من
        # أكبر مصدّري هذا الرمز عالميًا => تحذيرٌ استشاري (422) حتى موافقةٍ صريحة
        # (`producer_ack`). زيرو نداء مدفوع (كلود/سقف) — كومتريد فقط بميزانيته.
        # الاستشارة تُسجَّل (shown/consent). الصمّام مُطفأ (SILK_PRODUCER_ADVISORY)
        # => السلوك كاليوم. يُتخطّى عند الاستئناف (وافق المستخدم وقت الإنشاء).
        if req.resume is None and hs_code and _producer_advisory_enabled():
            import silk_ops_log
            import datetime as _dt
            from silk_market_ranker import is_top_world_exporter
            _is_top, _top = is_top_world_exporter(
                hs_code, market_ref.iso3, _dt.date.today().year - 1)
            if _is_top and not req.producer_ack:
                silk_ops_log.record_error(
                    "producer_advisory_shown",
                    f"استشارةُ بلد المنشأ: {market_ref.iso3} من أكبر مصدّري "
                    f"HS {hs_code} عالميًا — دراسةُ دخولها تنافسية جدًّا",
                    context={"product": product,
                             "market_iso3": market_ref.iso3, "hs_code": hs_code,
                             "top_exporters": [t["iso3"] for t in _top]})
                raise HTTPException(status_code=422, detail={
                    "error": "producer_country_advisory",
                    "message": "⚠ هذه الدولة من أكبر مصدّري هذا المنتج عالميًا "
                               "— دراسة دخولها تنافسية جدًّا. أكمل؟",
                    "top_exporters": [t["iso3"] for t in _top],
                    "needs_ack": True})
            if _is_top and req.producer_ack:
                silk_ops_log.record_error(
                    "producer_advisory_consent",
                    f"موافقةٌ صريحة على دراسة {market_ref.iso3} (من أكبر "
                    f"مصدّري HS {hs_code} عالميًا)",
                    context={"product": product,
                             "market_iso3": market_ref.iso3, "hs_code": hs_code})

        # البند أ٢ — معقولية بلد المورّد (إشارةٌ اقتصاديةٌ مُعاضِدةٌ لتأكيد الرمز):
        # تفكّكٌ شبه تامٌّ بين موردي السوق الفعليين وأكبر مصدّري الرمز عالميًا =>
        # الرمز قد يصف عائلةً مختلفة (حادثة زبدة الفول السوداني/الألبان). تحذيرٌ
        # حاجبٌ (422) حتى موافقةٍ صريحة (`a2_ack`) تؤكّد الرمز أو تعيد التصنيف —
        # لا رفضٌ نهائي (قد يعرف المستخدم رمزه صحيح). صفر نداء مدفوع (كومتريد
        # فقط بميزانيته). مُطفأ افتراضيًا (SILK_A2_PLAUSIBILITY) => السلوك كاليوم.
        # يُتخطّى عند الاستئناف (وافق المستخدم وقت الإنشاء). المذكّرة:
        # docs/DESIGN_A2_SUPPLIER_PLAUSIBILITY.md.
        if req.resume is None and hs_code:
            from silk_market_ranker import (_a2_plausibility_enabled,
                                            supplier_plausibility)
            if _a2_plausibility_enabled():
                import datetime as _dt2
                _a2 = supplier_plausibility(
                    hs_code, market_ref.iso3, market_ref.m49,
                    _dt2.date.today().year - 1)
                if _a2 and _a2.get("implausible") and not req.a2_ack:
                    import silk_ops_log
                    silk_ops_log.record_error(
                        "a2_plausibility_shown",
                        f"معقولية بلد المورّد ({market_ref.iso3}): موردو السوق "
                        f"{_a2['market_suppliers']} لا يظهرون بين أكبر مصدّري "
                        f"HS {hs_code} عالميًا {_a2['world_exporters']}",
                        context={"product": product,
                                 "market_iso3": market_ref.iso3,
                                 "hs_code": hs_code, "overlap": _a2["overlap"]})
                    raise HTTPException(status_code=422, detail={
                        "error": "supplier_plausibility_advisory",
                        "message": "⚠ كبار موردي هذه السوق لهذا الرمز لا يظهرون "
                                   "بين أكبر مصدّري الرمز عالميًا — قد يكون الرمز "
                                   "يصف عائلة منتجٍ مختلفة. راجع التصنيف أو أكّد "
                                   "الرمز للمتابعة.",
                        "market_suppliers": _a2["market_suppliers"],
                        "world_exporters": _a2["world_exporters"],
                        "overlap": _a2["overlap"],
                        "needs_ack": True})
                if _a2 and _a2.get("implausible") and req.a2_ack:
                    import silk_ops_log
                    silk_ops_log.record_error(
                        "a2_plausibility_consent",
                        f"موافقةٌ صريحة على رمز HS {hs_code} رغم تحذير معقولية "
                        f"بلد المورّد ({market_ref.iso3})",
                        context={"product": product,
                                 "market_iso3": market_ref.iso3,
                                 "hs_code": hs_code})

        # أشقّاء عائلة «الدراسة بالاتجاه الخاطئ» (Wave 1.5، عائلة A): تصدير إلى
        # بلد المنشأ / سوق تحت عقوبات / فصل مقيَّد قانونيًا — config-driven، صفر
        # نداء مدفوع. تحذيرٌ (422) حتى موافقةٍ موحّدة (`advisories_ack`). مُطفأ
        # افتراضيًا (SILK_PRERUN_ADVISORIES) => السلوك كاليوم. يُتخطّى عند الاستئناف.
        if req.resume is None:
            import silk_prerun
            if silk_prerun.advisories_enabled():
                _sib = silk_prerun.sibling_advisories(hs_code, market_ref.iso3)
                if _sib and not req.advisories_ack:
                    import silk_ops_log
                    silk_ops_log.record_error(
                        "prerun_advisory_shown",
                        f"استشارةُ ما قبل التشغيل ({market_ref.iso3}): "
                        + "؛ ".join(a["kind"] for a in _sib),
                        context={"product": product,
                                 "market_iso3": market_ref.iso3,
                                 "hs_code": hs_code,
                                 "kinds": [a["kind"] for a in _sib]})
                    raise HTTPException(status_code=422, detail={
                        "error": "prerun_advisory",
                        "message": _sib[0]["message"],
                        "advisories": _sib, "needs_ack": True})
                if _sib and req.advisories_ack:
                    import silk_ops_log
                    silk_ops_log.record_error(
                        "prerun_advisory_consent",
                        f"موافقةٌ صريحة على أشقّاء استشارة {market_ref.iso3}",
                        context={"product": product,
                                 "market_iso3": market_ref.iso3,
                                 "hs_code": hs_code,
                                 "kinds": [a["kind"] for a in _sib]})

        # بوابة ما قبل التشغيل بعد التحقق من صحة الإدخال (422 على خطأ
        # الطالب يسبق 409 على جهوزية الخادم — خطأ العميل يستحق أن يُشرَح
        # حتى لو كان الخادم متدهوراً الآن) وقبل تشغيل أي بعثة أو حجز ميزانية.
        ready, ready_reason = _research_readiness()
        if not ready and not req.allow_degraded:
            raise HTTPException(status_code=409, detail={
                "error": "research_not_ready", "reason": ready_reason,
                "hint": "اضبط ANTHROPIC_API_KEY (وSILK_API_KEY إن لزم) ثم "
                        "أعد المحاولة، أو مرّر allow_degraded=true لتسليم "
                        "تقرير موسوم صراحة كمتدهور (غير مُنصَح به تسليمياً)."})

        # H6 (تدقيق): بوابة الميزانية الدولارية اليومية — تحجز تكلفة التشغيلة
        # المتوقَّعة ذرّيًا قبل بدئها (try_reserve_usd، لا فحص قراءة فقط)، فلا
        # يمكن لتشغيلتين متزامنتين قرب السقف أن تمرّا معًا وتتجاوزا الحدّ (سباق
        # TOCTOU مسدود — نفس نمط عدّاد التفعيلات الذرّي). السقف غير مضبوط => لا
        # حجب. الإنفاق الفعلي يُصالَح بعد التشغيلة في _run_research_pipeline
        # (reconcile_usd) فيحمل الدفتر المُنفَق الحقيقي لا التقدير. تسبق حجز
        # عدّاد التفعيلات كي لا تُستهلك تفعيلة على طلب مرفوض. الاستئناف المكتمل
        # رجع مبكراً قبل هنا.
        _expected_usd = float(os.environ.get("SILK_RESEARCH_EXPECTED_USD", "3.0"))
        if not silk_usage.try_reserve_usd(_expected_usd):
            # ITEM 5ب: رفض حجز بحالة السقف — نص خادمي بحت، لا محتوى كلود.
            import silk_ops_log
            silk_ops_log.record_error(
                "reservation_refused",
                f"الميزانية اليومية بالدولار أوشكت على النفاد — "
                f"أُنفِق {round(silk_usage.usd_spent_today(), 2)}$ اليوم",
                context={"expected_usd": _expected_usd,
                        "spent_today_usd": round(silk_usage.usd_spent_today(), 2)})
            raise HTTPException(status_code=429, detail={
                "error": "daily_usd_budget_exhausted",
                "reason": f"الميزانية اليومية بالدولار أوشكت على النفاد — "
                          f"أُنفِق {round(silk_usage.usd_spent_today(), 2)}$ اليوم؛ "
                          f"تشغيلة /research متوقَّعة بنحو {_expected_usd}$."})

        # (رمز HS + hs_note حُسِما أعلاه قبل البوّابات والحجز — Wave 1.)
        ai_ok, ai_note = _free_ai_extras_allowed()
        prefs = _clean_agent_prefs(req.agent_prefs)
        if prefs is None:
            prefs = stored_request.get("agent_prefs") or _saved_agent_settings()

        # بطاقة المنتج — بلاغ حي (الموجة ٩): كانت تُقبَل في النموذج ولا تصل
        # أي بعثة أو المحلل إطلاقاً، فيغيب "الموقع التنافسي"/هامش المضاهاة
        # من كل تقرير بحث عميق رغم إرسال المستخدم للبطاقة فعلياً.
        product_card_dict = (req.product_card.model_dump() if req.product_card
                             else stored_request.get("product_card"))
        own_price = (req.own_price if req.own_price is not None
                    else stored_request.get("own_price"))
        if product_card_dict is not None and own_price is not None:
            product_card_dict = dict(product_card_dict)
            product_card_dict["own_price"] = own_price

        if analysis_id is None and req.persist:
            from silk_storage import create_research_run
            request_snapshot = {
                "product": product, "market": market_name,
                # الموجة ٢ (بوّابة نطاق السوق أعلاه): iso3 مُخزَّن بنيوياً —
                # لا استنتاج لاحق من اسمٍ عربي/إنجليزي غامض عند الاستئناف.
                "market_iso3": market_ref.iso3, "hs_code": hs_code,
                "product_card": product_card_dict, "own_price": own_price,
                "agent_prefs": prefs, "allow_degraded": req.allow_degraded}
            analysis_id = create_research_run(
                product, market_ref.iso3, hs_code, request_snapshot,
                market_name=market_ref.name_ar or market_ref.name_en)

        if req.async_run:
            if analysis_id is None:
                raise HTTPException(status_code=400, detail={
                    "error": "async_requires_persist",
                    "reason": "async_run=true needs persist=true (or an "
                             "existing resume target) — otherwise there is "
                             "no analysis_id to poll a status for."})
            threading.Thread(
                target=_research_background,
                args=(market_ref, product, hs_code, hs_note, product_card_dict,
                     ai_ok, ai_note, prefs, ready, ready_reason, analysis_id,
                     resume_reports, req.report_style, hs_confidence,
                     hs_provenance),
                daemon=True).start()
            return JSONResponse(status_code=202, content={
                "analysis_id": analysis_id, "status": "running",
                "async": True,
                "poll_url": f"/research/{analysis_id}/status"})

        try:
            result = _run_research_pipeline(
                market_ref, product, hs_code, hs_note, product_card_dict,
                ai_ok, ai_note, prefs, ready, ready_reason, analysis_id,
                resume_reports, req.report_style, hs_confidence=hs_confidence,
                hs_provenance=hs_provenance)
        except Exception as e:  # noqa: BLE001 — P0: فشل لا يخسر البعثات المكتملة
            log.error("sync /research run %s failed: %s", analysis_id, e)
            if analysis_id is not None:
                from silk_storage import (mark_research_failed,
                                          reconcile_failed_run_usd)
                mark_research_failed(analysis_id, f"{type(e).__name__}: {e}")
                reconcile_failed_run_usd(analysis_id)  # البند #3 الموجة ٢
                raise HTTPException(status_code=500, detail={
                    "error": "research_run_failed",
                    "reason": f"{type(e).__name__}: {e}",
                    "analysis_id": analysis_id,
                    "hint": f"البعثات المكتملة قبل العطل محفوظة — أعد "
                            f"المحاولة بـ resume={analysis_id} بدل تشغيلة "
                            "كاملة جديدة (لا حرق اعتمادات مضاعف)."}) from e
            raise
        _finish_research_run(analysis_id, result)  # no-op إن persist=false
        return _json(result)

    # تسمية عربية للمرحلة الحيّة (GET /status) — مرآة نصّية لقيم `stage` التي
    # تُسجَّلها silk_context.snapshot_research_progress (missions/analyst/
    # writer/reviewer/done). قيمة غير معروفة (تشغيلة قديمة قبل هذه الميزة،
    # بلا لقطة بعد) تعرض None لا تسمية مُخترَعة.
    _STAGE_LABEL_AR = {
        "missions": "بحث البعثات", "analyst": "تحليل شامل",
        "enrich_leads": "إكمال بيانات المستوردين",
        "writer": "كتابة التقرير", "reviewer": "مراجعة التقرير",
        "done": "اكتمل"}

    @app.get("/research/{analysis_id}/status")
    def research_status(analysis_id: int, request: Request):
        """حالة تشغيلة بحث عميق — تقدّم لكل بعثة من الاثنتي عشرة + الحالة
        العامة (P0، حادثة نفاد الاعتمادات) — اللوحة تستطلعها دورياً بدل
        انتظار اتصال HTTP واحد طويل قد تقطعه بوّابة عكسية.

        تقدّم حيّ (المرحلة/الزمن المنقضي/التكلفة حتى الآن): من لقطة
        `silk_storage.get_research_progress` — نفس عدّادات `data_economics`
        النهائية نفسها تُقرأ أثناء التشغيل بدل عدّاد جديد. التكلفة **مُقدَّرة
        من دفتر أسعار مُسعَّر فقط** (`silk_pricing`) — نموذج غير مُسعَّر يظهر
        في `cost_unpriced_models` صراحةً بدل أن يُحتسَب صفراً بصمت (لا اختلاق).
        """
        _require_key(request)
        _rate_limit(request)
        from silk_missions import MISSION_ORDER
        from silk_storage import (get_research_run, mission_status_map,
                                  get_research_progress)
        run_row = get_research_run(analysis_id)
        if run_row is None or run_row.get("kind") != "research":
            raise HTTPException(status_code=404,
                                detail=f"research run {analysis_id} not found")
        done = mission_status_map(analysis_id)
        missions = {key: done.get(key, "pending") for key in MISSION_ORDER}
        progress = get_research_progress(analysis_id)
        stage = progress.get("stage")
        elapsed_seconds = None
        started_at = progress.get("started_at")
        if started_at:
            try:
                import datetime as _dt
                elapsed_seconds = round(
                    (_dt.datetime.now() - _dt.datetime.fromisoformat(started_at))
                    .total_seconds())
            except Exception:  # noqa: BLE001 — طابع زمني فاسد = فجوة لا استثناء
                elapsed_seconds = None
        return _json({
            "analysis_id": analysis_id, "status": run_row.get("status"),
            "product": run_row.get("product"), "hs_code": run_row.get("hs_code"),
            "created_at": run_row.get("created_at"),
            "updated_at": run_row.get("updated_at"),
            "stage": stage, "stage_label": _STAGE_LABEL_AR.get(stage),
            "elapsed_seconds": elapsed_seconds,
            "llm_calls": progress.get("llm_calls"),
            "tool_calls": progress.get("tool_calls"),
            "cost_usd_estimate": progress.get("cost_usd_estimate"),
            "cost_unpriced_models": progress.get("cost_unpriced_models") or [],
            "missions": missions,
            "missions_completed": sum(1 for v in missions.values()
                                      if v != "pending"),
            "missions_total": len(missions),
        })

    class DiscoverRequest(BaseModel):
        """طلب اكتشاف الفرص المعكوس (الموجة ٥أ، vision §11) — سوق بدل منتج."""
        market_iso3: str
        year: int | None = None
        sector: str | None = None          # food|textile|industrial|None=الكل
        min_import_usd: float = 0.0
        with_seasonality: bool = False     # pytrends — تكميلية بوزن أدنى

    @app.post("/discover")
    def discover(req: DiscoverRequest, request: Request):
        """اكتشف فرص سوق — reverse discovery: "ما المطلوب في هذا السوق؟"

        مجاني (Comtrade + trends القائمان — صفر مصادر جديدة، §11.5-4)؛
        حارس المصادقة يعمل قبل أي جلب. كل فرصة تحمل hs_code يُمرَّر
        مباشرة إلى /analyze أو /deepen (زر "حلّل هذه الفرصة"، §11.5-3).
        """
        _require_key(request)
        _rate_limit(request)
        import silk_discovery
        return _json(silk_discovery.discover(
            req.market_iso3, req.year, sector=req.sector,
            min_import_usd=req.min_import_usd,
            with_seasonality=req.with_seasonality))

    class TrendRequest(BaseModel):
        """طلب خط الاتجاه متعدد السنوات — multi-year import-trend request."""
        hs_code: str
        market_iso3: str
        end_year: int | None = None
        span: int = 5

    @app.post("/trend")
    def trend(req: TrendRequest, request: Request):
        """خط اتجاه استيراد سوق لرمز عبر مدى سنوات — multi-year import trend.

        مجاني (Comtrade القائم — صفر مصادر جديدة)؛ حارس المصادقة يعمل قبل أي
        جلب. سنة بلا بيانات = فجوة معلنة لا صفر. يغذّي تبويب «الاتجاه» في الواجهة.
        """
        _require_key(request)
        _rate_limit(request)
        import silk_trend
        from silk_data_layer import ISO3_TO_M49
        m49 = ISO3_TO_M49.get((req.market_iso3 or "").upper())
        if not m49:
            raise HTTPException(status_code=422,
                                detail=f"unknown market ISO3: {req.market_iso3}")
        end_year = req.end_year or 2023
        return _json(silk_trend.import_trend(req.hs_code, m49, end_year, req.span))

    @app.get("/diagnostics")
    def diagnostics(request: Request, year: int = 2022):
        """تشخيص المصادر الحيّ — probe each data source live with the server's keys.

        يفحص Comtrade والبنك الدولي وSerper وGoogle Maps وClaude فعلياً ويصنّف:
        متصل/فارغ/محجوب/بلا مفتاح مع تلميح إصلاح. للقراءة فقط، لا يُصدر 500.
        يخبرك على نشرك أيّ مفتاحٍ يعمل وأيّه لا.

        محروسة (مراجعة المشروع): كل نقرة تُطلق نداءاتٍ حيّةً بمفاتيح الخادم
        (Serper/Maps/Claude) — بلا مصادقةٍ وحدِّ معدّلٍ كانت باباً مفتوحاً
        لاستنزاف الرصيد من أي مجهول.
        """
        _require_key(request)
        _rate_limit(request)
        # البند ٣ (حزمة الإغلاق): /diagnostics يُطلق نداءات مدفوعة حيّة
        # (Serper/Maps/Claude) بمفاتيح الخادم، فيجب أن يحجز وحدة من السقف
        # المدفوع كأيّ مسار مدفوع — وإلا فهو ثقب يستنزف الرصيد تحت السقف. يُحجَز
        # فقط حين السقف مضبوط (بلا سقف: لا شيء يُحمى، فالسلوك الافتراضي غير
        # متأثّر). المالك يُعفيه صراحةً بـ SILK_DIAG_EXEMPT=1 (تشخيص متكرر أثناء
        # تصحيح النشر بلا استهلاك السقف — موثَّق في .env.example).
        # البند #5 (تدقيق v2 الموجة ٢) — لماذا لا حارس `_unprotected_paid_keys`
        # 503 هنا كبقية المسارات المدفوعة: قرارٌ مقصود موثَّق لا سهو. تلك
        # المسارات تُحجَب حين تُضبَط مفاتيح مدفوعة بلا SILK_API_KEY لأنها
        # إنتاجية العميل؛ أمّا /diagnostics فهو **أداة اختبار المفاتيح قبل ضبط
        # المصادقة** — غرضه أن يخبرك «أيّ مفتاح يعمل» على نشرٍ لم تُضبَط فيه
        # SILK_API_KEY بعد، فحجبُه عند غيابها يُبطِل وظيفته. حدّه الفعليّ حجزُ
        # السقف المدفوع أعلاه (اضبط SILK_PAID_DAILY_CAP حتى قبل SILK_API_KEY)
        # + حدّ المعدّل، لا حارس 503 — الفرق تعاقديّ لا ثغرة.
        _diag_exempt = os.environ.get("SILK_DIAG_EXEMPT", "").strip().lower() in (
            "1", "true", "yes", "on")
        if (not _diag_exempt and silk_usage.daily_cap() is not None
                and not silk_usage.try_reserve_paid_calls(1)):
            raise HTTPException(status_code=429, detail={
                "error": "daily_paid_cap_exhausted",
                "reason": "السقف المدفوع اليومي مُستنفَد — التشخيص يُطلق نداءات "
                          "مدفوعة حيّة فيُحجَز منه وحدة واحدة. اضبط "
                          "SILK_DIAG_EXEMPT=1 لإعفائه، أو ارفع SILK_PAID_DAILY_CAP."})
        import silk_diagnostics
        try:
            return silk_diagnostics.run_diagnostics(year)
        except Exception as e:  # noqa: BLE001 — diagnostics must never 500
            return {"overall": "unreachable", "agents_can_work": False,
                    "error": f"{type(e).__name__}: {e}", "sources": []}

    @app.get("/ops/last-errors")
    def ops_last_errors(request: Request, n: int = 20):
        """ITEM 5ب (مذكّرة العمليات، تدقيق 2026-07-15): آخر n خطأ تشغيلي —
        فشل تصدير (docx 501)، فشل كاتب (تقرير None)، رفض حجز (429 بحالة
        السقف) — بلا حاجة لسجلات Railway (البروكسي يمنع الوصول لها من
        صندوق تطوير معزول عن الشبكة الحيّة؛ هذه النقطة تقطع تلك الحلقة).

        محروسة كبقية سطوح المشغّل (`/diagnostics`)؛ كل سبب مخزَّن **مُطهَّر
        مسبقاً** (`silk_render._strip_internal_plumbing`، راجع مواقع
        `silk_ops_log.record_error`) — لا `stop_reason`/تتبّع استثناء خام
        يصل هذا الردّ إطلاقاً.
        """
        _require_key(request)
        _rate_limit(request)
        import silk_ops_log
        return _json({"errors": silk_ops_log.last_errors(n)})

    @app.get("/watchdog")
    def watchdog(request: Request, n: int = 50):
        """الحارس — سطحُ مالكٍ منفصلٌ تماماً (LAW: تسلسل القيادة، «تقرير
        الحارس» ليس جزءاً من أي تحليل). آخر `n` سجلّ صحّةٍ + الشارة العامة +
        اتجاهات آخر التشغيلات. محروسة كبقية سطوح المشغّل."""
        _require_key(request)
        _rate_limit(request)
        import silk_watchdog
        records = silk_watchdog.list_records(n)
        return _json({
            "badge": silk_watchdog.overall_badge(records),
            "records": records,
            "trend": silk_watchdog.trend_report(records),
            "known_backlog_note": silk_watchdog.KNOWN_OPEN_BACKLOG_NOTE,
        })

    @app.get("/watchdog/report.md")
    def watchdog_report_md(request: Request, n: int = 50):
        """تقرير مراقبة المنصّة — ملفٌّ مستقلٌّ تماماً بذاته (PART 2-2: لا
        علاقة بأي مُصدِّر تحليل/عميل). محروسة، نفس عقد `report.md`."""
        _require_key(request)
        _rate_limit(request)
        import silk_watchdog
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(silk_watchdog.render_report_md(n=n),
                                 media_type="text/markdown; charset=utf-8")

    @app.get("/sources")
    def sources(request: Request):
        """خريطة حالة طبقات المصادر الاثنتي عشرة — 12-layer data-source status map.

        For each layer: {name, type (free/paid), wired, key_env[, key_present]}.
        M0: عندما تكون المصادقة مفعّلة، أعلام key_present تُعرض لحامل المفتاح فقط
        — مجهول يرى قائمة الطبقات بلا كشف إعدادات الخادم (ANALYSIS.md §7-5).
        وضع التطوير (بلا SILK_API_KEY) يبقى كما كان: الأعلام ظاهرة.
        """
        _rate_limit(request)
        layers = [
            ("UN Comtrade", "free", None),
            ("World Bank", "free", None),
            ("FAOSTAT", "free", None),
            ("WITS", "free", None),
            ("Google Trends", "free", None),
            ("Google Maps", "free", "GOOGLE_MAPS_API_KEY"),
            ("Web Search", "free", "SEARCH_API_KEY"),
            ("Local retail prices", "paid", "LOCALPRICE_API_KEY"),
            ("Volza", "paid", "VOLZA_API_KEY"),
            ("explee", "paid", "EXPLEE_API_KEY"),
            ("Claude (AI judge)", "ai", "ANTHROPIC_API_KEY"),
            ("Requirements L1 reference (GCC + Saudi exit)", "free", None),
        ]
        expected = _api_key_expected()
        show_flags = (not expected) or hmac.compare_digest(
            request.headers.get("x-api-key", ""), expected)
        out = []
        for name, kind, key_env in layers:
            row = {"name": name, "type": kind, "wired": True, "key_env": key_env}
            if show_flags:  # أعلام المفاتيح للمصرَّح له (أو وضع التطوير) فقط
                row["key_present"] = (bool(os.environ.get(key_env))
                                      if key_env else False)
            out.append(row)
        return _json(out)

    @app.get("/analyses")
    def analyses(request: Request):
        """اسرد التحليلات المحفوظة — list persisted analyses (metadata only).

        C-1: محروسة بالمصادقة — الجرد يكشف ما يُحلَّل من منتجات/أسواق.
        """
        _require_key(request)
        _rate_limit(request)
        return _json(silk_storage.list_analyses())

    @app.get("/analyses/{analysis_id}")
    def analysis(analysis_id: int, request: Request):
        """أعد تحليلًا محفوظًا — fetch one persisted analysis, or 404.

        C-1: محروسة — البلوب المخزّن يحمل بطاقة المنتج الاقتصادية،
        والمعرّفات متسلسلة، فبلا مصادقة يقرؤها مجهول بالتعداد.

        ITEM 5أ (خدمة ذاتية للمشغّل، تدقيق 2026-07-15): `?economics=1` يعيد
        ملخّص اقتصاد التشغيلة فقط (لا البلوب الكامل — قد يبلغ عشرات
        الكيلوبايتات) — يقطع حلقة «الصق لي بيانات Railway» التي كانت
        مستحيلة الإغلاق من صندوق تطوير معزول عن الشبكة الحيّة.
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        # HIGH#5 (تدقيق الواجهة 2026-07-15): البلوب الكلاسيكي (/analyze) يُخزَّن
        # داخل المحرّك (silk_engine.py:619) *قبل* إرفاق result["view"] (api.py:579،
        # بعد الحفظ)، فيصل هذا المسار بلا view — ونقر «التحليلات الأخيرة» على
        # تحليل كلاسيكي كان يفتح لوحةً فارغة («شغّل تحليلاً أولاً»). أعِد بناء
        # العرض عند غيابه فقط (مسار /research يحفظ *مع* view فلا يُمَسّ)، تماماً
        # كما تفعل مسارات القراءة الأخرى (brief/report.md/report.docx تبني
        # build_view(found) طازجاً). المعرّف كذلك يُضمَن للبلوبات الأقدم.
        found.setdefault("analysis_id", analysis_id)
        if not found.get("view"):
            found["view"] = _view(found)
        if str(request.query_params.get("economics") or "").strip().lower() \
                in ("1", "true", "yes"):
            return _json(_economics_summary(analysis_id, found))
        return _json(found)

    def _economics_summary(analysis_id: int, found: dict) -> dict:
        """ITEM 5أ: ملخّص اقتصاد تشغيلة واحدة — llm_usage/mission_usage
        (#96)/cost_usd_by_mission/العدّادات/التكلفة النهائية. تشغيلة سابقة
        لتفعيل الإسناد لكل بعثة تعرض `mission_usage`/`cost_usd_by_mission`
        فارغين صراحة (`mission_usage_available: false`) — فجوة معلنة، لا
        استثناء، ولا اختلاق رقم لتشغيلة أقدم من الميزة. `note` (نص حرّ
        بُنِي خادمياً) يمرّ عبر نفس مُطهِّر السباكة الداخلية دفاعاً بالعمق."""
        de = found.get("data_economics") or {}
        from silk_render import _strip_internal_plumbing
        note = de.get("note")
        return {
            "analysis_id": analysis_id,
            "llm_calls": de.get("llm_calls"),
            "tool_calls": de.get("tool_calls"),
            "store_hits": de.get("store_hits"),
            "cache_hits": de.get("cache_hits"),
            "live_fetches": de.get("live_fetches"),
            "llm_usage": de.get("llm_usage") or {},
            "mission_usage": de.get("mission_usage") or {},
            "mission_usage_available": bool(de.get("mission_usage")),
            "cost_usd_estimate": de.get("cost_usd_estimate"),
            "cost_usd_by_model": de.get("cost_usd_by_model") or {},
            "cost_usd_by_mission": de.get("cost_usd_by_mission") or {},
            "cost_unpriced_models": de.get("cost_unpriced_models") or [],
            "note": _strip_internal_plumbing(note) if note else None,
        }

    @app.get("/analyses/{analysis_id}/brief")
    def brief(analysis_id: int, request: Request):
        """المختصر (§10.4) — one-page mobile-style brief from the ONE template.

        C-1: محروسة — تشتق من التحليل المخزّن نفسه (بطاقة/هوامش).
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        from silk_render import build_view
        from silk_reports import render_brief
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(render_brief(build_view(found)))

    @app.get("/analyses/{analysis_id}/report.docx")
    def report_docx(analysis_id: int, request: Request):
        """تقرير Word — derived from the ONE view-model (build_view).

        فصل الجمهور (بلاغ المالك): نتيجة بحث عميق (/research) تُصدَّر بقالب
        **العميل** (`render_client_docx`) بمفردات تجارية بحتة بلا تِلِمِتري —
        هو ما يستلمه العميل الدافع. تِلِمِتري المشغّل (بعثات/حالات/اقتصاد
        بيانات) يبقى على اللوحة (web/index.html)، ويبقى التصدير التشغيلي
        الكامل للمدقّق متاحاً عبر `?internal=1`. نتيجة /analyze الكلاسيكية
        (بلا deep_research) تبقى على `render_docx` العادي.

        C-1: محروسة. 404 للتحليل المفقود؛ 501 بلا python-docx.
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        import tempfile
        from silk_render import build_view
        from silk_reports import render_client_docx, render_docx
        from fastapi.responses import FileResponse
        view = build_view(found)
        # التصدير الكامل التشغيلي للمدقّق فقط عند طلب صريح.
        internal = str(request.query_params.get("internal") or "").lower() in (
            "1", "true", "yes")
        # HF4.2: علِّم النموذجَ بالجمهور — سطرُ إفصاح التنقية للمدقّق فقط.
        view["internal"] = internal
        is_research = bool(view.get("deep_research"))
        if is_research and not internal:
            _block_client_export_if_gate_failed(
                view, analysis_id, found, "docx", request)
        if is_research and internal:
            _attach_override_history(view, analysis_id)
        # القالب الأكاديمي (قرار المالك 2026-07-22): ?style=academic يبدّل
        # ترتيب/نبرة تقرير العميل فقط — نفس النموذج القانوني ونفس بوابة
        # التسليم أعلاه ونفس مُطهِّرات العميل؛ صفر نداء كلود إضافي.
        # أسلوب مخزَّن مع السجل (إعادة توليد أكاديمية سابقة) = الافتراضي.
        style = (str(request.query_params.get("style") or "").lower()
                 or str((view.get("deep_research") or {})
                        .get("report_style") or "").lower())
        # البند #8 (تدقيق v2 الموجة ٣): مجلّد مؤقّت **واحد** يُنظَّف بعد إرسال
        # الردّ (BackgroundTask) — كان كلّ طلب يُنشئ mkdtemp لا يُحذَف أبداً
        # (FileResponse يبثّ الملف لا مجلّده)، فيتراكم على قرص النشر حتى الدوران.
        _td = tempfile.mkdtemp()
        try:
            if is_research and not internal and style == "academic":
                from silk_reports import render_academic_docx
                path = render_academic_docx(
                    view, os.path.join(_td, "report.docx"))
                fname = f"silk_academic_report_{analysis_id}.docx"
            elif is_research and not internal:
                path = render_client_docx(
                    view, os.path.join(_td, "report.docx"))
                fname = f"silk_client_report_{analysis_id}.docx"
            else:
                path = render_docx(
                    view, os.path.join(_td, "report.docx"))
                fname = f"silk_report_{analysis_id}.docx"
        except RuntimeError as e:
            # ITEM 5ب: سبب ثابت عام لا نص الاستثناء الخام — رسالة حارس
            # التصدير (`_client_assert_clean`) قد تقتبس فئة/شظية داخلية
            # (مثال: "algorithm_language: «درجة الثقة»") لا يلتقطها مُطهِّر
            # السباكة العام (يعرّب EN→AR، لا يحذف أسماء فئات عربية موجودة
            # أصلاً) — فبدل مطاردة كل شكل تسريب محتمل بتعبير نمطي جديد،
            # لا يصل /ops/last-errors نص الاستثناء إطلاقاً؛ ردّ الـHTTP نفسه
            # (الذي يخصّ الطالب لا سطحاً عاماً) يبقى يحمل str(e) كاملاً كما
            # كان دوماً — لا تغيير هناك.
            import shutil as _sh
            _sh.rmtree(_td, ignore_errors=True)   # البند #8: نظّف عند الفشل أيضاً
            import silk_ops_log
            silk_ops_log.record_error(
                "export_failure",
                "فشل تصدير docx (منصّة ناقصة أو محتوى رفضه حارس التصدير) — "
                "التفصيل الكامل في استجابة الطلب الأصلي، لا هنا",
                context={"analysis_id": analysis_id})
            raise HTTPException(status_code=501, detail=str(e))
        return FileResponse(
            path, filename=fname,
            media_type="application/vnd.openxmlformats-officedocument"
                       ".wordprocessingml.document",
            background=_rmtree_bg(_td))

    @app.get("/analyses/{analysis_id}/report.pdf")
    def report_pdf(analysis_id: int, request: Request):
        """§3 (أمر العمل الرئيس): المُسلَّم النهائي PDF غير قابل للتحرير —
        يُبنى تقرير العميل docx (RTL، مُطهَّر) ثم يُحوَّل PDF ويُسلَّم الـPDF
        فقط. نفس فصل الجمهور: العميل افتراضاً، المدقّق عبر `?internal=1`.
        503 نظيف إن غاب محرّك التحويل أو فشل — لا docx بديل صامت، لا PDF جزئي.
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        import tempfile
        from silk_render import build_view
        from silk_reports import render_client_pdf, render_research_pdf
        from fastapi.responses import FileResponse
        view = build_view(found)
        internal = str(request.query_params.get("internal") or "").lower() in (
            "1", "true", "yes")
        # HF4.2: علِّم النموذجَ بالجمهور — سطرُ إفصاح التنقية للمدقّق فقط.
        view["internal"] = internal
        is_research = bool(view.get("deep_research"))
        if is_research and not internal:
            _block_client_export_if_gate_failed(
                view, analysis_id, found, "pdf", request)
        if is_research and internal:
            _attach_override_history(view, analysis_id)
        _td = tempfile.mkdtemp()   # البند #8: يُنظَّف بعد الإرسال (background)
        out = os.path.join(_td, "report.pdf")
        style = (str(request.query_params.get("style") or "").lower()
                 or str((view.get("deep_research") or {})
                        .get("report_style") or "").lower())
        try:
            if is_research and not internal and style == "academic":
                from silk_reports import render_academic_pdf
                path = render_academic_pdf(view, out)
                fname = f"silk_academic_report_{analysis_id}.pdf"
            elif is_research and not internal:
                path = render_client_pdf(view, out)
                fname = f"silk_client_report_{analysis_id}.pdf"
            else:
                path = render_research_pdf(view, out)
                fname = f"silk_report_{analysis_id}.pdf"
        except RuntimeError as e:
            import silk_ops_log
            silk_ops_log.record_error(
                "pdf_export_failure",
                "فشل إنتاج PDF (محرّك التحويل غير متاح أو فشل) — التفصيل في "
                "استجابة الطلب الأصلي",
                context={"analysis_id": analysis_id})
            import shutil as _sh
            _sh.rmtree(_td, ignore_errors=True)   # نظّف عند الفشل أيضاً
            raise HTTPException(status_code=503, detail=str(e))
        return FileResponse(path, filename=fname, media_type="application/pdf",
                            background=_rmtree_bg(_td))

    @app.get("/analyses/{analysis_id}/report.md")
    def report_md(analysis_id: int, request: Request):
        """التقرير الكامل Markdown (Stage 5، §7) — من القالب الموحّد نفسه.

        نفس عقد report.docx (محروسة، 404 للمفقود) لكن نصّ خالص بلا تبعيات —
        يعمل حيث لا python-docx، وهو مصدر اشتقاق PDF على النشر.
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        from silk_render import build_view
        from silk_reports import render_markdown
        from fastapi.responses import PlainTextResponse
        try:
            text = render_markdown(build_view(found))
        except RuntimeError as e:
            # نفس عقد report.docx (البند أعلاه): تناقض حكمٍ أو تسريبٌ يستحيل
            # تنقيته يُفشِل التوليد داخلياً — 501 نظيف لا 500 غير مُدار.
            import silk_ops_log
            silk_ops_log.record_error(
                "export_failure",
                "فشل تصدير Markdown (محتوى رفضه حارس التصدير) — التفصيل "
                "الكامل في استجابة الطلب الأصلي، لا هنا",
                context={"analysis_id": analysis_id})
            raise HTTPException(status_code=501, detail=str(e))
        return PlainTextResponse(text,
                                 media_type="text/markdown; charset=utf-8")

    @app.get("/analyses/{analysis_id}/writer-diagnostics")
    def writer_diagnostics(analysis_id: int, request: Request):
        """أحداث `report_call` الخام لتشغيلة — الدليل غير المُطهَّر لفشل الكاتب.

        القضية المفتوحة (كاتب التقرير، PRs 69/70/71): كل سطوح الـHTTP الأخرى
        (`/ops/last-errors`، `/diagnostics`، متن التقرير) تُطهِّر `error_type`/
        `stop_reason`/`status_code` قبل العرض — فيستحيل على المالك قراءة نوع
        الاستثناء الفعلي عن بُعد، ويصير أي إصلاح تخميناً. هذه النقطة تكشف أحداث
        `report_call` كما كُتبت في الأثر (‏`data/traces/{trace_id}.jsonl`):
        `stage`/`timeout`/`elapsed_ms`/`success`/`error_type`/`error_message`/
        `status_code`/`response_body` — فيُميَّز ReadTimeout (المهلة فعلاً) من
        ConnectTimeout (شبكة) من 429/529 (حصّة/ازدحام) من 400 (حمولة). محروسة
        بالمفتاح كبقية سطوح المشغّل؛ الأحداث **مُنقّاة من الأسرار مسبقاً** عند
        الكتابة (`silk_trace._redacted`) فلا مفتاح يتسرّب — لكنها **غير مُطهَّرة
        من التفصيل التقني** عمداً: هذا هو الغرض (قياس لا تخمين).
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        trace_id = (found.get("deep_research") or {}).get("trace_id")
        report_calls: list = []
        if trace_id:
            import silk_trace
            report_calls = [e for e in silk_trace.read_trace(trace_id)
                            if e.get("kind") == "report_call"]
        # سبب الفشل كما هو مخزَّن (قد يكون مُطهَّراً في البلوب) — للمقارنة فقط.
        dr = found.get("deep_research") or {}
        rep = dr.get("report") if isinstance(dr.get("report"), dict) else {}
        return _json({
            "analysis_id": analysis_id,
            "trace_id": trace_id,
            "report_present": bool((rep or {}).get("report")),
            "failure_reason_stored": (rep or {}).get("failure_reason"),
            "report_calls": report_calls,
            "note": ("لا trace_id (تشغيلة أقدم من التتبّع أو تحليل كلاسيكي)"
                     if not trace_id else
                     ("لا أحداث report_call — الكاتب لم يُستدعَ أو الأثر مُنمَحٍ"
                      if not report_calls else None)),
        })

    @app.post("/analyses/{analysis_id}/report")
    def regenerate_report(analysis_id: int, request: Request):
        """أعد توليد التقرير الكامل من بحث محفوظ — نداء كاتب واحد (+مراجع)
        فقط، بلا إعادة تشغيل أي بعثة من الاثنتي عشرة ولا المحلل الشامل.

        بلاغ حي (تمور/هولندا): كاتب التقرير قد يفشل (مهلة/شبكة) رغم نجاح
        كل شيء آخر في تشغيلة مكلفة كاملة — هذه النقطة تُنقِذ تلك التشغيلة
        بتكلفة نداء واحد بدل إعادة البحث كله (§سنتات لا دولارات)، ومصدر
        اختبار رخيص لإصلاحات مهلة الكاتب. تقرأ نقاط تفتيش البعثات
        (`silk_storage.load_mission_checkpoints`، مخزَّنة فور اكتمال كل
        بعثة بصرف النظر عن مصير الكاتب لاحقاً) بدل إعادة بنائها من
        `json_blob` النهائي — نفس آلية استئناف `/research`، لا منطق موازٍ.
        تحدّث السجل المخزَّن بالتقرير الجديد وتعيد بناء القالب الموحّد +
        بوابة الجودة قبل الحفظ.
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        dr = found.get("deep_research")
        if not dr:
            raise HTTPException(
                status_code=400,
                detail=f"analysis {analysis_id} is not a /research run "
                       "(no deep_research section) — nothing to regenerate")
        ai_ok, ai_note = _free_ai_extras_allowed()
        if not ai_ok:
            return _json({"report": None, "note": ai_note})
        mission_reports = silk_storage.load_mission_checkpoints(analysis_id)
        if not mission_reports:
            raise HTTPException(
                status_code=409,
                detail=f"no mission checkpoints stored for analysis "
                       f"{analysis_id} — cannot regenerate without them")
        from silk_ai_judge import write_reviewed_report
        analyst_summary = ((dr.get("analyst") or {}).get("report") or {}) \
            .get("summary", "")
        verdict = dr.get("verdict") or {}
        market_name = (found.get("market") or {}).get("name_en", "")
        trace_id = dr.get("trace_id")
        # قرار المالك (متابعة القالب الأكاديمي): ?style=academic يعيد كتابة
        # النثر نفسه بالسجل الأكاديمي (نداء كاتب واحد — قروش لا دولارات)؛
        # الأسلوب يُخزَّن مع السجل فتتبعه التصديرات افتراضياً.
        regen_style = str(request.query_params.get("style") or "").lower() \
            or None
        # نفسُ عقد `resume` (يمرّ ويُوسَم): إعادةُ التوليد تُعيد استعمال
        # `found["hs_code"]` المخزَّن بلا أيّ فحص — فتُعيد كتابةَ تقريرٍ كاملٍ
        # على رمزٍ ربّما صار خاطئاً. الوسمُ يُحفَظ مع السجلّ فيظهر في الحدود.
        try:
            from silk_hs_confirm import revalidate as _reval_fn
            _reval = _reval_fn(found.get("product", ""), found.get("hs_code"))
            if _reval:
                found["hs_revalidation"] = _reval
        except Exception as e:  # noqa: BLE001 — وسمٌ تحسينيّ لا يُسقِط الطلب
            log.warning("hs revalidation skipped on regen: %s", e)
        # PR A §A1: إعادة التوليد كانت لا تمرّر `hs_confirmation` للكاتب ولا
        # تُسقِّف الثقة — فتعيد إنتاج نفس تعارض §4«73%»/الغلاف«50%» في كل regen.
        # نحسب العقد ونُسقِّف الحكم من المصدر الواحد قبل الكاتب، ونمرّر العقد
        # كي يؤطّر الكاتب أرقام كومتريد سياقياً أيضاً (كان مفقوداً على هذا المسار).
        hs_conf_regen = None
        try:
            from silk_hs_confirm import (confirm_hs as _confirm_regen,
                                        cap_confidence_for_flagged_hs
                                        as _cap_regen)
            _hs_regen = found.get("hs_code")
            hs_conf_regen = (_confirm_regen(found.get("product", ""), _hs_regen)
                             if _hs_regen else None)
            verdict = _cap_regen(verdict, hs_conf_regen)
        except Exception as e:  # noqa: BLE001 — تسقيفٌ تحسينيّ لا يُسقِط الطلب
            log.warning("hs confidence cap skipped on regen: %s", e)
        report_out = write_reviewed_report(
            mission_reports, analyst_summary, verdict,
            found.get("product", ""), market_name, trace_id=trace_id,
            hs_code=found.get("hs_code"), hs_confirmation=hs_conf_regen,
            style=regen_style)
        # H1 (تدقيق): إعادة التوليد كانت تطمس التقرير المخزَّن بـreport_out حتى
        # لو فشل الكاتب هذه المرة (report=None) — فيُفقَد تقرير سابق ناجح كلّفت
        # تشغيلته الكاملة، وهو بالضبط ما تُنقِذه هذه النقطة. الآن: لا نحفظ null
        # فوق تقرير سابق ناجح؛ نُبقي المخزَّن ونُبلّغ الفشل (السجل لا يُلمَس).
        from silk_render import _strip_internal_plumbing
        prior_report = (dr.get("report") or {}).get("report")
        if not report_out.get("report"):
            # ITEM 5ب: فشل كاتب أثناء regen — يُسجَّل بصرف النظر عن وجود
            # تقرير سابق محفوظ أم لا (كلاهما فشل كاتب حقيقي يستحق الرصد).
            import silk_ops_log
            silk_ops_log.record_error(
                "writer_failure",
                _strip_internal_plumbing(report_out.get("failure_reason") or "")
                or "فشل الكاتب بلا سبب مسجَّل",
                context={"analysis_id": analysis_id, "regen": True,
                         "prior_preserved": bool(prior_report)})
        if not report_out.get("report") and prior_report:
            return _json({"report": None, "regenerated": False,
                          "note": "تعذّرت إعادة توليد التقرير هذه المرة؛ "
                                  "التقرير السابق محفوظ كما هو دون تغيير.",
                          "failure_reason": _strip_internal_plumbing(
                              report_out.get("failure_reason") or "")})
        found["deep_research"]["report"] = report_out
        if regen_style:
            found["deep_research"]["report_style"] = regen_style
        found["analysis_id"] = analysis_id
        found["view"] = _view(found)
        _attach_quality_gate(found, trace_id)
        silk_storage.save_analysis(found, analysis_id=analysis_id)
        return _json(found)

    @app.post("/analyses/{analysis_id}/enrich-leads")
    def enrich_leads(analysis_id: int, request: Request):
        """أعد رصد جهات اتصال المستوردين لبحث محفوظ — كشط الخرائط فقط، بلا
        أيّ نداء كلود ولا إعادة تشغيل البعثات (المسار الرخيص).

        السياق (بلاغ UK الحي، أمر العمل الرئيس ITEM 2): المكشطة (`silk_gmaps`)
        نُشرت متأخّراً (SILK_GMAPS_SCRAPER_URL ضُبط بعد إنجاز تقارير سابقة)،
        فتقرير المالك القائم يحمل «فجوة معلنة» في جدول المستوردين رغم أن
        المكشطة صارت حيّة الآن. إعادة تشغيل /research كاملة تكلّف ~3$ (١٢ بعثة
        + محلل + كاتب) لمجرّد تعبئة هاتف/إيميل — هذه النقطة تُنجزها بقروش:
        تكشط الخرائط للسوق/المنتج المخزَّنَين، تدمج مرشّحي الويب من نقاط
        تفتيش البعثات المحفوظة، وتحدّث `importer_leads` في مكانه ثم تعيد بناء
        القالب الموحّد قبل الحفظ. لا نداء كلود، ولا حجز من السقف اليومي
        المدفوع — المكشطة خدمة منفصلة رخيصة. عقد عدم الاختلاق مقدَّس: فشل/غياب
        المكشطة = فجوة معلنة، لا صفّ مخترَع.
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        dr = found.get("deep_research")
        if not dr:
            raise HTTPException(
                status_code=400,
                detail=f"analysis {analysis_id} is not a /research run "
                       "(no deep_research section) — no importer leads to enrich")
        import silk_gmaps
        if not silk_gmaps.enabled():
            # تعطيل نظيف: المكشطة غير مُهيَّأة — لا نلمس التحليل المخزَّن، نُبلّغ.
            return _json({
                "enriched": False, "path": (dr.get("importer_leads") or {})
                .get("path", "gap"),
                "leads_count": len((dr.get("importer_leads") or {})
                                   .get("leads") or []),
                "note": "مكشطة الخرائط غير مُهيَّأة (SILK_GMAPS_SCRAPER_URL "
                        "غائب) — لم تُحدَّث الروابط."})

        product = found.get("product") or ""
        market_blob = found.get("market") or {}
        from silk_market_resolver import resolve_market
        market_ref, _sugg = resolve_market(
            market_blob.get("name_en") or market_blob.get("iso3") or "")
        if market_ref is None:
            raise HTTPException(
                status_code=422,
                detail=f"stored market {market_blob!r} could not be resolved "
                       "— cannot target the scraper")

        # مرشّحو الويب من نقاط تفتيش البعثات المحفوظة (اسم فقط، للمضاهاة/الدمج)
        # — تُقرأ من نفس آلية استئناف /research، لا إعادة تشغيل بعثة.
        mission_reports = silk_storage.load_mission_checkpoints(analysis_id) or {}
        web_cands = silk_gmaps.extract_web_candidates(mission_reports)

        # البند #4 (تدقيق v2 الموجة ٢): الكشط كان يحجب الطلب متزامناً حتى ٣٠٠ث،
        # فبوّابة النشر (Railway/بروكسي) تقطعه عند ~٣٠-٦٠ث فيصل العميل ٥٠٢/٥٠٤
        # بلا جسم — لا يُميّزه عن فشلٍ صلب. المهلة الآن **آمنة للبروكسي** افتراضياً
        # (٢٥ث)، وخيط الكشط يواصل ويخزّن نتائجه ذاتياً عند الاكتمال (silk_gmaps
        # `_worker`)، فإعادة الضغط تجلبها من المخزن فوراً (نمط 202-غير-حاجب رخيص
        # يعتمد التخزين القائم، بلا نظام مهامّ منفصل). env يظلّ ضابطاً لمن يريد أطول.
        grace = float(os.environ.get("SILK_GMAPS_ENRICH_GRACE_S", "25"))
        fut = silk_gmaps.submit_scrape_async(product, market_ref)
        new_leads = silk_gmaps.finalize_leads(
            fut, product, market_ref, web_cands, timeout_s=grace)

        # لا تطمس روابط قائمة بفجوة: نُحدّث فقط إن أتى الكشط بروابط فعلية.
        prev = dr.get("importer_leads") or {"leads": [], "path": "gap"}
        processing = False
        if new_leads.get("leads"):
            found["deep_research"]["importer_leads"] = new_leads
            found["analysis_id"] = analysis_id
            found["view"] = _view(found)
            silk_storage.save_analysis(found, analysis_id=analysis_id)
            enriched = True
            note = new_leads.get("note") or "حُدِّثت الروابط عبر كشط الخرائط."
        else:
            # لا روابط ضمن المهلة الآمنة — نُبقي المخزَّن كما هو (لا اختلاق، لا
            # طمس). الكشط **قد يكون ما زال جارياً** في الخلفية ويخزّن نتائجه عند
            # الاكتمال، فنُصرّح بذلك ونقترح إعادة المحاولة (لا نزعم «لا شيء»).
            enriched = False
            processing = True
            note = ("لم تكتمل جهات الاتصال ضمن المهلة الآمنة — قد يكون الكشط "
                    "ما زال جارياً في الخلفية؛ أعد الضغط بعد قليل لجلب ما اكتمل. "
                    "الروابط السابقة محفوظة كما هي دون تغيير.")

        current = found["deep_research"].get("importer_leads") or prev
        return _json({
            "processing": processing,
            "enriched": enriched,
            "path": current.get("path", "gap"),
            "leads_count": len(current.get("leads") or []),
            "importer_leads": current,
            "note": note})

    class AskRequest(BaseModel):
        """سؤال فوق تحليل قائم (10b) — question over a stored analysis."""
        question: str

    @app.post("/analyses/{analysis_id}/ask")
    def ask_analysis(analysis_id: int, req: AskRequest, request: Request):
        """دردشة سياقية فوق تحليل مخزّن (10b) — من الذاكرة حصراً.

        لا إعادة تشغيل وكلاء، لا نداء خارجي سوى نداء كلود الواحد؛ الأرضية
        سياق التحليل المحسوب (analysis_context) والسؤال داخل العزل. ما ليس
        في السياق يُعلن «غير متوفر في هذا التحليل» — لا اختلاق. نفس حارس
        إضافات كلود المجانية (H2): نشر غير محمي يحجبها، والمحمي يحجز من
        السقف اليومي.
        """
        _require_key(request)
        _rate_limit(request)
        found = silk_storage.get_analysis(analysis_id)
        if found is None:
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        ai_ok, ai_note = _free_ai_extras_allowed()
        if not ai_ok:
            return _json({"answer": None, "note": ai_note})
        from silk_ai_judge import answer_about_analysis, failure_reason
        from silk_render import _strip_internal_plumbing, analysis_context
        out = answer_about_analysis(req.question, analysis_context(found))
        if out is None:
            # بلاغ حي (بحث "تمور/هولندا"): None لا يعني بالضرورة غياب
            # المفتاح — قد يكون فشل نداء فعلي (مهلة/شبكة) رغم مفتاح فعّال.
            # H3 (تدقيق): كان يعيد failure_reason() خاماً — يحمل
            # empty_response/stop_reason/«راجع سجلّات الخادم». يمرّ الآن عبر
            # نفس مُطهِّر طبقة العرض (H4 يُعرّب تلك الرموز).
            return _json({"answer": None,
                          "note": _strip_internal_plumbing(failure_reason())})
        # سدّ تسريب: جواب كلود يمرّ بلا أي مُطهِّر مباشرة للعميل — كلود قد
        # يقتبس مفتاحاً داخلياً حرفياً من السياق رغم تعريب السياق نفسه، أو
        # يستخدم رمز حكم خام بنفسه؛ نفس مُطهِّر طبقة العرض (مرة واحدة).
        if out.get("answer"):
            out["answer"] = _strip_internal_plumbing(out["answer"])
        return _json(out)

    class OutcomeRequest(BaseModel):
        """جسم تسجيل النتيجة الفعلية — actual-outcome body (wave 1)."""
        outcome: str

    @app.patch("/analyses/{analysis_id}/outcome")
    def set_outcome(analysis_id: int, req: OutcomeRequest, request: Request):
        """سجّل ما حدث فعلاً لتحليل — record the real-world outcome (wave 1).

        يبني سجل المصداقية التراكمي (عمودا outcome/outcome_date). 404 إن لم
        يوجد التحليل؛ لا يغيّر بيانات التحليل نفسها إطلاقاً.
        M0: خلف المصادقة وتحديد المعدّل — كانت الوحيدة المكشوفة، فكان بوسع أي
        مجهول الكتابة فوق سجل النتائج بالتعداد (ANALYSIS.md §7-1).
        """
        _require_key(request)
        _rate_limit(request)
        outcome = (req.outcome or "").strip()
        if not outcome:
            raise HTTPException(status_code=422, detail="outcome must be non-empty")
        if not silk_storage.set_outcome(analysis_id, outcome):
            raise HTTPException(status_code=404,
                                detail=f"analysis {analysis_id} not found")
        return {"id": analysis_id, "outcome": outcome, "recorded": True}

    # منصّة SaaS متعددة المستأجرين (PR-1: مصادقة + عزل) تحت /platform — تُركَّب
    # قبل الواجهة الثابتة وبأمان: أي فشل استيراد يُسجَّل ولا يُسقِط المحرّك القائم
    # (مبدأ «لا انهيار»). النقاط تحت /platform مستقلّة عن مسارات المحرّك أعلاه.
    # حارس الإقلاع **يصعد** ولا يُبتلَع: `boot_config_guard` يرفع RuntimeError
    # عند إشارة إنتاج بلا سرّ توقيع، وابتلاعه هنا كان يعني إقلاعاً «أخضر»
    # و/platform غائبة صامتة (404 من الملفات الثابتة) — نفس عائلة «الفقدان
    # الصامت» التي يمنعها SILK_REQUIRE_PERSISTENT_DATA_DIR بالفشل العالي.
    # Only a missing/broken import degrades; a config RuntimeError refuses boot.
    try:
        import silk_platform
    except ImportError:  # المنصّة غير متوفّرة استيراداً · optional at import time
        log.warning("silk_platform not importable — router not mounted", exc_info=True)
    else:
        if silk_platform.mount(app):   # RuntimeError من الحارس يصعد عمداً
            log.info("silk_platform router mounted at /platform")

    # الواجهة الثابتة على نفس الخدمة — serve the static frontend at "/" so one
    # Render service hosts BOTH the API and the UI (same origin, no CORS needed).
    # Registered last so the API routes above take precedence over static files.
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
    if os.path.isdir(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app


# تطبيق على مستوى الوحدة — module-level app, None when fastapi is unavailable.
# استثناء مصيدة التخزين (LESSONS.md البند ٤) لا يُبتلَع: fastapi غائبة => app=None
# ليبقى الاستيراد يعمل؛ أما رفض التخزين الفاني الإنتاجي فيُعاد رفعه ليفشل
# استيراد `api:app` بصوت عالٍ على Railway (رفض الإقلاع المقصود، لا خدمة صامتة).
try:
    app = create_app()
except RuntimeError as _exc:
    if str(_exc) == _PIP_HINT:  # fastapi absent: keep import working, hold None.
        app = None
        log.warning(_PIP_HINT)
    else:  # مصيدة التخزين الدائم أو أي رفض إقلاع صريح آخر — أفشِل بصوت عالٍ.
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        import uvicorn
    except ImportError:
        print(_PIP_HINT)
    else:
        uvicorn.run(create_app(), host="127.0.0.1", port=8000)
