"""حوارُ الالتباس اللفظيّ في التصنيف — fat-content/preservation disambiguation.

بلاغ المُشرِف: `confirm_hs` تداخلٌ لفظيّ صرف، فـ«حليب» تطابق 040110/040120/
040140/0402 معاً (الفارقُ نسبةُ دهنٍ/حالةُ حفظٍ لا كلمة). اسمٌ عامّ يمضي
صامتاً على البند الأدنى بدل أن يُطالَب المشغّلُ بالاختيار. هذا الملف يقفل أنّ
البوّابة تعرض حوارَ التمييز (نسبة الدهن + حالة الحفظ) على الاسم العامّ، ولا
تُطلِق على اسمٍ يُثبِّت البند أو على منتجٍ بترويسةٍ أحاديّة البند.

Run: python3 -m pytest tests/test_hs_axis_disambiguation.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_generic_milk_name_needs_axis_disambiguation():
    import silk_hs_confirm as C
    assert C.axis_disambiguation_needed("حليب", "040110") is True


def test_fat_qualified_name_does_not_need_disambiguation():
    import silk_hs_confirm as C
    # اسمٌ يحمل صفةَ درجةٍ («كامل الدسم») يُثبِّت البند — لا التباسَ محوريّ.
    assert C.axis_disambiguation_needed("حليب كامل الدسم", "040140") is False


def test_explicit_percentage_pins_the_band():
    import silk_hs_confirm as C
    assert C.axis_disambiguation_needed("حليب 3.5%", "040120") is False


def test_single_band_heading_is_not_ambiguous():
    import silk_hs_confirm as C
    # تمور (080410) ترويسةٌ بلا محورٍ رقميّ متعدّد — لا التباس.
    assert C.axis_disambiguation_needed("تمور", "080410") is False


def test_preflight_surfaces_axis_dialog_for_generic_milk():
    import silk_hs_confirm as C
    blk = C.preflight_block("حليب", "040110", hs_confirmed=False,
                            allow_claude=False)
    assert blk is not None
    assert blk["error"] == "hs_axis_disambiguation_needed"
    # حدود الدهن المفهومة حاضرة — لا عتبةٌ جمركية خام.
    bands = [c.get("band_ar") for c in blk.get("candidates") or []
             if c.get("band_ar")]
    assert len(bands) >= 2
    assert any("نسبة الدهن" in b for b in bands)
    # الرسالة تطالب صراحةً بتحديد الدهن/الحفظ لا مجرّد «حاول مجدداً».
    assert "نسبة الدهن" in blk["message"] and "الحفظ" in blk["message"]


def test_preflight_axis_candidates_span_preservation_state():
    """المرشّحون يشملون بندَ حفظٍ مغايراً (مركّز/مجفّف 0402) لا الطازج وحده —
    حالةُ الحفظ محورٌ ثانٍ يجب أن يراه المشغّل."""
    import silk_hs_confirm as C
    blk = C.preflight_block("حليب", "040110", hs_confirmed=False,
                            allow_claude=False)
    codes = [str(c.get("hs6") or "") for c in blk.get("candidates") or []]
    assert any(c.startswith("0401") for c in codes)   # طازج (نسب دهن)
    assert any(c.startswith("0402") for c in codes)   # مركّز/مجفّف


def test_hs_confirmed_bypasses_axis_dialog():
    """المشغّل الذي أكّد صراحةً (اختار البند) لا يُعاد سؤاله."""
    import silk_hs_confirm as C
    assert C.preflight_block("حليب", "040110", hs_confirmed=True) is None


def test_non_dairy_products_not_over_blocked():
    """منتجاتٌ بترويساتٍ أحاديّة البند تمرّ كما كانت — لا حجبَ زائف."""
    import silk_hs_confirm as C
    for name, code in [("تمور", "080410"), ("زبدة الفول السوداني", "200811"),
                       ("عسل سدر", "040900")]:
        blk = C.preflight_block(name, code, hs_confirmed=False,
                                allow_claude=False)
        err = None if blk is None else blk.get("error")
        assert err != "hs_axis_disambiguation_needed", (name, code, err)


def test_data_driven_no_hardcoded_product_or_code():
    """القاعدة مبنيّة على البيانات (أشقّاء المرجع + صفات الدرجة) لا على اسمِ
    منتجٍ أو رمزٍ صلب في المنطق (الدرس ٢٤/٣٠)."""
    import inspect
    import re
    import silk_hs_confirm as C
    src = inspect.getsource(C.axis_disambiguation_needed)
    # لا رمزَ HS صلب (تسلسلُ أرقامٍ ٤-٦) ولا اسمَ منتجٍ حرفيّ في المنطق.
    assert not re.search(r"\b\d{4,6}\b", src), "رمز HS صلب في المنطق"
    for banned in ("حليب", "milk", "الحليب"):
        assert banned not in src, f"اسمُ منتجٍ صلب في المنطق: {banned}"
