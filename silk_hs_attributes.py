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

import logging
import os
import re

log = logging.getLogger("silk.hs_attributes")


def enabled() -> bool:
    """الصمّام — مفعّلٌ افتراضياً، يُطفَأ صراحةً بـ`SILK_HS_ATTRIBUTE_RESOLVE=0`.

    مفعّلٌ افتراضياً لأنّ المُطفَأ هو **الحالةُ المُبلَّغ عنها** (سؤالُ التاجر
    عن عتبةٍ جمركية)؛ والتفعيل لا يُخمِّن شيئاً: لا يحسم إلا برقمٍ مقيسٍ
    يقع في نطاقٍ وحيد، وإلا يعود للحوار كما كان بالضبط."""
    raw = os.environ.get("SILK_HS_ATTRIBUTE_RESOLVE", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


# ══════════════ ١) معجمُ أبعادِ القياس (لغويّ، لا أسماءَ منتجات) ═════════════
#
# المفتاحُ بُعدُ قياسٍ يظهر في النصّ الرسميّ («fat content»، «Brix value»)،
# والقيمةُ مرادفاتُه ثنائيةُ اللغة + تسميتُه العربية (تُستعمَل في استعلام
# الويب وفي شرح الحوار للتاجر). بُعدٌ غيرُ مذكورٍ هنا يبقى صالحاً تماماً:
# مفتاحُه يصير مرادفَه الوحيد وتسميتَه — فالمعجم يُثري ولا يُقيّد.
_DIMENSION_LEXICON: dict[str, dict] = {
    "fat":      {"ar": ("دهن", "دهون", "دسم"), "label_ar": "نسبة الدهن"},
    "alcohol":  {"ar": ("كحول", "كحولي"), "label_ar": "النسبة الكحولية"},
    "sugar":    {"ar": ("سكر", "سكريات"), "label_ar": "نسبة السكر"},
    "cocoa":    {"ar": ("كاكاو",), "label_ar": "نسبة الكاكاو"},
    "protein":  {"ar": ("بروتين",), "label_ar": "نسبة البروتين"},
    "moisture": {"ar": ("رطوبه", "رطوبة"), "label_ar": "نسبة الرطوبة"},
    "starch":   {"ar": ("نشا", "نشاء"), "label_ar": "نسبة النشا"},
    "ash":      {"ar": ("رماد",), "label_ar": "نسبة الرماد"},
    "volume":   {"ar": ("حجم", "سعه", "سعة", "لتر", "مل"),
                 "label_ar": "الحجم/السعة"},
    "weight":   {"ar": ("وزن", "كجم", "كغم", "جرام", "غرام"),
                 "label_ar": "الوزن"},
    "length":   {"ar": ("طول", "عرض", "مقاس"), "label_ar": "المقاس"},
    "brix":     {"ar": ("بريكس",), "label_ar": "درجة بريكس"},
}

# كلماتٌ لا تصلح بُعداً («of a content», «net weight content») — تُتخطّى
# بحثاً عن الكلمة الدالّة قبلها.
_DIM_STOPWORDS = frozenset({"a", "an", "the", "of", "net", "immediate", "any",
                            "no", "not", "its", "their", "such"})


# ══════════════ ٢) الوحدات وعائلاتُها (تحويلٌ إلى وحدةِ أساسٍ واحدة) ═════════
#
# كلُّ عائلةٍ لها وحدةُ أساسٍ واحدة تُقارَن عليها النطاقاتُ والقيَم، فلا
# تُقارَن كيلوغرامات بغرامات صامتةً. وحدةٌ غير معروفة => لا تحويل => لا حسم.
_UNIT_FAMILY: dict[str, tuple[str, float]] = {
    "%": ("percent", 1.0), "percent": ("percent", 1.0),
    "per cent": ("percent", 1.0), "pct": ("percent", 1.0),
    "mg": ("weight", 0.001), "g": ("weight", 1.0), "gr": ("weight", 1.0),
    "gram": ("weight", 1.0), "grams": ("weight", 1.0),
    "kg": ("weight", 1000.0), "kgs": ("weight", 1000.0),
    "kilogram": ("weight", 1000.0), "kilograms": ("weight", 1000.0),
    "ml": ("volume", 0.001), "cl": ("volume", 0.01),
    "l": ("volume", 1.0), "litre": ("volume", 1.0), "litres": ("volume", 1.0),
    "liter": ("volume", 1.0), "liters": ("volume", 1.0),
    "mm": ("length", 1.0), "cm": ("length", 10.0), "m": ("length", 1000.0),
    "": ("scalar", 1.0),
}
# وحدةُ العرض لكل عائلة (وحدةُ الأساس نفسها).
_FAMILY_UNIT = {"percent": "%", "weight": "g", "volume": "L",
                "length": "mm", "scalar": ""}
# عائلةُ الوحدة تحسم البُعدَ مباشرةً حين تكون مادّية؛ النِّسَب والمجرَّدات
# يحسمها النصُّ («fat content» / «Brix value»).
_FAMILY_DIMENSION = {"weight": "weight", "volume": "volume",
                     "length": "length"}


def _unit_family(unit: object) -> tuple[str, float] | None:
    """(عائلة، معامل التحويل لوحدة الأساس) — أو `None` لوحدةٍ غير معروفة."""
    u = str(unit or "").strip().lower().rstrip(".")
    u = re.sub(r"\s+", " ", u)
    return _UNIT_FAMILY.get(u)


# ══════════════ ٣) قراءةُ النطاق الرقميّ من الوصف الرسميّ ════════════════════

_NUM = r"(\d+(?:\.\d+)?)"
# لاحقةُ الوحدة اختيارية. الحدُّ `(?![a-z])` لا `\b` عمداً: `%` محرفٌ غيرُ
# كلميّ فلا حدَّ كلميّ بعده، وكان `\b` يُفشِل التقاطه فتُقرأ «1%» مجرَّدةً
# (عائلةُ `scalar`) فتفشل مقارنتُها بنسبةٍ من البطاقة/الويب.
_UNIT = (r"\s*(%|per\s*cent|percent|kgs?|kilograms?|grams?|gr|g|mg"
         r"|litres?|liters?|ml|cl|l|mm|cm)?(?![a-z])")
# حدٌّ أعلى: «not exceeding N» / «no side exceeding N» / «not more than N» /
# «less than N» / «up to N» + مقابلاتها العربية.
_HI = (r"(?:not|no)\s+(?:\w+\s+)?exceeding|not\s+more\s+than|no\s+more\s+than"
       r"|less\s+than|up\s+to|لا\s+يتجاوز|أقل\s+من|حتى")
# حدٌّ أدنى: «exceeding N» / «more than N» / «over N» + مقابلاتها.
_LO = (r"exceeding|more\s+than|greater\s+than|over|above"
       r"|يتجاوز|أكثر\s+من|تزيد\s+عن")
# صيغةُ اللاحقة: «2 litres or less» / «2kg or less».
_HI_SUFFIX = r"or\s+less|or\s+under|أو\s+أقل|فأقل"

_EVENT_RE = re.compile(
    rf"(?P<hi>{_HI})\s*{_NUM}{_UNIT}"
    rf"|(?P<lo>{_LO})\s*{_NUM}{_UNIT}"
    rf"|{_NUM}{_UNIT}\s*(?P<hisuf>{_HI_SUFFIX})",
    re.I)

_DIM_WORD_RE = re.compile(r"(\w+)\s+(?:content|value|strength|degree)", re.I)


def _events(text: str) -> list[dict]:
    """أحداثُ الحدود في النصّ — [{kind:'lo'|'hi', value, unit, pos}]."""
    out: list[dict] = []
    for m in _EVENT_RE.finditer(text or ""):
        if m.group("hi"):
            kind, num, unit = "hi", m.group(2), m.group(3)
        elif m.group("lo"):
            kind, num, unit = "lo", m.group(5), m.group(6)
        else:
            kind, num, unit = "hi", m.group(7), m.group(8)
        try:
            value = float(num)
        except (TypeError, ValueError):   # pragma: no cover — النمط رقميّ
            continue
        fam = _unit_family(unit or "")
        if fam is None:
            continue
        out.append({"kind": kind, "value": value, "unit": (unit or "").lower(),
                    "family": fam[0], "factor": fam[1], "pos": m.start()})
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


def band_of(hs6: object, description: str = "") -> dict | None:
    """نطاقُ العتبة الرقمية لرمز HS6 — أو `None` حين لا عتبةَ في وصفه.

    الوصفُ الرسميّ أولاً (المصدرُ الذي تعيش فيه العتبة فعلاً)، ثم الوصفُ
    المُمرَّر إن غاب الرمزُ عن المرجع. يعيد dict:
    `{hs6, lo, hi, unit, family, dimension, desc}` بوحدةِ أساسِ العائلة —
    `lo`/`hi` قد يكون أيٌّ منهما `None` (نطاقٌ مفتوحُ الطرف)."""
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
    if lo is not None and hi is not None and lo >= hi:
        return None                      # نطاقٌ متناقض — فجوة لا اختلاق
    dimension = _FAMILY_DIMENSION.get(family) or _dimension_from_text(
        desc, chosen[0]["pos"])
    if not dimension:
        return None                      # بُعدٌ مجهول => لا يصلح مُميِّزاً
    return {"hs6": code, "lo": lo, "hi": hi, "family": family,
            "unit": _FAMILY_UNIT.get(family, ""), "dimension": dimension,
            "desc": desc}


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

    يُشترَط: **مرشّحان فأكثر** لهما نطاقٌ فعليّ، **بُعدٌ ووحدةٌ واحدة** لهم
    جميعاً، ونطاقاتٌ **غيرُ متداخلة** (تقسيمُ ترويسةٍ سليم). أيُّ إخلالٍ =>
    `None` => الحوارُ كما كان (لا حسمَ على أساسٍ مشوَّش)."""
    bands = [b for b in (band_of(code, desc)
                         for code, desc in _candidate_codes(candidates))
             if b is not None]
    if len(bands) < 2:
        return None
    dims = {b["dimension"] for b in bands}
    fams = {b["family"] for b in bands}
    if len(dims) != 1 or len(fams) != 1:
        return None
    ordered = sorted(bands, key=lambda b: (b["lo"] if b["lo"] is not None
                                           else float("-inf")))
    for prev, nxt in zip(ordered, ordered[1:]):
        prev_hi = prev["hi"] if prev["hi"] is not None else float("inf")
        nxt_lo = nxt["lo"] if nxt["lo"] is not None else float("-inf")
        if nxt_lo < prev_hi:
            return None                  # نطاقاتٌ متداخلة — لا تقسيمَ حاسم
    dimension = ordered[0]["dimension"]
    lex = _DIMENSION_LEXICON.get(dimension, {})
    return {"dimension": dimension, "family": ordered[0]["family"],
            "unit": ordered[0]["unit"],
            "label_ar": lex.get("label_ar") or dimension,
            "synonyms": tuple(lex.get("ar", ())) + (dimension,),
            "bands": ordered}


def _contains(band: dict, base_value: float) -> bool:
    """هل تقع القيمة (بوحدة الأساس) داخل النطاق؟ — الحدُّ الأعلى شامل،
    والأدنى حصريّ (عقدُ نصّ HS: «exceeding N but not exceeding M»)."""
    if band["lo"] is not None and base_value <= band["lo"]:
        return False
    if band["hi"] is not None and base_value > band["hi"]:
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
    hits = [b["hs6"] for b in disc["bands"] if _contains(b, base)]
    return hits[0] if len(hits) == 1 else None


# ══════════════ ٥) وصفُ النطاق بلغةٍ مفهومة (للحوار الاحتياطي) ═══════════════

def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def range_ar(band: dict, label_ar: str = "") -> str:
    """«حتى 1%» / «أكثر من 6% وحتى 10%» — لا رمزَ ولا عتبةً جمركية خام."""
    unit = band.get("unit") or ""
    lo, hi = band.get("lo"), band.get("hi")
    if lo is None and hi is not None:
        txt = f"حتى {_fmt(hi)}{unit}"
    elif lo is not None and hi is None:
        txt = f"أكثر من {_fmt(lo)}{unit}"
    elif lo is not None and hi is not None:
        txt = f"أكثر من {_fmt(lo)}{unit} وحتى {_fmt(hi)}{unit}"
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
        "searched": [], "missing_ar": "",
        "bands_ar": [{"hs6": b["hs6"],
                      "range_ar": range_ar(b, (disc or {}).get("label_ar") or "")}
                     for b in (disc or {}).get("bands", [])],
    }
    if disc:
        base["unit"] = disc.get("unit")
    base.update(over)
    return base


def resolve_by_attribute(product: str, candidates: list,
                         label_attributes: object = None,
                         allow_web: bool = True,
                         gl: str | None = None) -> dict:
    """احسِم الرمزَ بالسمة الرقمية قبل عرض الحوار — التقريرُ الموحّد دوماً.

    الترتيب: صورةُ العبوة (بلا أيّ نداءٍ إضافي) ← استعلامُ ويبٍ واحد ← فجوة.
    `hs6` غيرُ `None` يعني حسماً بدليلٍ موسوم؛ `None` يعني **اعرِض الحوار**
    محمَّلاً بـ`searched`/`missing_ar`/`bands_ar` (ما جُرِّب، ما نقص، وحدودُ
    كلّ مرشّحٍ بلغةٍ مفهومة) — لا رمزَ مُخمَّن أبداً."""
    if not enabled():
        return _report(None, missing_ar="حسمُ السمة الرقمية مُعطَّل "
                                        "(SILK_HS_ATTRIBUTE_RESOLVE=0)")
    disc = discriminator(candidates)
    if disc is None:
        return _report(None, missing_ar="لا تتمايز المرشّحات بعتبةٍ رقمية "
                                        "واحدة — لا حسمَ آليّ ممكن")
    label = disc.get("label_ar") or disc["dimension"]
    searched: list[str] = []

    # (١) صورةُ العبوة — سماتٌ مستخلَصةٌ سلفاً، صفر نداءٍ إضافي.
    value, unit, attr_name = value_from_label(label_attributes, disc)
    if value is not None:
        hs6 = select_by_value(disc, value, unit)
        searched.append(f"صورة العبوة ({attr_name}: {_fmt(value)}{unit})")
        if hs6:
            return _report(disc, hs6=hs6, resolved_from="image", value=value,
                           unit=unit, confidence=1.0, searched=searched,
                           note_ar=f"{_IMAGE_NOTE} — {label} {_fmt(value)}{unit}")
        searched.append(f"القيمة {_fmt(value)}{unit} لا تقع في نطاقٍ وحيد")
    else:
        searched.append("صورة العبوة (لا سمةٌ رقمية مقروءة)"
                        if label_attributes else "صورة العبوة (لم تُرفَق)")

    # (٢) الويب — استعلامٌ واحد برابطٍ قابلٍ للاستشهاد.
    if allow_web:
        hit = probe_web(product, disc, gl=gl)
        if hit:
            hs6 = select_by_value(disc, hit["value"], hit["unit"])
            searched.append(
                f"بحث ويب ({label}: {_fmt(hit['value'])}{hit['unit']})")
            if hs6:
                return _report(
                    disc, hs6=hs6, resolved_from="web", value=hit["value"],
                    unit=hit["unit"], source_url=hit["source_url"] or None,
                    confidence=_WEB_CONFIDENCE, searched=searched,
                    note_ar=(f"{_WEB_NOTE}: {hit['source_url']} — "
                             f"{label} {_fmt(hit['value'])}{hit['unit']}"))
            searched.append(
                f"القيمة {_fmt(hit['value'])}{hit['unit']} لا تقع في نطاقٍ وحيد")
        else:
            searched.append(f"بحث ويب عن {label} (بلا رقمٍ موثَّق)")
    else:
        searched.append("بحث الويب غير متاح لهذا الطلب")

    return _report(disc, searched=searched,
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
