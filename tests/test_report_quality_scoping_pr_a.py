"""PR A — أقفال جودة التقرير الحاجبة للتسليم (اختبار-أولاً · test-first).

بلاغ إنتاجي (تحليل ٧، Nadec/اليمن) — أربعة عيوب تمنع تسليم التقرير لعميل،
كلٌّ منها **بنيويّ** (يمسّ كل تقرير مستقبلي) لا خاصّ بتحليل واحد:

- A1 تعارض قيمة الثقة: الملخّص «ثقة منخفضة (50%)» والقسم ٤ «الثقة متوسطة
  (73%)» — رقمٌ واحد (`verdict["confidence"]`) سُقِّف في طبقة العرض (سقف رمز
  HS المُعلَّم 0.5) **بعد** أن جمّد الكاتب النسخة غير المسقوفة 0.73 في المتن.
  الفكس: تمرير القيمة المسقوفة نفسها للكاتب (مصدر واحد) + بوابة تُفشِل على
  نسبتَي ثقة مختلفتين.
- A2 تعارض تسمية الحكم: الغلاف/§5 «التوصية بالدخول» بينما §4 «توصية أولية».
  الرمز `PRELIMINARY GO` يُترجَم بدالّتين متباعدتين. الفكس: نغمة/تسمية
  `preliminary` واحدة يتّفق عليها الكاتب والغلاف («توصية أولية بالدخول»
  حيثما ورد — قرار المالك) + بوابة تُفشِل على تسميتَي حكم متعارضتين.
- A3 TAM أصغر من تدفّق دولة واحدة: §3.1 يضع TAM = 2.09M بينما يذكر تدفّق
  السعودية وحدها 22.14M لنفس السوق/السنة (تعارض منظور استيراد↔تصدير المرآة
  الذي لا تُصالحه مصالحة العالم↔الشركاء). الفكس: بوابة تُفشِل حين تكون
  TAM المذكورة < تدفّق دولة واحدة مذكور لنفس السوق.
- A4 CAGR الطرفي يقرأ صدمةً كانحدارٍ مطّرد: المسار 5.59→13.03→10.21→2.09
  (تضاعف ثم انهيار) يُلخَّص «‑22% انكماش مطّرد». النقاط السنوية متاحة للكاتب
  أصلاً (كل سنة DataPoint من comtrade_imports)؛ الفكس قاعدة كاتب: صف المسار
  الفعلي وسمِّ سنة الانكسار حين تكون السلسلة غير رتيبة، ووحِّد عدد سنوات
  النافذة.

Run: python3 -m pytest tests/test_report_quality_scoping_pr_a.py -q
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dr(text="", missions=None, verdict=None, analyst=None):
    return {"deep_research": {
        "report": {"text": text},
        "missions": missions or {},
        "verdict": verdict or {},
        "analyst": analyst or {"summary": "", "by_category": {},
                               "missing_categories": []},
    }}


# ════════════════════ A1 — تعارض قيمة الثقة ════════════════════

def test_confidence_value_conflict_gate_fires_on_two_distinct_pcts():
    from silk_quality_gate import _check_confidence_value_conflict as chk
    text = ("الخلاصة: ثقة منخفضة (50%) في هذا الحكم.\n\n"
            "القسم ٤: الثقة متوسطة (73%) في تحليل السوق، والثقة متوسطة "
            "(73%) في المنافسة.")
    out = chk(text)
    assert any(f["check"] == "confidence_value_conflict" for f in out)
    assert all(f["repairable"] is False for f in out)


def test_confidence_value_conflict_ignores_single_value_repeated():
    from silk_quality_gate import _check_confidence_value_conflict as chk
    # نفس النسبة مكرّرة ليست تعارضاً.
    assert chk("الثقة متوسطة (73%) هنا، والثقة متوسطة (73%) هناك.") == []


def test_confidence_value_conflict_ignores_non_confidence_percentages():
    from silk_quality_gate import _check_confidence_value_conflict as chk
    # نسب حصص/نمو ليست نسب ثقة — لا تعارض.
    assert chk("حصة السعودية 22% ونمو الواردات 73% وحصة هولندا 50%.") == []


def test_confidence_conflict_no_false_positive_near_keyword():
    from silk_quality_gate import _check_confidence_value_conflict as chk
    # «ثقة» تصادف قربَ نسبتَي حصّة/نمو غير ملاصقتين لها — ليست نسب ثقة.
    assert chk("نثق بأن حصة السوق 40% ونثق أن النمو بلغ 60% هذا العام.") == []


def test_confidence_value_conflict_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "confidence_value_conflict" in G.FAIL_TRIGGER_CHECKS


def test_confidence_conflict_run_quality_gate_fails():
    from silk_quality_gate import run_quality_gate, FAIL
    view = _dr("ثقة منخفضة (50%).\n\nالثقة متوسطة (73%).")
    out = run_quality_gate(view)
    assert out["verdict"] == FAIL
    assert any(f["check"] == "confidence_value_conflict"
               for f in out["findings"])


# ── تمرير الثقة المسقوفة من مصدر واحد ───────────────────────────────────────

def _flagged_conf():
    # عقد تأكيد رمز غير مؤكَّد (confirmed=False) — يُشغِّل السقف.
    return {"confirmed": False, "hs_code": "040110",
            "code_desc": "حليب لا يتجاوز 1% دسم",
            "missing_terms": ["مبستر", "طويل الأمد"]}


def test_cap_confidence_helper_caps_flagged_and_leaves_unflagged():
    from silk_hs_confirm import cap_confidence_for_flagged_hs as cap
    capped = cap({"confidence": 0.73}, _flagged_conf())
    assert capped["confidence"] == 0.5
    # نغمة إلى الأدنى فقط — قيمة دون السقف لا تُرفَع.
    assert cap({"confidence": 0.3}, _flagged_conf())["confidence"] == 0.3
    # رمز مؤكَّد/غير معلَّم: بلا تغيير.
    assert cap({"confidence": 0.73}, {"confirmed": True})["confidence"] == 0.73
    assert cap({"confidence": 0.73}, None)["confidence"] == 0.73


def test_cap_confidence_helper_is_idempotent_and_caps_ai():
    from silk_hs_confirm import cap_confidence_for_flagged_hs as cap
    once = cap({"confidence": 0.9, "ai": {"confidence": 0.88}}, _flagged_conf())
    twice = cap(once, _flagged_conf())
    assert once["confidence"] == 0.5 and once["ai"]["confidence"] == 0.5
    assert twice == once


def test_writer_sees_capped_confidence_after_threading():
    """المصدر الواحد: بعد تسقيف الحكم، ملخّص الكاتب يذكر 50% لا 73%."""
    from silk_hs_confirm import cap_confidence_for_flagged_hs as cap
    from silk_ai_judge import _summarize_verdict
    capped = cap({"verdict": "GO", "confidence": 0.73}, _flagged_conf())
    summary = _summarize_verdict(capped)
    assert "50%" in summary and "73%" not in summary


def test_cap_helper_wired_into_render_and_api_paths():
    """المصدر الواحد بنيوياً: طبقة العرض والمعالج (رئيسي + regen) يستعملون
    نفس المُسقِّف — لا نسخة سقفٍ محلية تتباعد عمّا يراه الكاتب."""
    import inspect
    import silk_render
    import api
    assert "cap_confidence_for_flagged_hs" in inspect.getsource(silk_render)
    api_src = inspect.getsource(api)
    assert api_src.count("cap_confidence_for_flagged_hs") >= 2  # main + regen


# ════════════════════ A2 — تعارض تسمية الحكم ════════════════════

def test_preliminary_go_has_own_tone_and_label():
    from silk_render import _verdict_tone, _VERDICT_LABELS_AR
    assert _verdict_tone("PRELIMINARY GO — مبدئي إيجابي") == "preliminary"
    assert _VERDICT_LABELS_AR["preliminary"] == "توصية أولية بالدخول"


def test_preliminary_arabic_label_also_maps_to_preliminary_tone():
    """مسارٌ يضع التسمية العربية «توصية أولية بالدخول» بدل الرمز الإنجليزي
    لا ينهار إلى go (وإلا عاد التناقض)."""
    from silk_render import _verdict_tone
    assert _verdict_tone("توصية أولية بالدخول") == "preliminary"


def test_preliminary_cover_label_matches_writer_label():
    """الغلاف (تصنيف النغمة) والكاتب (verdict_ar) يعطيان التسمية نفسها —
    لا «التوصية بالدخول» على الغلاف مقابل «توصية أولية» في المتن."""
    from silk_render import _verdict_tone, _VERDICT_LABELS_AR
    from silk_narrative import verdict_ar
    token = "PRELIMINARY GO — مبدئي إيجابي"
    cover = _VERDICT_LABELS_AR[_verdict_tone(token)]
    writer = verdict_ar(token)
    assert cover == writer == "توصية أولية بالدخول"


def test_preliminary_tone_has_no_keyerror_in_badge_dicts():
    """`_VERDICT_TEXT_COLORS[tone]` فهرسةٌ مباشرة — النغمة الجديدة يجب أن
    تحمل لوناً وإلا KeyError على شارة الغلاف."""
    from silk_reports import _VERDICT_TEXT_COLORS
    assert "preliminary" in _VERDICT_TEXT_COLORS


def test_verdict_label_conflict_gate_fires_on_two_bare_labels():
    from silk_quality_gate import _check_verdict_label_conflict as chk
    text = ("## 1. الخلاصة\nالتوصية بالدخول إلى السوق اليمني.\n\n"
            "## 4. تحليل السوق\nنرى توصية أولية بالدخول في هذه المرحلة.")
    out = chk(_dr(text)["deep_research"])
    assert any(f["check"] == "verdict_label_conflict" for f in out)


def test_verdict_label_conflict_allows_future_flip_condition():
    from silk_quality_gate import _check_verdict_label_conflict as chk
    # حكمٌ واحد + شرط قلبٍ مستقبليّ مصاغ صراحة ليس تعارضاً.
    text = ("## 1. الخلاصة\nدخول مشروط بالسوق.\n\n"
            "## 10. التوصيات\nيتحول إلى التوصية بالدخول إذا تحقّق شرطان.")
    assert chk(_dr(text)["deep_research"]) == []


def test_verdict_label_conflict_single_label_passes():
    from silk_quality_gate import _check_verdict_label_conflict as chk
    text = ("## 1. الخلاصة\nتوصية أولية بالدخول.\n\n"
            "## 5. الحكم\nنؤكّد توصية أولية بالدخول بثقة مسقوفة.")
    assert chk(_dr(text)["deep_research"]) == []


def test_verdict_label_conflict_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "verdict_label_conflict" in G.FAIL_TRIGGER_CHECKS


# ════════════════════ A3 — TAM < تدفّق دولة واحدة ════════════════════

def test_tam_below_single_country_flow_gate_fires():
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    text = ("## 3. حجم السوق\nإجمالي واردات السوق (TAM) 2.09 مليون دولار "
            "سنة 2023.\n\nفي المقابل بلغت صادرات السعودية وحدها إلى هذا "
            "السوق 22.14 مليون دولار لنفس السنة.")
    out = chk(_dr(text)["deep_research"])
    assert any(f["check"] == "tam_below_single_country_flow" for f in out)
    assert all(f["repairable"] is False for f in out)


def test_tam_gate_passes_when_flow_below_tam():
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    text = ("## 3. حجم السوق\nإجمالي واردات السوق (TAM) 50 مليون دولار.\n\n"
            "صادرات السعودية إلى هذا السوق 12 مليون دولار.")
    assert chk(_dr(text)["deep_research"]) == []


def test_tam_gate_passes_when_no_tam_stated():
    from silk_quality_gate import _check_tam_below_single_country_flow as chk
    text = "صادرات السعودية 22 مليون دولار (بلا ذكر لإجمالي السوق)."
    assert chk(_dr(text)["deep_research"]) == []


def test_tam_gate_is_a_fail_trigger():
    import silk_quality_gate as G
    assert "tam_below_single_country_flow" in G.FAIL_TRIGGER_CHECKS


# ════════════════════ A4 — CAGR واعٍ للمسار ════════════════════

def test_writer_prompt_has_non_monotonic_path_rule():
    import silk_ai_judge
    src = inspect.getsource(silk_ai_judge.deep_report)
    assert "سنة الانكسار" in src        # سمِّ سنة الانكسار
    assert "غير رتيب" in src or "غير رتيبة" in src   # سلسلة غير رتيبة
    # لا تُقدَّم النسبة الطرفية كأنها انحدار مطّرد
    assert "المسار الفعلي" in src


def test_writer_prompt_requires_consistent_window_count():
    import silk_ai_judge
    src = inspect.getsource(silk_ai_judge.deep_report)
    # عدد سنوات النافذة مذكورٌ بصياغة واحدة متسقة (§3.1/§3.3 لا يختلفان)
    assert "عدد سنوات" in src or "طول النافذة" in src
