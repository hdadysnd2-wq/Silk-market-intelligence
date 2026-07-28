"""عرضُ مرشّحي الرمز للمستخدم — the ONE renderer for HS candidate dialogs.

> **الحادثة (بلاغ المُشرِف).** حوارُ اختيار الرمز عرض على التاجر نصوصاً
> **تناقض المرجع الرسميّ**: `040110` بوصف «نسبة الدهن لا تتجاوز 6%» بينما
> بندُه الرسميّ «≤١٪»، و`040120` بوصف «تتجاوز 6%» بينما بندُه «>١٪ و≤٦٪».
> وعُرِض `040190` — **رمزٌ لا وجود له في المرجع إطلاقاً**. وسقط الشقيقان
> `040140`/`040150` فلم يجد منتجٌ كامل الدسم خياراً صحيحاً أصلاً. والنصُّ
> المعروض حمل **اسم العلامة التجارية** داخل وصفٍ عامّ.
>
> **ليست حادثةَ ألبان.** لقطةُ ترويسةٍ واحدة كشفتها، لكنّ العيبَ في **كيفية
> بناء أيّ حوارِ مرشّحين** لأيّ منتج وأيّ ترويسة وأيّ بُعدِ قياس. الحوارُ
> يُبلَغ على ٦٢ من ٧٢ ترويسةً متعدّدةَ النطاقات، وعلى كلّ ترويسةٍ يرفضها
> المُحلِّل أو يعجز عن قياسها — فهو **المسارُ الافتراضيّ لا الاحتياطيّ**.
>
> **الجذر (مُشخَّصٌ لا مُفترَض).** النصُّ المعروض كان يُختار من ثلاثة مصادر
> متنافسة (بذرةٌ محلّية، نثرُ نموذجٍ وقتَ الطلب، ووصفٌ رسميّ) بحسب أيُّها
> يسجّل تداخلاً لفظياً أعلى — أي أنّ **نثرَ النموذج كان يغلب الوصفَ الرسميّ**
> حين يصادف كلماتٍ مشتركة. عودةُ اللائحة ٣٣ بعينها: *حلِّل المصدر لا النثر*.
> وقائمةُ المرشّحين كانت تُجمَع بتسجيلٍ لفظيٍّ ثم تُقتطَع إلى ثلاثة، بلا أيّ
> معرفةٍ ببنية الترويسة — فتسقط الأشقّاء بنيوياً.

**العقد الدائم.**
  (١) **مصدرٌ واحد للنصّ**: كلُّ وصفِ بندٍ يراه مستخدمٌ يُصيَّر من
      `silk_hs_resolver.official_description` عبر هذه الوحدة حصراً. لا نصَّ
      بذرة، ولا نثرَ نموذج، ولا مُصيِّرَ ثانٍ.
  (٢) **اكتمالُ الأشقّاء**: حين تُعرَض بنودٌ من ترويسةٍ ذات محورٍ رقميّ،
      تُعرَض **كلُّ** أشقّائها على ذلك المحور — لا مجموعةٌ جزئية تُخفي البندَ
      الصحيح.
  (٣) **رمزٌ مجهولٌ لمدوّنتَينا معاً لا يُعرَض أبداً** (`is_a_real_code`) —
      عرضُه اختلاقُ رمزٍ جمركيّ. لكنّ الشرطَ **مدوّنتان لا واحدة**: مرجعُنا
      الرسميّ مجموعةٌ جزئية من البذرة، فاشتراطُه وحدَه يجعل نقصَ نسختنا
      سقفاً على التاجر — عائلةُ اللائحة ٣٩.
  (٤) **لا اسمَ منتجٍ أو علامةٍ أو دولةٍ في نثرٍ مولَّد** (عائلة اللائحة ٣٠).

هذه الوحدة **عرضٌ فقط**: لا تقرّر قبولاً ولا رفضاً، ولا تمسّ منطقَ الحسم
(`silk_hs_attributes.discriminator`/`select_by_value`) ولا تُوسِّع تغطيته.

المكتبات: stdlib فقط؛ الاستيرادُ كسول — تعمل بلا شبكة وبلا مفاتيح.
"""
from __future__ import annotations

import functools
import re

# **لا سقفَ هنا إطلاقاً.** كان سقفٌ «معقول» يُقصّ المرشّحين خارج المحور
# الرقميّ — فأسقط خمسةً من ثلاثة عشرَ بنداً حقيقياً مرّرها المستدعي صراحةً.
# هذا هو العطلُ نفسُه في ثوبٍ أصغر: **الاقتطاعُ قرارُ المستدعي لا قرارُ
# المُصيِّر**؛ من يعرف كم مرشّحاً يطلب هو من يحدّده (`_CANDIDATE_N`).


def official_text(hs6: object) -> str:
    """الوصفُ الرسميّ لبندٍ — من المرجع حصراً، و`""` إن لم يوجد.

    نقطةُ الحقيقة الوحيدة لكلّ نصٍّ يراه المستخدم عن بند."""
    from silk_hs_resolver import official_description
    return official_description(hs6)


def in_official_reference(hs6: object) -> bool:
    """هل البندُ موجودٌ فعلاً في المرجع الرسميّ؟ — شرطُ **نصِّه** الرسميّ."""
    return bool(official_text(hs6))


@functools.lru_cache(maxsize=1)
def _seed_codes() -> frozenset:
    from silk_hs_resolver import load_hs_codes
    return frozenset(r.get("hs_code") for r in load_hs_codes() if r.get("hs_code"))


def is_a_real_code(hs6: object) -> bool:
    """هل البندُ معروفٌ لأيّ من مدوّنتَينا — المرجعُ الرسميّ **أو** البذرة؟

    شرطُ عرضه إطلاقاً. الفارقُ عن `in_official_reference` مقصود: مرجعُنا
    (`data/hs_reference.csv`، ٥٦١٣ بنداً) **مجموعةٌ جزئية** من البذرة
    (٥٦٢٦)، فثلاثة عشر بنداً حقيقياً (`040310`, `080250`, …) موجودةٌ في
    البذرة وحدها. اشتراطُ المرجع وحدَه كان يُسقِطها — أي يجعل نقصَ نسخةِ
    المرجع لدينا **سقفاً** على ما يراه التاجر، وهي بعينها عائلةُ اللائحة ٣٩.
    ما يُسقَط هو الرمزُ المجهول للمدوّنتين معاً (حادثةُ `040190`).

    بندٌ تعرفه البذرةُ وحدها يُعرَض **بلا نصٍّ مُختلَق**: `description_ar`
    فارغٌ لأنّ لا وصفَ رسميّ له — لا نصَّ بذرةٍ ولا نثرَ نموذجٍ يسدّ الفراغ."""
    code = str(hs6 or "").strip()
    return in_official_reference(code) or code in _seed_codes()


@functools.lru_cache(maxsize=1)
def _heading_index() -> dict:
    """فهرسُ الترويسات: `{heading: {axis: [hs6, …]}}` من المرجع الرسميّ.

    التجميعُ بالمحور غيرِ الرقميّ (`silk_hs_attributes._residual_axis`) كي لا
    يُضَمّ شقيقٌ يختلف في محورٍ آخر: ترويسةُ الشاي تنقسم باللون **وبالوزن**،
    فأشقّاءُ «الأخضر ≤٣كجم» هم بنودُ الأخضر وحدها لا بنودُ الأسود."""
    import collections
    import silk_hs_attributes as attrs
    from silk_hs_resolver import load_hs_reference
    index: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for code in load_hs_reference():
        band = attrs.band_of(code)
        if band is None:
            continue
        index[code[:4]][band["axis"]].append(code)
    return {h: {a: sorted(v) for a, v in axes.items()}
            for h, axes in index.items()}


def axis_siblings(hs6: object) -> list[str]:
    """كلُّ أشقّاء البند على **محوره الرقميّ** — أو `[]` حين لا محورَ له.

    يشمل البندَ نفسَه. مصدرُه المرجعُ الرسميّ لا البذرة، فلا يتأثّر بفجوات
    التغطية المحلّية (٨ من ٣٩٣ بنداً فقط قابلةٌ للبلوغ باسمٍ عربيّ)."""
    import silk_hs_attributes as attrs
    code = str(hs6 or "").strip()
    band = attrs.band_of(code)
    if band is None:
        return []
    return list(_heading_index().get(code[:4], {}).get(band["axis"], []))


def band_text_ar(hs6: object) -> str:
    """حدُّ البند بلغةٍ مفهومة («حتى 1% نسبة الدهن») — مشتقٌّ من نطاقه
    الرسميّ، أو `""` حين لا عتبةَ رقمية في وصفه."""
    import silk_hs_attributes as attrs
    code = str(hs6 or "").strip()
    band = attrs.band_of(code)
    if band is None:
        return ""
    label, _syn = attrs.dimension_terms(band["dimension"])
    return attrs.range_ar(band, label)


# ── تنقيةُ النثر المولَّد من أسماءِ المنتجات/العلامات/الدول (اللائحة ٣٠) ──────
#
# النصُّ المعروض في البلاغ حمل اسمَ العلامة داخل وصفٍ عامّ («… حليب نادك
# كامل الدسم»). أيُّ نثرٍ مولَّد يُعرَض في الحوار يُنقّى من صفات **المنتج
# المطلوب نفسِه** ومن أسماء الدول — فلا يُعاد للمستخدم اسمُه الذي كتبه
# بوصفه جزءاً من «الوصف الرسميّ» للبند.
_WORD_SPLIT = re.compile(r"[^0-9A-Za-z؀-ۿ%]+")


@functools.lru_cache(maxsize=1)
def _country_terms() -> frozenset:
    """أسماءُ الدول (عربي/إنجليزي) من مرجع الأسواق — بياناتٌ لا قائمةٌ صلبة."""
    out: set = set()
    try:
        from silk_narrative import COUNTRY_AR
        out.update(str(v).strip() for v in COUNTRY_AR.values() if v)
    except Exception:  # noqa: BLE001 — مرجعٌ اختياري
        pass
    try:
        from silk_market_ranker import COUNTRIES
        from silk_data_layer import partner_name
        for c in COUNTRIES:
            name = partner_name(c.get("m49"))
            if name:
                out.add(str(name).strip())
    except Exception:  # noqa: BLE001
        pass
    return frozenset(t for t in out if len(t) >= 3)


# سوابقُ عربية تلتصق بالكلمة (واو العطف، الباء، اللام، الكاف، الفاء، أل
# التعريف) — «وهولندا» هي «هولندا». بلا تجريدها يمرّ الاسمُ الممنوع ملتصقاً،
# وهو ما أظهره القفلُ فعلياً.
_AR_CLITICS = ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ب", "ل", "ك", "ف")


def _matches_banned(token: str, banned: set) -> bool:
    """هل الكلمةُ (بعد تجريد السوابق الملتصقة) ضمن الممنوعات؟"""
    if token in banned:
        return True
    for clitic in _AR_CLITICS:
        if token.startswith(clitic) and len(token) > len(clitic) + 1:
            if token[len(clitic):] in banned:
                return True
    return False


def sanitize_prose(text: object, product: object = "") -> str:
    """انزع من النثر المولَّد صفاتِ المنتج المطلوب وأسماءَ الدول.

    تُبقي الجملةَ مقروءةً (تُسقِط الكلمةَ لا الجملة). نصٌّ يصير فارغاً بعد
    التنقية يُعاد `""` فيُسقِطه المستدعي — لا نُعيد نصفَ جملةٍ مبتور."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    from silk_hs_confirm import _norm
    banned = {_norm(t) for t in _WORD_SPLIT.split(str(product or "")) if len(t) >= 2}
    banned |= {_norm(c) for c in _country_terms()}
    banned.discard("")
    kept = []
    for token in re.split(r"(\s+)", raw):
        bare = token.strip(" ،؛.,:()،؟!\"'«»")
        if bare and _matches_banned(_norm(bare), banned):
            continue
        kept.append(token)
    cleaned = re.sub(r"\s+", " ", "".join(kept)).strip(" ،؛-—,:")
    # لا تترك أداةَ ربطٍ/جرٍّ معلّقةً في الطرف بعد حذف الكلمة التي تسبقها.
    cleaned = re.sub(r"(?:\s|^)(?:في|من|على|عن|مع|إلى|الى|و)$", "", cleaned)
    return cleaned.strip(" ،؛-—,:")


# ── نقطةُ الاختناق الوحيدة لبناء قائمة الحوار ────────────────────────────────

def build_candidates(product: object, codes, reasons: dict | None = None
                     ) -> list[dict]:
    """ابنِ قائمةَ المرشّحين المعروضة — المُصيِّرُ الوحيد لأيّ حوار اختيار.

    `codes`: رموزٌ مرشّحة (بأيّ ترتيب، من أيّ مصدر). `reasons`: نثرٌ مولَّد
    اختياريّ لكلّ رمز (يُنقّى قبل العرض).

    يعيد `[{hs6, description_ar, band_ar, reason_ar, verified}]` حيث:
      - `description_ar` **الوصفُ الرسميّ حرفياً** (لا بذرة، لا نثرُ نموذج)؛
      - `band_ar` حدُّ البند بلغةٍ مفهومة مشتقٌّ من نطاقه الرسميّ؛
      - رمزٌ خارج المرجع **يُسقَط**؛
      - كلُّ أشقّاء المحور الرقميّ **يُضمّون** ولا يُقتطَع منهم شيء.
    """
    wanted: list[str] = []
    for c in (codes or []):
        code = str(c or "").strip()
        if code and code not in wanted:
            wanted.append(code)

    kept = [c for c in wanted if is_a_real_code(c)]

    # اكتمالُ الأشقّاء — على المحور الرقميّ حصراً.
    complete: list[str] = []
    for code in kept:
        for sib in (axis_siblings(code) or [code]):
            if sib not in complete:
                complete.append(sib)
    for code in kept:                       # بنودٌ بلا محورٍ رقميّ تبقى كما هي
        if code not in complete:
            complete.append(code)

    on_axis = [c for c in complete if band_text_ar(c)]
    off_axis = [c for c in complete if c not in on_axis]
    # على المحور: رتّب بالحدّ الأدنى (قراءةٌ طبيعية من الأصغر للأكبر). ولا
    # اقتطاعَ في أيٍّ من الفريقين — لا هنا ولا خارج المحور.
    import silk_hs_attributes as attrs

    def _lo(code: str) -> float:
        band = attrs.band_of(code)
        lo = (band or {}).get("lo")
        return lo if lo is not None else float("-inf")

    ordered = sorted(on_axis, key=_lo) + off_axis
    reasons = reasons or {}
    seed_codes = _seed_codes()
    requested = set(kept)
    out = []
    for code in ordered:
        out.append({
            "hs6": code,
            "description_ar": official_text(code),
            "band_ar": band_text_ar(code),
            "reason_ar": sanitize_prose(reasons.get(code), product),
            "verified": code in seed_codes,
            # علامةٌ داخلية: هذا البندُ **أضافته هذه الوحدة** لإكمال المحور،
            # ولم يطلبه المستدعي. العرضُ لا يفرّق (الاكتمالُ شرطُ صحّة)، لكنّ
            # **المُحلِّل الرقميّ يُغذّى بالمطلوب وحدَه** كي لا يتّسع نطاقُ
            # حسمِه من بابٍ خلفيّ — أمرُ المُشرِف: لا توسيعَ لتغطية المُحلِّل.
            "axis_completion": code not in requested,
        })
    return out


if __name__ == "__main__":   # فحصٌ يدوي — بلا شبكة، بلا مفاتيح
    import sys
    args = [a for a in sys.argv[1:] if a.isdigit()]
    for row in build_candidates("—", args):
        print(f"{row['hs6']}  [{row['band_ar']}]")
        print(f"    {row['description_ar'][:100]}")
