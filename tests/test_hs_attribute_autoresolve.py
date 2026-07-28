"""قفل حسم الرمز بالسمة الرقمية — the numeric-attribute auto-resolve lock.

> **الحادثة (بلاغ المُشرِف).** صندوقُ حوار تأكيد HS كان يسأل التاجر أن يختار
> بين بنود الترويسة الواحدة (040110/040120/040140/040150) **بنسبةِ دهنٍ لا
> يعرفها** — حرفياً «إن كان حليب نادك كامل الدسم (أكثر من ٦٪)». هذا سؤالٌ
> **المنتجُ نفسُه يجيب عنه**: بطاقةُ العبوة تحمل الرقم، والويب يستشهد به.
>
> **القانون.** حين تتمايز بنودُ الترويسة بعتبةٍ رقمية، يُقاس الرقم قبل أن
> يُسأل المستخدم: صورةُ العبوة أولاً (نداء الرؤية القائم نفسه)، ثم استعلامُ
> ويبٍ واحد برابطٍ قابل للاستشهاد. الحوار **احتياطٌ لا افتراض**، وحين يُعرَض
> يحمل ما وُجد وما نقص وحدودَ كل مرشّح بلغةٍ مفهومة لا عتبةً جمركية. **بلا
> رقمٍ مقيس لا يُحسَم رمز أبداً** — رمزٌ مستنتَج رقمٌ مستنتَج (المبدأ المؤسِّس).

هرمتي بالكامل: الشبكة مقطوعة في كل اختبارٍ يلمس مسار الويب، والوصف الرسمي
يُقرأ من `data/hs_reference.csv` الحقيقي (لا نموذج).

Run: python3 -m pytest tests/test_hs_attribute_autoresolve.py -q
"""
from __future__ import annotations

import os
import socket
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import silk_hs_attributes as attrs  # noqa: E402


# ── أدوات ────────────────────────────────────────────────────────────────────

class _NoNet:
    """اقطع الشبكة تماماً — أي نداء خارجي يرفع OSError."""

    def __enter__(self):
        self._real = socket.socket

        def _blocked(*a, **k):
            raise OSError("network blocked by test")

        socket.socket = _blocked            # type: ignore[assignment]
        return self

    def __exit__(self, *exc):
        socket.socket = self._real          # type: ignore[assignment]
        return False


def _cands(*codes: str) -> list[dict]:
    """مرشّحون بشكل `_public_candidates` — بلا وصفٍ محليّ عمداً: النطاقات
    تُقرأ من المرجع الرسمي لا من بذرتنا الجزئية."""
    return [{"hs6": c, "description_ar": "", "reason_ar": "",
             "confidence": 0.5, "verified": True} for c in codes]


# ══════════════ ١) قراءة النطاقات من الوصف الرسمي (data-driven) ══════════════

# ≥٤ عائلاتٍ متنوّعة (الدرس ٢٤: القاعدة تُعمَّم من عيّناتٍ متعدّدة، والعيّنةُ
# الواحدة لا تصنع الحارس) — نسبة دهن، دهن مسحوق، سعة لتر، وزن تعبئة، بريكس.
@pytest.mark.parametrize("hs6,lo,hi,dimension", [
    ("040110", None, 1.0, "fat"),      # milk, fat ≤ 1%
    ("040120", 1.0, 6.0, "fat"),       # milk, 1% < fat ≤ 6%   ← حالة نادك
    ("040140", 6.0, 10.0, "fat"),      # milk, 6% < fat ≤ 10%
    ("040150", 10.0, None, "fat"),     # milk, fat > 10%
    ("040210", None, 1.5, "fat"),      # milk powder, fat ≤ 1.5%
    ("220421", None, 2.0, "volume"),   # wine, containers ≤ 2 L
    ("220422", 2.0, 10.0, "volume"),   # wine, 2 L < c ≤ 10 L
    ("220429", 10.0, None, "volume"),  # wine, containers > 10 L
    ("090210", None, 3000.0, "weight"),  # green tea, packings ≤ 3kg (grams)
    ("090220", 3000.0, None, "weight"),  # green tea, packings > 3kg
    ("200912", None, 20.0, "brix"),    # orange juice, Brix ≤ 20
    ("200919", 20.0, None, "brix"),    # orange juice, Brix > 20
])
def test_official_band_parsed_from_reference_not_from_our_seed(
        hs6, lo, hi, dimension):
    """النطاقُ الرقمي يُقرأ من الوصف الرسمي الكامل — خمسُ عائلاتٍ متنوّعة."""
    band = attrs.band_of(hs6)
    assert band is not None, f"{hs6}: لم يُقرأ نطاقٌ من الوصف الرسمي"
    assert band["lo"] == lo and band["hi"] == hi, (
        f"{hs6}: نطاقٌ خاطئ {band['lo']}..{band['hi']} (المتوقّع {lo}..{hi})")
    assert band["dimension"] == dimension, (
        f"{hs6}: بُعدٌ خاطئ {band['dimension']!r} (المتوقّع {dimension!r})")


def test_code_without_a_numeric_band_yields_none_not_a_guessed_range():
    """رمزٌ بلا عتبةٍ رقمية في وصفه => `None` — لا نطاقٌ مختلَق."""
    assert attrs.band_of("040510") is None       # Butter — بلا عتبة
    assert attrs.band_of("999999") is None       # رمزٌ غير موجود


def test_discriminator_detected_only_when_candidates_share_one_dimension():
    """المُميِّز يُكتشَف حين تتمايز المرشّحات ببُعدٍ واحد — وإلا `None`."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert d and d["dimension"] == "fat" and d["unit"] == "%"
    assert {b["hs6"] for b in d["bands"]} == {
        "040110", "040120", "040140", "040150"}
    # أبعادٌ مختلطة (دهن + سعة) => لا مُميِّز واحد => لا حسمَ تلقائي.
    assert attrs.discriminator(_cands("040110", "220421")) is None
    # مرشّحٌ واحدٌ ذو نطاق لا يصنع مُميِّزاً (لا شيء يُميَّز عنه).
    assert attrs.discriminator(_cands("040110", "040510")) is None


# ══════════════ ٢) الاختيار بالقيمة — وحدةٌ واحدة أو لا حسم ══════════════════

@pytest.mark.parametrize("value,expected", [
    (0.5, "040110"),
    (3.5, "040120"),      # نادك كامل الدسم الفعليّ ~٣٫٥٪ => البند الصحيح
    (6.0, "040120"),      # الحدُّ الأعلى شامل (exceeding 1% but not exceeding 6%)
    (8.0, "040140"),
    (35.0, "040150"),
])
def test_value_selects_exactly_one_band(value, expected):
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert attrs.select_by_value(d, value, "%") == expected


def test_no_band_matches_or_wrong_unit_never_fabricates_a_code():
    """قيمةٌ لا تقع في نطاقٍ وحيد أو بوحدةٍ غير قابلة للتحويل => `None`."""
    d = attrs.discriminator(_cands("040110", "040140"))   # فجوةٌ بين ١٪ و٦٪
    assert attrs.select_by_value(d, 3.5, "%") is None     # لا نطاق يحويها
    assert attrs.select_by_value(d, 3.5, "L") is None     # وحدةٌ غير متوافقة
    assert attrs.select_by_value(d, None, "%") is None    # لا قيمة أصلاً


# ══════════════ ٣) مسار الصورة — سمات البطاقة تحسم بلا نداءٍ إضافي ═══════════

def test_image_label_attribute_resolves_the_code_with_image_provenance():
    """سمةٌ رقمية من بطاقة العبوة تحسم الرمز وتُوسَم «من صورة العبوة»."""
    with _NoNet():
        out = attrs.resolve_by_attribute(
            "حليب نادك كامل الدسم",
            _cands("040110", "040120", "040140", "040150"),
            label_attributes=[{"name": "نسبة الدهن", "value": 3.5, "unit": "%"},
                              {"name": "الحجم", "value": 1, "unit": "L"}],
            allow_web=False)
    assert out["hs6"] == "040120"
    assert out["resolved_from"] == "image"
    assert out["value"] == 3.5 and out["unit"] == "%"
    assert out["source_url"] is None
    assert "صورة العبوة" in out["note_ar"]


def test_image_attribute_of_a_different_dimension_is_ignored_not_misread():
    """سمةٌ ببُعدٍ آخر (الحجم) لا تُقرأ كنسبة دهن — لا حسمَ بالخطأ."""
    with _NoNet():
        out = attrs.resolve_by_attribute(
            "حليب نادك", _cands("040110", "040120", "040140", "040150"),
            label_attributes=[{"name": "الحجم", "value": 1.5, "unit": "L"}],
            allow_web=False)
    assert out["hs6"] is None
    assert out["resolved_from"] is None


# ══════════════ ٤) مسار الويب — رقمٌ برابطٍ قابل للاستشهاد ═══════════════════

def _fake_hits(items):
    """محاكاة `web_search` — DataPoints بشكل النتائج العضوية."""
    from silk_data_layer import DataPoint, _today
    return [DataPoint(it, "Web Search (Serper)", 0.5, "organic", _today())
            for it in items]


def test_web_probe_resolves_with_source_url_and_web_provenance(monkeypatch):
    monkeypatch.setattr(attrs, "_web_search", lambda q, num, gl: _fake_hits([
        {"title": "حليب نادك طازج كامل الدسم 1 لتر",
         "snippet": "نسبة الدهن 3.5% — حليب طازج مبستر.",
         "link": "https://example-retailer.sa/nadec-full-fat"}]))
    monkeypatch.setattr(attrs, "_cache_read", lambda k: None)
    monkeypatch.setattr(attrs, "_cache_write", lambda k, v: None)
    out = attrs.resolve_by_attribute(
        "حليب نادك", _cands("040110", "040120", "040140", "040150"),
        label_attributes=None, allow_web=True)
    assert out["hs6"] == "040120"
    assert out["resolved_from"] == "web"
    assert out["value"] == 3.5
    assert out["source_url"] == "https://example-retailer.sa/nadec-full-fat"
    assert 0.0 < out["confidence"] <= 1.0
    assert "مصدر ويب" in out["note_ar"] and out["source_url"] in out["note_ar"]


def test_web_hit_without_the_product_token_is_rejected_not_borrowed(monkeypatch):
    """رقمٌ في نتيجةٍ لا تذكر المنتجَ إطلاقاً لا يُستعار — لا اختلاق بالجوار."""
    monkeypatch.setattr(attrs, "_web_search", lambda q, num, gl: _fake_hits([
        {"title": "Butter fat standards", "snippet": "fat content 82%",
         "link": "https://example.org/butter"}]))
    monkeypatch.setattr(attrs, "_cache_read", lambda k: None)
    monkeypatch.setattr(attrs, "_cache_write", lambda k, v: None)
    out = attrs.resolve_by_attribute(
        "حليب نادك", _cands("040110", "040120", "040140", "040150"),
        label_attributes=None, allow_web=True)
    assert out["hs6"] is None and out["resolved_from"] is None


def test_web_failure_is_a_declared_gap_and_visible_to_the_operator(monkeypatch):
    """فشلُ خدمةِ البحث => فجوةٌ معلنة + سطرُ `service_failure` للمشغّل
    (عائلة الدرس ٢٦: لا فشلٍ خارجيٍّ صامت)."""
    seen: list[tuple] = []
    monkeypatch.setattr(attrs, "_cache_read", lambda k: None)
    monkeypatch.setattr(attrs, "_cache_write", lambda k, v: None)
    # الخدمة **مُهيَّأة** (مفتاحٌ مضبوط) ثم تفشل — هذا بالضبط ما يجب أن يراه
    # المشغّل؛ خدمةٌ غير مهيَّأة أصلاً ليست عطلاً (تدهورٌ نظيف بلا سطر).
    monkeypatch.setenv("SEARCH_API_KEY", "test-key-not-used-offline")
    import silk_ops_log
    monkeypatch.setattr(silk_ops_log, "record_service_failure",
                        lambda s, r, **k: seen.append((s, r)))
    with _NoNet():           # `web_search` الحقيقي يتدهور لفجوةٍ معلنة
        out = attrs.resolve_by_attribute(
            "حليب نادك", _cands("040110", "040120", "040140", "040150"),
            label_attributes=None, allow_web=True)
    assert out["hs6"] is None and out["resolved_from"] is None
    assert seen and seen[0][0] == "hs_attribute_web"


# ══════════════ ٥) تقريرُ الحوار — ما وُجد وما نقص، لا عتبةٌ جمركية ══════════

def test_dialog_report_states_what_was_searched_and_what_is_missing():
    with _NoNet():
        out = attrs.resolve_by_attribute(
            "حليب نادك", _cands("040110", "040120", "040140", "040150"),
            label_attributes=None, allow_web=False)
    assert out["hs6"] is None
    assert out["attribute"] == "fat" and out["unit"] == "%"
    assert out["label_ar"] and "دهن" in out["label_ar"]
    assert out["searched"], "لم يُذكر ما جُرِّب"
    assert out["missing_ar"], "لم تُذكر الفجوة صراحةً"
    # حدودُ كل مرشّح بلغةٍ مفهومة — التاجر يقرأ «حتى 1%» لا «040110».
    ranges = {b["hs6"]: b["range_ar"] for b in out["bands_ar"]}
    assert ranges["040110"] and "1" in ranges["040110"]
    assert ranges["040150"] and "10" in ranges["040150"]
    for hs6, txt in ranges.items():
        assert hs6 not in txt, "سطرُ الحدّ يعيد الرمز بدل وصفه بلغةٍ مفهومة"


def test_valve_off_disables_auto_resolution_entirely(monkeypatch):
    """`SILK_HS_ATTRIBUTE_RESOLVE=0` => السلوك السابق حرفياً (حوارٌ مباشر)."""
    monkeypatch.setenv("SILK_HS_ATTRIBUTE_RESOLVE", "0")
    assert attrs.enabled() is False
    with _NoNet():
        out = attrs.resolve_by_attribute(
            "حليب نادك كامل الدسم",
            _cands("040110", "040120", "040140", "040150"),
            label_attributes=[{"name": "نسبة الدهن", "value": 3.5, "unit": "%"}],
            allow_web=False)
    assert out["hs6"] is None and out["resolved_from"] is None


# ══════════════ ٦) قاعدةٌ عامّة لا حالةُ منتج (الدرس ٢٤) ═════════════════════

def test_module_has_no_hardcoded_product_name_or_iso_or_hs_code():
    """صفر اسم منتج/دولة/رمز HS مكتوبٍ صلباً في منطق الوحدة — القاعدةُ
    مبنيّةٌ على البيانات (المرجع الرسمي) ومعجمٍ لأبعادِ القياس اللغوية."""
    import re
    src = open(os.path.join(_ROOT, "silk_hs_attributes.py"),
               encoding="utf-8").read()
    # جرّد التوثيق/التعليقات: الحادثةُ تُوثَّق بمثالها، والمنطقُ لا يعرفه.
    body = re.sub(r'"""(?:.|\n)*?"""', "", src)
    body = "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())
    assert not re.search(r"\b\d{6}\b", body), "رمز HS مكتوبٌ صلباً في المنطق"
    for word in ("نادك", "nadec", "حليب", "milk", "تمور", "dates", "wine"):
        # حدٌّ كلميّ: «candidates» ليست اسمَ منتجٍ لأنها تحوي «dates».
        assert not re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])",
                             body, re.I), f"اسمُ منتجٍ صلب: {word}"


def test_generalizes_across_families_no_family_is_privileged():
    """نفسُ المنطق يحسم في عائلاتٍ لا تشترك في شيءٍ سوى بنية العتبة."""
    cases = [
        (_cands("220421", "220422", "220429"), 5.0, "L", "220422"),
        (_cands("090210", "090220"), 500.0, "g", "090210"),
        (_cands("200912", "200919"), 12.0, "", "200912"),
        (_cands("040210", "040221"), 26.0, "%", "040221"),
    ]
    for cands, value, unit, expected in cases:
        d = attrs.discriminator(cands)
        assert d is not None, f"لم يُكتشف مُميِّزٌ لـ{[c['hs6'] for c in cands]}"
        assert attrs.select_by_value(d, value, unit) == expected
