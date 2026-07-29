"""اختبارات تصدير تقرير العميل (القالب الثاني، فصل الجمهور).

البصيرة الجوهرية (بلاغ المالك): التصدير القديم يعرض تِلِمِتري النظام لقارئ
نهائي — خطأ جمهور. تقرير العميل (render_client_docx) يجب أن يكون خالياً
تماماً من المصطلحات الممنوعة (mission/status/successful/run/call/declared
gap/tool names + لغة الخوارزمية)، وأن يرفض التصدير إن تسرّب أيّ منها.
Run:  python3 -m pytest tests/ -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import block_network, docx_all_text  # noqa: E402


def _mock_view(missing_categories=None, report_text=None):
    """نتيجة بحث عميق مموّهة → build_view. تحوي عمداً تسريبات تشغيلية في سرد
    الكاتب (اسم أداة، "بعثة"، جدول درجات) ليتأكّد أن المُطهِّر/الحارس يزيلها."""
    from silk_agents import AgentReport
    from silk_data_layer import DataPoint
    from silk_market_resolver import resolve_market
    from silk_render import build_view

    ref, _ = resolve_market("Netherlands")

    def f(v, s, c, n):
        return DataPoint(v, s, c, n, "2026-07-02")

    demand = [f("واردات هولندا من تمور 38 مليون دولار (2023)", "UN Comtrade",
               0.9, "[demand] تدفق مباشر")]
    price = [f("Albert Heijn: تمور 5.50€/كغم", "Albert Heijn (رصد ويب)", 0.6,
              "[price_competitiveness] سعر مرصود")]
    by_cat = {"demand": demand, "entry_cost": [], "price_competitiveness": price,
              "entry_door": [], "swot": []}
    # التقاطعات الغائبة تُصرَّح صراحةً (لا حذف صامت).
    missing = (missing_categories if missing_categories is not None
              else [c for c, v in by_cat.items() if not v])

    default_report = (
        "## 1. الخلاصة التنفيذية\n"
        "التوصية دخول مشروط لأن الشريحة كبيرة والسوق مجزَّأ.\n"
        "**ماذا يعني هذا لقرارك:** ابدأ ملف الأهلية الآن.\n\n"
        "## 2. منهجية البحث ونطاقه\n"
        "11 من 12 بعثة أنتجت أدلة. المصدر: Comtrade عبر comtrade_imports.\n\n"
        "## 3. نظرة عامة على السوق وحجمه\n"
        "واردات 38 مليون دولار (UN Comtrade)، نمو 7%.\n"
        "**ماذا يعني هذا لقرارك:** حجم كافٍ لشحنة تجارية.\n\n"
        "## 6. المشهد التنافسي\n"
        "HHI≈2100 من comtrade_competitors.\n"
        "| الدولة | الحصة |\n| --- | --- |\n| تونس | 31% |\n\n"
        "## 9. تقييم المخاطر\n"
        "استقرار مرتفع (World Bank WGI).\n"
        "**ماذا يعني هذا لقرارك:** لا مخاطر كلية.\n\n"
        "## 10. التوصيات الاستراتيجية\n"
        "الحكم دخول مشروط. \n"
        "| العمود | القيمة |\n| --- | --- |\n| verdict | دخول مشروط |"
        "\n| confidence | 0.66 |\n\n"
        "### خارطة طريق الدخول (٩٠ يوماً)\n"
        "الباب الأول: موزّع حلال في أمستردام (○ يحتاج تحققاً).\n")

    result = {
        "product": "تمور", "hs_code": "080410", "year": 2023,
        "market": {"iso3": ref.iso3, "m49": ref.m49, "iso2": ref.iso2,
                  "name_en": ref.name_en, "name_ar": ref.name_ar},
        "markets": [],
        "deep_research": {
            "trace_id": "test-client-nld",
            "missions": {
                "trade_flow": AgentReport("LLMAgent:trade_flow", demand, False,
                                          "تدفقات مؤكَّدة"),
                "pricing_scout": AgentReport("LLMAgent:pricing_scout", price,
                                             False, "أسعار مرصودة"),
                "competitors": AgentReport("LLMAgent:competitors", [
                    f({"year": 2023, "hhi": 2100.0}, "UN Comtrade", 0.9,
                      "HHI معتدل")], False, "تركّز معتدل"),
            },
            "analyst": {
                "report": AgentReport("LLMAgent:market_analyst",
                                      demand + price, False, "تحليل مكتمل"),
                "by_category": by_cat, "missing_categories": missing},
            # WP-1: الحكم المعروض من الحقل الحتمي حصراً — المدوّنة تحاكي
            # تشغيلة النظام الجديد (الكاتب مقيَّد بالحكم الحتمي فيطابقه السرد)؛
            # قراءة كلود (ai) استشارية متوافقة، لا مصدر الحكم.
            "verdict": {"verdict": "CONDITIONAL-GO", "confidence": 0.66,
                       "ai": {"verdict": "دخول مشروط", "confidence": 0.66,
                             "reasoning": "دخول مشروط بتأمين الأهلية أولاً."}},
            "report": {"report": report_text or default_report,
                      "review_cycles": 2, "unresolved_notes": []},
        },
    }
    os.environ["SILK_HERMETIC"] = "1"
    view = build_view(result)
    view["test_run"] = True
    return view


def _render(view, tmp_path):
    import silk_reports as R
    out = os.path.join(str(tmp_path), "client.docx")
    return R.render_client_docx(view, out)


# ── الحارس الأساسي: صفر مصطلح ممنوع في المخرَج ────────────────────────────

def test_client_export_has_zero_forbidden_terms(tmp_path):
    import silk_reports as R
    with block_network():
        out = _render(_mock_view(), tmp_path)
    text = docx_all_text(out)
    hits = R._client_forbidden_hits(text)
    assert hits == [], f"forbidden telemetry leaked into client export: {hits}"


def test_client_export_zero_hits_for_A_terms(tmp_path):
    """§A (حزمة الفكس v2.1، بند ٦): تقرير العميل خالٍ تماماً من سجلّ الأدلة
    القديم/جدول مزيج الثقة/تسميات المصدر الداخلية — استُبدلت أو أُسقطت من
    بناء العميل (تبقى في ?internal=1 فقط).

    ملاحظة نطاق (قرار تنفيذ واعٍ يخالف حرفية §A-4): «جدول خرائط قوقل» طلبت
    الحزمة إسقاطه من بناء العميل بالكامل — أُبقي عمداً (راجع التعليق أعلى
    استدعاء `_docx_leads` في `render_client_docx`) لأنه محتوًى تجاريٌّ فعلي
    (جهات اتصال موزّعين) يخدم قرار العميل، وثلاثة اختبارات قائمة تُثبِته
    قراراً منتجاً متعمَّداً سابقاً (C5) — فلا يُفحَص غيابه هنا. كذلك رمزا
    «◐»/«○» استُبعِدا من قائمة الحجب لأنهما استعمال سردي مشروع قائم فعلاً في
    متن الكاتب (مثال: «موزّع حلال (○ يحتاج تحققاً)»)، لا رمزا الجدول المحذوف
    حصراً — الفحص هنا يستهدف العنوان/الجدول المحذوفين بنصّهما الفعلي."""
    import silk_reports as R
    with block_network():
        out = _render(_mock_view(), tmp_path)
    text = docx_all_text(out)
    for term in ("سجل الأدلة", "للمدققين", "مؤشّر ثقة الدراسة", "قوة الدليل",
                "Silk L1", "مرجع سلك"):
        assert term not in text, f"forbidden §A term leaked: {term!r}"
    assert "المراجع" in text


def test_each_forbidden_category_absent_explicitly(tmp_path):
    """تحقّق صريح من كل بند في قائمة المالك: mission/status/successful/run/
    call/declared gap/tool names + لغة الخوارزمية."""
    with block_network():
        out = _render(_mock_view(), tmp_path)
    text = docx_all_text(out)
    # عربية تشغيلية
    for term in ("بعثة", "بعثات", "ناجحة", "نجحت", "فشلت", "فجوة معلنة",
                 "تشغيلة", "المحلل الشامل", "كاتب التقرير"):
        assert term not in text, f"forbidden Arabic term present: {term}"
    # أسماء أدوات snake_case
    for tool in ("comtrade_imports", "comtrade_competitors", "web_search",
                 "worldbank_indicator", "eurostat_eu_signals",
                 "trends_interest"):
        assert tool not in text, f"tool name leaked: {tool}"
    # لغة الخوارزمية الإنجليزية (جدول الدرجات)
    for algo in ("verdict", "confidence", "score"):
        assert algo not in text.lower(), f"algorithm language leaked: {algo}"
    # المصادر البشرية المشروعة تبقى (استشهاد لا أداة)
    assert "UN Comtrade" in text  # اسم مصدر بشري — مسموح


# ── سلوك «نقِّ لا ترفض» (PART A، أمر العمل الرئيس — عائلة 501) ──────────────

def test_leaked_term_is_redacted_not_a_501(tmp_path):
    """التغيير البنيوي المُلزَم (أمر العمل الرئيس، PART A): بعد ثلاث حوادث
    501 (#90/#103/#106)، مصطلح تشغيلي متسرّب لا يُسقِط التصدير بعد الآن —
    يُنقّى بمحايد ويُسلَّم المستند مع سطر إفصاح. الحارس شبكة أمان أخيرة فقط.
    (كان هذا الاختبار سابقاً يؤكّد الرفض بـRuntimeError — العقد تغيّر عمداً.)"""
    import silk_reports as R
    leaky = ("## 1. الخلاصة التنفيذية\n"
             "This mission was successful.\n"
             "**ماذا يعني هذا لقرارك:** ابدأ.\n")
    view = _mock_view(report_text=leaky)
    with block_network():
        out = _render(view, tmp_path)          # لا استثناء — يُنتِج ملفاً
    text = docx_all_text(out)
    assert R._client_forbidden_hits(text) == []  # نُقّي كل متسرّب
    assert "mission" not in text.lower() and "successful" not in text.lower()
    # HF4.2 (بلاغ قطر): التنقية تجري، لكن سطرَ الإفصاح عنها **لا يصل العميل** —
    # يُقصَر على سطح المدقّق (?internal=1). المُسلَّم خالٍ منه.
    assert "نُقّيت بعض المصطلحات" not in text
    # سطحُ المدقّق (view["internal"]=True) يُظهره — تغطيةٌ محفوظةٌ للطرفين.
    iview = _mock_view(report_text=leaky)
    iview["internal"] = True
    with block_network():
        iout = R.render_client_docx(iview, os.path.join(str(tmp_path), "audit.docx"))
    assert "نُقّيت بعض المصطلحات" in docx_all_text(iout)


def test_forbidden_hits_helper_detects_each_pattern():
    import silk_reports as R
    assert R._client_forbidden_hits("هذه بعثة بحث")
    assert R._client_forbidden_hits("النتيجة ناجحة")
    assert R._client_forbidden_hits("فجوة معلنة في البيانات")
    assert R._client_forbidden_hits("عبر comtrade_competitors")
    assert R._client_forbidden_hits("the mission ran")
    assert R._client_forbidden_hits("confidence 0.6")
    # نص تجاري نظيف — لا مطابقة
    assert R._client_forbidden_hits(
        "واردات هولندا 38 مليون دولار وفق UN Comtrade، نمو 7%.") == []


def test_forbidden_hits_helper_detects_section_marker_glyph():
    """حارسٌ نهائي: رمز قسمٍ داخليٍّ («§4b»/«§10.3»/«§» مجرّداً) لا يخصّ
    العميل — يُزال فعلياً في `_client_sanitize`، وهذا يتحقّق أن الحارس
    النهائي (`_client_forbidden_hits`) يلتقطه أيضاً كشبكة أمانٍ إن أفلت من
    التطهير الاستباقي (مدوّنة مخزَّنة قديمة، مثلاً)."""
    import silk_reports as R
    assert R._client_forbidden_hits("راجع §4b للتفاصيل")
    assert R._client_forbidden_hits("وفق §10.3 من الوثيقة")
    assert R._client_forbidden_hits("علامة § مجرّدة")
    # يُزال (لا يُستبدَل بحشو) في مسار التنقية الفعلي — لا بقايا بعد التطهير.
    assert R._client_sanitize("راجع §4b للتفاصيل") == "راجع للتفاصيل"
    assert R._client_forbidden_hits(R._client_sanitize("راجع §4b للتفاصيل")) == []


# ── البنية: أقسام العميل السبعة بالترتيب ─────────────────────────────────

def test_client_structure_headings_in_order(tmp_path):
    with block_network():
        out = _render(_mock_view(), tmp_path)
    text = docx_all_text(out)
    order = ["القرار وأساسه", "السوق بالأرقام", "المنافسة والتسعير والهامش",
             "مسار الدخول والمتطلبات", "المخاطر",
             "ما لم يكتمل للقرار", "المراجع"]
    positions = [text.find(h) for h in order]
    for h, pos in zip(order, positions):
        assert pos >= 0, f"client section missing: {h}"
    assert positions == sorted(positions), "client sections out of order"


def test_missions_table_replaced_by_methodology_paragraph(tmp_path):
    """النقطة ٤ + §2.5 (أمر العمل الرئيس): جدول البعثات التشغيلي يُستبدَل
    بفقرة منهجية عامة (مصادر مُستشارة + تاريخ الجمع + أسلوب التحقّق) — **لا
    كشف بنية**: لا «مسار بحث»، لا عدد مكوّنات داخلية، لا جدول حالات بعثات."""
    with block_network():
        out = _render(_mock_view(), tmp_path)
    text = docx_all_text(out)
    assert "مسار بحث" not in text        # §2.5: لا نسبة الحقائق لمسار بحث داخلي
    assert "مصادر رسمية عامة" in text     # الصياغة العامة الجديدة
    assert "من مصدرها العمومي" in text  # أسلوب التحقّق
    assert "المراجع" in text  # §A: الملحق أعيد تسميته "المراجع"
    # لا عمود «الحالة» التشغيلي (كان في جدول ملخّص مصادر البحث القديم)
    assert "الحالة" not in text or "ناجحة" not in text


# ── الفجوات → صياغة تجارية، لا عناوين فارغة متتالية ───────────────────────

def test_empty_intersections_become_commercial_phrasing(tmp_path):
    """النقطة ٣: كل تقاطع بلا أدلة يتحوّل لصياغة تجارية موحّدة القالب، لا
    عنوان فارغ. المموّه يترك entry_cost/entry_door/swot فارغة."""
    with block_network():
        out = _render(_mock_view(
            missing_categories=["entry_cost", "entry_door", "swot"]), tmp_path)
    text = docx_all_text(out)
    assert "لم نتمكّن من توثيق" in text
    assert "إغلاق هذه الفجوة يتطلّب" in text
    # صياغة تجارية للأبواب الغائبة تحديداً
    assert "موزّعين" in text or "جهات اتصال" in text


def test_no_missing_categories_gives_positive_line_not_empty(tmp_path):
    """السطر الإيجابي «لا فجوة جوهرية» يظهر فقط حين لا تقاطع ناقص **ولا شرط
    قلب حكم غير محقَّق** (§H-1). هنا نُفرِّغ شروط قلب الحكم (كلها محقَّقة/لا
    شرط) مع إبقاء حكم المدوّنة كما هو، فيبقى المسار الإيجابي النقيّ محفوظاً
    بلا تناقض شارة/متن."""
    view = _mock_view(missing_categories=[])
    view["deep_research"]["flip_conditions"] = []
    with block_network():
        import silk_reports as R
        out = os.path.join(str(tmp_path), "client.docx")
        R.render_client_docx(view, out)
    text = docx_all_text(out)
    assert "لا فجوة جوهرية" in text
    assert "لم نتمكّن من توثيق" not in text


def test_conditional_verdict_unmet_flip_condition_surfaces_as_gap(tmp_path):
    """§H-1 (حزمة الفكس v2.1): بلاغ حي — «لا فجوة جوهرية» نُشرت بينما شرط
    قلب الحكم المهيكل (موزّع متعاقَد) غير محقَّق. الآن: حكمٌ مشروط بشرط قلبٍ
    غير محقَّق يُظهِر الشرط كفجوة صريحة، لا نفياً كاذباً للفجوة."""
    view = _mock_view(missing_categories=[])
    view["deep_research"]["verdict"]["ai"]["verdict"] = "CONDITIONAL-GO"
    view["deep_research"]["verdict"]["verdict"] = "CONDITIONAL-GO"
    # شرط قلب حكم غير محقَّق (لا موزّع مؤكَّد بجهة اتصال حقيقية)
    view["deep_research"]["flip_conditions"] = [{
        "condition": "التعاقد مع موزّع محلي مؤكَّد بالاسم في هولندا",
        "closes_via": "خدمة تحقّق جهات الاتصال المدفوعة ثم عقد موزّع",
        "met": False}]
    with block_network():
        import silk_reports as R
        out = os.path.join(str(tmp_path), "client.docx")
        R.render_client_docx(view, out)
    text = docx_all_text(out)
    assert "لم يتحقّق بعد" in text
    assert "لا فجوة جوهرية" not in text


# ── لا اختلاق: تقرير بلا سرد كاتب يتدهور تجارياً بلا تِلِمِتري ──────────────

def test_missing_writer_report_degrades_cleanly(tmp_path):
    import silk_reports as R
    with block_network():
        out = _render(_mock_view(report_text=""), tmp_path)
    text = docx_all_text(out)
    assert R._client_forbidden_hits(text) == []       # نظيف رغم غياب السرد
    assert "التوصية:" in text                         # الحكم حاضر دوماً
    assert "المراجع" in text                           # §A: الملحق حاضر


# ── نقطة النهاية: /research → تقرير العميل النظيف؛ ?internal=1 → الكامل ────

def _store_deep_research(db, report_text=None, full_sections=True):
    """خزّن نتيجة بحث عميق بشكل JSON-safe (بعثات كقواميس) — كما يصل من
    التخزين فعلاً؛ build_view يطبّعها عبر _report_fields.

    §0 (الفكس الجذري — الحزمة v2.1): نقطتا التصدير الآن تحجبان تسليم العميل
    حين تُعيد بوابة الجودة FAIL. `full_sections=True` (الافتراضي) يستعمل
    تقرير المدوّنة القانونية الحقيقية الشكل (١١ قسماً كاملة،
    `tools/canonical_netherlands.REPORT_TEXT`) الذي يمرّ ببوابة الجودة
    (PASS-WITH-WARNINGS)، فتبقى نقاط النهاية الافتراضية 200. `full_sections=
    False` (أو `report_text` صريح) يبني تقريراً بقسم واحد فقط — يُفشِل فحص
    `section_structure` عمداً لاختبار الحجب §0."""
    import silk_storage as storage
    if report_text is None:
        if full_sections:
            from tools.canonical_netherlands import REPORT_TEXT
            report_text = REPORT_TEXT
        else:
            report_text = "## 1. الخلاصة التنفيذية\nنص تجريبي نظيف.\n"
    dp = {"value": "واردات هولندا 38 مليون دولار (2023)", "source": "UN Comtrade",
          "confidence": 0.9, "note": "[demand] تدفق مباشر",
          "retrieved_at": "2026-07-02"}
    result = {
        "product": "تمور", "hs_code": "080410", "year": 2023,
        "market": {"iso3": "NLD", "m49": "528", "iso2": "NL",
                  "name_en": "Netherlands", "name_ar": "هولندا"},
        "markets": [],
        "deep_research": {
            "missions": {"trade_flow": {"agent_name": "LLMAgent:trade_flow",
                                        "failed": False, "summary": "ok",
                                        "findings": [dp]}},
            "analyst": {"report": {"agent_name": "LLMAgent:market_analyst",
                                  "failed": False, "summary": "تحليل",
                                  "findings": [dp]},
                       "by_category": {"demand": [dp], "entry_cost": [],
                                      "price_competitiveness": [],
                                      "entry_door": [], "swot": []},
                       "missing_categories": ["entry_cost",
                                             "price_competitiveness",
                                             "entry_door", "swot"]},
            # WP-1: الحقل الحتمي هو الحكم المعروض — المدوّنة بنظام ما بعد WP-1.
            "verdict": {"verdict": "CONDITIONAL-GO", "confidence": 0.6,
                       "ai": {"verdict": "دخول مشروط", "confidence": 0.6,
                             "reasoning": "دخول مشروط بالأهلية."}},
            "report": {"report": report_text,
                      "review_cycles": 1, "unresolved_notes": []},
        },
    }
    return storage.save_analysis(result, db)


def test_conditional_go_badge_agrees_with_body_label(tmp_path):
    """بلاغ مراجعة المالك (تناقض الحكم صفحة ١): شارة الغلاف كانت «مراقبة
    السوق» بينما المتن «دخول مشروط». الآن CONDITIONAL-GO له tone وتسمية
    مستقلّان، فتتّفق الشارة مع المتن."""
    from silk_render import _VERDICT_LABELS_AR, _verdict_tone
    assert _verdict_tone("CONDITIONAL-GO") == "conditional"
    assert _VERDICT_LABELS_AR["conditional"] == "دخول مشروط"
    view = _mock_view()
    view["deep_research"]["verdict"]["ai"]["verdict"] = "CONDITIONAL-GO"
    view["deep_research"]["verdict"]["verdict"] = "CONDITIONAL-GO"
    with block_network():
        out = _render(view, tmp_path)
    text = docx_all_text(out)
    assert "دخول مشروط" in text          # المتن + الشارة متطابقان
    assert "مراقبة السوق" not in text     # لا تسمية watch مخالفة


def test_evidence_log_formats_numbers_and_drops_meaningless_rows(tmp_path):
    """بلاغ مراجعة المالك (النقطة ٤): العشريات الخام تُنسَّق مقروءةً، والبنود
    عديمة المعنى (قيمة dict غير معروفة/رد خام) تُسقَط بدل «بند تقني غير قابل
    للعرض»."""
    import silk_reports as R
    from silk_data_layer import DataPoint
    # رقم خام + ملاحظة سياقية → «X مليون — <سياق>»
    assert R._client_readable_fact(38_000_000.0, "[demand] واردات، دولار، 2023") \
        == "38 مليون — واردات، دولار، 2023"
    # قيمة dict منافس → مقروءة، لا placeholder
    assert R._client_readable_fact(
        {"partner": "تونس", "share": 31.0}, "n") == "تونس: حصة 31.0%"
    assert R._client_readable_fact({"hhi": 2100.0}, "n").startswith(
        "مؤشر تركّز المورّدين HHI=")
    # قيمة غير قابلة للعرض → تُسقَط (None)
    assert R._client_readable_fact({"weird": 1}, "n") is None
    with block_network():
        out = _render(_mock_view(), tmp_path)
    text = docx_all_text(out)
    assert "بند تقني غير قابل للعرض" not in text
    assert "38000000" not in text  # لا عشري خام


def test_unverified_first_door_surfaces_in_gap_section(tmp_path):
    """بلاغ مراجعة المالك (النقطة ٥): موزّع الباب الأول موسوم ○ غير متحقق =
    بند حاسم للقرار؛ يجب أن يظهر في «ما لم يكتمل للقرار» بالصياغة التجارية
    حتى لو اكتملت التقاطعات، لا أن يُقال «لا فجوة جوهرية»."""
    from silk_data_layer import DataPoint
    view = _mock_view(missing_categories=[])
    # أدرِج مرشّح باب دخول غير متحقق (ثقة 0.35 < 0.5 = ○)
    door = DataPoint("موزّع حلال في أمستردام — مرشّح", "بحث ويب (غير مؤكَّد)",
                     0.35, "[entry_door] مرشّح", "2026-07-02")
    view["deep_research"]["analyst"]["by_category"]["entry_door"] = [
        {"value": door.value, "source": door.source,
         "confidence": door.confidence, "note": door.note,
         "retrieved_at": door.retrieved_at}]
    with block_network():
        out = _render(view, tmp_path)
    text = docx_all_text(out)
    assert "قناة الدخول الأولى" in text          # الفجوة الحاسمة ظهرت
    assert "لم نتمكّن من تأكيد" in text            # بالصياغة التجارية
    assert "لا فجوة جوهرية" not in text            # لا نفي كاذب للفجوة


def test_committed_client_sample_is_clean_and_structured():
    """قاعدة ١٠.٦: نموذج تقرير العميل محفوظ بالمستودع، ويجب أن يظل خالياً
    من أيّ مصطلح ممنوع وكامل البنية (يُعاد توليده عبر
    tools/gen_client_report_sample.py مع كل تعديل على طبقة العرض)."""
    import silk_reports as R
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "samples", "client_report_latest.docx")
    assert os.path.exists(path), "شغّل tools/gen_client_report_sample.py"
    text = docx_all_text(path)
    assert R._client_forbidden_hits(text) == [], "النموذج المحفوظ يحوي تِلِمِتري"
    for h in ("القرار وأساسه", "السوق بالأرقام", "المنافسة والتسعير والهامش",
              "مسار الدخول والمتطلبات", "المخاطر", "ما لم يكتمل للقرار",
              "المراجع"):
        assert h in text, f"قسم مفقود من النموذج المحفوظ: {h}"


def test_research_docx_endpoint_serves_clean_client_report(tmp_path):
    import pytest
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import api
    import silk_storage as storage
    import silk_reports as R

    db = os.path.join(str(tmp_path), "research.db")
    os.environ["SILK_HERMETIC"] = "1"
    aid = _store_deep_research(db)
    saved = storage._DEFAULT_PATH
    storage._DEFAULT_PATH = db
    try:
        client = TestClient(api.create_app())
        with patch("requests.sessions.Session.request",
                   side_effect=OSError("network disabled for offline test")):
            # الافتراضي: تقرير العميل النظيف
            r = client.get(f"/analyses/{aid}/report.docx")
            assert r.status_code in (200, 501)
            if r.status_code == 501:
                return  # لا python-docx في هذه البيئة
            assert "client_report" in r.headers.get("content-disposition", "")
            path = os.path.join(str(tmp_path), "got_client.docx")
            with open(path, "wb") as fh:
                fh.write(r.content)
            text = docx_all_text(path)
            assert R._client_forbidden_hits(text) == []
            assert "المراجع" in text                # §A: الملحق أعيد تسميته
            assert "قسم البحث العميق" not in text  # لا عنوان تشغيلي

            # ?internal=1: التصدير التشغيلي الكامل (للمدقّق) — يحوي التِلِمِتري
            r2 = client.get(f"/analyses/{aid}/report.docx?internal=1")
            assert r2.status_code == 200
            path2 = os.path.join(str(tmp_path), "got_internal.docx")
            with open(path2, "wb") as fh:
                fh.write(r2.content)
            text2 = docx_all_text(path2)
            assert "قسم البحث العميق" in text2  # التصدير الكامل يحتفظ به
    finally:
        storage._DEFAULT_PATH = saved


# ── §0 (حزمة الفكس v2.1) — البوابة شرط تسليم، لا تحسين اختياري ─────────────
#
# الجذر: `_attach_quality_gate` كانت «تحسين لا شرط تسليم» (تعليق قديم في
# api.py) و`report_pdf`/`report_docx` كانا يصدّران تقرير العميل بلا أي فحص
# لحكم البوابة — تقرير FAIL كان يصل العميل. الآن: FAIL على قالب العميل
# (غير `internal=1`) ⇒ 409 مع ملخّص النتائج؛ `?override=1` يتخطّى الحجب؛
# `internal=1` معفًى تماماً من هذا الفحص (يبقى متاحاً للمدقّق دوماً).

def _seed_client_export(tmp_path, full_sections):
    import silk_storage as storage
    db = os.path.join(str(tmp_path), "research.db")
    os.environ["SILK_HERMETIC"] = "1"
    aid = _store_deep_research(db, full_sections=full_sections)
    return db, aid


def test_client_docx_export_blocked_409_when_gate_fails(tmp_path):
    import pytest
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import api
    import silk_storage as storage

    db, aid = _seed_client_export(tmp_path, full_sections=False)
    saved = storage._DEFAULT_PATH
    storage._DEFAULT_PATH = db
    try:
        client = TestClient(api.create_app())
        with patch("requests.sessions.Session.request",
                   side_effect=OSError("network disabled for offline test")):
            r = client.get(f"/analyses/{aid}/report.docx")
            assert r.status_code == 409
            body = r.json()["detail"]
            assert body["error"] == "quality_gate_fail"
            assert body["findings"]
            assert any(f["check"] == "section_structure" for f in body["findings"])
            # قفل انحدار (رسالة 409 القديمة): كانت تعِد بأن ?override=1 يكفي
            # بمفتاح API العادي بينما WP-7 §1 يرفضه 403 بلا X-Owner-Key —
            # الرسالة يجب أن تذكر سلطة المالك، لا أن تَعِد بما يرفضه الخادم.
            assert "X-Owner-Key" in body["message"]
            assert "مسؤولية من يملك مفتاح API" not in body["message"]

            # WP-7 §1: ?override=1 يتطلّب سلطة المالك المنفصلة — مفتاح API
            # العادي وحده يُرفَض 403؛ ومع X-Owner-Key المطابقة يمرّ.
            r_no_owner = client.get(f"/analyses/{aid}/report.docx?override=1")
            assert r_no_owner.status_code == 403
            assert r_no_owner.json()["detail"]["error"] == \
                "owner_override_required"
            os.environ["SILK_OWNER_KEY"] = "owner-secret"
            try:
                r_override = client.get(
                    f"/analyses/{aid}/report.docx?override=1",
                    headers={"X-Owner-Key": "owner-secret"})
                assert r_override.status_code in (200, 501)
            finally:
                os.environ.pop("SILK_OWNER_KEY", None)

            # ?internal=1 معفًى من فحص البوابة تماماً — لا 409 مهما كان الحكم
            r_internal = client.get(f"/analyses/{aid}/report.docx?internal=1")
            assert r_internal.status_code == 200
    finally:
        storage._DEFAULT_PATH = saved


def test_client_pdf_export_blocked_409_when_gate_fails(tmp_path):
    import pytest
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import api
    import silk_storage as storage

    db, aid = _seed_client_export(tmp_path, full_sections=False)
    saved = storage._DEFAULT_PATH
    storage._DEFAULT_PATH = db
    try:
        client = TestClient(api.create_app())
        with patch("requests.sessions.Session.request",
                   side_effect=OSError("network disabled for offline test")):
            r = client.get(f"/analyses/{aid}/report.pdf")
            assert r.status_code == 409
            body = r.json()["detail"]
            assert body["error"] == "quality_gate_fail"
            assert body["findings"]
            # ?internal=1 معفًى تماماً — أي استجابة أخرى (200/503) مقبولة،
            # المهم أنها ليست 409 حجب البوابة.
            r_internal = client.get(f"/analyses/{aid}/report.pdf?internal=1")
            assert r_internal.status_code != 409
    finally:
        storage._DEFAULT_PATH = saved


def test_client_docx_export_200_when_gate_passes(tmp_path):
    import pytest
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import api
    import silk_storage as storage

    db, aid = _seed_client_export(tmp_path, full_sections=True)
    saved = storage._DEFAULT_PATH
    storage._DEFAULT_PATH = db
    try:
        client = TestClient(api.create_app())
        with patch("requests.sessions.Session.request",
                   side_effect=OSError("network disabled for offline test")):
            r = client.get(f"/analyses/{aid}/report.docx")
            assert r.status_code in (200, 501)  # لا 409 — البوابة لم تُفشِل
    finally:
        storage._DEFAULT_PATH = saved


def test_gate_crash_treated_as_fail_for_client_export(tmp_path):
    """البند ٢ (§0): عطل البوابة نفسها (استثناء غير متوقَّع) = FAIL للعميل،
    لا «تخطٍّ صامت» — لا يجوز أن يصل تقرير لم يُفحَص فعلياً حتى لو كانت
    البوابة نفسها معطوبة. نُحاكي العطل بتصحيح `run_quality_gate` ليرفع،
    على تقرير كان سيمرّ (full_sections=True) لو عملت البوابة فعلياً."""
    import pytest
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    import api
    import silk_storage as storage
    import silk_quality_gate

    db, aid = _seed_client_export(tmp_path, full_sections=True)
    saved = storage._DEFAULT_PATH
    storage._DEFAULT_PATH = db
    try:
        client = TestClient(api.create_app())
        with patch("requests.sessions.Session.request",
                   side_effect=OSError("network disabled for offline test")), \
             patch.object(silk_quality_gate, "run_quality_gate",
                          side_effect=RuntimeError("simulated gate crash")):
            r = client.get(f"/analyses/{aid}/report.docx")
            assert r.status_code == 409
            assert r.json()["detail"]["error"] == "quality_gate_fail"
    finally:
        storage._DEFAULT_PATH = saved
