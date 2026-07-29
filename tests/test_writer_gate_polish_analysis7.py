"""بلاغ إنتاجي (Nadec/اليمن #7): بعد إصلاح agent_failed/hhi، بقيت أربع
ملاحظات بوّابة من طبقة الكاتب — واحدة حاجبة (dangling_cross_reference =
FAIL) وثلاث تحذيرية (style_repeated_key_figure ×4، markdown_artifacts،
lpi_invalid_edition_year). تُصلَح في برومبت الكاتب/المصدر لا بإعادة التشغيل.

الأقفال (test-first):
1. الإحالة المنهجية تستعمل مرساةً يعرفها الحارس («قسم المنهجية») لا
   «الملاحظة المنهجية» التي يُفشِلها `_check_dangling_cross_reference`.
2. برومبت الكاتب يحمل قاعدة «اذكر الرقم المفتاحي كاملاً مرّة ثم أحِل إليه».
3. برومبت الكاتب يسمّي نسخ LPI المنشورة الصحيحة (لا سنة بينية).
4. المصدر: `world_bank` لعائلة LPI يثبّت السنة على نسخة منشورة فعلاً —
   فلا يقرن سنةً غير منشورة بالمؤشر في الملاحظة (يجتاز `_check_lpi_edition_year`).

Run: python3 -m pytest tests/test_writer_gate_polish_analysis7.py -q
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── ١) الإحالة المنهجية: مرساة يعرفها الحارس (يُنهي FAIL الحاجب) ────────────

def test_writer_prompt_references_recognized_methodology_anchor():
    """برومبت الكاتب يحيل إلى «قسم المنهجية» (مرساة يقبلها الحارس) لا إلى
    «الملاحظة المنهجية» (التي يُفشِلها الحارس ما لم يوجد النصّ الحرفي)."""
    import silk_ai_judge
    src = inspect.getsource(silk_ai_judge.deep_report)
    assert "انظر الملاحظة المنهجية" not in src   # المرساة المرفوضة أُزيلت
    assert "انظر قسم المنهجية" in src              # المرساة المقبولة


def test_recognized_anchor_does_not_trip_dangling_cross_reference():
    """سلوكياً: نصّ يحيل «انظر قسم المنهجية» لا يُطلِق dangling_cross_reference
    (المرساة حاضرة)؛ بينما «انظر الملاحظة المنهجية» بلا نصّها يبقى يُطلِقه."""
    from silk_quality_gate import _check_dangling_cross_reference as chk
    ok = ("## 2. منهجية البحث ونطاقه\nنُطِّرت الأرقام سياقياً.\n\n"
          "## 8. تحليل التجارة\nالرقم مؤشر سياقي (انظر قسم المنهجية).")
    assert chk(ok) == []
    bad = ("## 8. تحليل التجارة\nالرقم مؤشر سياقي "
           "(انظر الملاحظة المنهجية).")
    assert any(f["check"] == "dangling_cross_reference" for f in chk(bad))


# ── ٢) قاعدة الرقم المفتاحي: اذكره كاملاً مرّة ثم أحِل إليه ──────────────────

def test_writer_prompt_has_repeated_key_figure_rule():
    import silk_ai_judge
    src = inspect.getsource(silk_ai_judge.deep_report)
    assert "رقم مفتاحي" in src
    # نصّ القاعدة: كاملاً مرّة ثم إحالة لفظية (لا تكرار الرقم حرفياً)
    assert "أحِل إليه" in src or "أحِل إليها" in src


# ── ٣) قاعدة نسخ LPI المنشورة ───────────────────────────────────────────────

def test_writer_prompt_names_valid_lpi_editions():
    import silk_ai_judge
    src = inspect.getsource(silk_ai_judge.deep_report)
    assert "LPI" in src
    for ed in ("2018", "2023"):
        assert ed in src


# ── ٤) المصدر: LPI يثبّت السنة على نسخة منشورة فعلاً ────────────────────────

def test_lpi_family_snaps_to_published_edition_year():
    """`world_bank` لعائلة LPI لا تقرن سنةً غير منشورة (2022) بالمؤشر — تثبّت
    على أحدث نسخة منشورة (2023) فتجتاز `_check_lpi_edition_year`."""
    import silk_data_layer as D
    from silk_quality_gate import _check_lpi_edition_year

    captured = {}

    def _fake_for_year(iso3, indicator, year):
        captured["year"] = year   # السنة الفعلية المطلوبة بعد التثبيت
        return D.DataPoint(3.1, "World Bank", 0.95,
                           f"{indicator} (2023)", "2026-07-29", data_year=2023)

    orig = D._world_bank_for_year
    D._world_bank_for_year = _fake_for_year
    try:
        dp = D.world_bank("YEM", "LP.LPI.OVRL.XQ", year=2022)
    finally:
        D._world_bank_for_year = orig
    # السنة غير المنشورة 2022 ثُبِّتت على نسخة منشورة قبل الجلب
    assert captured["year"] in (2023, 2018, None)
    assert captured["year"] != 2022
    # الملاحظة الناتجة لا تُطلِق حارس نسخة LPI
    assert _check_lpi_edition_year(f"LPI {dp.note}") == []


def test_non_lpi_indicator_year_unchanged():
    """التثبيت خاصٌّ بعائلة LPI فقط — مؤشر آخر (WGI) يمرّ بسنته كما هي."""
    import silk_data_layer as D
    captured = {}

    def _fake_for_year(iso3, indicator, year):
        captured["year"] = year
        return D.DataPoint(0.4, "World Bank", 0.95, f"{indicator} (2022)",
                           "2026-07-29", data_year=2022)

    orig = D._world_bank_for_year
    D._world_bank_for_year = _fake_for_year
    try:
        D.world_bank("YEM", "PV.EST", year=2022)
    finally:
        D._world_bank_for_year = orig
    assert captured["year"] == 2022   # لا تثبيت لغير LPI
