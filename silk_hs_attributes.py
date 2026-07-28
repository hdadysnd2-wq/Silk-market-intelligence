"""حَسْمُ الرمز بالسمة الرقمية — numeric-attribute HS auto-resolution.

> **البلاغ (المُشرِف).** صندوقُ حوار تأكيد HS كان يطلب من التاجر أن يختار بين
> بنود الترويسة الواحدة اعتماداً على **نسبةِ دهنٍ لا يعرفها** — حرفياً «إن كان
> حليب نادك كامل الدسم (أكثر من ٦٪)». هذا نقلٌ للعبء لا حسم: الرقمُ مكتوبٌ
> على بطاقة العبوة، ومنشورٌ على الويب. النظامُ يسأل عمّا يُجيب عنه المنتجُ
> نفسُه.
>
> **العقد الدائم (عائلة `ask-what-the-product-answers`).**
>   (١) **مسار الصورة** — سماتُ البطاقة الرقمية المستخلَصة من **نداء الرؤية
>       القائم نفسه** (`silk_product_intake`, نداءٌ واحدٌ مقيس، صفر تكلفة
>       إضافية) تُطابَق ببُعد العتبة فيُحسَم الرمز، `resolved_from="image"`.
>   (٢) **مسار الويب** — استعلامٌ واحد
>       (`silk_websearch_agent.web_search`) عن قيمة البُعد؛ رقمٌ برابطٍ قابلٍ
>       للاستشهاد => حسمٌ بـ`resolved_from="web"` و`source_url`.
>   (٣) **الحوار احتياطٌ لا افتراض** — لا يُعرَض إلا حين يعجز المساران، ويحمل
>       حينها ما وُجد وما نقص وحدودَ كل مرشّح **بلغةٍ مفهومة** («حتى 1% دهن»)
>       لا عتبةً جمركية يُفترَض أن يحفظها التاجر.
>   (٤) **لا اختلاق أبداً** — بلا رقمٍ مقيس، أو حين لا يقع الرقمُ في نطاقٍ
>       **وحيد**، لا يُحسَم رمزٌ (`hs6=None`) ويُعرَض الحوار. رمزٌ مستنتَجٌ
>       رقمٌ مستنتَج (المبدأ المؤسِّس، `CLAUDE.md`).

**قاعدةٌ عامّة لا حالةُ منتج** (عائلة `hardcoded-product-rule`، الدرس ٢٤):
صفر اسم منتج/دولة/رمز HS مكتوبٍ صلباً في منطق هذه الوحدة. النطاقاتُ تُقرأ من
**الوصف الرسميّ** (`silk_hs_resolver.official_description` ← `data/hs_reference.csv`)
لكل مرشّح — لا من بذرتنا الجزئية ولا من قائمةٍ مكتوبةٍ يدوياً (الدرس ٣٣:
حلِّل المصدر لا النثر) — والمعجمُ أدناه **أبعادُ قياسٍ لغوية** (دهن/كحول/سكر/
حجم/وزن) لا أسماءَ منتجات، بنفس منطق `silk_hs_confirm._DEGREE_TERMS`. القفل
`tests/test_hs_attribute_autoresolve.py` يثبت التعميم على ≥٤ عائلاتٍ متنوّعة
(دهن حليب، دهن مسحوق، سعة نبيذ، وزن تعبئة شاي، بريكس عصير).

المكتبات: stdlib فقط في النواة؛ الويب/المخزن استيرادٌ كسول — الوحدة تستورد
وتعمل بلا شبكة وبلا مفاتيح.
"""
from __future__ import annotations

import functools
import logging
import os
import re

log = logging.getLogger("silk.hs_attributes")


def enabled() -> bool:
    """الصمّام — **مُطفأٌ افتراضياً**؛ يُفعَّل صراحةً بـ
    `SILK_HS_ATTRIBUTE_RESOLVE=1`.

    كان مفعّلاً افتراضياً في أوّل شحنة، وذلك **قرارٌ خاطئ صُحِّح بأمر
    المُشرِف (D1)**: تفعيلُه عند الدمج يجعل ميزةً **لم تُجرَّب قطّ بمفتاحٍ
    حيّ** تُصدِر رموزاً جمركية لكلّ مستخدم. الحجّةُ السابقة («المُطفَأ هو
    الحالةُ المُبلَّغ عنها») تُبرِّر **الرغبة** في تفعيله، لا تفعيلَه قبل
    الدليل الحيّ — وهي حجّةٌ اعتُمِدت ذاتياً بلا إقرار المالك.

    تفعيلُه قرارُ مالكٍ منفصلٌ بعد الدمج، مشروطٌ بإغلاق G8 (تشغيلٌ حيّ
    مُوثَّق لمساري الصورة والويب ولمسار الرفض). مُطفأً يبقى السلوكُ حرفياً
    كما كان قبل هذا الفرع: الحوارُ يُعرَض كما هو."""
    raw = os.environ.get("SILK_HS_ATTRIBUTE_RESOLVE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ══════════════ ١) معجمُ أبعادِ القياس — من ملفٍ لا من الشيفرة (G2) ═════════
#
# **لا مصطلحَ بُعدٍ مكتوبٌ صلباً هنا** (عائلة `hardcoded-product-rule`، الدرسان
# ٢٤/٣٠: كلمةُ نطاقٍ حرفيةٌ مجمَّدةٌ في قالبٍ قابلٍ لإعادة الاستعمال هي العيبُ
# نفسُه الذي أخرج «التمور السعودية» في تقرير معكرونة). المعجمُ يعيش في
# `data/measurement_dimensions.csv` — بُعدٌ جديد يُضاف بصفٍّ لا بتعديل شيفرة،
# واستعلامُ الويب يُركِّب مصطلحَه من هناك. قفلُ `test_no_literal_dimension_term_
# in_module_source` يُفشِل البناءَ على أيّ مصطلحٍ من الملفّ يظهر حرفياً في
# منطق الوحدة.

_DIMENSIONS_CSV = os.environ.get("SILK_DIMENSIONS_CSV",
                                 "data/measurement_dimensions.csv")


@functools.lru_cache(maxsize=1)
def load_dimensions(path: str = "") -> dict:
    """معجمُ أبعادِ القياس من الملفّ — `{dimension: {label_ar, syn}}`.

    ملفٌّ غائب/تالف => قاموسٌ فارغ: الأبعادُ تبقى مكتشَفةً من النصّ الرسميّ
    (مفتاحُها الإنجليزيّ) ويُستعمَل المفتاحُ نفسُه تسميةً ومرادفاً — تدهورٌ
    نظيف، لا مصطلحٌ مختلَق."""
    import csv as _csv
    fp = path or _DIMENSIONS_CSV
    if not os.path.isabs(fp):
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), fp)
    out: dict = {}
    try:
        with open(fp, newline="", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                dim = (row.get("dimension") or "").strip().lower()
                if not dim:
                    continue
                syn = [t.strip() for field in ("synonyms_ar", "synonyms_en")
                       for t in (row.get(field) or "").split("|") if t.strip()]
                out[dim] = {"label_ar": (row.get("label_ar") or "").strip(),
                            "syn": tuple(syn)}
    except Exception as exc:  # noqa: BLE001 — ملفٌّ غائب = تدهورٌ نظيف
        log.warning("failed to load dimension lexicon %s: %s", fp, exc)
    return out


def dimension_terms(dimension: str) -> tuple[str, tuple[str, ...]]:
    """(التسميةُ العربية، المرادفات) لبُعدٍ — المفتاحُ نفسُه حين لا صفَّ له."""
    row = load_dimensions().get((dimension or "").strip().lower()) or {}
    label = row.get("label_ar") or dimension
    syn = tuple(row.get("syn") or ()) + (dimension,)
    return label, syn


# كلماتٌ لا تصلح بُعداً («of a content», «net weight content») — تُتخطّى
# بحثاً عن الكلمة الدالّة قبلها. أدواتُ لغةٍ لا مصطلحاتُ نطاق.
_DIM_STOPWORDS = frozenset({"a", "an", "the", "of", "net", "immediate", "any",
                            "no", "not", "its", "their", "such"})


# ══════════════ ٢) الوحدات وعائلاتُها (تحويلٌ إلى وحدةِ أساسٍ واحدة) ═════════
#
# كلُّ عائلةٍ لها وحدةُ أساسٍ واحدة تُقارَن عليها النطاقاتُ والقيَم، فلا
# تُقارَن كيلوغرامات بغرامات صامتةً. وحدةٌ غير معروفة => لا تحويل => لا حسم.
_UNIT_FAMILY: dict[str, tuple[str, float]] = {
    # نِسَبٌ — النسبةُ المئوية ووحداتُ البطاقة المكافئة لها اصطلاحاً في وسم
    # الأغذية (G3): «3.5 g/100ml» هي «3.5%» على العبوة. **حدٌّ موثَّق:**
    # g/100ml نسبةُ كتلةٍ إلى حجم و«%» في نصّ HS نسبةُ كتلةٍ إلى كتلة؛
    # يتباعدان بمقدار الكثافة (~٣٪ نسبيّاً للحليب). مقبولٌ لأنّ بطاقةَ
    # العبوة تستعملهما بالتبادل، والانحرافُ يبقى دون أضيق نطاقٍ في المرجع.
    "%": ("percent", 1.0), "percent": ("percent", 1.0),
    "per cent": ("percent", 1.0), "pct": ("percent", 1.0),
    "% vol": ("percent", 1.0), "%vol": ("percent", 1.0),
    "% v/v": ("percent", 1.0), "% m/m": ("percent", 1.0),
    "% w/w": ("percent", 1.0), "abv": ("percent", 1.0),
    "g/100ml": ("percent", 1.0), "g/100 ml": ("percent", 1.0),
    "g/100g": ("percent", 1.0), "g/100 g": ("percent", 1.0),
    "ml/100ml": ("percent", 1.0), "gm/100ml": ("percent", 1.0),
    "mg/100g": ("percent", 0.001), "mg/100ml": ("percent", 0.001),
    # كتلة — وحدةُ الأساس الغرام.
    "mg": ("weight", 0.001), "g": ("weight", 1.0), "gr": ("weight", 1.0),
    "gm": ("weight", 1.0),
    "gram": ("weight", 1.0), "grams": ("weight", 1.0),
    "kg": ("weight", 1000.0), "kgs": ("weight", 1000.0),
    "kilo": ("weight", 1000.0),
    "kilogram": ("weight", 1000.0), "kilograms": ("weight", 1000.0),
    "t": ("weight", 1_000_000.0), "tonne": ("weight", 1_000_000.0),
    "tonnes": ("weight", 1_000_000.0),
    # حجم — وحدةُ الأساس اللتر.
    "ml": ("volume", 0.001), "cl": ("volume", 0.01),
    "l": ("volume", 1.0), "litre": ("volume", 1.0), "litres": ("volume", 1.0),
    "liter": ("volume", 1.0), "liters": ("volume", 1.0),
    "cc": ("volume", 0.001),
    # طول — وحدةُ الأساس المليمتر.
    "mm": ("length", 1.0), "cm": ("length", 10.0), "m": ("length", 1000.0),
    "micron": ("length", 0.001), "microns": ("length", 0.001),
    # وحداتٌ عربية — بطاقةُ العبوة في سوقنا عربيةٌ أصلاً، ورفضُها كان
    # يُسقِط قراءةً صحيحةً إلى الحوار (انحدارُ جودةٍ متنكّرٌ في ثوب أمان).
    "٪": ("percent", 1.0), "بالمئة": ("percent", 1.0),
    "في المئة": ("percent", 1.0), "بالمائة": ("percent", 1.0),
    "جم": ("weight", 1.0), "غم": ("weight", 1.0), "جرام": ("weight", 1.0),
    "غرام": ("weight", 1.0), "كجم": ("weight", 1000.0),
    "كغم": ("weight", 1000.0), "كيلو": ("weight", 1000.0),
    "مل": ("volume", 0.001), "لتر": ("volume", 1.0), "ل": ("volume", 1.0),
    "مم": ("length", 1.0), "سم": ("length", 10.0),
    # مجرَّدٌ بلا وحدة (درجةُ بريكس مثلاً).
    "": ("scalar", 1.0),
}
# وحدةُ العرض لكل عائلة (وحدةُ الأساس نفسها).
_FAMILY_UNIT = {"percent": "%", "weight": "g", "volume": "L",
                "length": "mm", "scalar": ""}
# عائلةُ الوحدة تحسم البُعدَ مباشرةً حين تكون مادّية؛ النِّسَب والمجرَّدات
# يحسمها النصُّ («fat content» / «Brix value»).
_FAMILY_DIMENSION = {"weight": "weight", "volume": "volume",
                     "length": "length"}


# ══════════════ ٢ب) أساسُ النسبة — الحافّةُ لا تُعبَر بتحويلٍ تقريبيّ (D2) ══
#
# `%` في نصّ HS نسبةُ كتلةٍ إلى **كتلة** («by weight»)، بينما بطاقةُ العبوة
# قد تكتب كتلة/**حجم** (`g/100ml`) أو حجم/حجم (`% vol`). الأساسان يتباعدان
# بمقدار الكثافة — ~٣٪ نسبيّاً للحليب، أي ~٠٫١٨ مطلقة عند حدّ ٦٫٠٪. هذا
# **يكفي لعبور الحدّ**: صرامةُ G1 التي ترفض ٦٢ ترويسةً تُلتَفّ من هذا الباب
# الواحد. لذا: قراءةٌ بأساسٍ مخالف **قريبةٌ من أيّ حافّة** لا تحسم شيئاً —
# تسقط للحوار بسببٍ يُسمّي الأساس. بعيداً عن الحواف تبقى مقبولةً (التحويلُ
# تقريبيٌّ لا خاطئ، والخطأ لا يقلب النطاق).
#
# «mm» = كتلة/كتلة، «mv» = كتلة/حجم، «vv» = حجم/حجم. و`%` العارية بلا أساسٍ
# مُعلَن => ترث أساسَ النطاق فلا تعارضَ فيها أبداً.
_UNIT_BASIS: dict[str, str] = {
    "g/100ml": "mv", "g/100 ml": "mv", "gm/100ml": "mv", "mg/100ml": "mv",
    "ml/100ml": "vv", "% vol": "vv", "%vol": "vv", "% v/v": "vv", "abv": "vv",
    "g/100g": "mm", "g/100 g": "mm", "mg/100g": "mm",
    "% m/m": "mm", "% w/w": "mm",
}
# أساسُ النطاق يُقرأ من نصّه الرسميّ («by weight» / «by volume»).
# النصُّ الرسميّ في `hs_reference.csv` إنجليزيٌّ حصراً، فالأنماطُ إنجليزية.
# (مقابلاتٌ عربيةٌ هنا كانت ستكون تخميناً غيرَ مُختبَر، وتصطدم بمعجم الأبعاد.)
_BAND_BASIS_RE = (
    (re.compile(r"by\s+weight", re.I), "mm"),
    (re.compile(r"by\s+volume|alcoholic\s+strength", re.I), "vv"),
)
# مسافةُ الأمان من الحافّة — بوحدة أساس العائلة. config-driven.
_EDGE_MARGIN = float(os.environ.get("SILK_HS_ATTR_EDGE_MARGIN", "0.5") or "0.5")


def unit_basis(unit: object) -> str | None:
    """أساسُ نسبةِ الوحدة (`mm`/`mv`/`vv`) — أو `None` حين لا أساسَ مُعلَن."""
    u = re.sub(r"\s+", " ", str(unit or "").strip().lower().rstrip("."))
    return _UNIT_BASIS.get(u)


def band_basis(band: dict) -> str | None:
    """أساسُ نسبةِ النطاق من وصفه الرسميّ — أو `None` حين لا يُصرّح به."""
    desc = (band or {}).get("desc") or ""
    for pattern, basis in _BAND_BASIS_RE:
        if pattern.search(desc):
            return basis
    return None


def cross_basis_conflict(unit: object, bands: list) -> bool:
    """هل تُقرأ هذه الوحدةُ بأساسٍ يخالف أساسَ النطاقات؟

    `mv` (كتلة/حجم) لا يوافق أيّ أساسٍ يستعمله نصُّ HS، فيُعَدّ مخالفاً
    دوماً. غيرُها يُقارَن بأساس النطاق المُصرَّح به؛ نطاقٌ لا يُصرّح بأساسه
    لا يُتَّهم (لا اختلاقَ تعارضٍ بلا دليل)."""
    ub = unit_basis(unit)
    if ub is None:
        return False                       # «%» عارية — ترث أساسَ النطاق
    if ub == "mv":
        return True                        # كتلة/حجم: خارج اصطلاح HS دائماً
    declared = {b for b in (band_basis(x) for x in (bands or [])) if b}
    return bool(declared) and ub not in declared


def near_any_edge(bands: list, base_value: float,
                  margin: float | None = None) -> bool:
    """هل تقع القيمةُ ضمن `margin` من أيّ حافّةِ نطاقٍ في المجموعة؟"""
    m = _EDGE_MARGIN if margin is None else margin
    for b in bands or []:
        for edge in (b.get("lo"), b.get("hi")):
            if edge is not None and abs(base_value - edge) <= m:
                return True
    return False


def _unit_family(unit: object) -> tuple[str, float] | None:
    """(عائلة، معامل التحويل لوحدة الأساس) — أو `None` لوحدةٍ غير معروفة."""
    u = str(unit or "").strip().lower().rstrip(".")
    u = re.sub(r"\s+", " ", u)
    return _UNIT_FAMILY.get(u)


# ══════════════ ٣) قراءةُ النطاق الرقميّ من الوصف الرسميّ ════════════════════

# الرقمُ ووحدتُه — مجموعاتٌ **مسمّاة** (`np/up` للبادئة، `ns/us` للاحقة) كي
# تُقرأ بالاسم لا بترتيبٍ ينكسر كلّما أُضيف بديلٌ للنمط.
_NUM_RAW = r"\d+(?:\.\d+)?"
# لاحقةُ الوحدة اختيارية. الحدُّ `(?![a-z])` لا `\b` عمداً: `%` محرفٌ غيرُ
# كلميّ فلا حدَّ كلميّ بعده، وكان `\b` يُفشِل التقاطه فتُقرأ «1%» مجرَّدةً
# (عائلةُ `scalar`) فتفشل مقارنتُها بنسبةٍ من البطاقة/الويب.
# الوحداتُ المركّبة (g/100ml…) أولاً — الأطولُ يفوز فلا يُقتطَع «g» وحدها.
_UNIT_RAW = (r"\s*(?:g/100\s*ml|g/100\s*g|gm/100\s*ml|ml/100\s*ml"
             r"|mg/100\s*g|mg/100\s*ml|%\s*vol|%\s*v/v|%\s*m/m|%\s*w/w"
             r"|%|per\s*cent|percent|abv|kgs?|kilograms?|kilo|tonnes?"
             r"|grams?|gm|gr|g|mg|litres?|liters?|ml|cl|cc|l"
             r"|microns?|mm|cm|m)?(?![a-z0-9])")


_NUM_P = f"(?P<np>{_NUM_RAW})"
_NUM_S = f"(?P<ns>{_NUM_RAW})"
_UNIT_P = _UNIT_RAW.replace("(?:", "(?P<up>", 1)
_UNIT_S = _UNIT_RAW.replace("(?:", "(?P<us>", 1)
# نسخةٌ مجهّلةٌ لإعادة الاستعمال خارج محلّل الحدود (قراءةُ قيمةٍ من نصّ ويب).
_NUM = f"({_NUM_RAW})"
_UNIT = _UNIT_RAW.replace("(?:", "(", 1)
# ── صرامةُ الحدّ تُحمَل من العبارة المطابِقة، لا تُفترَض بالجانب (G1) ────────
#
# نصُّ HS **غيرُ متماثل** عمداً: «exceeding 1%» يستثني 1.0 و«not exceeding 6%»
# يشمل 6.0. تسطيحُ الاثنين إلى «أدنى حصريّ / أعلى شامل» يعمل صدفةً على
# الصياغة الغالبة ويُخطئ على «less than N» (حصريّ) و«N or more» (شامل) —
# وخطأُ حدٍّ واحد يُصدِر **رمزاً خاطئاً بوسمِ مصدرٍ واثق**، أي اختلاقاً لا
# فجوة. لذا كل عبارةٍ مصنَّفةٌ صراحةً هنا بجانبها **وشمولها**.
_BOUNDS: tuple[tuple[str, str, bool], ...] = (
    # (نمط، الجانب، شامل؟)
    (r"(?:not|no)\s+(?:\w+\s+)?exceeding", "hi", True),
    (r"not\s+more\s+than|no\s+more\s+than", "hi", True),
    (r"up\s+to|not\s+over|no\s+greater\s+than", "hi", True),
    (r"less\s+than|under|below|fewer\s+than", "hi", False),
    (r"not\s+less\s+than|at\s+least", "lo", True),
    (r"exceeding|more\s+than|greater\s+than|over|above", "lo", False),
    (r"لا\s+يتجاوز|حتى", "hi", True),
    (r"أقل\s+من|دون", "hi", False),
    (r"لا\s+يقل\s+عن", "lo", True),
    (r"يتجاوز|أكثر\s+من|تزيد\s+عن", "lo", False),
)
# صيغُ اللاحقة («2 litres or less») — الرقمُ يسبق العبارة.
_BOUNDS_SUFFIX: tuple[tuple[str, str, bool], ...] = (
    (r"or\s+less|or\s+under|أو\s+أقل|فأقل", "hi", True),
    (r"or\s+more|and\s+over|or\s+over|أو\s+أكثر|فأكثر", "lo", True),
)

# البادئاتُ مرتَّبةٌ بالأطول-أولاً كي يفوز «not more than» على «more than»
# و«not exceeding» على «exceeding» عند نفس الموضع.
_PREFIX_ALT = "|".join(f"(?P<b{i}>{pat})" for i, (pat, _s, _inc)
                       in enumerate(_BOUNDS))
_SUFFIX_ALT = "|".join(f"(?P<s{i}>{pat})" for i, (pat, _s, _inc)
                       in enumerate(_BOUNDS_SUFFIX))

_EVENT_RE = re.compile(
    rf"(?:{_PREFIX_ALT})\s*{_NUM_P}{_UNIT_P}"
    rf"|{_NUM_S}{_UNIT_S}\s*(?:{_SUFFIX_ALT})",
    re.I)

_DIM_WORD_RE = re.compile(r"(\w+)\s+(?:content|value|strength|degree)", re.I)


def _events(text: str) -> list[dict]:
    """أحداثُ الحدود — [{kind, inclusive, value, unit, family, factor, pos, span}].

    `inclusive` مأخوذٌ من **العبارة التي طابقت فعلاً** لا من الجانب (G1)."""
    out: list[dict] = []
    for m in _EVENT_RE.finditer(text or ""):
        kind = inclusive = None
        for i, (_pat, side, inc) in enumerate(_BOUNDS):
            if m.group(f"b{i}"):
                kind, inclusive = side, inc
                num, unit = m.group("np"), m.group("up")
                break
        if kind is None:
            for i, (_pat, side, inc) in enumerate(_BOUNDS_SUFFIX):
                if m.group(f"s{i}"):
                    kind, inclusive = side, inc
                    num, unit = m.group("ns"), m.group("us")
                    break
        if kind is None:                       # pragma: no cover — فرعٌ مستحيل
            continue
        try:
            value = float(num)
        except (TypeError, ValueError):        # pragma: no cover — النمط رقميّ
            continue
        fam = _unit_family(unit or "")
        if fam is None:
            continue
        out.append({"kind": kind, "inclusive": inclusive, "value": value,
                    "unit": (unit or "").lower(), "family": fam[0],
                    "factor": fam[1], "pos": m.start(), "span": m.span()})
    return out


def _dimension_from_text(text: str, before: int) -> str:
    """البُعدُ من النصّ — أقربُ «<كلمة> content/value/strength» قبل الحدّ."""
    best = ""
    for m in _DIM_WORD_RE.finditer(text or ""):
        if m.start() > before:
            break
        word = (m.group(1) or "").strip().lower()
        if word and word not in _DIM_STOPWORDS:
            best = word
    return best


# ── المحورُ غيرُ الرقميّ (G1، اكتُشف بمسحِ المرجع كاملاً) ────────────────────
#
# ترويسةٌ قد تنقسم بمحورين معاً: 0902 = لونُ الشاي (أخضر/أسود) × وزنُ التعبئة
# (≤٣كجم/>٣كجم). قياسُ الوزن وحده لا يُحدِّد بنداً — يختار أحدَ اثنين
# يختلفان في اللون أيضاً، **فيَصدُر رمزٌ خاطئ بوسمِ مصدرٍ واثق**. لذا لا
# يُقبَل مُميِّزٌ إلا حين تكون أوصافُ المرشّحين متطابقةً **بعد نزع عباراتِ
# الحدّ** — أي أنّ المحورَ الرقميَّ هو الفارقُ الوحيد بينهم.
# `but` وحدَها تُسقَط: تُدخِلها صياغةُ النطاق ذي الطرفين ولا معنى لها.
_RESIDUAL_DROP = frozenset({"but"})
_RESIDUAL_SPLIT = re.compile(r"[^0-9a-z\u0600-\u06ff]+", re.I)


def _residual_axis(desc: str, evs: list[dict]) -> frozenset:
    """صفاتُ الوصف بعد نزع عباراتِ الحدّ — بصمةُ المحاور غير الرقمية."""
    text, out = desc or "", []
    for a, b in sorted((e["span"] for e in evs), reverse=True):
        text = text[:a] + " " + text[b:]
    for tok in _RESIDUAL_SPLIT.split(text.lower()):
        tok = tok.strip()
        if tok and not tok.isdigit() and tok not in _RESIDUAL_DROP:
            out.append(tok)
    return frozenset(out)


def band_of(hs6: object, description: str = "") -> dict | None:
    """نطاقُ العتبة الرقمية لرمز HS6 — أو `None` حين لا عتبةَ في وصفه.

    الوصفُ الرسميّ أولاً (المصدرُ الذي تعيش فيه العتبة فعلاً)، ثم الوصفُ
    المُمرَّر إن غاب الرمزُ عن المرجع. يعيد dict:
    `{hs6, lo, lo_inclusive, hi, hi_inclusive, unit, family, dimension,
    axis, desc}` بوحدةِ أساسِ العائلة — `lo`/`hi` قد يكون أيٌّ منهما `None`
    (نطاقٌ مفتوحُ الطرف)، و`*_inclusive` مأخوذٌ من عبارة الحدّ نفسِها (G1)."""
    from silk_hs_resolver import official_description
    code = str(hs6 or "").strip()
    desc = official_description(code) or (description or "").strip()
    if not desc:
        return None
    evs = _events(desc)
    if not evs:
        return None
    # اجمع بالعائلة واختر الأكثرَ حضوراً (تعادلٌ => الأحدث موضعاً): وصفٌ قد
    # يذكر عتبتين من عائلتين (وزنُ عبوةٍ ونسبةُ محتوى) — العائلةُ الغالبة هي
    # التي يميّز بها البند فعلاً.
    groups: dict[str, list[dict]] = {}
    for e in evs:
        groups.setdefault(e["family"], []).append(e)
    family = max(groups, key=lambda f: (len(groups[f]), groups[f][-1]["pos"]))
    chosen = groups[family]
    los = [e for e in chosen if e["kind"] == "lo"]
    his = [e for e in chosen if e["kind"] == "hi"]
    lo = los[-1]["value"] * los[-1]["factor"] if los else None
    hi = his[-1]["value"] * his[-1]["factor"] if his else None
    lo_inc = bool(los[-1]["inclusive"]) if los else False
    hi_inc = bool(his[-1]["inclusive"]) if his else False
    if lo is not None and hi is not None and lo >= hi:
        return None                      # نطاقٌ متناقض — فجوة لا اختلاق
    dimension = _FAMILY_DIMENSION.get(family) or _dimension_from_text(
        desc, chosen[0]["pos"])
    if not dimension:
        return None                      # بُعدٌ مجهول => لا يصلح مُميِّزاً
    return {"hs6": code, "lo": lo, "lo_inclusive": lo_inc,
            "hi": hi, "hi_inclusive": hi_inc, "family": family,
            "unit": _FAMILY_UNIT.get(family, ""), "dimension": dimension,
            "axis": _residual_axis(desc, evs), "desc": desc}


# ══════════════ ٤) المُميِّز — هل تتمايز المرشّحات ببُعدٍ رقميٍّ واحد؟ ═══════

def _candidate_codes(candidates: list) -> list[tuple[str, str]]:
    """(رمز، وصفٌ محليٌّ إن وُجد) من مرشّحي `_public_candidates`/البوّابة."""
    out: list[tuple[str, str]] = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        code = str(c.get("hs6") or c.get("hs_code") or "").strip()
        if code:
            out.append((code, str(c.get("description_ar")
                                  or c.get("code_desc") or "")))
    return out


def discriminator(candidates: list) -> dict | None:
    """المُميِّزُ الرقميّ بين المرشّحين — أو `None` حين لا يوجد.

    يُشترَط أربعةُ شروطٍ **كلُّها**، وإخلالُ أيٍّ منها يعيد `None` (فيُعرَض
    الحوارُ كما كان — لا حسمَ على أساسٍ مشوَّش):
      (١) مرشّحان فأكثر لهما نطاقٌ فعليّ؛
      (٢) بُعدٌ ووحدةٌ واحدة لهم جميعاً؛
      (٣) **المحورُ الرقميُّ هو الفارقُ الوحيد** — أوصافُهم متطابقةٌ بعد نزع
          عباراتِ الحدّ (`axis`). ترويسةٌ تنقسم بمحورين (لونُ الشاي × وزنُ
          التعبئة) لا يُحدِّدها قياسٌ واحد، وقبولُها يُصدِر رمزاً خاطئاً
          بوسمِ مصدرٍ واثق — اختلاقٌ لا فجوة؛
      (٤) نطاقاتٌ **متّصلةٌ بلا تداخلٍ ولا ازدواجِ تغطية** — الحدُّ المشترك
          يملكه **طرفٌ واحدٌ بالضبط** (`prev.hi_inclusive ^ next.lo_inclusive`).
    """
    bands = [b for b in (band_of(code, desc)
                         for code, desc in _candidate_codes(candidates))
             if b is not None]
    if len(bands) < 2:
        return None
    if len({b["dimension"] for b in bands}) != 1:
        return None
    if len({b["family"] for b in bands}) != 1:
        return None
    if len({b["axis"] for b in bands}) != 1:
        return None                      # محورٌ غيرُ رقميٍّ إضافي — لا حسم
    ordered = sorted(bands, key=lambda b: (b["lo"] if b["lo"] is not None
                                           else float("-inf")))
    for prev, nxt in zip(ordered, ordered[1:]):
        prev_hi = prev["hi"]
        nxt_lo = nxt["lo"]
        if prev_hi is None or nxt_lo is None:
            return None                  # طرفٌ مفتوحٌ في الوسط — تداخلٌ حتميّ
        if prev_hi != nxt_lo:
            return None                  # فجوةٌ أو تداخلٌ رقميّ
        if bool(prev["hi_inclusive"]) == bool(nxt["lo_inclusive"]):
            return None                  # الحدُّ مزدوجُ التغطية أو مكشوف
    dimension = ordered[0]["dimension"]
    label_ar, syn = dimension_terms(dimension)
    return {"dimension": dimension, "family": ordered[0]["family"],
            "unit": ordered[0]["unit"], "label_ar": label_ar,
            "synonyms": syn, "bands": ordered}


def _contains(band: dict, base_value: float) -> bool:
    """هل تقع القيمة (بوحدة الأساس) داخل النطاق؟ — بالشمول **المُحلَّل** من
    عبارة الحدّ لا بافتراضٍ حسب الجانب (G1): «less than 6» تستثني 6.0 بينما
    «not exceeding 6» تشملها، وكلتاهما حدٌّ أعلى."""
    lo, hi = band.get("lo"), band.get("hi")
    if lo is not None:
        if band.get("lo_inclusive"):
            if base_value < lo:
                return False
        elif base_value <= lo:
            return False
    if hi is not None:
        if band.get("hi_inclusive"):
            if base_value > hi:
                return False
        elif base_value >= hi:
            return False
    return True


def select_by_value(disc: dict | None, value: object,
                    unit: object = "") -> str | None:
    """الرمزُ الذي يحوي هذه القيمة — أو `None` (صفر تطابقٍ أو أكثر من واحد).

    التطابقُ **الوحيد** شرطٌ صريح: قيمةٌ على الحدّ بين نطاقين أو خارجهما
    جميعاً لا تحسم شيئاً — تُعرَض للمستخدم بدل أن تُخمَّن."""
    if not disc or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    fam = _unit_family(unit if unit is not None else "")
    if fam is None or fam[0] != disc["family"]:
        return None
    base = v * fam[1]
    # D2: قراءةٌ بأساسٍ مخالف قربَ حافّة لا تحسم — التحويلُ التقريبيّ قد
    # يعبر الحدّ، فيَصدُر رمزُ البند المجاور بوسمِ مصدرٍ واثق.
    if cross_basis_conflict(unit, disc["bands"]) and near_any_edge(
            disc["bands"], base):
        return None
    hits = [b["hs6"] for b in disc["bands"] if _contains(b, base)]
    return hits[0] if len(hits) == 1 else None


# ══════════════ ٥) وصفُ النطاق بلغةٍ مفهومة (للحوار الاحتياطي) ═══════════════

def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def range_ar(band: dict, label_ar: str = "") -> str:
    """«حتى 1%» / «أكثر من 6% وحتى 10%» — لا رمزَ ولا عتبةً جمركية خام."""
    unit = band.get("unit") or ""
    lo, hi = band.get("lo"), band.get("hi")
    # اللغةُ تعكس الشمولَ المُحلَّل: «حتى ٦٪» تشمل ٦ بينما «دون ٦٪» تستثنيها.
    hi_txt = (f"حتى {_fmt(hi)}{unit}" if band.get("hi_inclusive")
              else f"دون {_fmt(hi)}{unit}") if hi is not None else ""
    lo_txt = (f"من {_fmt(lo)}{unit}" if band.get("lo_inclusive")
              else f"أكثر من {_fmt(lo)}{unit}") if lo is not None else ""
    if lo is None and hi is not None:
        txt = hi_txt
    elif lo is not None and hi is None:
        txt = lo_txt
    elif lo is not None and hi is not None:
        txt = f"{lo_txt} و{hi_txt}"
    else:
        return ""
    return f"{txt} {label_ar}".strip() if label_ar else txt


# ══════════════ ٦) مسار الصورة — سماتُ البطاقة المستخلَصة سلفاً ══════════════

def _norm_ar(s: object) -> str:
    from silk_hs_confirm import _norm
    return _norm(str(s or ""))


def value_from_label(label_attributes: object, disc: dict
                     ) -> tuple[float | None, str, str]:
    """(قيمة، وحدة، اسمُ السمة) من سمات بطاقة العبوة — أو (None, "", "").

    السمةُ تُقبَل فقط حين يطابق **اسمُها** بُعدَ المُميِّز و**وحدتُها** عائلتَه
    — سمةُ حجمٍ لا تُقرأ نسبةَ دهنٍ بالخطأ."""
    syns = [_norm_ar(s) for s in disc.get("synonyms") or ()]
    for item in (label_attributes or []):
        if not isinstance(item, dict):
            continue
        name = _norm_ar(item.get("name"))
        if not name or not any(s and s in name for s in syns):
            continue
        fam = _unit_family(item.get("unit") or "")
        if fam is None or fam[0] != disc["family"]:
            continue
        try:
            val = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        return val, str(item.get("unit") or ""), str(item.get("name") or "")
    return None, "", ""


# ══════════════ ٧) مسار الويب — استعلامٌ واحد برابطٍ قابلٍ للاستشهاد ═════════

# نافذةُ الجوار بين مرادفِ البُعد والرقم — رقمٌ بعيدٌ عن ذِكر البُعد ليس
# قيمتَه (حارسُ «لا اختلاق بالجوار»).
_PROXIMITY = int(os.environ.get("SILK_HS_ATTR_PROXIMITY", "60") or "60")
_WEB_RESULTS = int(os.environ.get("SILK_HS_ATTR_WEB_RESULTS", "5") or "5")
# ثقةُ رقمٍ من نتيجةِ بحثٍ عضوية — دليلٌ ثانويٌّ برابط، لا مصدرٌ أوّليّ؛
# تُعلَن كما هي ولا تُرفَع (نفس وسم `_PREFERRED_NOTE` في وكيل البحث).
_WEB_CONFIDENCE = 0.5

_VALUE_RE = re.compile(rf"{_NUM}{_UNIT}")


def _web_search(query: str, num: int, gl: str | None):
    """نقطةُ اختناقٍ واحدة للبحث — تُستبدَل في الاختبارات بلا شبكة."""
    from silk_websearch_agent import web_search
    return web_search(query, num=num, gl=gl)


def _search_configured() -> bool:
    from silk_websearch_agent import search_key
    return bool(search_key())


def _cache_read(key: str):
    try:
        import silk_store
        payload = silk_store.get_cached_hs_classification(key)
        return payload if isinstance(payload, dict) else None
    except Exception as e:  # noqa: BLE001 — الذاكرة تحسينٌ لا شرط
        log.debug("attribute cache read skipped: %s", e)
        return None


def _cache_write(key: str, payload: dict) -> None:
    try:
        import silk_store
        silk_store.cache_hs_classification(key, payload)
    except Exception as e:  # noqa: BLE001
        log.debug("attribute cache write skipped: %s", e)


def _product_tokens(product: str) -> list[str]:
    """صفاتُ المنتج المميّزة — بلا صفاتِ الدرجة (كامل/منزوع…) التي تشيع في
    كل نتيجةٍ فلا تُثبِت أن النتيجة عن هذا المنتج."""
    from silk_hs_confirm import _tokens, _DEGREE_TERMS
    return [t for t in _tokens(product or "") if t not in _DEGREE_TERMS]


def _value_near(text: str, disc: dict) -> tuple[float | None, str]:
    """أقربُ رقمٍ بوحدةٍ متوافقة إلى مرادفٍ للبُعد في هذا النصّ."""
    norm = _norm_ar(text)
    for syn in disc.get("synonyms") or ():
        s = _norm_ar(syn)
        if not s:
            continue
        for hit in re.finditer(re.escape(s), norm):
            window = norm[hit.end():hit.end() + _PROXIMITY]
            for m in _VALUE_RE.finditer(window):
                fam = _unit_family(m.group(2) or "")
                if fam is None or fam[0] != disc["family"]:
                    continue
                try:
                    return float(m.group(1)), (m.group(2) or "")
                except (TypeError, ValueError):  # pragma: no cover
                    continue
    return None, ""


def probe_web(product: str, disc: dict, gl: str | None = None) -> dict | None:
    """استعلامٌ واحد عن قيمة البُعد — dict برابطٍ قابلٍ للاستشهاد أو `None`.

    شرطان لقبول الرقم: (١) يقع بجوار مرادفٍ للبُعد داخل النافذة، (٢) النتيجةُ
    تذكر **صفةً مميّزة من اسم المنتج** — رقمُ منتجٍ آخر لا يُستعار لمنتجنا.
    فشلُ الخدمة **المُهيَّأة** يُعلَن للمشغّل (`service_failure`، الدرس ٢٦)."""
    query = f"{product} {disc.get('label_ar') or disc['dimension']}".strip()
    cache_key = f"hsattr::{disc['dimension']}::{query}"
    cached = _cache_read(cache_key)
    if isinstance(cached, dict) and cached.get("query") == query:
        return cached.get("hit")
    try:
        findings = _web_search(query, _WEB_RESULTS, gl) or []
    except Exception as e:  # noqa: BLE001 — البحث لا يُسقط المسار أبداً
        log.warning("attribute web probe failed: %s", e)
        findings = []
    real = [f for f in findings if getattr(f, "value", None) is not None]
    if not real:
        if _search_configured():
            try:
                import silk_ops_log
                silk_ops_log.record_service_failure(
                    "hs_attribute_web",
                    "استعلامُ سمةِ التصنيف لم يُرجِع نتائج (فشل/مهلة/بلا نتائج)",
                    context={"query": query, "dimension": disc["dimension"]})
            except Exception:  # noqa: BLE001
                pass
        return None
    wanted = _product_tokens(product)
    hit = None
    for f in real:
        v = f.value if isinstance(f.value, dict) else {}
        blob = " ".join(str(v.get(k) or "") for k in ("title", "snippet", "link"))
        norm_blob = _norm_ar(blob)
        if wanted and not any(_norm_ar(t) in norm_blob for t in wanted):
            continue                     # نتيجةٌ لا تخصّ هذا المنتج — لا تُستعار
        value, unit = _value_near(blob, disc)
        if value is None:
            continue
        hit = {"value": value, "unit": unit,
               "source_url": str(v.get("link") or ""),
               "title": str(v.get("title") or ""),
               "snippet": str(v.get("snippet") or "")}
        break
    _cache_write(cache_key, {"query": query, "hit": hit})
    return hit


# ══════════════ ٨) نقطةُ الاختناق الواحدة ════════════════════════════════════

_IMAGE_NOTE = "الرمز محدَّد من صورة العبوة"
_WEB_NOTE = "الرمز محدَّد من مصدر ويب"


def _report(disc: dict | None, **over) -> dict:
    """شكلُ التقرير الموحّد — يُعاد دوماً (نجاحاً أو فجوة)، فيقرؤه المستدعي
    مرّة للحسم ومرّة لبناء الحوار الاحتياطي بلا شكلين متوازيين."""
    base = {
        "hs6": None, "resolved_from": None, "value": None, "unit": None,
        "source_url": None, "confidence": None, "note_ar": "",
        "attribute": (disc or {}).get("dimension"),
        "label_ar": (disc or {}).get("label_ar"),
        "searched": [], "missing_ar": "", "readings": [],
        "bands_ar": [{"hs6": b["hs6"],
                      "range_ar": range_ar(b, (disc or {}).get("label_ar") or "")}
                     for b in (disc or {}).get("bands", [])],
    }
    if disc:
        base["unit"] = disc.get("unit")
    base.update(over)
    return base


# تفاوتٌ مسموحٌ بين قراءتين قبل اعتبارهما متعارضتين — **صفرٌ افتراضياً**
# (تطابقٌ تامّ بعد التحويل لوحدة الأساس). قراءتان تختلفان رقمياً تعنيان أنّ
# القياس **غيرُ ثابت**، وثباتُه شرطُ الحسم — حتى لو وقع كلاهما في نفس النطاق
# صدفةً (أمرُ المُشرِف G4: «التعارضُ يتصرّف كعدم التطابق تماماً»).
_AGREE_TOL = float(os.environ.get("SILK_HS_ATTR_AGREE_TOL", "0") or "0")
_FLOAT_EPS = 1e-9

# أولويةُ المصادر عند **الاتّفاق** (لا عند التعارض — التعارضُ يحجب مطلقاً):
# بطاقةُ العبوة قراءةٌ مباشرةٌ للمنتج نفسه، والويبُ استشهادٌ ثانويّ عنه.
_SOURCE_RANK = {"image": 2, "web": 1}


def _base(value: float, unit: object, disc: dict) -> float | None:
    fam = _unit_family(unit if unit is not None else "")
    if fam is None or fam[0] != disc["family"]:
        return None
    return float(value) * fam[1]


def _readings_agree(readings: list[dict], disc: dict) -> bool:
    """هل تتّفق كلُّ القراءات رقمياً (بعد التحويل لوحدة الأساس)؟"""
    bases = [b for b in (_base(r["value"], r["unit"], disc) for r in readings)
             if b is not None]
    if len(bases) != len(readings):
        return False                     # قراءةٌ بوحدةٍ غير قابلة للتحويل
    return max(bases) - min(bases) <= max(_AGREE_TOL, _FLOAT_EPS)


def resolve_by_attribute(product: str, candidates: list,
                         label_attributes: object = None,
                         allow_web: bool = True,
                         gl: str | None = None) -> dict:
    """احسِم الرمزَ بالسمة الرقمية قبل عرض الحوار — التقريرُ الموحّد دوماً.

    **يُستشار المساران كلاهما** (بطاقةُ العبوة + استعلامُ ويبٍ واحد) ثم
    يُقارَنان (G4): اتّفاقٌ => حسمٌ بأعلى المصدرين سلطةً؛ **تعارضٌ => لا
    رمز**، حالُه حالُ عدم التطابق تماماً، والقراءتان معاً في التقرير. لا
    يُقصَّر مسارُ الويب حين ينجح مسارُ الصورة: قراءةٌ ثانيةٌ تُناقض الأولى
    دليلٌ على أنّ القياس غيرُ ثابت، وإخفاؤها يُحوِّل تعارضاً إلى ثقةٍ كاذبة.

    `hs6` غيرُ `None` يعني حسماً بدليلٍ موسوم؛ `None` يعني **اعرِض الحوار**
    محمَّلاً بـ`searched`/`missing_ar`/`bands_ar`/`readings` — لا رمزَ
    مُخمَّن أبداً."""
    if not enabled():
        return _report(None, missing_ar="حسمُ السمة الرقمية مُعطَّل "
                                        "(SILK_HS_ATTRIBUTE_RESOLVE=0)")
    disc = discriminator(candidates)
    if disc is None:
        return _report(None, missing_ar="لا تتمايز المرشّحات بعتبةٍ رقمية "
                                        "واحدة — لا حسمَ آليّ ممكن")
    label = disc.get("label_ar") or disc["dimension"]
    searched: list[str] = []
    readings: list[dict] = []

    # (١) صورةُ العبوة — سماتٌ مستخلَصةٌ سلفاً، صفر نداءٍ إضافي.
    value, unit, attr_name = value_from_label(label_attributes, disc)
    if value is not None:
        readings.append({"source": "image", "value": value, "unit": unit,
                         "source_url": None, "detail": attr_name})
        searched.append(f"صورة العبوة ({attr_name}: {_fmt(value)}{unit})")
    else:
        searched.append("صورة العبوة (لا سمةٌ رقمية مقروءة)"
                        if label_attributes else "صورة العبوة (لم تُرفَق)")

    # (٢) الويب — استعلامٌ واحد برابطٍ قابلٍ للاستشهاد. **يُشغَّل دائماً**
    # (لا يُختصَر عند نجاح الصورة) كي يُكتشَف التعارضُ لا أن يُطمَس.
    if allow_web:
        hit = probe_web(product, disc, gl=gl)
        if hit:
            readings.append({"source": "web", "value": hit["value"],
                             "unit": hit["unit"],
                             "source_url": hit["source_url"] or None,
                             "detail": hit.get("title") or ""})
            searched.append(
                f"بحث ويب ({label}: {_fmt(hit['value'])}{hit['unit']})")
        else:
            searched.append(f"بحث ويب عن {label} (بلا رقمٍ موثَّق)")
    else:
        searched.append("بحث الويب غير متاح لهذا الطلب")

    # (٣) المقارنة — تعارضٌ رقميّ يحجب مطلقاً (G4).
    if len(readings) > 1 and not _readings_agree(readings, disc):
        detail = "، ".join(
            f"{'صورة العبوة' if r['source'] == 'image' else 'مصدر ويب'} "
            f"{_fmt(r['value'])}{r['unit']}" for r in readings)
        searched.append(f"تعارضٌ بين القراءات: {detail}")
        return _report(disc, searched=searched, readings=readings,
                       missing_ar=(f"قراءتان متعارضتان لـ{label} ({detail}) — "
                                   "لا يُحسَم رمزٌ على قياسٍ غير ثابت؛ أكّد "
                                   "البند المطابق أدناه."))

    # (٤) الحسم — أعلى المصدرين سلطةً بين المتّفقين.
    for r in sorted(readings, key=lambda x: -_SOURCE_RANK.get(x["source"], 0)):
        hs6 = select_by_value(disc, r["value"], r["unit"])
        if not hs6:
            # D2: سببٌ يُسمّي الأساس حين كان الرفضُ لقُربِ حافّةٍ بأساسٍ مخالف
            # — «لا تقع في نطاقٍ وحيد» وحدَها تُخفي أنّ القراءة **صالحة**
            # لكنّ تحويلَها تقريبيٌّ عند هذا الحدّ بالذات.
            base = _base(r["value"], r["unit"], disc)
            if (base is not None
                    and cross_basis_conflict(r["unit"], disc["bands"])
                    and near_any_edge(disc["bands"], base)):
                reason = (
                    f"القيمة {_fmt(r['value'])}{r['unit']} بوحدةٍ أساسُ "
                    f"نسبتها ({unit_basis(r['unit'])}) يخالف أساسَ حدود "
                    f"البنود، وهي على مسافة ≤{_fmt(_EDGE_MARGIN)} من حافّة "
                    "— فارقُ الأساس قد يعبر الحدّ فلا تُحسَم")
            else:
                reason = (f"القيمة {_fmt(r['value'])}{r['unit']} لا تقع في "
                          "نطاقٍ وحيد")
            searched.append(reason)
            break
        if r["source"] == "image":
            note = f"{_IMAGE_NOTE} — {label} {_fmt(r['value'])}{r['unit']}"
            conf = 1.0
        else:
            note = (f"{_WEB_NOTE}: {r['source_url']} — "
                    f"{label} {_fmt(r['value'])}{r['unit']}")
            conf = _WEB_CONFIDENCE
        return _report(disc, hs6=hs6, resolved_from=r["source"],
                       value=r["value"], unit=r["unit"],
                       source_url=r["source_url"], confidence=conf,
                       searched=searched, readings=readings, note_ar=note)

    return _report(disc, searched=searched, readings=readings,
                   missing_ar=(f"لم يُعثَر على {label} لهذا المنتج لا من صورة "
                               "العبوة ولا من مصدرٍ ويبٍ قابلٍ للاستشهاد — "
                               "أرفِق صورة العبوة أو اختر الرمز المطابق أدناه."))


if __name__ == "__main__":   # فحصٌ يدوي — بلا شبكة، بلا مفاتيح
    # الرموزُ من سطر الأوامر لا من الشيفرة (لا رمزَ مكتوبٌ صلباً هنا كذلك):
    #   python3 silk_hs_attributes.py 040110 040120 040140 040150 -- 3.5 %
    import sys
    logging.basicConfig(level=logging.INFO)
    argv = sys.argv[1:]
    codes = [a for a in argv if a.isdigit()]
    tail = argv[argv.index("--") + 1:] if "--" in argv else []
    cands = [{"hs6": c} for c in codes]
    d = discriminator(cands)
    print("discriminator:", d and (d["dimension"], d["unit"]))
    for b in (d or {}).get("bands", []):
        print("  ", b["hs6"], range_ar(b, d["label_ar"]))
    if d and tail:
        val, unit = float(tail[0]), (tail[1] if len(tail) > 1 else "")
        print(f"{val}{unit} ->", select_by_value(d, val, unit))
        print(resolve_by_attribute(
            "—", cands, allow_web=False,
            label_attributes=[{"name": d["label_ar"], "value": val,
                               "unit": unit}]))
