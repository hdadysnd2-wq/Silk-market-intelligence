"""PR B — تلميعُ جودة التقرير (بلاغ تحليل ٧، بعد دمج PR A الحاجبة).

بنودٌ غير حاجبةٍ للتسليم لكنها تُضعِف مهنيّة التقرير — كلُّها **بنيويّة** عدا
B1 (بنيةُ التقادم قائمةٌ أصلاً؛ التسرّب مدوّنةٌ مخزَّنة قديمة). اختبار-أولاً:

- B2 خلطُ سنة النمو: قاعدةُ كاتبٍ تُميّز النموَّ الفعليّ من الإسقاط.
- B3 تلوّثُ العملة (ريال عُمانيّ في دراسةِ اليمن): بوابةٌ حاجبة.
- B4 جدولُ الأسعار المعطَّل: قاعدةُ كاتبٍ (اشتقاق السعر/كجم أو إعلان الفجوة).
- B5 بعثةُ Trends «مكتملة» بلا بياناتها: ملاحظةٌ منهجية تُبرِز التغطية الناقصة.
- B7 فجوةٌ مكرّرةٌ حرفياً: إزالةُ التكرار على المحتوى لا السطر المُلحَق باسم البعثة.
- B8 تسريبُ الإنجليزية: ملاحظةُ الحكم المبدئيّ عربيّةٌ فقط في المصدر.
- B9 HHI «7743.7»: مُصلِحُ عرضٍ يقرّبها إلى صحيح.
- B10 «المؤشرات المساهمة: 100»: صياغةُ الأساس «مقسوماً على» لا مقامٌ عارٍ.

Run: python3 -m pytest tests/test_report_quality_scoping_pr_b.py -q
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dr(text="", missions=None, market=None):
    return {"deep_research": {
        "report": {"text": text},
        "missions": missions or {},
        "market": market or {},
    }}


# ════════════════════ B2 — النمو الفعلي مقابل الإسقاط ════════════════════

def test_writer_prompt_distinguishes_actual_growth_from_projection():
    import silk_ai_judge
    src = inspect.getsource(silk_ai_judge.deep_report)
    assert "النمو الفعلي مقابل الإسقاط" in src or "نموٍّ فعليّ" in src
    assert "إسقاط" in src


# ════════════════════ B3 — تلوّث العملة (خارج السوق) ════════════════════

def test_off_market_currency_gate_fires_omr_in_yemen():
    from silk_quality_gate import _check_off_market_currency as chk
    dr = _dr(text="بلغ السعر 3 ريال عماني للتر في السوق اليمني.",
             market={"iso3": "YEM"})["deep_research"]
    out = chk(dr)
    assert any(f["check"] == "off_market_currency" for f in out)
    assert all(f["repairable"] is False for f in out)


def test_off_market_currency_allows_market_own_currency():
    from silk_quality_gate import _check_off_market_currency as chk
    # عملةُ السوق نفسها مشروعة.
    dr = _dr(text="بلغ السعر 500 ريال يمني للتر.",
             market={"iso3": "YEM"})["deep_research"]
    assert chk(dr) == []


def test_off_market_currency_skips_when_market_unknown():
    from silk_quality_gate import _check_off_market_currency as chk
    dr = _dr(text="السعر 3 ريال عماني.", market={})["deep_research"]
    assert chk(dr) == []


def test_off_market_currency_allows_usd():
    from silk_quality_gate import _check_off_market_currency as chk
    dr = _dr(text="بلغ السعر 2 دولار للتر في اليمن.",
             market={"iso3": "YEM"})["deep_research"]
    assert chk(dr) == []


def test_off_market_currency_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "off_market_currency" in G.FAIL_TRIGGER_CHECKS


# ════════════════════ B4 — جدول الأسعار ════════════════════

def test_writer_prompt_has_price_per_unit_and_benchmark_gap_rule():
    import silk_ai_judge
    src = inspect.getsource(silk_ai_judge.deep_report)
    assert "السعر/الوحدة" in src or "السعر/كجم عند توفّر" in src
    assert "فجوةَ المقارنة التنافسية" in src


# ════════════════════ B5 — بعثة Trends مكتملة بلا بياناتها ════════════════════

def test_trends_hollow_completion_flagged():
    from silk_quality_gate import _check_trends_hollow_completion as chk
    dr = _dr(missions={"demand_trends": {
        "failed": False, "label": "تحليل الطلب والاتجاهات",
        "summary": "رُصد طلبٌ عام. فجوات: بيانات Google Trends والموسمية "
                   "الرمضانية غير متاحة."}})["deep_research"]
    out = chk(dr)
    assert any(f["check"] == "trends_hollow_completion" for f in out)


def test_trends_hollow_completion_silent_when_no_trends_gap():
    from silk_quality_gate import _check_trends_hollow_completion as chk
    dr = _dr(missions={"demand_trends": {
        "failed": False, "label": "تحليل الطلب",
        "summary": "رُصدت الموسمية الرمضانية بوضوح من مؤشرات الاتجاهات."}}
        )["deep_research"]
    assert chk(dr) == []


def test_trends_hollow_completion_silent_when_failed():
    from silk_quality_gate import _check_trends_hollow_completion as chk
    # فشلٌ صريح محكومٌ عبر agent_failed — لا تكرار.
    dr = _dr(missions={"demand_trends": {
        "failed": True, "label": "تحليل الطلب",
        "summary": "فجوات: Trends والموسمية غير متاحة."}})["deep_research"]
    assert chk(dr) == []


# ════════════════════ B7 — إزالة تكرار الفجوة على المحتوى ════════════════════

def test_duplicate_gap_deduped_across_missions():
    from silk_reports import _client_gap_inputs
    # بعثتان تُعلنان نفس الفجوة نصّاً — يجب أن تظهر مرّة واحدة.
    dr = {"missions": {
        "channels_importers": {"label": "قنوات ومستوردون",
                               "summary": "فجوات: قنوات التوزيع والاستيراد"},
        "logistics": {"label": "اللوجستيات",
                      "summary": "فجوات: قنوات التوزيع والاستيراد"}}}
    _critical, informational = _client_gap_inputs(dr)
    hits = [g for g in informational if "قنوات التوزيع والاستيراد" in g]
    assert len(hits) == 1, hits


# ════════════════════ B8 — لا تسريب إنجليزي في ملاحظة الحكم ════════════════════

def test_preliminary_note_is_arabic_only_in_source():
    import silk_agents
    src = inspect.getsource(silk_agents)
    # النصفُ الإنجليزيّ حُذف من المصدر (المُنظِّف يبقى حارساً للمخزَّن القديم).
    assert "Preliminary only" not in src
    import silk_engine
    assert "gaps flagged, not estimated" not in inspect.getsource(silk_engine)


# ════════════════════ B9 — تقريب دقّة HHI الوهميّة ════════════════════

def test_fix_hhi_false_precision_rounds_to_integer():
    from silk_render import _fix_hhi_false_precision
    assert "HHI 7744" in _fix_hhi_false_precision("سوق مفتّت (HHI 7743.7).")
    assert "7743.7" not in _fix_hhi_false_precision("HHI 7743.7")
    # عددٌ صحيحٌ أصلاً لا يتغيّر.
    assert _fix_hhi_false_precision("HHI 7743") == "HHI 7743"


def test_hhi_false_precision_now_repairable_and_guarded():
    import silk_quality_gate as G
    f = G._check_hhi_false_precision("سوق مفتّت (HHI 7743.7).")
    assert f and f[0]["repairable"] is True
    assert "hhi_false_precision" in G._REGRESSION_GUARD_FIRED


# ════════════════════ B10 — أساس شدّة المنافسة بلا مقامٍ عارٍ ════════════════════

def test_competition_basis_spells_out_divisor():
    import silk_decision
    # نصُّ الأساس المُنتَج فعلاً (لا مصدر الوحدة الذي يشرح الشكل القديم).
    basis = silk_decision._pillar_competition(
        {"hhi": 0.4, "top_supplier_share_pct": 30,
         "named_company_count": 5})["basis"]
    assert "مقسومةً على" in basis or "مقسوماً على" in basis
    # لم يعد المقام العاري «/100» ظاهراً في نص الأساس المعروض.
    assert "الأكبر/100" not in basis
