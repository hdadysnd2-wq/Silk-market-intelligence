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


# الصمّامُ **مُطفأٌ افتراضياً** (D1) — هذه الملفّاتُ تختبر الميزةَ نفسَها،
# فتُفعّلها صراحةً. اختبارُ الافتراض نفسِه يعيش في
# `test_flag_is_off_by_default_and_needs_explicit_opt_in` ولا يستعمل هذه.
@pytest.fixture(autouse=True)
def _enable_attribute_resolver(monkeypatch):
    monkeypatch.setenv("SILK_HS_ATTRIBUTE_RESOLVE", "1")



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


def test_flag_is_off_by_default_and_needs_explicit_opt_in(monkeypatch):
    """D1 — **مُطفأٌ افتراضياً.** تفعيلُه عند الدمج كان سيجعل ميزةً لم
    تُجرَّب بمفتاحٍ حيّ تُصدِر رموزاً جمركية لكلّ مستخدم؛ تفعيلُه قرارُ
    مالكٍ منفصلٌ بعد الدمج مشروطٌ بإغلاق G8."""
    monkeypatch.delenv("SILK_HS_ATTRIBUTE_RESOLVE", raising=False)
    assert attrs.enabled() is False, "الصمّامُ مفعّلٌ افتراضياً — خرقُ D1"
    # ومُطفأً: السلوكُ حرفياً كما كان قبل هذا الفرع (حوارٌ، لا حسم).
    with _NoNet():
        out = attrs.resolve_by_attribute(
            "منتجٌ ما", _cands("040110", "040120", "040140", "040150"),
            label_attributes=[{"name": "نسبة الدهن", "value": 3.5,
                               "unit": "%"}],
            allow_web=False)
    assert out["hs6"] is None and out["resolved_from"] is None
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("SILK_HS_ATTRIBUTE_RESOLVE", on)
        assert attrs.enabled() is True, f"لم يُفعَّل بـ{on!r}"
    for off in ("0", "false", "no", "off", "", "  ", "maybe"):
        monkeypatch.setenv("SILK_HS_ATTRIBUTE_RESOLVE", off)
        assert attrs.enabled() is False, f"فُعِّل بقيمةٍ ليست تفعيلاً: {off!r}"


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
    """نفسُ المنطق يحسم في عائلاتٍ **غيرِ لبنية**، لكلٍّ بُعدُها (G2).

    الترويسات مأخوذةٌ من `data/hs_reference.csv` الفعليّ لا من نموذج، وكلٌّ
    منها أحاديّةُ المحور فعلاً (يقبلها `discriminator` بشروطه الأربعة)."""
    cases = [
        # (مرشّحون، قيمة، وحدة، المتوقَّع، البُعد) — ٣ أبعادٍ غيرِ لبنية.
        (_cands("220421", "220422", "220429"), 5.0, "L", "220422", "volume"),
        (_cands("721391", "721399"), 20.0, "mm", "721399", "length"),
        (_cands("010391", "010392"), 30.0, "kg", "010391", "weight"),
        (_cands("200912", "200919"), 12.0, "", "200912", "brix"),
    ]
    for cands, value, unit, expected, dim in cases:
        d = attrs.discriminator(cands)
        assert d is not None, f"لم يُكتشف مُميِّزٌ لـ{[c['hs6'] for c in cands]}"
        assert d["dimension"] == dim, f"{expected}: بُعدٌ خاطئ {d['dimension']}"
        assert attrs.select_by_value(d, value, unit) == expected
    assert len({c[4] for c in cases}) == 4, "العيّناتُ لا تغطّي أبعاداً متمايزة"


# ══════════════ G1 — صرامةُ الحدّ تُحمَل من العبارة لا من الجانب ═════════════

_FAKE = "000000"        # رمزٌ غيرُ موجودٍ في المرجع => يُقرأ الوصفُ المُمرَّر


@pytest.mark.parametrize("phrase,lo,lo_inc,hi,hi_inc", [
    ("not exceeding 6%",   None, False, 6.0, True),
    ("less than 6%",       None, False, 6.0, False),
    ("not more than 6%",   None, False, 6.0, True),
    ("under 6%",           None, False, 6.0, False),
    ("exceeding 1%",       1.0,  False, None, False),
    ("more than 1%",       1.0,  False, None, False),
    ("at least 1%",        1.0,  True,  None, False),
    ("not less than 1%",   1.0,  True,  None, False),
    ("1% or less",         None, False, 1.0, True),
    ("1% or more",         1.0,  True,  None, False),
])
def test_bound_strictness_comes_from_the_matched_phrase(
        phrase, lo, lo_inc, hi, hi_inc):
    """«less than 6%» و«not exceeding 6%» حدّان أعليان **مختلفا الشمول** —
    تسطيحُهما يُصدِر رمزاً خاطئاً بوسمِ مصدرٍ واثق (اختلاقٌ لا فجوة)."""
    b = attrs.band_of(_FAKE, f"of a fat content, by weight, {phrase}")
    assert b is not None, f"لم يُقرأ حدٌّ من «{phrase}»"
    assert (b["lo"], b["lo_inclusive"], b["hi"], b["hi_inclusive"]) == (
        lo, lo_inc, hi, hi_inc)


def test_strict_and_inclusive_upper_bounds_differ_at_the_boundary():
    """القيمةُ على الحدّ: تدخل «not exceeding 6» ولا تدخل «less than 6»."""
    inclusive = attrs.band_of(_FAKE, "of a fat content, not exceeding 6%")
    strict = attrs.band_of(_FAKE, "of a fat content, less than 6%")
    assert attrs._contains(inclusive, 6.0) is True
    assert attrs._contains(strict, 6.0) is False
    assert attrs._contains(strict, 5.999) is True


@pytest.mark.parametrize("value,expected", [
    (0.99, "040110"),   # داخل «حتى 1%»
    (1.0,  "040110"),   # الحدُّ يملكه البندُ الأدنى («not exceeding 1%» شامل)
    (1.01, "040120"),
    (5.99, "040120"),
    (6.0,  "040120"),   # «exceeding 1% but not exceeding 6%» — الأعلى شامل
    (6.01, "040140"),
])
def test_040120_boundaries_exactly(value, expected):
    """حدودُ البند المُبلَّغ عنه بالضبط — لا انزلاقَ بمقدارٍ واحد."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert attrs.select_by_value(d, value, "%") == expected


def test_property_every_multiband_heading_is_either_clean_or_refused():
    """خاصّيةٌ على **كلّ** ترويسةٍ متعدّدةِ النطاقات في المرجع الفعليّ:

    إمّا أن تكون قسمةً نظيفةً (بُعدٌ واحد، محورٌ رقميٌّ وحيد، حدودٌ متّصلةٌ
    بلا فجوةٍ ولا ازدواجِ تغطية) — وعندها يقبلها `discriminator` —
    وإمّا أن **يرفضها** فلا تُحسَم آلياً أبداً. لا حالةَ ثالثة: قبولُ قسمةٍ
    غيرِ نظيفة يعني إصدارَ رمزٍ خاطئ بوسمِ مصدرٍ واثق."""
    import collections
    from silk_hs_resolver import load_hs_reference
    by_head = collections.defaultdict(list)
    for code in load_hs_reference():
        b = attrs.band_of(code)
        if b is not None:
            by_head[code[:4]].append(b)
    multi = {h: bs for h, bs in by_head.items() if len(bs) >= 2}
    assert len(multi) >= 40, f"عيّنةٌ أضعفُ من المتوقَّع: {len(multi)} ترويسة"
    accepted = 0
    for head, bands in multi.items():
        disc = attrs.discriminator([{"hs6": b["hs6"]} for b in bands])
        if disc is None:
            continue                      # مرفوضة — آمنة بالتعريف
        accepted += 1
        got = disc["bands"]
        assert len({b["dimension"] for b in got}) == 1, head
        assert len({b["axis"] for b in got}) == 1, (
            f"{head}: قُبِلت رغم محورٍ غيرِ رقميٍّ إضافي")
        for prev, nxt in zip(got, got[1:]):
            assert prev["hi"] is not None and nxt["lo"] is not None, head
            assert prev["hi"] == nxt["lo"], f"{head}: فجوةٌ/تداخلٌ رقميّ"
            assert bool(prev["hi_inclusive"]) != bool(nxt["lo_inclusive"]), (
                f"{head}: الحدُّ المشترك مزدوجُ التغطية أو مكشوف")
        # ولا قيمةٌ تنتمي لأكثر من بندٍ واحد على أيٍّ من الحدود.
        for b in got:
            for edge in (b["lo"], b["hi"]):
                if edge is None:
                    continue
                owners = [x["hs6"] for x in got if attrs._contains(x, edge)]
                assert len(owners) <= 1, f"{head}: {edge} يملكه {owners}"
    assert accepted >= 5, f"الحارسُ رفض كلَّ شيء تقريباً ({accepted} مقبولة)"


def test_second_axis_heading_is_refused_not_resolved():
    """0902 = لونُ الشاي × وزنُ التعبئة. قياسُ الوزن وحده لا يُحدِّد بنداً —
    يُرفَض. (نفسُ المحور فقط يمرّ.)"""
    assert attrs.discriminator(_cands("090210", "090240")) is None
    assert attrs.discriminator(_cands("090210", "090220")) is not None


# ══════════════ G2 — صفرُ مصطلحِ بُعدٍ حرفيٍّ في الشيفرة ═════════════════════

def test_dimension_terms_come_from_the_config_file_not_the_code():
    lex = attrs.load_dimensions()
    assert lex and "fat" in lex, "لم يُقرأ معجمُ الأبعاد من الملفّ"
    label, syn = attrs.dimension_terms("fat")
    assert label and syn
    # بُعدٌ خارج الملفّ يتدهور نظيفاً لمفتاحه — لا مصطلحٌ مختلَق.
    label2, syn2 = attrs.dimension_terms("zzz_unknown_dimension")
    assert label2 == "zzz_unknown_dimension" and syn2 == ("zzz_unknown_dimension",)


def _module_body() -> str:
    """مصدرُ الوحدة مجرَّداً من التوثيق والتعليقات — المنطقُ وحده."""
    import re
    src = open(os.path.join(_ROOT, "silk_hs_attributes.py"),
               encoding="utf-8").read()
    body = re.sub(r'"""(?:.|\n)*?"""', "", src)
    return "\n".join(ln.split("#", 1)[0] for ln in body.splitlines())


def test_no_literal_arabic_dimension_term_anywhere_in_the_module():
    """قفلٌ ضدّ عودة عائلة الدرس ٣٠ (كلمةُ نطاقٍ حرفيةٌ مجمَّدةٌ في قالب):
    لا تسميةَ عربيةَ بُعدٍ ولا مرادفَه العربيّ يظهر حرفياً في منطق الوحدة.

    العربيةُ هي السطحُ الذي يواجه التاجرَ (الاستعلام + شرحُ الحوار)، فتجميدُ
    مصطلحٍ عربيٍّ في الشيفرة هو العيبُ بعينه. المرادفاتُ الإنجليزية تُفحَص
    في `test_query_construction_has_no_literal_term` لأنّ بعضَها يتصادف مع
    رموز الوحدات (`litre`) التي يحتاجها المحلّلُ لأسبابٍ لا علاقة لها."""
    body = _module_body()
    arabic = set()
    for row in attrs.load_dimensions().values():
        if row["label_ar"]:
            arabic.add(row["label_ar"])
        arabic.update(t for t in row["syn"]
                      if any("\u0600" <= ch <= "\u06ff" for ch in t))
    leaked = sorted(t for t in arabic if t and t in body)
    assert not leaked, f"مصطلحُ بُعدٍ عربيٌّ حرفيٌّ في المنطق: {leaked}"


def test_query_construction_has_no_literal_term():
    """سطحُ الاستعلام/التوجيه **خالٍ تماماً** من أيّ مصطلحٍ من الملفّ (بأيّ
    لغة) — الاستعلامُ يُركَّب من البُعد المكتشَف لا من كلمةٍ مجمَّدة."""
    import inspect
    src = inspect.getsource(attrs.probe_web)
    terms = set()
    for dim, row in attrs.load_dimensions().items():
        if row["label_ar"]:
            terms.add(row["label_ar"])
        terms.update(t for t in row["syn"] if t != dim)
        terms.add(dim)
    leaked = sorted(t for t in terms if len(t) >= 3 and t in src)
    assert not leaked, f"مصطلحٌ حرفيٌّ في بناء الاستعلام: {leaked}"


def test_web_query_interpolates_the_detected_dimension(monkeypatch):
    """استعلامُ الويب يُركَّب من البُعد المكتشَف — يتغيّر بتغيّر الترويسة."""
    seen = []
    monkeypatch.setattr(attrs, "_web_search",
                        lambda q, num, gl: seen.append(q) or [])
    monkeypatch.setattr(attrs, "_cache_read", lambda k: None)
    monkeypatch.setattr(attrs, "_cache_write", lambda k, v: None)
    for cands, dim in ((_cands("040110", "040120", "040140", "040150"), "fat"),
                       (_cands("220421", "220422", "220429"), "volume"),
                       (_cands("010391", "010392"), "weight")):
        attrs.probe_web("منتجٌ ما", attrs.discriminator(cands))
    assert len(seen) == 3 and len(set(seen)) == 3, (
        f"الاستعلامُ لا يتغيّر بتغيّر البُعد: {seen}")
    for q, dim in zip(seen, ("fat", "volume", "weight")):
        assert attrs.dimension_terms(dim)[0] in q


# ══════════════ G3 — تطبيعُ الوحدات ══════════════════════════════════════════

@pytest.mark.parametrize("unit,family,factor", [
    ("%", "percent", 1.0), ("percent", "percent", 1.0),
    ("g/100ml", "percent", 1.0), ("g/100g", "percent", 1.0),
    ("ml/100ml", "percent", 1.0), ("% vol", "percent", 1.0),
    ("abv", "percent", 1.0), ("mg/100g", "percent", 0.001),
    ("g", "weight", 1.0), ("kg", "weight", 1000.0),
    ("mg", "weight", 0.001), ("tonne", "weight", 1_000_000.0),
    ("ml", "volume", 0.001), ("cl", "volume", 0.01),
    ("l", "volume", 1.0), ("litres", "volume", 1.0),
    ("mm", "length", 1.0), ("cm", "length", 10.0), ("m", "length", 1000.0),
    ("micron", "length", 0.001),
    ("", "scalar", 1.0),
    # وحداتٌ عربية — بطاقةُ العبوة في سوقنا عربيةٌ أصلاً.
    ("٪", "percent", 1.0), ("بالمئة", "percent", 1.0),
    ("جم", "weight", 1.0), ("كجم", "weight", 1000.0),
    ("مل", "volume", 0.001), ("لتر", "volume", 1.0),
    ("سم", "length", 10.0),
])
def test_unit_conversion_table_entry(unit, family, factor):
    assert attrs._unit_family(unit) == (family, factor)


def test_arabic_label_reading_resolves_like_a_western_one():
    """«٣٫٥٪» على بطاقةٍ عربية = «3.5%» — لا تُهدَر قراءةٌ صحيحة للحوار."""
    from silk_product_intake import _sanitize_attributes
    cleaned = _sanitize_attributes(
        [{"name": "نسبة الدهن", "value": "٣٫٥", "unit": "٪"}])
    assert cleaned == [{"name": "نسبة الدهن", "value": 3.5, "unit": "٪"}]
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    val, unit, _n = attrs.value_from_label(cleaned, d)
    assert attrs.select_by_value(d, val, unit) == "040120"


def test_label_in_g_per_100ml_resolves_like_a_percentage():
    """«3.5 g/100ml» على البطاقة = «3.5%» — لا تُهدَر قراءةٌ صحيحة للحوار."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    val, unit, _name = attrs.value_from_label(
        [{"name": "نسبة الدهن", "value": 3.5, "unit": "g/100ml"}], d)
    assert (val, unit) == (3.5, "g/100ml")
    assert attrs.select_by_value(d, 3.5, "g/100ml") == "040120"


@pytest.mark.parametrize("unit", ["oz", "fl oz", "lb", "cup", "iu",
                                 "kcal", "gal", "ppm", "g/l"])
def test_unconvertible_units_are_rejected_not_guessed(unit):
    """وحدةٌ خارج الجدول => لا تحويل => لا حسم (تُعرَض في الحوار)."""
    assert attrs._unit_family(unit) is None
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert attrs.select_by_value(d, 3.5, unit) is None


# ══════════════ G4 — قاعدةُ التعارض ══════════════════════════════════════════

def _stub_web(monkeypatch, value_text: str, link="https://x.test"):
    monkeypatch.setattr(attrs, "_cache_read", lambda k: None)
    monkeypatch.setattr(attrs, "_cache_write", lambda k, v: None)
    monkeypatch.setattr(attrs, "_web_search", lambda q, n, g: _fake_hits([
        {"title": "منتجٌ تجريبي", "snippet": value_text, "link": link}]))


def test_image_and_web_disagreement_blocks_exactly_like_no_match(monkeypatch):
    """الصورةُ ٣٫٥ والويبُ ١٫٥ => **لا رمز**، الحوارُ يُعرَض، والقراءتان معاً."""
    _stub_web(monkeypatch, "نسبة الدهن 1.5%")
    out = attrs.resolve_by_attribute(
        "منتجٌ تجريبي", _cands("040110", "040120", "040140", "040150"),
        label_attributes=[{"name": "نسبة الدهن", "value": 3.5, "unit": "%"}],
        allow_web=True)
    assert out["hs6"] is None and out["resolved_from"] is None
    assert {r["source"] for r in out["readings"]} == {"image", "web"}
    assert {r["value"] for r in out["readings"]} == {3.5, 1.5}
    assert "تعارض" in out["missing_ar"]
    assert any("تعارض" in s for s in out["searched"])


def test_agreeing_readings_resolve_with_the_stronger_source(monkeypatch):
    """اتّفاقُ القراءتين => حسمٌ، والوسمُ لبطاقة العبوة (الأعلى سلطةً)."""
    _stub_web(monkeypatch, "نسبة الدهن 3.5%")
    out = attrs.resolve_by_attribute(
        "منتجٌ تجريبي", _cands("040110", "040120", "040140", "040150"),
        label_attributes=[{"name": "نسبة الدهن", "value": 3.5, "unit": "%"}],
        allow_web=True)
    assert out["hs6"] == "040120" and out["resolved_from"] == "image"
    assert len(out["readings"]) == 2


def test_same_band_but_different_numbers_is_still_a_conflict(monkeypatch):
    """٢٫٠ و٥٫٠ يقعان في نفس البند — ومع ذلك **تعارض**: القياسُ غيرُ ثابت،
    وثباتُه شرطُ الحسم (الحسمُ هنا يُخفي خلافاً حقيقياً خلف نتيجةٍ صحيحة)."""
    _stub_web(monkeypatch, "نسبة الدهن 5%")
    out = attrs.resolve_by_attribute(
        "منتجٌ تجريبي", _cands("040110", "040120", "040140", "040150"),
        label_attributes=[{"name": "نسبة الدهن", "value": 2.0, "unit": "%"}],
        allow_web=True)
    assert out["hs6"] is None, "حُسِم رغم تعارضِ القراءتين"


def test_web_is_consulted_even_when_the_label_already_answered(monkeypatch):
    """لا يُقصَّر مسارُ الويب عند نجاح الصورة — وإلا لَما اكتُشف التعارضُ أبداً."""
    calls = []
    monkeypatch.setattr(attrs, "_cache_read", lambda k: None)
    monkeypatch.setattr(attrs, "_cache_write", lambda k, v: None)
    monkeypatch.setattr(attrs, "_web_search",
                        lambda q, n, g: calls.append(q) or [])
    attrs.resolve_by_attribute(
        "منتجٌ تجريبي", _cands("040110", "040120", "040140", "040150"),
        label_attributes=[{"name": "نسبة الدهن", "value": 3.5, "unit": "%"}],
        allow_web=True)
    assert calls, "الويبُ لم يُستشَر — التعارضُ يمرّ بلا اكتشاف"


# ══════════════ D2 — أساسُ النسبة قربَ الحافّة ═══════════════════════════════

def test_band_basis_read_from_the_official_text():
    """أساسُ النطاق يُقرأ من نصّه الرسميّ لا يُفترَض."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert attrs.band_basis(d["bands"][0]) == "mm"      # «by weight»
    assert attrs.band_basis({"desc": ""}) is None       # لا تصريح => لا اتّهام


@pytest.mark.parametrize("unit,basis", [
    ("g/100ml", "mv"), ("g/100 ml", "mv"), ("gm/100ml", "mv"),
    ("mg/100ml", "mv"), ("ml/100ml", "vv"), ("% vol", "vv"), ("abv", "vv"),
    ("g/100g", "mm"), ("mg/100g", "mm"), ("% w/w", "mm"),
    ("%", None), ("percent", None), ("٪", None),
])
def test_unit_basis_table_entry(unit, basis):
    assert attrs.unit_basis(unit) == basis


@pytest.mark.parametrize("value", [5.9, 6.0, 6.1])
def test_cross_basis_reading_near_a_band_edge_refuses(value):
    """كتلة/حجم قربَ حدّ ٦٪: فارقُ الكثافة (~٠٫١٨ عند ٦) قد يعبر الحدّ،
    فالحسمُ هنا يُصدِر البندَ المجاور بوسمِ مصدرٍ واثق."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert attrs.select_by_value(d, value, "g/100ml") is None


def test_cross_basis_reading_far_from_any_edge_still_resolves():
    """بعيداً عن الحواف يبقى التحويلُ تقريبياً لا خاطئاً — لا نُهدِر قراءة."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert attrs.select_by_value(d, 3.5, "g/100ml") == "040120"


@pytest.mark.parametrize("unit", ["%", "percent", "٪", "g/100g", "% w/w"])
def test_same_basis_reading_near_an_edge_is_unaffected(unit):
    """`%` و`g/100g` بأساس النطاق نفسِه — الحافّةُ لا تُقلقها."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    assert attrs.select_by_value(d, 5.9, unit) == "040120"


def test_cross_basis_refusal_reason_names_the_basis():
    """الحوارُ يقول **لماذا** — «لا تقع في نطاقٍ وحيد» وحدَها تُخفي أنّ
    القراءة صالحةٌ وأنّ العيبَ في تحويل الأساس عند هذا الحدّ."""
    with _NoNet():
        out = attrs.resolve_by_attribute(
            "منتجٌ ما", _cands("040110", "040120", "040140", "040150"),
            label_attributes=[{"name": "نسبة الدهن", "value": 5.9,
                               "unit": "g/100ml"}],
            allow_web=False)
    assert out["hs6"] is None
    joined = " | ".join(out["searched"])
    # يُسمّي الوحدةَ وأساسَها ويقول إنّ الرفضَ لقُربِ حافّة — لا «لا تقع في
    # نطاقٍ وحيد» العارية التي تُخفي أنّ القراءة صالحةٌ أصلاً.
    assert "g/100ml" in joined and "(mv)" in joined, joined
    assert "حافّة" in joined and "أساس" in joined, joined


def test_every_cross_basis_unit_in_the_table_is_guarded():
    """كلُّ مدخلٍ مخالفِ الأساس في `_UNIT_FAMILY` محروسٌ فعلاً — لا مدخلٌ
    يُضاف لاحقاً فيفتح البابَ نفسَه بصمت."""
    d = attrs.discriminator(_cands("040110", "040120", "040140", "040150"))
    cross = [u for u in attrs._UNIT_FAMILY
             if attrs.cross_basis_conflict(u, d["bands"])]
    assert set(cross) >= {"g/100ml", "g/100 ml", "gm/100ml", "mg/100ml",
                          "ml/100ml", "% vol", "%vol", "% v/v", "abv"}, cross
    for unit in cross:
        fam = attrs._unit_family(unit)
        if fam is None or fam[0] != d["family"]:
            continue
        edge = d["bands"][1]["hi"]           # حافّةٌ حقيقية من المجموعة
        val = edge / fam[1]
        assert attrs.select_by_value(d, val, unit) is None, (
            f"{unit}: حُسِم على الحافّة رغم مخالفة الأساس")
