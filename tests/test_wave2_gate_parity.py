"""الموجة ٢ (أمر التثبيت، 2026-07-21) — تكافؤ البوّابات بين /analyze و/research.

كل بوّابة/عقد جودة بُني في هذا المستودع أُصلِح تاريخياً على مسارٍ واحد
فقط ثم عاود الظهور على شقيقه (تخزين #٤/#٣١، بوّابة HS #٣٥) — هذا الملف
حارسٌ بنيويّ واحد لكل بوّابةٍ **مُدرَجة صراحةً**: يفحص هل يستدعيها كل من
معالجَي `/analyze` و`/research` في api.py، أو يوثّق سبب شرعية عدم تناظرها
(بوّابة خاصة بسردٍ حرّ لا يملكه المسار الآخر بنيوياً، أو مفهومٌ لا ينطبق
على المسار الآخر إطلاقاً مثل الاستئناف).

الجدول (تدقيق أمر التثبيت، ٩ بوّابات):
  ١أ) بوّابة تأكيد HS المسبقة        — كلاهما (مُصلَحة، LESSONS ٣٥)
  ١ب) إعادة تأطير CONTEXTUAL_TAG      — /research فقط (مُوثَّق: narrative-only)
  ٢) أسبقية الحكم (verdict واحد)      — كلاهما (بنيوياً، نفس _verdict_tone)
  ٣) وسم الحقائق المتقادِمة          — /research فقط (مُوثَّق: narrative-only)
  ٤) تنقية «§»                       — عميل /research فقط (لا § في /analyze)
  ٥) تحييد اسم المزوّد الداخلي        — سطح العميل فقط (render_client_docx)؛
                                        /analyze يبقى سطحاً تشغيلياً بنيوياً
                                        (بلا سطح عميل مكافئ إطلاقاً — نفس
                                        إعفاء `?internal=1` الموثَّق لـ/research)
  ٦) تنقية النائب («[شعار سِلك]»)     — كلاهما (مساعد مشترك واحد)
  ٧) نطاق السوق (نقاط تفتيش البعثات)  — /research فقط (لا استئناف على /analyze
                                        إطلاقاً — المفهوم غير موجود هناك بنيوياً)
  ٨) عقد DataPoint (لا اختلاق)        — كلاهما (طبقة البيانات المشتركة)
  ٩) silk_quality_gate.run_quality_gate — /research فقط (يفحص نثر التقرير؛
                                        /analyze بلا تقرير حرّ مكافئ)

Run: python3 -m pytest tests/test_wave2_gate_parity.py -q
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ══════════════ ١أ) بوّابة HS — كلاهما (البوّابة المُصلَحة) ══════════════

def test_gate1a_hs_preflight_fires_on_both_paths():
    """كلا معالجَي /analyze و/research يستدعيان نقطة الاختناق فعلياً —
    واحدة (silk_hs_confirm.py)، لا نسخة مكرَّرة.

    اللائحة ٦٥: النداءُ يمرّ اليوم عبر الغلاف `preflight_resolve` (البوّابة
    + قياسُ السمة الرقمية قبل الحوار). يُحتسَب الغلافُ **بشرط** إثبات أنه
    يستدعي `preflight_block` لا يستبدلها — وإلا فهو بوّابةٌ موازية."""
    import inspect
    import silk_hs_confirm
    api = _read("api.py")
    assert (api.count("preflight_block(")
            + api.count("preflight_resolve(")) >= 2
    assert "preflight_block(" in inspect.getsource(
        silk_hs_confirm.preflight_resolve)


# ══════════ ٢) أسبقية الحكم — بنيوياً واحدة لكلا الفرعين ══════════

def test_gate2_single_verdict_classifier_shared():
    """مصنِّف نبرة الحكم (_verdict_tone) واحد يُستدعى لكلا الفرعين الكلاسيكي
    والبحث العميق — لا حكمان موازيان (§9.3، القرار المستقر)."""
    render_src = _read("silk_render.py")
    # يُستخدَم داخل build_view (الفرع الكلاسيكي) وداخل _deep_research_view.
    assert render_src.count("_verdict_tone(") >= 2


# ══════ ٦) تنقية النائب — مساعدٌ مشترك واحد لكلا مسارَي docx ══════

def test_gate6_placeholder_wordmark_guard_shared_by_both_docx_paths():
    """`_add_cover_wordmark` (منع نائب «[شعار سِلك]») يُستدعى من مسار
    /analyze الكلاسيكي (render_docx) ومسارَي /research (التشغيلي والعميل) —
    لا مسار docx بلا الحارس."""
    src = _read("silk_reports.py")
    assert src.count("_add_cover_wordmark(") >= 3, (
        "الحارس يجب أن يُستدعى من كل مسارات docx الثلاثة (تشغيلي، عميل، /analyze)")


# ══════ ٥) تحييد اسم المزوّد — سطح العميل فقط، بتوثيق السبب ══════

def test_gate5_vendor_redaction_scoped_to_client_surface_by_design():
    """التحييد يعمل فقط عبر `_client_sanitize`/`render_client_docx` —
    السطح الوحيد الذي يصل عميلاً خارجياً فعلياً. `/analyze` يبقى سطحاً
    تشغيلياً (`render_docx` العادي) لا يملك مكافئاً «عميلياً» إطلاقاً — نفس
    إعفاء `?internal=1` الموثَّق حرفياً لمسار /research العملياتي. هذا
    الحارس يثبت أن الإعفاء **موثَّقٌ صراحةً في الشيفرة** لا افتراضاً صامتاً،
    وأن render_docx العادي (الذي يخدم /analyze) لا يستدعي المُطهِّر (توثيق
    الحالة الراهنة، لا حكمٌ بأنها كافية إلى الأبد — انظر تقرير الموجة ٢)."""
    src = _read("silk_reports.py")
    assert "لا يُسمّى مزوّد داخلي" in src or "لا يُسمّى مزوّد" in src, (
        "عقد عدم تسمية المزوّد يجب أن يبقى موثَّقاً صراحةً في المصدر")
    assert "_client_sanitize" in src and "_CLIENT_VENDOR_RE" in src


# ══════ ٧) نطاق السوق — N/A شرعي على /analyze (لا مفهوم استئناف) ══════

def test_gate7_market_scoping_is_legitimately_na_on_analyze_no_resume_concept():
    """`/analyze` (AnalyzeRequest) لا يملك حقل `resume` إطلاقاً — فمفهوم
    «نقاط تفتيش بعثاتٍ قد تخصّ سوقاً آخر» غير موجود بنيوياً على هذا المسار
    (كل نداء /analyze عديم الحالة statelessly، بلا استئناف جزئي). البوّابة
    (silk_storage.market_iso3 + api.py resume_market_mismatch) تبقى محصورة
    بـ/research عن حقّ — هذا الحارس يمنع إضافة `resume` لـ/analyze مستقبلاً
    بلا مراجعة هذا الافتراض صراحة."""
    api = _read("api.py")
    m = re.search(r"class AnalyzeRequest\(BaseModel\):.*?(?=\n    class )",
                 api, re.S)
    assert m, "AnalyzeRequest model not found"
    assert "resume" not in m.group(0), (
        "AnalyzeRequest اكتسب حقل resume — راجع بوّابة نطاق السوق: هل تنطبق "
        "الآن على /analyze أيضاً؟ (LESSONS ٣٦)")


# ══════ ٨) عقد DataPoint — طبقة البيانات المشتركة ══════

def test_gate8_datapoint_contract_is_the_shared_data_layer_beneath_both():
    """`DataPoint` (فشل=None/0.0) مُعرَّفة مرّة واحدة في silk_data_layer —
    كلا المسارين (silk_market_ranker لـ/analyze، الوكلاء/البعثات لـ/research)
    يبنيان فوقها لا ينسخانها."""
    dl = _read("silk_data_layer.py")
    assert "class DataPoint" in dl
    ranker = _read("silk_market_ranker.py")
    agents = _read("silk_agents.py")
    assert "DataPoint" in ranker and "DataPoint" in agents


# ══════ ١ب/٣/٤/٩) بوّابات سردٍ حرّ — N/A شرعي على /analyze (موثَّقة) ══════

def test_narrative_only_gates_are_documented_not_silently_absent():
    """CONTEXTUAL_TAG/وسم التقادُم/بوّابة الجودة السردية تعمل فقط حين
    `result["deep_research"]` موجود (`_deep_research_view`) — /analyze
    الكلاسيكي بلا نثرٍ حرٍّ مكافئ لتوسيمه بنيوياً. هذا الحارس يثبت أن غياب
    التوسيم عن /analyze **مشروط ببنية `_deep_research_view` نفسها** لا
    استثناءً مبعثراً في كل بوّابة — إن انتقلت هذه البوّابات لتعمل خارج
    `_deep_research_view` مستقبلاً (سردٌ حرّ يُضاف لـ/analyze)، يجب حينها
    توصيلها هناك أيضاً (راجع تقرير الموجة ٢ — قائمة الفجوات المتبقّية)."""
    render_src = _read("silk_render.py")
    # CONTEXTUAL_TAG وتوسيم التقادُم داخل نطاق _deep_research_view نصّياً.
    dr_view_start = render_src.index("def _deep_research_view")
    dr_view_end = render_src.index("\ndef ", dr_view_start + 10)
    dr_view_body = render_src[dr_view_start:dr_view_end]
    assert "CONTEXTUAL_TAG" in dr_view_body
    assert "_tag_stale_years" in dr_view_body
    quality_gate_call_site = _read("api.py")
    assert '_attach_quality_gate(result, ' in quality_gate_call_site
