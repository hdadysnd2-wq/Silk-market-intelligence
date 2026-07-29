"""أقفالُ انحدارٍ للبوّابات الثلاث — على مدوّنة تحليل ٧ الحقيقية الشكل.

> **القاعدة المعيارية للمُشرِف (بعد حادثة A3 التي مرّت الاختبارات التركيبية ثم
> لم تُطلِق على تحليل ٧ الحيّ).** كلّ بوّابةٍ تُضاف/تُعدَّل تُشحَن بقفلين معاً:
>   ١) **قفلٌ تركيبيّ** (وحدة تُثبِت المنطق)، و
>   ٢) **قفلُ انحدارٍ من عرض تحليلٍ حقيقيّ الشكل يُظهِر العيب**، يؤكّد أنّ
>      البوّابة **تُطلِق** عليه فعلاً.
> «لا بوّابة منجَزة على اختبارات تركيبية وحدها.»

هذا الملف يقفل العيوب الثلاثة التي بقيت تُسلَّم على تحليل ٧ (Nadec/اليمن) رغم
بوابات PR A، كلٌّ منها بقفلَيه على مدوّنة `tools/canonical_nadec_yemen_dairy.py`
المُمرَّرة عبر `build_view` (مسارُ العرض الفعليّ بعد التطهير):

  A3    — TAM أصغر من تدفّق دولةٍ واحدة، بصيغة رمز `$` التي كان يفلتها الاشتراط
          القديم (لفظ «دولار») فتبقى البوابة صامتة.
  المرآة — سردُ انكماشٍ (‑CAGR) مؤطَّرٌ تهديداً بينما تدفّق المرآة يفوق التصريح.
  التقادُم — سنةٌ متقادِمة (2018/2013) تقود استنتاجاً تسعيرياً مذكوراً.

Run: python3 -m pytest tests/test_gate_regression_locks_analysis7.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "tools"))


def _nadec_dr():
    """عرضُ تحليل ٧ كما يبنيه مسارُ العرض الفعليّ (build_view + تطهير)."""
    import silk_render as R
    from canonical_nadec_yemen_dairy import nadec_yemen_research_blob
    return R.build_view(nadec_yemen_research_blob())["deep_research"]


def _dr_text(text: str) -> dict:
    return {"report": {"text": text}, "missions": {}, "analyst": {}}


# ════════════════ عيّنةٌ حقيقية الشكل تُثبِت أنّها فعلاً تُظهِر العيب ════════════════

def test_canonical_nadec_report_keeps_dollar_symbol_tam_form():
    """القفلُ الجوهريّ لـA3: بعد التطهير، TAM يبقى بصيغة رمز `$` (`2,090,000$`)
    لا لفظ «دولار» — إعادةُ إنتاجٍ أمينة لسبب صمت البوابة القديمة (لو حُوِّل
    إلى «مليون دولار» لالتقطه الاشتراط القديم ولانتفت قيمة القفل)."""
    dr = _nadec_dr()
    txt = (dr.get("report") or {}).get("text") or ""
    assert "2,090,000$" in txt          # صيغة الرمز محفوظة (سبب الصمت)
    assert "22,140,000$" in txt          # تدفّق المرآة بنفس الصيغة
    assert "مليون دولار" not in txt.split("## 3")[1].split("## 4")[0]


# ════════════════════════ A3 — TAM < تدفّق دولةٍ واحدة ════════════════════════

def test_a3_real_lock_fires_on_dollar_symbol_form():
    """قفلُ انحدار (حقيقيّ الشكل): البوّابة تُطلِق على TAM بصيغة `$` — الحالة
    التي كانت صامتةً على تحليل ٧ الحيّ."""
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    out = chk(_nadec_dr())
    assert any(f["check"] == "tam_below_single_country_flow" for f in out)


def test_a3_synthetic_lock_dollar_symbol_amounts():
    """قفلٌ تركيبيّ: صيغة الرمز `$` (لا لفظ «دولار») تُطلِق البوّابة."""
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    text = ("## 3\nإجمالي واردات السوق (TAM) = 2,090,000$ عام 2023.\n\n"
            "صادرات السعودية وحدها إلى هذا السوق 22,140,000$ لنفس السنة.")
    out = chk(_dr_text(text)["deep_research"] if False else _dr_text(text))
    assert any(f["check"] == "tam_below_single_country_flow" for f in out)


def test_a3_iter_usd_amounts_recognizes_all_forms():
    """المستخلِص يلتقط الصيغ الثلاث: لاحق `$`، سابق `$`، ولفظيّ «دولار»."""
    from silk_quality_gate import _iter_usd_amounts
    vals = [round(v) for _, _, v in _iter_usd_amounts(
        "TAM = 61,000,000$، و$2 مليون، و789 ألف دولار، و17000 دولار.")]
    assert 61_000_000 in vals and 2_000_000 in vals
    assert 789_000 in vals and 17_000 in vals


def test_a3_old_word_only_form_still_matches():
    """عدم ارتداد: الصيغة اللفظية «مليون دولار» (التي كانت تعمل) تبقى تعمل."""
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    text = ("إجمالي واردات السوق (TAM) 2.09 مليون دولار.\n\n"
            "صادرات السعودية وحدها 22.14 مليون دولار.")
    assert any(f["check"] == "tam_below_single_country_flow"
               for f in chk(_dr_text(text)))


def test_a3_no_false_positive_when_flow_below_tam_dollar_form():
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    text = ("إجمالي واردات السوق (TAM) = 61,000,000$.\n\n"
            "صادرات السعودية وحدها 12,000,000$.")
    assert chk(_dr_text(text)) == []


def test_a3_no_false_positive_on_committed_spain_sample():
    """العيّنة المُلتَزَمة (إسبانيا، TAM = 61,000,000$، نموّ 9%) — لا تدفّق
    دولةٍ يفوق TAM، فلا إطلاق زائف رغم التقاط TAM بصيغة `$` الآن."""
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spain = open(os.path.join(root, "samples",
                              "research_report_latest.md")).read()
    assert chk(_dr_text(spain)) == []


def test_a3_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "tam_below_single_country_flow" in G.FAIL_TRIGGER_CHECKS


# ════════════════════════ المرآة — سردُ الانكماش ════════════════════════

def test_mirror_real_lock_fires_on_analysis7():
    from silk_quality_gate import \
        _check_mirror_divergence_contraction_narrative as chk
    out = chk(_nadec_dr())
    assert any(f["check"] == "mirror_divergence_contraction_narrative"
               for f in out)
    assert all(f["repairable"] is False for f in out)


def test_mirror_synthetic_lock_fires():
    from silk_quality_gate import \
        _check_mirror_divergence_contraction_narrative as chk
    text = ("## 3\nإجمالي واردات السوق (TAM) 2,090,000$.\n\n"
            "## 4\nانكماشٌ سنويٌّ مركّب ‑22% — التهديد الرئيسي انكماش الحجم. "
            "صادرات السعودية وحدها 22,140,000$.")
    assert any(f["check"] == "mirror_divergence_contraction_narrative"
               for f in chk(_dr_text(text)))


def test_mirror_suppressed_when_divergence_reconciled():
    """حين تُسمّى المفارقة وتُعتمَد المرآة بديلاً صراحةً — لا إطلاق (القيادة
    بالمصالحة لا بالانكماش)."""
    from silk_quality_gate import \
        _check_mirror_divergence_contraction_narrative as chk
    text = ("## 3\nإجمالي واردات السوق (TAM) 2,090,000$.\n\n"
            "## 4\nالسلسلة المُصرّحة تنكمش، لكن تدفّق المرآة (تصدير الشريك "
            "السعودية وحدها 22,140,000$) يفوق التصريح فنعتمد رقم المرآة "
            "كتقدير طلبٍ أقرب.")
    assert chk(_dr_text(text)) == []


def test_mirror_no_fire_without_contraction():
    """مرآةٌ تفوق التصريح لكن بلا سردِ انكماش — لا إطلاق (الشرطان معاً)."""
    from silk_quality_gate import \
        _check_mirror_divergence_contraction_narrative as chk
    text = ("## 3\nإجمالي واردات السوق (TAM) 2,090,000$.\n\n"
            "صادرات السعودية وحدها 22,140,000$ بنموّ صحّي.")
    assert chk(_dr_text(text)) == []


def test_mirror_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "mirror_divergence_contraction_narrative" in G.FAIL_TRIGGER_CHECKS


# ════════════════════════ التقادُم — سنةٌ تقود استنتاجاً ════════════════════════

def test_stale_real_lock_fires_on_analysis7():
    """قفلُ انحدار: سنتا 2018 (دخل الفرد) و2013 (PPP) تقودان استنتاج «القدرة
    الشرائية تقيّد التسعير» على مدوّنة تحليل ٧."""
    from silk_quality_gate import _check_stale_year_driving_conclusion as chk
    out = chk(_nadec_dr())
    years = " ".join(f["note"] for f in out)
    assert out and "2018" in years and "2013" in years


def test_stale_synthetic_lock_fires():
    from silk_quality_gate import _check_stale_year_driving_conclusion as chk
    dr = {"report": {"text": "دخل الفرد نحو 1,200 دولار (2018). القدرة "
                             "الشرائية المحدودة تقيّد التسعير المتاح."},
          "missions": {"economic": {"findings": [
              {"value": 1200, "source": "World Bank",
               "note": "دخل الفرد (2018)", "data_year": 2018}]}},
          "analyst": {}}
    out = chk(dr)
    assert any(f["check"] == "stale_year_driving_conclusion" for f in out)


def test_stale_no_fire_when_year_recent():
    """سنةٌ حديثة (ليست متقادِمة) لا تُطلِق البوّابة."""
    from silk_quality_gate import _check_stale_year_driving_conclusion as chk
    import datetime
    recent = datetime.date.today().year - 1
    dr = {"report": {"text": f"دخل الفرد ({recent}). القدرة الشرائية تقيّد "
                             "التسعير."},
          "missions": {"economic": {"findings": [
              {"value": 1200, "note": f"دخل الفرد ({recent})",
               "data_year": recent}]}},
          "analyst": {}}
    assert chk(dr) == []


def test_stale_no_fire_without_pricing_conclusion():
    """سنةٌ متقادِمة مذكورة بلا استنتاجٍ تسعيريّ مبنيٍّ عليها — وسمٌ لا فشل."""
    from silk_quality_gate import _check_stale_year_driving_conclusion as chk
    dr = {"report": {"text": "بلغ عدد السكان في 2018 نحو 30 مليون نسمة."},
          "missions": {"economic": {"findings": [
              {"value": 30_000_000, "note": "السكان (2018)",
               "data_year": 2018}]}},
          "analyst": {}}
    assert chk(dr) == []


def test_stale_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "stale_year_driving_conclusion" in G.FAIL_TRIGGER_CHECKS


# ════════════════ سنةُ الأساس تقلب إشارةَ CAGR (بلاغ المالك، مِجَسّ /trend) ════════════════

def test_cagr_sign_flip_real_lock_fires_on_analysis7():
    """قفلُ انحدار: سلسلةُ 040110 الحقيقية (2018=0.88M، ذروة 2019=5.59M،
    2023=2.09M) — أساس 2018 نموّ، أساس 2019 انكماش (‑22.08% كما في التقرير).
    الادّعاءُ الأحاديّ يُفشِل."""
    from silk_quality_gate import _check_cagr_sign_flips_under_base_year as chk
    out = chk(_nadec_dr())
    assert any(f["check"] == "cagr_sign_flips_under_base_year" for f in out)
    note = " ".join(f["note"] for f in out)
    assert "2018" in note and "2019" in note   # كلا سنتَي الأساس مُسمّاتان


def test_cagr_sign_flip_synthetic_fires_on_interior_peak():
    from silk_quality_gate import _check_cagr_sign_flips_under_base_year as chk
    dr = {"report": {"text": "شهدت السوق انكماشاً سنوياً مركّباً (CAGR)."},
          "missions": {"th": {"findings": [
              {"value": 880_000, "data_year": 2018,
               "note": "إجمالي استيراد السوق من العالم 2018, USD"},
              {"value": 5_590_000, "data_year": 2019,
               "note": "إجمالي استيراد السوق من العالم 2019, USD"},
              {"value": 2_090_000, "data_year": 2023,
               "note": "إجمالي استيراد السوق من العالم 2023, USD"}]}},
          "analyst": {}}
    assert any(f["check"] == "cagr_sign_flips_under_base_year" for f in chk(dr))


def test_cagr_sign_flip_no_fire_on_monotonic_series():
    """سلسلةٌ رتيبةٌ صاعدة — كلّ الأسس تعطي نموّاً، لا قلبَ إشارة."""
    from silk_quality_gate import _check_cagr_sign_flips_under_base_year as chk
    dr = {"report": {"text": "نموٌّ مركّب مطّرد (CAGR)."},
          "missions": {"th": {"findings": [
              {"value": 1_000_000, "data_year": 2019,
               "note": "إجمالي استيراد السوق 2019, USD"},
              {"value": 2_000_000, "data_year": 2021,
               "note": "إجمالي استيراد السوق 2021, USD"},
              {"value": 3_000_000, "data_year": 2023,
               "note": "إجمالي استيراد السوق 2023, USD"}]}},
          "analyst": {}}
    assert chk(dr) == []


def test_cagr_sign_flip_no_fire_without_directional_claim():
    """سلسلةٌ إشارتُها تنقلب لكن بلا ادّعاء اتجاهٍ في المتن — لا شيء يُقلَب."""
    from silk_quality_gate import _check_cagr_sign_flips_under_base_year as chk
    dr = {"report": {"text": "لمحةٌ عامة عن حجم السوق وقيمته الإجمالية بالدولار."},
          "missions": {"th": {"findings": [
              {"value": 880_000, "data_year": 2018, "note": "استيراد 2018, USD"},
              {"value": 5_590_000, "data_year": 2019, "note": "استيراد 2019, USD"},
              {"value": 2_090_000, "data_year": 2023, "note": "استيراد 2023, USD"}]}},
          "analyst": {}}
    assert chk(dr) == []


def test_cagr_sign_flip_suppressed_when_base_year_disclosed():
    """إفصاحٌ صحيح (§3.6): تقريرٌ يذكر «سنة الأساس» ويعرض القراءتين لا يُعاقَب —
    القاعدة تُفشِل التثبيتَ المُختار الصامت لا الإفصاح."""
    from silk_quality_gate import _check_cagr_sign_flips_under_base_year as chk
    dr = {"report": {"text": "الاتجاه غير محسوم لحساسيته لسنة الأساس: من أساس "
                             "2018 نموّ ومن أساس 2019 انكماش."},
          "missions": {"th": {"findings": [
              {"value": 880_000, "data_year": 2018, "note": "استيراد 2018, USD"},
              {"value": 5_590_000, "data_year": 2019, "note": "استيراد 2019, USD"},
              {"value": 2_090_000, "data_year": 2023, "note": "استيراد 2023, USD"}]}},
          "analyst": {}}
    assert chk(dr) == []


def test_cagr_sign_flip_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "cagr_sign_flips_under_base_year" in G.FAIL_TRIGGER_CHECKS


# ════════════════════ الحكم الكلّي: تحليل ٧ يُفشِل، والعيّنات النظيفة لا ════════════════════

def test_full_gate_fails_analysis7_view():
    """الحكم الكلّي على عرض تحليل ٧ = FAIL (البوّابات الثلاث تُطلِق)."""
    import silk_render as R
    import silk_quality_gate as QG
    from canonical_nadec_yemen_dairy import nadec_yemen_research_blob
    out = QG.run_quality_gate(R.build_view(nadec_yemen_research_blob()))
    assert out["verdict"] == QG.FAIL
    fired = {f["check"] for f in out["findings"]}
    assert {"tam_below_single_country_flow",
            "mirror_divergence_contraction_narrative",
            "stale_year_driving_conclusion",
            "cagr_sign_flips_under_base_year"} <= fired


def test_full_gate_does_not_regress_kuwait_canonical():
    """المدوّنة القانونية النظيفة (الكويت) لا تُطلِق أياً من البوّابات الأربع."""
    import silk_render as R
    import silk_quality_gate as QG
    from canonical_kuwait_peanut_butter import kuwait_research_blob
    out = QG.run_quality_gate(R.build_view(kuwait_research_blob()))
    fired = {f["check"] for f in out["findings"]}
    assert not ({"tam_below_single_country_flow",
                 "mirror_divergence_contraction_narrative",
                 "stale_year_driving_conclusion",
                 "cagr_sign_flips_under_base_year"} & fired)
