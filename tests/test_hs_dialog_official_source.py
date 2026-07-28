"""قفلُ حوار المرشّحين — مصدرٌ رسميٌّ واحد، أشقّاءُ مكتملون، بلا نثرٍ مولَّد.

> **الحادثة (بلاغ المُشرِف).** الحوارُ عرض على التاجر حدوداً **تناقض المرجع
> الرسميّ** (`040110` بوصف «لا تتجاوز 6%» وبندُه ≤١٪)، ورمزاً **لا وجود له**
> (`040190`)، ومجموعةً **ناقصة** (سقط `040140`/`040150` فلم يجد منتجٌ كامل
> الدسم خياراً صحيحاً)، ونصّاً يحمل **اسم العلامة** داخل وصفٍ عامّ.
>
> **ليست حادثةَ ألبان.** كلُّ تأكيدٍ هنا يجري على **كامل** `data/hs_reference.csv`
> لا على عيّنة ولا على `0401`. الحوارُ يُبلَغ على ٦٢ من ٧٢ ترويسةً متعدّدةَ
> النطاقات وعلى كلّ ترويسةٍ يعجز المُحلِّل عن قياسها — فهو المسارُ الافتراضيّ.
>
> **الخطورة:** يُنتِج **رمزاً جمركياً خاطئاً بلا أيّ إشارةٍ للتاجر** — أسوأ
> من البلاغ الذي فتح #177 (سؤالٌ زائد لا خطأٌ صامت).

هرمتي بالكامل: يقرأ المرجعَ الحقيقيّ، بلا شبكة وبلا مفاتيح.

Run: python3 -m pytest tests/test_hs_dialog_official_source.py -q
"""
from __future__ import annotations

import collections
import os
import re
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import silk_hs_attributes as attrs          # noqa: E402
import silk_hs_dialog as dialog             # noqa: E402
from silk_hs_resolver import load_hs_reference  # noqa: E402


# ── مساعِدات: بنيةُ المرجع الحقيقيّ كاملةً ───────────────────────────────────

def _multi_band_headings() -> dict:
    """`{heading: {axis: [hs6, …]}}` لكلّ ترويسةٍ ذات بندين فأكثر بنطاق."""
    index: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for code in load_hs_reference():
        band = attrs.band_of(code)
        if band is not None:
            index[code[:4]][band["axis"]].append(code)
    return {h: {a: sorted(v) for a, v in axes.items()}
            for h, axes in index.items()
            if sum(len(v) for v in axes.values()) >= 2}


_HEADINGS = _multi_band_headings()
_AXIS_GROUPS = [(h, a, sorted(codes))
                for h, axes in _HEADINGS.items()
                for a, codes in axes.items() if len(codes) >= 2]


def test_reference_sample_is_large_enough_to_be_a_property_test():
    """الحارسُ بلا عيّنةٍ كافية ليس خاصّية — أثبِت الحجم صراحةً."""
    assert len(_HEADINGS) >= 60, f"ترويسات: {len(_HEADINGS)}"
    assert len(_AXIS_GROUPS) >= 60, f"مجموعاتُ محاور: {len(_AXIS_GROUPS)}"


# ══════════════ E2 — مصدرٌ واحد للنصّ، على كامل المرجع ══════════════════════

def test_every_multiband_heading_renders_text_faithful_to_the_reference():
    """**خاصّيةٌ على كلّ** ترويسةٍ متعدّدةِ النطاقات: النصُّ المعروض لكلّ بند
    هو وصفُه الرسميّ **حرفياً**، لا بذرةً ولا نثرَ نموذج."""
    ref = load_hs_reference()
    checked = 0
    for heading, axes in _HEADINGS.items():
        for _axis, codes in axes.items():
            rows = dialog.build_candidates("منتجٌ ما", codes)
            by_code = {r["hs6"]: r for r in rows}
            for code in codes:
                assert code in by_code, f"{heading}: {code} سقط من العرض"
                shown = by_code[code]["description_ar"]
                assert shown == ref[code], (
                    f"{heading}/{code}: النصُّ المعروض يخالف المرجع الرسميّ\n"
                    f"  معروض: {shown!r}\n  رسميّ : {ref[code]!r}")
                checked += 1
    assert checked >= 380, f"بنودٌ مفحوصة: {checked}"


def test_band_text_matches_the_official_numeric_range_on_every_heading():
    """حدُّ البند المعروض مشتقٌّ من نطاقه الرسميّ — تُقارَن أرقامُه بالنطاق."""
    checked = 0
    for _heading, _axis, codes in _AXIS_GROUPS:
        for row in dialog.build_candidates("منتجٌ ما", codes):
            band = attrs.band_of(row["hs6"])
            if band is None:
                continue
            text = row["band_ar"]
            assert text, f"{row['hs6']}: بلا حدٍّ معروض رغم وجود نطاق"
            for edge in (band["lo"], band["hi"]):
                if edge is None:
                    continue
                shown = str(int(edge)) if float(edge).is_integer() else f"{edge:g}"
                assert shown in text, (
                    f"{row['hs6']}: الحدُّ {shown} غائبٌ عن «{text}»")
            checked += 1
    # ١٥٠ بنداً في ٦١ مجموعةَ محورٍ ذاتِ عضوين فأكثر (المقيسُ فعلياً؛ الـ٣٩٣
    # الكاملة تشمل مجموعاتٍ مفردةَ العضو لا حدودَ مقارَنة فيها).
    assert checked >= 150, f"بنودٌ مفحوصة: {checked}"


def test_a_code_absent_from_the_official_reference_is_never_offered():
    """`040190` الذي عُرِض في البلاغ لا وجود له في **أيٍّ** من مدوّنتَينا —
    ولا يُعرَض. ويُعمَّم: أيُّ رمزٍ مجهولٍ لكليهما يُسقَط بمعزلٍ عن مصدره."""
    from silk_hs_resolver import load_hs_codes
    known = set(load_hs_reference()) | {r["hs_code"] for r in load_hs_codes()}
    invented = [c for c in ("040190", "999998", "010199", "220499")
                if c not in known]
    assert invented, "لم تُبنَ عيّنةُ رموزٍ غيرِ موجودة"
    rows = dialog.build_candidates("منتجٌ ما", ["040110"] + invented)
    shown = {r["hs6"] for r in rows}
    assert not (shown & set(invented)), f"عُرِض رمزٌ مُختلَق: {shown & set(invented)}"
    assert all(dialog.is_a_real_code(c) for c in shown)


def test_our_incomplete_reference_copy_is_not_a_ceiling_on_the_merchant():
    """اللائحة ٣٩ من الطرف المقابل: نسختُنا من المرجع (٥٦١٣) **مجموعةٌ جزئية**
    من البذرة (٥٦٢٦). ثلاثةَ عشرَ بنداً حقيقياً تعرفه البذرةُ وحدها — تُعرَض،
    لأنّ نقصَ نسختنا ليس سقفاً على ما يراه التاجر. وتُعرَض **بلا نصٍّ
    مُختلَق**: `description_ar` فارغٌ لأنّ لا وصفَ رسميّ له (البند ٧٢)."""
    from silk_hs_resolver import load_hs_codes
    seed_only = sorted({r["hs_code"] for r in load_hs_codes()}
                       - set(load_hs_reference()))
    assert seed_only, "لا فجوةَ بين المدوّنتين — الاختبارُ فقد موضوعَه"
    rows = dialog.build_candidates("منتجٌ ما", seed_only)
    shown = {r["hs6"]: r for r in rows}
    missing = [c for c in seed_only if c not in shown]
    assert not missing, f"بنودٌ حقيقيةٌ سقطت لنقصٍ في نسختنا: {missing}"
    for code in seed_only:
        assert shown[code]["description_ar"] == "", (
            f"{code}: نصٌّ غيرُ رسميٍّ سدّ فراغَ الوصف")
        assert shown[code]["band_ar"] == "", f"{code}: حدٌّ بلا مصدرٍ رسميّ"


def test_no_seed_text_and_no_model_prose_can_reach_the_description():
    """نثرُ النموذج ونصُّ البذرة لا يصلان `description_ar` مهما مُرِّرا."""
    ref = load_hs_reference()
    rows = dialog.build_candidates(
        "حليب نادك", ["040110"],
        reasons={"040110": "حليب وقشطة، نسبة الدهن لا تتجاوز 6%"})
    row = next(r for r in rows if r["hs6"] == "040110")
    assert row["description_ar"] == ref["040110"]
    assert "6%" not in row["description_ar"]


# ══════════════ E3 — اكتمالُ الأشقّاء، على كامل المرجع ══════════════════════

def test_no_axis_group_can_ever_present_a_proper_subset_of_its_siblings():
    """**خاصّيةٌ على كلّ** مجموعةِ محور: تمريرُ **أيّ** بندٍ منها يُعيدها
    كاملةً. مجموعةٌ جزئية تُخفي البندَ الصحيح — وهو ما وقع فعلاً."""
    for heading, axis, codes in _AXIS_GROUPS:
        for seed_code in codes:
            shown = {r["hs6"] for r in
                     dialog.build_candidates("منتجٌ ما", [seed_code])}
            missing = set(codes) - shown
            assert not missing, (
                f"{heading} (محور {axis}): تمريرُ {seed_code} أعاد مجموعةً "
                f"ناقصة — غائب {sorted(missing)}")


def test_reported_case_offers_every_sibling_including_the_full_fat_band():
    """حالةُ البلاغ حرفياً: 040110/040120/040190 => الأشقّاءُ الأربعة، ولا
    وجودَ لـ040190، وللمنتج كامل الدسم بندٌ صحيحٌ يختاره."""
    rows = dialog.build_candidates("حليب نادك", ["040110", "040120", "040190"])
    assert [r["hs6"] for r in rows] == ["040110", "040120", "040140", "040150"]
    assert any("10" in r["band_ar"] for r in rows), "لا بندَ لكامل الدسم"


def test_siblings_are_confined_to_the_same_non_numeric_axis():
    """لا يُضَمّ شقيقٌ يختلف في محورٍ آخر: أشقّاءُ الشاي الأخضر خُضرٌ فقط."""
    shown = {r["hs6"] for r in dialog.build_candidates("شاي", ["090210"])}
    assert "090220" in shown, "الشقيقُ على نفس المحور غائب"
    assert not (shown & {"090230", "090240"}), (
        f"ضُمّ شقيقٌ من محورٍ آخر (لونٌ مختلف): {shown}")


def test_axis_ordering_is_ascending_so_the_list_reads_naturally():
    """ترتيبُ المحور تصاعديٌّ بالحدّ الأدنى على كلّ مجموعة."""
    for _heading, _axis, codes in _AXIS_GROUPS:
        rows = dialog.build_candidates("منتجٌ ما", codes)
        los = [(attrs.band_of(r["hs6"]) or {}).get("lo") for r in rows
               if attrs.band_of(r["hs6"])]
        seq = [(-1e18 if v is None else v) for v in los]
        assert seq == sorted(seq), f"ترتيبٌ غيرُ تصاعديّ: {seq}"


# ══════════════ E2/E4 — لا اسمَ منتجٍ/علامةٍ/دولةٍ في نثرٍ مولَّد ═══════════

@pytest.mark.parametrize("product,prose", [
    ("حليب نادك", "إن كان حليب نادك كامل الدسم"),
    ("زيت زيتون إسبانيا", "زيت زيتون بكر من إسبانيا"),
    ("تمر سكري", "تمر سكري فاخر"),
])
def test_generated_prose_never_echoes_the_product_or_brand(product, prose):
    cleaned = dialog.sanitize_prose(prose, product)
    from silk_hs_confirm import _norm, _tokens
    for token in _tokens(product):
        assert _norm(token) not in _norm(cleaned), (
            f"اسمُ المنتج/العلامة «{token}» تسرّب إلى النثر: {cleaned!r}")


def test_country_names_are_stripped_from_generated_prose():
    cleaned = dialog.sanitize_prose("منتجٌ يُصدَّر إلى الصين وهولندا", "منتج")
    for country in ("الصين", "هولندا"):
        assert country not in cleaned, f"اسمُ دولةٍ تسرّب: {cleaned!r}"


def test_build_candidates_output_carries_no_product_literal_anywhere():
    """الناتجُ كلُّه (كلُّ الحقول) خالٍ من اسم المنتج — لا حقلَ يتسرّب منه."""
    rows = dialog.build_candidates(
        "حليب نادك", ["040110", "040120"],
        reasons={"040110": "حليب نادك قياسي", "040120": "حليب نادك كامل الدسم"})
    blob = " ".join(str(v) for r in rows for v in r.values())
    assert "نادك" not in blob, f"العلامةُ تسرّبت: {blob[:200]}"


def test_module_has_no_hardcoded_product_brand_or_country_literal():
    """قفلٌ ثابت: لا اسمَ منتجٍ/علامةٍ/دولةٍ مكتوبٌ صلباً في منطق الوحدة."""
    src = open(os.path.join(_ROOT, "silk_hs_dialog.py"), encoding="utf-8").read()
    body = re.sub(r'"""(?:.|\n)*?"""', "", src)
    body = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert not re.search(r"\b\d{6}\b", body), "رمز HS صلبٌ في المنطق"
    for word in ("نادك", "nadec", "حليب", "milk", "شاي", "tea",
                 "السعودية", "الصين", "saudi", "china"):
        assert not re.search(rf"(?<![a-z؀-ۿ]){re.escape(word)}"
                             rf"(?![a-z؀-ۿ])", body, re.I), (
            f"اسمٌ صلب: {word}")


# ══════════════ التوصيل — لا مُصيِّرَ ثانٍ يبقى ═══════════════════════════════

def test_every_user_facing_producer_delegates_to_the_one_renderer():
    """المُنتِجاتُ الثلاثة للنصّ المعروض تمرّ كلُّها من نقطة الاختناق."""
    import inspect
    import silk_hs_classifier as hsc
    import silk_hs_confirm as conf
    for fn in (hsc._public_candidates, hsc.manual, conf.deterministic_candidates):
        src = inspect.getsource(fn)
        assert "silk_hs_dialog" in src, (
            f"{fn.__name__} لا يمرّ بنقطة الاختناق — مُصيِّرٌ ثانٍ")


def test_public_candidates_never_emits_model_prose_as_description():
    """`_public_candidates` يُصيِّر من المرجع مهما كان `code_desc` الداخليّ."""
    import silk_hs_classifier as hsc
    ref = load_hs_reference()
    internal = [{"hs6": "040110", "code_desc": "وصفٌ مولَّدٌ خاطئ 6%",
                 "reason_ar": "نثرُ نموذج", "overlap": 0.9, "verified": True}]
    rows = hsc._public_candidates(internal, "حليب نادك")
    row = next(r for r in rows if r["hs6"] == "040110")
    assert row["description_ar"] == ref["040110"]
    assert "6%" not in row["description_ar"]


def test_frontend_does_not_truncate_the_candidate_list():
    """الواجهةُ لا تقصّ القائمة — القصُّ يُخفي شقيقاً فيُفقِد البندَ الصحيح."""
    html = open(os.path.join(_ROOT, "web", "index.html"), encoding="utf-8").read()
    block = html[html.index("function showHsCandidates"):]
    block = block[:block.index("function showHsManual")]
    assert "slice(0,3)" not in block and "slice(0, 3)" not in block, (
        "الواجهةُ ما زالت تقتطع قائمة المرشّحين")
    assert "band_ar" in block, "الواجهةُ لا تعرض حدَّ البند الرسميّ"


# ══════════ حدُّ النطاق: إصلاحُ العرض لا يُوسِّع تغطيةَ المُحلِّل ══════════

def test_axis_completion_reaches_the_dialog_but_never_the_numeric_resolver():
    """أمرُ المُشرِف: «Do not widen resolver coverage».

    إكمالُ الأشقّاء إصلاحُ **عرض**؛ لو غُذّي به المُحلِّلُ الرقميّ لاتّسع
    نطاقُ حسمِه من بابٍ خلفيّ (قياسٌ على ٦٠٠ منتج: أربعةُ منتجاتٍ تصير
    قابلةً للحسم لم تكن كذلك). فالبنودُ التي أضافتها هذه الوحدة تُوسَم
    `axis_completion=True` و`resolve_or_probe` يُسقِطها قبل المُحلِّل."""
    rows = dialog.build_candidates("—", ["040110"])
    added = [r for r in rows if r["axis_completion"]]
    asked = [r for r in rows if not r["axis_completion"]]
    assert [r["hs6"] for r in asked] == ["040110"], asked
    assert {r["hs6"] for r in added} == {"040120", "040140", "040150"}, added

    seen = {}
    import silk_hs_attributes as _attrs
    import silk_hs_confirm as confirm
    real = _attrs.resolve_by_attribute

    def _spy(product, candidates, **kw):
        seen["codes"] = [c.get("hs6") for c in candidates]
        return real(product, candidates, **kw)

    _attrs.resolve_by_attribute = _spy
    try:
        confirm.resolve_or_probe("—", rows, allow_web=False)
    finally:
        _attrs.resolve_by_attribute = real
    assert seen["codes"] == ["040110"], (
        f"المُحلِّلُ غُذّي بأشقّاءَ لم يطلبهم المستدعي: {seen['codes']}")


def test_manual_picker_keeps_its_alternates_shape_while_changing_the_text_source():
    """المراجعةُ الذاتية (اللائحة ٥٨) التقطت هذا في هذا الفرع نفسِه: توجيهُ
    `manual()` لنقطة الاختناق **كسر شكلَ `alternates`** — الواجهة تقرأ
    `a.label` في `showHsManual`، وصفوفُ المُصيِّر لا تحمله. المتغيّرُ يجب أن
    يكون **مصدرَ النصّ** لا شكلَه."""
    import silk_hs_classifier as hsc
    from silk_hs_resolver import official_description
    out = hsc.manual("حليب طازج")
    assert out["alternates"], "المنتقي اليدوي بلا بدائل"
    for a in out["alternates"]:
        assert set(a) == {"hs6", "label", "confidence"}, a
        if dialog.in_official_reference(a["hs6"]):
            assert a["label"] == official_description(a["hs6"]), (
                f"{a['hs6']}: نصُّ المنتقي ليس الوصفَ الرسميّ")


def test_a_code_with_no_official_description_declares_the_gap_in_the_dialog():
    """عقدُ عدم الاختلاق في طبقة العرض: بندٌ حقيقيٌّ بلا وصفٍ رسميٍّ لدينا
    يُعرَض بـ**فجوةٍ معلَنة** لا بسطرٍ فارغٍ صامت ولا بنصٍّ مُختلَق."""
    html = open(os.path.join(_ROOT, "web", "index.html"), encoding="utf-8").read()
    assert "لا وصفَ رسميّ لهذا البند في مرجعنا" in html, (
        "الواجهةُ تُسقِط سطرَ الوصف بصمتٍ بدل إعلان الفجوة")
