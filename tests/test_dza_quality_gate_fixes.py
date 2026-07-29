"""أقفال إصلاح بوابة الجودة — تحليل #1 (زبدة الفول السوداني/الجزائر، DZA).

> **الحادثة.** «تحليل #1» (DZA) الحيّ (2026-07-21) أخفق بوّابة الجودة بستّة
> نتائج معاً: تسريب رموز Markdown، رقم ثقة خام، رقمان مفتاحيان مكرّران أكثر
> من مرّتين، عمود سعر معنوَن بالدولار بقيمٍ باليورو فعلياً، و٩٨ استشهاداً
> يتجاوز سقف الملحق التقني (٨٠). **رمز HS الخاطئ (040510) خارج نطاق هذا
> الملف عمداً** — يُصلَح عبر مسار مصنِّف HS العام (بند منفصل تماماً).

مدوّنة الإعادة الإنتاجية: `tools/canonical_dza_peanut_butter.py` (نفس شكل
الإنتاج المخزَّن، تحمل كل العيوب الستة صراحةً قبل الإصلاح).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.canonical_dza_peanut_butter import dza_research_blob  # noqa: E402


def _dza_view():
    import silk_render as R
    return R.build_view(dza_research_blob())


def _dza_gate():
    import silk_quality_gate as QG
    return QG.run_quality_gate(_dza_view())


# ══════════ ٠ — الحالة قبل الإصلاح: الستة معاً أفشلوا البوّابة ══════════

def test_dza_blob_reproduces_all_six_pre_fix_findings_shape():
    """إثبات أن المدوّنة تُعيد إنتاج الشكل المُبلَّغ فعلياً — لا اختبار على
    نموذج مثالي؛ نفس الأرقام الحرفية من التدقيق (1.5%×4، 9.3%×3، ٩٨ استشهاداً)."""
    from tools.canonical_dza_peanut_butter import REPORT_TEXT
    assert REPORT_TEXT.count("1.5%") == 4
    assert REPORT_TEXT.count("9.3%") == 3
    blob = dza_research_blob()
    total = sum(len(m.get("findings") or [])
               for m in blob["deep_research"]["missions"].values())
    assert total == 98


# ══════════ ١ — markdown_artifacts: أسوار/تنسيق شارد يُنقَّى ══════════

def test_1_stray_code_fence_and_bold_stripped_before_render():
    """الأسوار العشوائية («```») والتنسيق «**» الشارد (خارج العبارة المرخَّصة
    الوحيدة) تُنقَّى من نص التقرير المعروض — العناوين "## "/"### " البنيوية
    تبقى (بنيوية بالعقد، تظهر WARN متوقَّعة أدناه، لا تُمَسّ)."""
    view = _dza_view()
    text = view["deep_research"]["report"]["text"]
    assert "```" not in text
    # كامل الكتلة المسوَّرة (السور + محتواها) تُزال — لا سطر خام متبقٍّ.
    assert "raw source excerpt" not in text
    assert "HS: 040510" not in text
    # العبارة المرخَّصة الوحيدة تبقى بتنسيقها.
    assert "**ماذا يعني هذا لقرارك:**" in text
    # لا تنسيق «**» آخر شارد (كان يلفّ «حصة السعودية لا تتجاوز 1.5%»).
    import re
    stray = [m for m in re.findall(r"\*\*[^\n*]*\*\*", text)
            if m != "**ماذا يعني هذا لقرارك:**"]
    assert stray == []


def test_1_markdown_artifacts_stays_the_only_warn_for_structural_headings():
    """حارس مضاد (test_quality_gate_stays_warn_for_ordinary_repairable_
    findings): عناوين "## N. القسم" الإلزامية تبقى تُطلِق markdown_artifacts
    (WARN متوقَّعة بالعقد، لا نُطفئها) — الإصلاح يعالج التسريب **الإضافي**
    فقط (أسوار/تنسيق شارد)، لا العناوين البنيوية."""
    findings = _dza_gate()["findings"]
    checks = {f["check"] for f in findings}
    assert "markdown_artifacts" in checks
    md = next(f for f in findings if f["check"] == "markdown_artifacts")
    assert md["repairable"] is True


# ══════════ ٢ — raw_confidence: «ثقة 0.x» خام يُنقَّى ══════════

def test_2_raw_confidence_token_stripped_before_render():
    """«بدرجة ثقة 0.6» الخامة في الخلاصة التنفيذية تُستبدَل بعبارة لغوية
    (silk_narrative.confidence_phrase) — لا كسر عشري خام على وجه التقرير."""
    view = _dza_view()
    text = view["deep_research"]["report"]["text"]
    assert "0.6" not in text
    assert "ثقة متوسطة" in text or "ثقة" in text  # عبارة لغوية بديلة موجودة


def test_2_raw_confidence_finding_absent_after_fix():
    checks = {f["check"] for f in _dza_gate()["findings"]}
    assert "raw_confidence" not in checks


# ══════════ ٣+٤ — تكرار رقم مفتاحي: عقد الكاتب + علم المراجع الحتمي ══════════

def test_3_writer_contract_already_bans_key_figure_repetition_beyond_twice():
    """عقد الكاتب (silk_ai_judge.deep_report) يمنع تكرار الرقم المفتاحي أكثر
    من مرّتين صراحةً — هذا الشرط موجود؛ الإصلاح الجديد هو علمٌ حتميّ إضافي
    (البند ٤) لا يعتمد فقط على امتثال النموذج لهذا النص."""
    import inspect
    import silk_ai_judge as J
    src = inspect.getsource(J.deep_report)
    assert "يتكرّر الرقم المفتاحي نفسه أكثر من مرّتين" in src


def test_4_repeated_key_figure_issues_flags_both_offenders_deterministically():
    """فحص حتمي مستقلّ (لا كلود) يكتشف تكرار «1.5%»×4 و«9.3%»×3 من نص
    التقرير الخام مباشرة — نفس عدّاد بوّابة الجودة (مصدر حقيقة واحد)."""
    import silk_ai_judge as J
    from tools.canonical_dza_peanut_butter import REPORT_TEXT
    issues = J._repeated_key_figure_issues(REPORT_TEXT)
    joined = " | ".join(issues)
    assert "1.5%" in joined
    assert "9.3%" in joined


def test_4_review_report_surfaces_repeated_key_figure_even_without_llm_reply(
        monkeypatch):
    """المراجع السريع يُبلِغ تكرار الرقم المفتاحي حتى بلا ردّ كلود صالح —
    نفس نمط test_review_report_rejects_incomplete_draft_even_without_llm_reply
    (wave10)، مطبَّقاً على تكرار الرقم المفتاحي بدل الأقسام الناقصة."""
    import silk_ai_judge as J
    monkeypatch.setattr(J, "available", lambda: True)
    monkeypatch.setattr(J, "_call", lambda *a, **k: None)
    from tools.canonical_dza_peanut_butter import REPORT_TEXT
    result = J.review_report(REPORT_TEXT, {})
    assert result is not None
    assert result["approved"] is False
    assert any("1.5%" in i for i in result["issues"])
    assert any("9.3%" in i for i in result["issues"])


def test_34_style_repeated_key_figure_finding_stays_warn_not_fail():
    """التكرار (٣-٤ مرّات) يبقى WARN (تحذير أسلوبي) لا FAIL — التصعيد لـFAIL
    محجوز لتكرار ≥٥ (style_repeated_key_figure_excess) فقط بعقد §8."""
    out = _dza_gate()
    checks = {f["check"] for f in out["findings"]}
    assert "style_repeated_key_figure" in checks
    assert "style_repeated_key_figure_excess" not in checks
    assert out["verdict"] != "FAIL"


# ══════════ ٥ — عمود السعر: يُعنوَن بالعملة المرصودة فعلاً ══════════

def test_5_price_column_relabeled_to_actually_observed_currency():
    """عنوان العمود «السعر/كجم بالدولار» يُستبدَل بـ«باليورو» — العملة التي
    رُصدت فعلاً في صفوف نفس الجدول — لا وعدُ تحويلٍ لم يُجرَ."""
    view = _dza_view()
    text = view["deep_research"]["report"]["text"]
    assert "السعر/كجم باليورو" in text
    assert "السعر/كجم بالدولار" not in text
    # لا سعر صرف مختلَق — القيم تبقى كما رُصدت (يورو)، بلا رقم دولار موازٍ جديد.
    assert "9.14€" in text or "9.14 يورو" in text or "9.14" in text


def test_5_currency_label_mismatch_finding_absent_after_fix():
    checks = {f["check"] for f in _dza_gate()["findings"]}
    assert "currency_label_mismatch" not in checks


def test_5_currency_check_scoped_to_table_not_whole_document():
    """حارس انحدار: البحث عن «العملة الأخرى» يقتصر على نافذة الجدول (من
    الترويسة حتى أول سطر فارغ) — لا كامل نص التقرير؛ التقرير يذكر شرعاً
    الاستيراد بالدولار (§1) وسعر التجزئة باليورو (§6) في قسمين مستقلّين
    بلا أي خطأ فعلي، وهذا لا يجوز أن يُبلَّغ تعارضاً."""
    import silk_quality_gate as QG
    dr = {"report": {"text": (
        "## 1. الخلاصة التنفيذية\n"
        "واردات السوق 61 مليون دولار.\n\n"
        "## 6. المشهد التنافسي\n"
        "| المنتج | السعر/كجم باليورو |\n"
        "| --- | --- |\n"
        "| تمر سكري | 7.49 يورو |\n\n"
        "## 9. تقييم المخاطر\nنص.")}, "missions": {}, "analyst": {}}
    findings = QG._check_currency_label_mismatch(dr)
    assert findings == []


def test_5_currency_check_still_fires_within_the_same_table_block():
    """حارس مضاد: التناقض الحقيقي (وعدُ دولارٍ بينما صفوف **نفس الجدول**
    يورو) يبقى مكتشَفاً — التضييق لا يُسقِط الحالة الحقيقية."""
    import silk_quality_gate as QG
    dr = {"report": {"text": (
        "| المنتج | السعر/كجم بالدولار |\n"
        "| تمر سكري | 7.49 يورو (تعذّر التحويل) |")}, "missions": {},
        "analyst": {}}
    findings = QG._check_currency_label_mismatch(dr)
    assert len(findings) == 1
    assert findings[0]["check"] == "currency_label_mismatch"
    assert findings[0]["repairable"] is True


# LESSONS ٤٥ — دالة الإصلاح الشقيقة (silk_render._fix_price_column_
# currency_label) لم تحمل نفس تضييق نافذة الجدول الذي حمله الفحص
# (silk_quality_gate._check_currency_label_mismatch، اللائحتان أعلاه) —
# اكتُشف عبر تدقيق عيّنة تقرير العميل (Master Prompt Part 2): جدولٌ مطبَّعٌ
# بالدولار عمداً أُعيد تعنونه «باليورو» زوراً لمجرّد أنّ قسماً آخر تماماً
# (نقاش خطر صرف العملة) يذكر «اليورو».

def test_5b_price_fix_scoped_to_table_not_whole_document():
    """حارس انحدار: دالة **الإصلاح** الشقيقة تقتصر بحثها عن العملة الأخرى
    على نافذة الجدول أيضاً — لا كامل المستند. جدولٌ مطبَّعٌ بالدولار عمداً
    (قيمه فعلاً دولارية) لا يُعاد تعنونه بسبب ذكر «اليورو» في قسمٍ آخر لا
    علاقة له بالجدول (مثل نقاش خطر صرف العملة)."""
    from silk_render import _fix_price_column_currency_label
    text = (
        "## 6. المشهد التنافسي\n"
        "| المنتج | السعر/كجم بالدولار |\n"
        "| --- | --- |\n"
        "| تمر سكري | 6.0$ |\n\n"
        "## 9. تقييم المخاطر\n"
        "يشكّل تقلّب سعر الصرف عاملاً لأنّ اليورو هو عملة السوق نفسها.")
    out = _fix_price_column_currency_label(text)
    assert "السعر/كجم بالدولار" in out
    assert "السعر/كجم باليورو" not in out


def test_5b_price_fix_still_fires_within_the_same_table_block():
    """حارس مضاد: التناقض الحقيقي داخل **نفس الجدول** (وعدُ دولارٍ بينما
    القيم الفعلية يورو) يبقى يُعنوَن بالعملة الصحيحة — التضييق لا يُسقِط
    الحالة الحقيقية (المدوّنة القانونية DZA بالضبط)."""
    from silk_render import _fix_price_column_currency_label
    text = (
        "## 6. المشهد التنافسي\n"
        "| المنتج | السعر/كجم بالدولار |\n"
        "| --- | --- |\n"
        "| زبدة فول سوداني | 9.14€ |\n\n"
        "## 7. التنظيم والوصول للسوق\nنص.")
    out = _fix_price_column_currency_label(text)
    assert "السعر/كجم باليورو" in out
    assert "السعر/كجم بالدولار" not in out


# ══════════ ٦ — سقف الملحق التقني: رسالة القطع نظيفة ══════════

def test_6_audit_coverage_message_is_clean_and_matches_counts():
    """رسالة سقف الملحق التقني تذكر الإجمالي الصحيح والسقف بلا أي مصطلح
    داخلي مسرَّب — «معلَناً هنا لا صامتاً» صراحةً (لا حذف صامت). السقف
    ارتفع 80 ← 150 (بلاغ 102 استشهاداً) فمدوّنة DZA (98) لم تعد تتجاوزه —
    يُختبَر التجاوز بمدوّنة اصطناعية فوق السقف الجديد."""
    from silk_quality_gate import _check_audit_coverage, AUDIT_APPENDIX_CAP
    out = _dza_gate()
    assert not [f for f in out["findings"]
                if f["check"] == "audit_coverage"]      # 98 ≤ 150 الآن
    total = AUDIT_APPENDIX_CAP + 18
    findings = _check_audit_coverage(
        {"missions": {"m": {"findings": [{}] * total}}})
    assert len(findings) == 1
    note = findings[0]["note"]
    assert str(total) in note
    assert str(AUDIT_APPENDIX_CAP) in note
    assert findings[0]["repairable"] is False
    for leaked in ("LLMAgent", "LLMMissionAgent", "pricing_scout",
                  "trade_flow", "dp1", "Claude", "كلود"):
        assert leaked not in note


def test_6_docx_technical_appendix_truncates_to_the_same_cap():
    """جدول الملحق الفعلي (docx) يُقصّ لنفس سقف البوّابة — ثابت واحد مشترك
    (`AUDIT_APPENDIX_CAP`)، لا 80 حرفية — فلا انحراف بين رسالة البوّابة
    وحجم الجدول المُسلَّم فعلياً."""
    import silk_reports as RP
    import inspect
    src = inspect.getsource(RP._docx_technical_appendix)
    assert "rows[:80]" not in src
    assert "AUDIT_APPENDIX_CAP" in src
    from silk_quality_gate import AUDIT_APPENDIX_CAP
    assert AUDIT_APPENDIX_CAP >= 102   # بلاغ حي: 102 استشهاداً كانت تُقصّ


# ══════════ التحقّق الشامل — البوّابة تنتقل من FAIL إلى غير-FAIL ══════════

def test_overall_verdict_moves_from_fail_to_pass_with_warnings():
    """البوّابة كانت FAIL على هذه المدوّنة قبل الإصلاح (raw_confidence كان
    ضمن حرّاس الانحدار) — بعد الإصلاح: لا FAIL، والنتائج المتبقّية (عناوين
    بنيوية متوقَّعة + تكرار رقم مفتاحي دون ٥ + سقف الملحق المُعلَن) كلّها
    WARN مقصودة بالتصميم، لا عيوباً غير مُصلَحة."""
    out = _dza_gate()
    assert out["verdict"] != "FAIL"
    checks = {f["check"] for f in out["findings"]}
    assert checks <= {"markdown_artifacts", "style_repeated_key_figure",
                     "audit_coverage"}


def test_no_regression_guard_fires_on_the_dza_blob_after_fix():
    """صفر حارس انحدار (raw_confidence/currency_label_mismatch/…) يُطلِق على
    هذه المدوّنة بعد الإصلاح — لو أطلق أحدها لعاد الحكم FAIL فوراً (مفارقة
    البوّابة، البند ١٢/الطبقة ٧)."""
    import silk_quality_gate as QG
    out = _dza_gate()
    fired = {f["check"] for f in out["findings"]} & QG._REGRESSION_GUARD_FIRED
    assert fired == set()
