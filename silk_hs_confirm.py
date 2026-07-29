"""بوابة تأكيد رمز HS المسبقة — HS pre-flight confirmation gate (Wave 1.2).

> **العائلة.** `unresolved-hs-silent-spend` موسَّعةً: رمز HS قد يُحسَم بثقة
> عالية لكنه **خاطئ دلالياً** — صفة المنتج المميّزة تضيع لصالح تطابق كلمة
> ثانوية عارية. البلاغ الأصلي (تدقيق المالك، تقرير زبدة الفول السوداني/اليمن):
> «زبدة الفول السوداني» حُسِمت إلى 040510 (زبدة/Butter) لأن «زبدة» طابقت،
> بينما الصفة المميّزة «فول سوداني» غائبة عن وصف الرمز — العائلة الصحيحة
> 200811 (فول سوداني محضّر) / 210690 (محضرات غذائية).
>
> **القاعدة الدائمة (عقد التأكيد المسبق).** قبل أيّ إنفاق، يُقاس تداخل صفات
> المنتج المميّزة مع وصف الرمز المُصنَّف. تداخل ضعيف => الرمز **غير مؤكَّد**
> (`confirmed=False`) => بوابة صلبة (خلف `SILK_HS_CONFIRM_GATE`) تُوقِف
> التشغيل وتسأل المستخدم، وطبقة العرض/الكاتب تعيد تأطير كل رقم مشتقّ من الرمز
> «مؤشر سياقي لا مقياس فعلي». صفر اسم منتج/رمز مكتوب صلباً — القاعدة مبنيّة
> على البيانات (`data/hs_codes.csv`) والعتبة من env (عائلة `hardcoded-product-rule`،
> الدرس ٢٤).

المكتبات: stdlib فقط — يستورده api (البوابة) وطبقة العرض (التأطير) بلا شبكة.
"""
from __future__ import annotations

import os
import re

# عتبة التداخل الأدنى لاعتبار الرمز مؤكَّداً — config-driven (لا رقم صلب في
# المنطق). تداخل صفات المنتج المغطّاة بوصف الرمز دونها => غير مؤكَّد.
_DEFAULT_MIN_OVERLAP = 0.5


def _min_overlap() -> float:
    """عتبة التداخل من env (`SILK_HS_CONFIRM_MIN_OVERLAP`) أو الافتراضي."""
    try:
        v = float(os.environ.get("SILK_HS_CONFIRM_MIN_OVERLAP", ""))
        return v if 0.0 < v <= 1.0 else _DEFAULT_MIN_OVERLAP
    except (TypeError, ValueError):
        return _DEFAULT_MIN_OVERLAP


# ── بوّابة ثقة التصنيف (بلاغ «حليب نادك») ────────────────────────────────────
# الحادثة: الرمز حُسِم وثقتُه **فارغة** («ثقة التصنيف —» في جدول التقرير)،
# ومضى الخطُّ إلى الترتيب والبعثات على رمزٍ داخل الترويسة الصحيحة لكنه البند
# الخاطئ (040110 «دسم ≤1%» — حليبٌ منزوع الدسم — لمنتجٍ كامل الدسم مرصودٍ في
# السوق). الفارق بين 040110/040120/040150 **نسبةُ دسمٍ رقمية** لا كلمةٌ في
# اسم المنتج، فبوّابةُ التطابق الدلالي (`confirm_hs`) لا تراه أصلاً: كلاهما
# «حليب». الحارس الصحيح هنا هو **الثقة نفسها**: ثقةٌ غائبة أو دون العتبة =
# لا ترتيبَ ولا إنفاق، بل سؤالُ المستخدم بين المرشّحين.
#
# رمزٌ خاطئ يُعيد تأطير **كل** رقمٍ لاحق (حجم السوق/الحصص/التركّز/الاتجاه)،
# فالخطأ هنا ليس رقماً واحداً بل التقرير كلّه — لذا فشل-آمن: مفعّلة افتراضياً.
_DEFAULT_MIN_CONFIDENCE = 0.8


def min_confidence() -> float:
    """عتبةُ ثقة التصنيف من env (`SILK_HS_MIN_CONFIDENCE`) أو الافتراضي.

    الافتراض 0.8 مُعايَرٌ على سلوك المُحلِّل الحتمي الفعلي (`silk_hs_resolver`):
    تطابقٌ تامّ = 1.0، إصابةُ كلمةٍ مفتاحية = 0.85، وما دونهما درجةُ difflib
    (وهو أصلاً يقصّ ما دون 0.7 إلى `None`). فالعتبة تُمرِّر المطابقةَ التامّة
    والمفتاحية، وتحجب النافذةَ الضبابية 0.7–0.79 — حيث يعيش تطابقُ الحروف
    العارضُ («نفط خام» → 520100 قطن بدرجة 0.71)."""
    try:
        v = float(os.environ.get("SILK_HS_MIN_CONFIDENCE", ""))
        return v if 0.0 < v <= 1.0 else _DEFAULT_MIN_CONFIDENCE
    except (TypeError, ValueError):
        return _DEFAULT_MIN_CONFIDENCE


def confidence_gate_enabled() -> bool:
    """هل بوّابة الثقة مفعّلة؟ (`SILK_HS_CONFIDENCE_GATE`) — فشل-آمن: نعم.

    صمّامٌ **مستقلّ** عن `SILK_HS_CONFIRM_GATE` (بوّابة التطابق الدلالي):
    البوّابتان تقيسان شيئين مختلفين، فإطفاءُ إحداهما يجب ألّا يُطفئ الأخرى."""
    raw = os.environ.get("SILK_HS_CONFIDENCE_GATE", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def gate_enabled() -> bool:
    """هل بوابة التأكيد الصلبة مفعّلة؟ (`SILK_HS_CONFIRM_GATE`).

    **فشل-آمن: مفعّلة افتراضياً** (البلاغ الحيّ 2026-07-21، عائلة
    `unresolved-hs-silent-spend`): تشغيلةُ `/research` مدفوعة على «زبدة الفول
    السوداني» مضت على 040510 (زبدة ألبان) لأن البوّابة كانت خلف صمّامٍ مُطفأ
    في الإنتاج، فأُنفِقت دولارات على فئةٍ مجاورةٍ خاطئةٍ دلالياً ولم يُحذَّر
    إلا نثراً. القانون (LAW): لا إنفاق صامت على رمزٍ خاطئ؛ المالك آخِر مؤكِّد.
    لذا البوّابة تعمل ما لم تُطفَأ صراحةً (`SILK_HS_CONFIRM_GATE=0/false/off`).
    الحساب الاستشاري (`confirm_hs`) يعمل دائماً بمعزلٍ عن هذا الصمّام."""
    raw = os.environ.get("SILK_HS_CONFIRM_GATE", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


# كلمات ربط/أدوات عامة تُستبعَد من صفات المنتج المميّزة (عربي + إنجليزي).
_STOPWORDS = frozenset({
    "و", "من", "في", "على", "عن", "مع", "او", "أو", "ذو", "ذات", "هذا",
    "the", "of", "and", "or", "with", "for", "in", "a", "an", "to",
    "fresh", "dried", "other", "n.e.c", "nec", "prepared", "preserved",
})

# التشكيل العربي — يُزال قبل المطابقة.
_DIACRITICS = re.compile(r"[ؗ-ًؚ-ْٰ]")


def _norm(s: str) -> str:
    """طبّع نصاً للمطابقة — إزالة تشكيل/تنميط ألف وياء وتصغير لاتيني."""
    s = (s or "").lower().strip()
    s = _DIACRITICS.sub("", s)
    s = (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
          .replace("ى", "ي").replace("ة", "ه"))
    return s


def _tokens(text: str) -> list[str]:
    """قسّم نصاً إلى صفات ذات معنى — إسقاط «ال» التعريف وأدوات الربط والقِصار."""
    raw = re.split(r"[\s,/\-()·،؛;]+", _norm(text))
    out: list[str] = []
    for t in raw:
        t = t.strip()
        if t.startswith("ال") and len(t) > 4:  # إسقاط أل التعريف
            t = t[2:]
        if len(t) < 2 or t in _STOPWORDS:
            continue
        out.append(t)
    return out


# حدُّ الطول الأدنى لاحتواء الجذر — دون التطابق التامّ (term==c، مسموحٌ دوماً
# بمعزلٍ عن الطول)، احتواءٌ لجذرٍ أقصر من هذا يُرفَض: جذورٌ قصيرة (حرفان)
# تتصادف داخل كلماتٍ لا علاقة لها لفظياً («بن» — قهوة — داخل «بنكهة»/«جبن»؛
# منتجٌ غذائيٌّ لا علاقة له بالبنّ يُصنَّف خطأً لمجرّد هذا التصادف الحرفي).
_MIN_CONTAINMENT_LEN = int(
    os.environ.get("SILK_HS_CONFIRM_MIN_CONTAINMENT_LEN", "3") or "3")


def _covered(term: str, code_terms: list[str]) -> bool:
    """هل صفة المنتج مغطّاة بأي صفة من وصف الرمز؟ (احتواء باتجاهين، بشرط ألّا
    يكون الجذر المُحتوى أقصر من `_MIN_CONTAINMENT_LEN` — تطابقٌ حرفيٌّ تامّ
    (term==c) لا يخضع لهذا الشرط أبداً)."""
    for c in code_terms:
        if not c:
            continue
        if term == c:
            return True
        shorter = c if len(c) <= len(term) else term
        if len(shorter) < _MIN_CONTAINMENT_LEN:
            continue
        if term in c or c in term:
            return True
    return False


def _code_terms(row: dict) -> list[str]:
    """صفات وصف الرمز — الاسم العربي/الإنجليزي + الكلمات المفتاحية، مُنمَّطة."""
    parts: list[str] = []
    for field in ("name_ar", "name_en", "keywords"):
        parts.extend(_tokens(row.get(field, "")))
    return parts


def _code_desc(row: dict) -> str:
    """وصف مقروء للرمز للعرض — العربي إن وُجد وإلا الإنجليزي."""
    return (row.get("name_ar") or row.get("name_en") or "").strip()


def _find_row(hs_code: str, path: str = "data/hs_codes.csv") -> dict | None:
    """صفّ الرمز من البذرة — reuse resolver's cached CSV loader."""
    from silk_hs_resolver import load_hs_codes
    code = str(hs_code or "").strip()
    for r in load_hs_codes(path):
        if str(r.get("hs_code", "")).strip() == code:
            return r
    return None


# رسالة موحّدة للتأطير — تُعرَض مرة واحدة في المنهجية (الدرس ٤.١: لا تكرار).
CONTEXTUAL_TAG = "بيانات فئة مجاورة — مؤشر سياقي لا مقياس فعلي"


# ── وعيُ النفي (بلاغ «full fat milk» ضد 040110) ─────────────────────────────
# الحادثة: `confirm_hs("full fat milk", "040110")` أعاد **مؤكَّداً** بتداخل
# 0.67 — لأنّ وصفَ الرمز «…fat content, by weight, not exceeding 1%» يحوي
# «fat» و«milk»، وهو يعني **النقيض** (منزوعُ الدسم). تداخلُ النصّ يَعُدّ
# الكلماتِ ولا يقرأ النفي: «لا يتجاوز ١٪» و«كامل الدسم» يتشاركان الكلمات
# نفسَها ويتناقضان تماماً.
#
# القاعدة: حين يكون الرمزُ **محدوداً بنفيٍ أو بعتبةٍ رقمية**، لا يجوز أن
# يحسم التداخلُ وحده. يُفرَض `confirmed=False` في حالتين:
#   (١) صفةُ المنتج تقع **داخل** المقطع المنفيّ نفسه («other than X» ومنتجُنا
#       هو X) — تأكيدٌ صريحٌ لِما يستثنيه الرمز.
#   (٢) اسمُ المنتج يحمل **صفةَ درجةٍ/كمّية** (كامل، عالي، منزوع، full، whole،
#       high…) والوصفُ محدودٌ بعتبة — فالتمييزُ رقميٌّ لا لفظيّ، والتداخلُ
#       أعمى عنه بنيوياً. فشلٌ آمن: يُسأل المستخدم بدل أن يُصادَق الخطأ.
# ليست هذه قائمةَ منتجاتٍ مكتوبةً صلباً (عائلة `hardcoded-product-rule`) —
# بل أدواتُ نفيٍ وصفاتُ درجةٍ لغوية، لا اسمَ منتجٍ ولا رمزَ فيها.
_NEGATION_SPAN_RE = re.compile(
    r"\b(?:not\s+exceeding|not\s+containing|not\s+more\s+than|less\s+than"
    r"|other\s+than|excluding|except(?:ing)?|free\s+of|without|unsweetened"
    r"|n\.?e\.?[cs]\.?|not\s+elsewhere\s+specified"
    r"|بخلاف|عدا|باستثناء|لا\s+يتجاوز|دون|خالٍ\s+من|بدون)\b",
    re.I)
# عتبةٌ رقمية (٪ أو نسبةُ وزن/حجم) — التمييزُ الحقيقيّ بين بنود الترويسة.
_THRESHOLD_RE = re.compile(
    r"\d+\s*(?:%|per\s*cent|في\s*المئة|بالمئة)|by\s+weight|by\s+volume"
    r"|بالوزن|بالحجم", re.I)
# صفاتُ الدرجة/الكمّية التي يستحيل على التداخل أن يحكم عليها أمام عتبة.
_DEGREE_TERMS = frozenset({
    "full", "whole", "high", "rich", "heavy", "concentrated", "pure",
    "low", "light", "skimmed", "skim", "lean", "reduced", "semi",
    "sweetened", "unsweetened", "fortified", "extra", "virgin", "crude",
    "كامل", "كامله", "كاملة", "عالي", "عاليه", "عالية", "غني", "غنيه",
    "منزوع", "منزوعه", "منزوعة", "مركز", "مركزه", "مركزة", "خام", "نقي",
    "قليل", "خفيف", "محلى", "مدعم", "بكر", "دسم", "الدسم",
})


def describes_by_exclusion(code_desc: str) -> bool:
    """هل يُعرَّف الرمزُ بنفيٍ أو بعتبةٍ رقمية؟ — negation/threshold-defined."""
    d = code_desc or ""
    return bool(_NEGATION_SPAN_RE.search(d) or _THRESHOLD_RE.search(d))


def _negated_terms(code_desc: str) -> set[str]:
    """صفاتُ المقطع المنفيّ — ما بعد أداةِ النفي حتى أقرب فاصل."""
    out: set[str] = set()
    for m in _NEGATION_SPAN_RE.finditer(code_desc or ""):
        tail = (code_desc or "")[m.end():]
        span = re.split(r"[,;()،؛]|\band\b|\bor\b", tail, maxsplit=1)[0]
        out.update(_tokens(span))
    return out


# فاصلُ الحقول عند ضمّ صفّ CSV لفحص النفي — **ليس مسافة**. المقطعُ المنفيّ
# يمتدّ حتى أقرب فاصل، فضمُّ الحقول بمسافةٍ كان يجعل النفيَ في `name_en`
# يبتلع `keywords` التالية: صفُّ 040221 «Milk powder unsweetened» بكلماتٍ
# مفتاحية تحوي «حليب» أنتج تعارضاً كاذباً على «حليب» نفسه (اكتُشف بفشل
# قفلِ بوّابة الالتباس قبل الدمج). الفاصلةُ المنقوطة تُنهي المقطع.
_FIELD_SEP = " ؛ "


def _negation_conflict(product_terms: list[str], code_desc: str
                       ) -> str | None:
    """سببُ رفضٍ قاطعٍ رغم التداخل — أو `None` حين لا تعارض.

    يُستدعى بعد حساب التداخل ويتقدّم عليه: التداخلُ يَعُدّ الكلمات، وهذا يقرأ
    ما تعنيه. Returns a human reason (Arabic) or None."""
    desc = code_desc or ""
    if not desc:
        return None
    # (١) صفةُ المنتج داخل المقطع المنفيّ = تأكيدٌ لِما يستثنيه الرمز.
    negated = _negated_terms(desc)
    if negated:
        clash = [t for t in product_terms if _covered(t, sorted(negated))]
        if clash:
            return (f"وصفُ الرمز يستثني صراحةً ما يؤكّده اسمُ المنتج "
                    f"({'، '.join(clash)})")
    # (٢) صفةُ درجةٍ في اسم المنتج أمام رمزٍ محدودٍ بعتبةٍ رقمية.
    if _THRESHOLD_RE.search(desc) or _NEGATION_SPAN_RE.search(desc):
        degree = [t for t in product_terms if t in _DEGREE_TERMS]
        if degree:
            return (f"الرمزُ محدودٌ بعتبةٍ/نفيٍ رقميّ واسمُ المنتج يحمل صفةَ "
                    f"درجة ({'، '.join(degree)}) — التمييزُ رقميٌّ لا لفظيّ، "
                    "فلا يُحسَم بتطابق الكلمات")
    return None


def _overlap_stats(p_terms: list[str], desc_terms: list[str]
                   ) -> tuple[list[str], list[str], float]:
    """نواةُ حساب التداخل المشتركة — (المُغطّى، الناقص، نسبة التداخل).

    مُستخرَجةٌ (الموجة ٣ — التصنيف العام) كي يستعملها `confirm_hs` (وصفٌ من
    صفّ CSV) و`confirm_against_description` (وصفٌ حرٌّ — من نموذجٍ أو أيّ
    مصدر) بمنطقٍ واحدٍ لا نسختين متوازيتين قابلتين للانحراف."""
    shared = [t for t in p_terms if _covered(t, desc_terms)]
    missing = [t for t in p_terms if not _covered(t, desc_terms)]
    overlap = round(len(shared) / len(p_terms), 2) if p_terms else 0.0
    return shared, missing, overlap


def confirm_against_description(product_name: str, hs_code: str,
                                code_desc: str,
                                min_overlap: float | None = None) -> dict:
    """قِس تطابق صفات المنتج مع **وصفٍ حرّ** لرمزٍ ما — نفس شكل/عقد
    `confirm_hs` بالضبط، لكن بلا اشتراط وجود الرمز في بذرة CSV.

    الاستعمال (الموجة ٣ — التصنيف العام `silk_hs_classifier.classify_general`):
    مرشّحٌ اقترحه نموذجٌ برمزٍ خارج بذرتنا الجزئية (~٥٦٠٠ من ~٦٩٤٠ رمزاً
    دولياً) يُصادَق عليه بوصفه **الرسمي الذي قدّمه النموذج نفسه** — عقد
    عدم الاختلاق لا يزال ساريًا: لا نثق برمزٍ لمجرّد ادّعائه، بل نقيس
    تداخل الصفات المميّزة مع الوصف المُقدَّم فعليًا، تمامًا كما نفعل مع
    صفّ CSV. `code_desc` فارغ => `confirmed=None` (لا وصف = لا حكم)."""
    p_terms = _tokens(product_name)
    desc = (code_desc or "").strip()
    if not desc:
        return {"confirmed": None, "hs_code": str(hs_code or ""),
                "code_desc": "", "product_terms": p_terms,
                "shared_terms": [], "missing_terms": [], "overlap": None,
                "reason": "لا وصفَ متاحاً لهذا الرمز — تعذّر تأكيده"}
    if not p_terms:
        return {"confirmed": None, "hs_code": str(hs_code or ""),
                "code_desc": desc, "product_terms": [],
                "shared_terms": [], "missing_terms": [], "overlap": None,
                "reason": "اسم المنتج بلا صفات قابلة للمطابقة"}
    desc_terms = _tokens(desc)
    shared, missing, overlap = _overlap_stats(p_terms, desc_terms)
    threshold = min_overlap if min_overlap is not None else _min_overlap()
    confirmed = overlap >= threshold
    # وعيُ النفي يتقدّم على التداخل مهما بلغ — راجع `_negation_conflict`.
    _conflict = _negation_conflict(p_terms, desc)
    if _conflict:
        return {"confirmed": False, "hs_code": str(hs_code or ""),
                "code_desc": desc, "product_terms": p_terms,
                "shared_terms": shared, "missing_terms": missing,
                "overlap": overlap, "reason": _conflict,
                "negation_conflict": True}
    if confirmed:
        reason = "وصف الرمز يشمل صفات المنتج المميّزة"
    else:
        reason = ("وصف الرمز «" + desc + "» لا يشمل الصفة/الصفات "
                  "المميّزة: " + "، ".join(missing))
    return {"confirmed": confirmed, "hs_code": str(hs_code or ""),
            "code_desc": desc, "product_terms": p_terms,
            "shared_terms": shared, "missing_terms": missing,
            "overlap": overlap, "reason": reason}


def confirm_hs(product_name: str, hs_code: str,
               path: str = "data/hs_codes.csv") -> dict:
    """قِس تطابق صفات المنتج المميّزة مع وصف الرمز المُصنَّف — عقد التأكيد.

    يعيد dict: {confirmed, hs_code, code_desc, product_terms, shared_terms,
    missing_terms, overlap, reason}. `confirmed=False` حين يقلّ تداخل صفات
    المنتج المغطّاة عن العتبة (`SILK_HS_CONFIRM_MIN_OVERLAP`) — أي أن صفةً
    مميّزةً للمنتج غائبة عن وصف الرمز. لا اختلاق: رمزٌ غير موجود في البذرة =>
    `confirmed=None` (غير قابل للتأكيد) لا False كاذبة. مبنيّةٌ الآن فوق
    `confirm_against_description` (نواةٌ واحدة مشتركة) — راجع تلك للوصف الحرّ."""
    p_terms = _tokens(product_name)
    row = _find_row(hs_code, path)
    if row is None:
        return {"confirmed": None, "hs_code": str(hs_code or ""),
                "code_desc": "", "product_terms": p_terms,
                "shared_terms": [], "missing_terms": [], "overlap": None,
                "reason": "الرمز خارج بذرة التصنيف — تعذّر تأكيده"}
    if not p_terms:
        return {"confirmed": None, "hs_code": str(hs_code or ""),
                "code_desc": _code_desc(row), "product_terms": [],
                "shared_terms": [], "missing_terms": [], "overlap": None,
                "reason": "اسم المنتج بلا صفات قابلة للمطابقة"}
    # مطابقةٌ ضد **كل** صفات وصف الصفّ (name_ar + name_en + keywords) — نفس
    # نطاق `_code_terms` الأصلي، لا الاسم المعروض فقط (`_code_desc` وحده
    # قد يخلو من كلماتٍ مفتاحية تحسم التداخل، كما في العيّنة الأصلية التي
    # أسّست هذا الحارس أصلاً — راجع الملفّ العلوي).
    c_terms = _code_terms(row)
    shared, missing, overlap = _overlap_stats(p_terms, c_terms)
    confirmed = overlap >= _min_overlap()
    desc = _code_desc(row)
    # وعيُ النفي يُقاس على **كلّ** نصّ الصفّ (الاسمان + الكلمات المفتاحية)،
    # لا على الوصف المعروض وحده: عتبةُ «not exceeding 1%» قد تعيش في name_en
    # بينما `_code_desc` يعيد name_ar القصير.
    _full_desc = _FIELD_SEP.join(
        str(row.get(f) or "") for f in ("name_ar", "name_en", "keywords"))
    _conflict = _negation_conflict(p_terms, _full_desc)
    if _conflict:
        return {"confirmed": False, "hs_code": str(hs_code or ""),
                "code_desc": desc, "product_terms": p_terms,
                "shared_terms": shared, "missing_terms": missing,
                "overlap": overlap, "reason": _conflict,
                "negation_conflict": True}
    if confirmed:
        reason = "وصف الرمز يشمل صفات المنتج المميّزة"
    else:
        reason = ("وصف الرمز «" + desc + "» لا يشمل الصفة/الصفات "
                  "المميّزة: " + "، ".join(missing))
    return {"confirmed": confirmed, "hs_code": str(hs_code or ""),
            "code_desc": desc, "product_terms": p_terms,
            "shared_terms": shared, "missing_terms": missing,
            "overlap": overlap, "reason": reason}


def is_flagged(confirmation: object) -> bool:
    """هل الرمز مُعلَّم غير مؤكَّد؟ — True فقط عند `confirmed is False`.

    None (غير قابل للتأكيد) لا يُعامَل تعليماً — لا نُطأطئ ثقة على مجهول
    (عقد عدم الاختلاق: لا نُعلن عيباً بلا دليل)."""
    return isinstance(confirmation, dict) and confirmation.get("confirmed") is False


def flagged_conf_cap() -> float:
    """سقفُ ثقةِ الحكم عند تعليم رمز HS — `SILK_HS_FLAGGED_CONF_CAP` (0.5)."""
    try:
        return float(os.environ.get("SILK_HS_FLAGGED_CONF_CAP", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def cap_confidence_for_flagged_hs(verdict: "dict | None",
                                  confirmation: object) -> "dict | None":
    """PR A §A1 (بلاغ تحليل ٧): مصدرٌ واحدٌ لثقةٍ مسقوفة عند تعليم الرمز.

    كان السقف يُطبَّق في طبقة العرض وحدها (`silk_render._deep_research_view`)
    **بعد** أن جمّد الكاتب النسخة غير المسقوفة (0.73) في المتن — فظهر الغلاف
    «ثقة منخفضة (50%)» بينما §4 «الثقة متوسطة (73%)». الحلّ تمريرُ القيمة
    المسقوفة نفسها للكاتب: هذا المُسقِّف الواحد يُستدعى قبل الكاتب (المسار
    الرئيسي + إعادة التوليد) **و** في طبقة العرض (فيبقى idempotent). ينغّم إلى
    الأدنى فقط ولا يرفع ثقةً قط؛ رمزٌ مؤكَّد/غير قابلٍ للتأكيد (None) لا يُسقَّف
    (عقد عدم الاختلاق: لا نُطأطئ ثقةً على مجهول). يعيد القاموس كما هو إن لم
    ينطبق شيء (لا نسخة زائدة)."""
    if not isinstance(verdict, dict) or not is_flagged(confirmation):
        return verdict
    cap = flagged_conf_cap()
    out = verdict
    c = out.get("confidence")
    if isinstance(c, (int, float)) and not isinstance(c, bool) and c > cap:
        out = {**out, "confidence": cap}
    ai = out.get("ai")
    if isinstance(ai, dict) and isinstance(ai.get("confidence"), (int, float)) \
            and not isinstance(ai.get("confidence"), bool) \
            and ai["confidence"] > cap:
        out = {**out, "ai": {**ai, "confidence": cap}}
    return out


# ══════════════ التباسُ المحور (بلاغ المُشرِف — الالتباس اللفظيّ) ════════════
#
# `confirm_hs` تداخلٌ لفظيّ صرف: «حليب» تطابق 040110/040120/040140/0402 معاً
# لأنّ الفارقَ بينها **نسبةُ دهنٍ/حالةُ حفظٍ** لا كلمةٌ في اسم المنتج. حين
# يمرّ اسمٌ عامّ (بلا صفةِ دهنٍ/حفظ) فقد يُؤكَّد لفظياً (`confirmed=True`)
# فيمضي الخطّ صامتاً على البند الأدنى (040110) بدل أن **يُطالَب المشغّل
# بالاختيار**. هذا الكاشفُ يرصد ذلك الالتباس بيانياً (لا اسمَ منتج/رمز صلب):
# ترويسةٌ تنقسم بمحورٍ رقميّ (≥بندين شقيقين) واسمٌ لا يحمل صفةَ درجةٍ تُثبِّت
# البند => التباسٌ يستوجب حواراً، حتى لو أكّده التداخل اللفظيّ.
_DAIRY_PCT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")


def axis_disambiguation_needed(product: str, hs_code: str,
                               path: str = "data/hs_codes.csv") -> bool:
    """هل الرمزُ داخل ترويسةٍ متعدّدةِ البنود على محورٍ رقميّ، واسمُ المنتج لا
    يُثبِّت أيَّ بند؟ — التباسٌ يستوجب حواراً (نسبةُ الدهن/حالةُ الحفظ صفةٌ
    رقمية يجيب عنها المنتج، فلا يُفترَض البند الأدنى صامتاً).

    بياناتٌ لا حالةُ منتج: الأشقّاءُ من المرجع الرسميّ (`axis_siblings`)،
    وصفاتُ الدرجة من `_DEGREE_TERMS`. اسمٌ يحمل صفةَ درجةٍ («كامل الدسم») أو
    نسبةً مئوية صريحة يُثبِّت البند => لا التباس."""
    code = str(hs_code or "").strip()
    if not code:
        return False
    try:
        import silk_hs_dialog
        sibs = silk_hs_dialog.axis_siblings(code)
    except Exception:  # noqa: BLE001 — كاشفٌ إضافيّ لا يكسر البوّابة
        return False
    if len(sibs) < 2:
        return False               # لا محورَ رقميّ — لا التباسَ من هذا النوع
    toks = _tokens(product or "")
    if any(t in _DEGREE_TERMS for t in toks):
        return False               # اسمٌ يحمل صفةَ درجةٍ يُثبِّت البند
    if _DAIRY_PCT_RE.search(product or ""):
        return False               # نسبةٌ صريحة في الاسم تُثبِّت البند
    return True                    # اسمٌ عامّ + ترويسةٌ متعدّدة => التباس


def deterministic_candidates(product: str, top_n: int = 3,
                             path: str = "data/hs_codes.csv") -> list[dict]:
    """مرشّحو الرمز من البذرة الحتميّة — بلا نداء كلود وبلا شبكة وبلا تكلفة.

    نفس شكل مرشّحي `silk_hs_classifier.classify_general` (`hs6` / `description_ar`
    / `reason_ar` / `confidence`) كي تعرضهما الواجهةُ بمكوّنٍ واحد. يُستعمَل في
    ردّ بوّابة الثقة: المستخدمُ يحتاج **خياراتٍ** لا رفضاً عارياً."""
    from silk_hs_resolver import resolve_all
    import silk_hs_dialog
    hits = [dp for dp in resolve_all(product or "", top_n=top_n, path=path)
            if dp.value is not None]
    # نفسُ نقطة اختناق العرض: الوصفُ من المرجع الرسميّ لا من البذرة، وأشقّاءُ
    # المحور الرقميّ كاملون. الثقةُ الحتمية تُنقَل كما هي (حسابٌ لا نصّ).
    scores = {dp.value: dp.confidence for dp in hits}
    rows = silk_hs_dialog.build_candidates(
        product, [dp.value for dp in hits],
        reasons={dp.value: dp.note for dp in hits})
    for r in rows:
        r["confidence"] = scores.get(r["hs6"])
    return rows


# حارسٌ للتمييز بين «لم يمرّر المستدعي ثقةً إطلاقاً» (سلوكٌ قديم — لا بوّابة
# ثقة) و«مرّرها None» (تصنيفٌ بلا ثقة — يُحجَب). لا يجوز خلطهما.
_UNSET = object()


def confidence_block(product: str, hs_code: str | None,
                     hs_confidence: object,
                     path: str = "data/hs_codes.csv") -> dict | None:
    """احجب رمزاً ثقتُه غائبةٌ أو دون العتبة — تفاصيل 422 أو `None` للمرور.

    `None`/غير رقمية => حجب (هذه حالةُ «ثقة التصنيف —» حرفياً: تصنيفٌ بلا
    ثقةٍ معلومة ليس تصنيفاً محسوماً). الردّ يحمل **مرشّحين حتميّين** ليختار
    المستخدم، لا رفضاً عارياً — نفس عقد `preflight_block`."""
    if not hs_code or not confidence_gate_enabled():
        return None
    threshold = min_confidence()
    try:
        conf = None if hs_confidence is None else float(hs_confidence)
    except (TypeError, ValueError):
        conf = None
    if conf is not None and conf >= threshold:
        return None
    shown = "غير معلومة" if conf is None else f"{conf:.2f}"
    return {
        "error": "hs_confidence_too_low",
        "message": (f"ثقةُ تصنيف «{product}» إلى رمز HS {hs_code} {shown} "
                    f"(الحدّ الأدنى {threshold:.2f}) — لن يبدأ التحليل على "
                    "رمزٍ غير محسوم: رمزٌ خاطئ يُعيد تأطير كل رقمٍ لاحق. "
                    "اختر من المرشّحين أدناه أو أدخل رمزاً يدوياً."),
        "hs_code": hs_code,
        "hs_confidence": conf,
        "min_confidence": threshold,
        "candidates": deterministic_candidates(product, path=path),
        "candidates_source": "deterministic",
    }


def revalidate(product: str, hs_code: str | None,
               hs_confidence: object = None,
               path: str = "data/hs_codes.csv") -> dict | None:
    """أعِد التحقّق من رمزٍ **مخزَّن** يُعاد تشغيلُه — وسمٌ لا حجب.

    `resume` وإعادةُ توليد التقرير يُعيدان استعمالَ رمزٍ حُسِم في الماضي: حجبُهما
    يُفقِد المالكَ عملاً مدفوعاً سبق أن اكتمل (والبعثات المخزَّنة تُعاد بلا نداء
    جديد) — لكنّ تمريرَهما بصمتٍ يُعيد إنتاج تقريرٍ على رمزٍ ربّما صار خاطئاً.
    فالعقد هنا: **يمرّ ويُوسَم**. تُعيد `None` حين لا شيء يُقال (الرمزُ يوافق
    حُكمَ اليوم ويجتاز التأكيد والعتبة)، وإلا dict يُرفَق بالنتيجة فتعرضه طبقةُ
    العرض في «حدود هذا التقرير».

    قراءةٌ حتميّة صرفة: صفر شبكة، صفر نداء كلود، صفر تكلفة."""
    code = str(hs_code or "").strip()
    if not code:
        return None
    from silk_hs_resolver import resolve
    dp = resolve(product or "", path=path)
    conf_contract = confirm_hs(product or "", code, path)
    notes: list[str] = []
    if dp.value and dp.value != code:
        notes.append(f"المُحلِّل الحتمي اليوم يعيد {dp.value} لهذا الاسم "
                     f"لا {code} المخزَّن")
    if is_flagged(conf_contract):
        _missing = "، ".join(conf_contract.get("missing_terms") or [])
        notes.append(f"وصف الرمز المخزَّن «{conf_contract.get('code_desc')}» "
                     f"لا يشمل صفة المنتج المميّزة"
                     + (f" ({_missing})" if _missing else ""))
    try:
        _c = None if hs_confidence is None else float(hs_confidence)
    except (TypeError, ValueError):
        _c = None
    if _c is not None and _c < min_confidence():
        notes.append(f"ثقةُ التصنيف {_c:.2f} دون العتبة "
                     f"{min_confidence():.2f} المعمول بها اليوم")
    if not notes:
        return None
    return {
        "agrees_with_resolver_today": bool(dp.value and dp.value == code),
        "stored_hs": code,
        "resolver_today": dp.value,
        "resolver_confidence_today": dp.confidence,
        "confirmed": conf_contract.get("confirmed"),
        "notes": notes,
        "message": ("رمزُ HS المستعمَل في هذه التشغيلة أُعيد من سجلٍّ سابق ولم "
                    "يَعُد يوافق حُكمَ التصنيف اليوم: " + "؛ ".join(notes)
                    + ". أعِد تصنيف المنتج قبل الاعتماد على أرقام هذا التقرير."),
    }


def seed_coverage(path: str = "data/hs_codes.csv") -> dict:
    """تغطيةُ بذرة التصنيف — كم صفّاً يملك حارساً دلالياً عربياً وكم لا يملك.

    صفٌّ بلا `name_ar` **وبلا** `keywords` لا يمكن بلوغُه من اسمٍ عربي إطلاقاً
    (المُحلِّل يطابق العربية على هذين الحقلين)، فيصير **رمزَ إمدادٍ فقط**: لا
    يُنتَج إلا حين يُمرَّر صراحةً — وهناك بالضبط تعيش حادثةُ 040110. يُستعمَل
    في قفلِ انحدارٍ يمنع نموَّ هذا العدد بلا قرار."""
    import csv as _csv
    total = ar = kw = guarded = 0
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                total += 1
                has_ar = bool((row.get("name_ar") or "").strip())
                has_kw = bool((row.get("keywords") or "").strip())
                ar += has_ar
                kw += has_kw
                guarded += bool(has_ar or has_kw)
    except OSError:
        return {"total": 0, "with_name_ar": 0, "with_keywords": 0,
                "arabic_guarded": 0, "supply_only": 0}
    return {"total": total, "with_name_ar": ar, "with_keywords": kw,
            "arabic_guarded": guarded, "supply_only": total - guarded}


def preflight_block(product: str, hs_code: str | None,
                    hs_confirmed: bool = False,
                    path: str = "data/hs_codes.csv",
                    allow_claude: bool = False,
                    ingredients: list | None = None,
                    category: str | None = None,
                    instruction: str = "",
                    hs_confidence: object = _UNSET) -> dict | None:
    """نقطةُ الاختناق المشتركة الوحيدة للبوّابة — the ONE choke-point both
    `/analyze` و`/research` يستدعيانها قبل أيّ إنفاق (الموجة ٢، تدقيق
    المُشرِف 2026-07-21: الحادثة الأصلية أُصلِحت على `/research` فقط ثم
    عاودت الظهور — «إصلاحٌ على مسارٍ واحد نصفُ إصلاح»). تُعيد `dict` تفاصيل
    422 (`error`, `message`, `hs_confirmation`, `candidates`) أو `None` إن
    كان الرمز مؤكَّداً/غير محسوم/الصمّام مُطفأ صراحةً/المستخدم أكّد صراحةً.

    منطقٌ واحدٌ يعيش هنا — لا نسخة مكرّرة داخل كل معالج HTTP؛ المعالجات
    تستدعي هذه الدالة فقط ثم ترفع `HTTPException` بنفسها (هذه الوحدة لا
    تستورد fastapi عمداً — تبقى مكتبة منطق صرفة بلا إطار HTTP).

    الموجة ٣ (المصنّف العام، systemic fix): رمزٌ مُعلَّم (`confirmed=False`)
    لا يُعاد بسبب رفضٍ عارٍ بعد الآن — `silk_hs_classifier.classify_general`
    يُستدعى فوراً (مرشّحون من معرفة النموذج الكاملة بنظام HS، لا بذرتنا
    الجزئية وحدها) فيحمل ردّ الـ422 **مرشّحين فعليّين** (رمز + وصف + سبب)
    يختار المستخدم منهم بنقرة — لا مجرّد «حاول مرة أخرى» بلا توجيه.
    `allow_claude` (يُقرَّره طبقة الـAPI عبر نفس بوّابة القياس التي يستعملها
    `/classify_hs` — هذه الوحدة لا تستورد `silk_usage`/`fastapi` عمداً)
    يتحكّم فقط بهل نداء كلود مسموحٌ هذه المرّة؛ المرشّحون الحتميّون (بذرتنا)
    يظهرون دائماً بلا أي نداء.

    بوّابةُ الثقة (بلاغ «حليب نادك»): يمرّر المستدعي `hs_confidence` صراحةً
    فتُفحَص **أولاً** (أرخص — بلا أيّ نداء) قبل التطابق الدلالي. المستدعي الذي
    لا يمرّرها يحتفظ بالسلوك القديم حرفياً (`_UNSET` ≠ `None`)."""
    if not hs_code or hs_confirmed:
        return None
    # (١) بوّابة الثقة — صمّامٌ مستقلّ، تسبق التطابق الدلالي.
    if hs_confidence is not _UNSET:
        low = confidence_block(product or "", hs_code, hs_confidence, path)
        if low is not None:
            return low
    # (٢) بوّابة التطابق الدلالي — كما كانت، بصمّامها الخاص.
    if not gate_enabled():
        return None
    conf = confirm_hs(product or "", hs_code, path)
    # التباسُ المحور (بلاغ المُشرِف): ترويسةٌ تنقسم بنودها بمحورٍ رقميّ (نسبةُ
    # دهنٍ/سعةٍ/وزن) واسمُ المنتج لا يُثبِّت البند — يُطالَب المشغّل صراحةً
    # بالاختيار بدل المضيّ صامتاً على البند الأدنى، **سواءٌ أكّده التداخلُ
    # اللفظيّ أم علّمه** (التداخل اللفظيّ لا يرى الفارقَ الرقميّ أصلاً: كلاهما
    # «حليب»). يسبق مسارَ التعليم العامّ كي يحمل حدودَ الأشقّاء المفهومة كاملةً
    # (لا مرشّحي المصنّف العام) — مصدرٌ حتميّ صفر تكلفة.
    if axis_disambiguation_needed(product or "", hs_code, path):
        cands = deterministic_candidates(product or "", path=path)
        if len([c for c in cands if c.get("band_ar")]) >= 2:
            return {
                "error": "hs_axis_disambiguation_needed",
                "message": (
                    f"رمز HS {hs_code} داخل ترويسةٍ تنقسم بنودها بحسب سمةٍ "
                    "رقمية (مثل نسبة الدهن أو حالة الحفظ: طازج/طويل الأمد/"
                    f"مركّز/مجفّف)، واسم المنتج «{product or ''}» لا يحدّد أيّها "
                    "— لن يبدأ التحليل على البند الأدنى افتراضاً. اختر البند "
                    "المطابق أدناه (يظهر حدُّ كلٍّ بلغةٍ مفهومة) أو أدخل رمزاً "
                    "يدوياً قبل بدء التحليل."),
                "hs_confirmation": conf,
                "candidates": cands,
                "candidates_source": "deterministic",
            }
    if not is_flagged(conf):
        return None
    from silk_hs_classifier import classify_general
    general = classify_general(product or "", hs_code=hs_code,
                               ingredients=ingredients, category=category,
                               allow_claude=allow_claude, instruction=instruction)
    return {
        "error": "hs_confirmation_needed",
        "message": (f"رمز HS {hs_code} («{conf.get('code_desc')}») "
                    "قد لا يطابق هذا المنتج — الصفة المميّزة غير مشمولة: "
                    f"{'، '.join(conf.get('missing_terms') or [])}. "
                    "اختر من المرشّحين أدناه أو أدخل رمزاً يدوياً قبل بدء "
                    "التحليل."),
        "hs_confirmation": conf,
        "candidates": general.get("candidates") or [],
        "candidates_source": general.get("source"),
    }


# ══════════════ حسمُ السمة الرقمية قبل الحوار (بلاغ المُشرِف) ════════════════
#
# البنودُ داخل الترويسة الواحدة تتمايز بعتبةٍ رقمية (نسبةُ دهنٍ مثلاً) لا
# بكلمة — وهذا بالضبط ما لا يراه `confirm_hs` (كلاهما «حليب»). كان الحلُّ
# السابق: أعرِض حواراً واسألِ المستخدمَ عن العتبة. لكنّ العتبة **رقمٌ يُجيب
# عنه المنتجُ نفسُه**: بطاقةُ العبوة تحمله والويبُ يستشهد به. فالحوارُ
# احتياطٌ لا افتراض، والقياسُ يسبقه دائماً (`silk_hs_attributes`).
#
# نقطةُ اختناقٍ واحدة هنا يستدعيها **كلُّ** معالجٍ ينفق (`/analyze`،
# `/research`، بوّابةُ الالتباس) — إصلاحٌ على مسارٍ واحد نصفُ إصلاح
# (الدرسان ٣٥/٣٧).

def _probe_public(report: dict) -> dict:
    """ما يحتاجه الحوارُ من تقرير القياس — بلا حقولٍ داخلية."""
    return {
        "attribute": report.get("attribute"),
        "label_ar": report.get("label_ar"),
        "unit": report.get("unit"),
        "searched": report.get("searched") or [],
        "missing_ar": report.get("missing_ar") or "",
        "bands_ar": report.get("bands_ar") or [],
    }


def resolve_or_probe(product: str, candidates: list,
                     label_attributes: object = None,
                     allow_web: bool = True,
                     gl: str | None = None) -> dict:
    """احسِم الرمزَ بالسمة الرقمية أو أعِد تقريرَ ما نقص — دوماً dict.

    `report["hs6"]` غيرُ `None` => حُسِم بدليلٍ موسوم (صورةُ عبوة أو رابطُ
    ويب) فلا حوارَ؛ وإلا يحمل التقريرُ ما جُرِّب وما نقص وحدودَ كلّ مرشّح
    بلغةٍ مفهومة كي يعرضها الحوارُ بدل عتبةٍ جمركية خام. لا اختلاق: بلا
    رقمٍ مقيسٍ يقع في نطاقٍ **وحيد** لا يُحسَم رمزٌ أبداً."""
    import silk_hs_attributes as _attrs
    # **مدخلُ المُحلِّل يبقى كما كان حرفياً.** إكمالُ أشقّاء المحور (إصلاحُ
    # العرض) يُغذّي الحوارَ لا المُحلِّل: قياسٌ على ٦٠٠ منتجٍ أظهر أنّ تمريرَ
    # المجموعة المكتملة يجعل أربعةَ منتجاتٍ قابلةً للحسم لم تكن كذلك — أي
    # **توسيعُ تغطية المُحلِّل من بابٍ خلفيّ**، وقد نهى المُشرِف عنه صراحةً.
    # التوسيعُ إن أُريد فقرارٌ مستقلٌّ بأمرٍ مستقلّ، لا أثرٌ جانبيٌّ لإصلاح عرض.
    asked = [c for c in (candidates or [])
             if not (isinstance(c, dict) and c.get("axis_completion"))]
    return _attrs.resolve_by_attribute(
        product or "", asked, label_attributes=label_attributes,
        allow_web=allow_web, gl=gl)


def preflight_resolve(product: str, hs_code: str | None,
                      hs_confirmed: bool = False,
                      path: str = "data/hs_codes.csv",
                      allow_claude: bool = False,
                      ingredients: list | None = None,
                      category: str | None = None,
                      instruction: str = "",
                      hs_confidence: object = _UNSET,
                      label_attributes: object = None,
                      allow_web: bool = True,
                      gl: str | None = None
                      ) -> tuple[str | None, dict | None, dict | None]:
    """البوّابةُ + القياسُ في نداءٍ واحد — `(hs_code, provenance, block)`.

    - `block` غيرُ `None` => ارفع ٤٢٢ بتفاصيله (تحمل الآن `attribute_probe`).
    - `provenance` غيرُ `None` => حُسِم الرمزُ آلياً؛ استعمل `hs_code` المُعاد
      وأرفِق `provenance` بالنتيجة كي يعرض التقريرُ مصدرَ الرمز.
    - كلاهما `None` => الرمزُ الأصليّ مؤكَّدٌ كما كان (السلوك السابق حرفياً).

    `preflight_block` يبقى بعقده الحرفيّ دون تغيير — هذه الدالةُ تغلّفه، فلا
    مستدعٍ قائمٌ ينكسر."""
    block = preflight_block(product, hs_code, hs_confirmed, path,
                            allow_claude, ingredients, category, instruction,
                            hs_confidence)
    if block is None:
        return hs_code, None, None
    report = resolve_or_probe(product, block.get("candidates") or [],
                              label_attributes=label_attributes,
                              allow_web=allow_web, gl=gl)
    if report.get("hs6"):
        return report["hs6"], report, None
    return hs_code, None, {**block, "attribute_probe": _probe_public(report)}


if __name__ == "__main__":  # فحص يدوي — عيّنات صحيحة وخاطئة
    for name, code in [("زبدة الفول السوداني", "040510"),
                       ("تمور", "080410"),
                       ("عسل سدر", "040900"),
                       ("زبدة", "040510"),
                       ("olive oil", "150910")]:
        c = confirm_hs(name, code)
        print(f"{name:>22} / {code}  confirmed={c['confirmed']}  "
              f"overlap={c['overlap']}  missing={c['missing_terms']}")
