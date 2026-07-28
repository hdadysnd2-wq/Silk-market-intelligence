"""مُصنِّف HS المُقنَّن — the HS-classifier agent (Wave 1, owner mandate).

عند فشل المُحلِّل الحتمي (`silk_hs_resolver`) أو ضعف ثقته (دون العتبة)، نداءٌ
**واحد** مقيسٌ لكلود يقترح HS6 **مبنيّاً على مرشّحي مرجع سِلك** (نفس
`silk_hs_resolver.resolve_all` + `load_hs_codes`) — لا اختلاق فصل، ولا «—»
صامت. النتيجة دومًا **اقتراح** يؤكّده المستخدم بنقرة قبل أيّ حجز/إنفاق.

عقد عدم الاختلاق (المبدأ المؤسِّس): رمزٌ لا يوجد في مرجع HS يُرفَض، وفصلٌ
مستبعَد (`exclusion_note`) يُرفَض، والفشل يُعيد `status="manual"` +
`hs6=None` بثقة `0.0` — لا تخمين فصل أبدًا. المستخلِص المتين
(`silk_ai_judge._extract_json`) يحرس مخرَج النموذج، وكلّ نصّ خارجيّ يمرّ عبر
`_isolate` قبل أن يصل كلود.

قاعدةٌ عامّةٌ لا حالةُ منتج: هذا الملف يخلو تمامًا من أيّ اسم منتج أو رمز
دولة (ISO) أو رمز HS مكتوب صلبًا — المنطق يعمل من البيانات وحدها، والقفل
`tests/test_wave1_hs_classifier.py::test_classifier_paths_have_no_hardcoded_product_or_iso_or_hs`
يثبت ذلك (عائلة `hardcoded-product-rule`).

The HS-classifier agent: when the deterministic resolver fails or is
low-confidence, ONE metered Claude call classifies to an HS6 **grounded on
the repo's HS reference lookup**. The result is always a PROPOSAL the user
confirms before any reservation. Never a guessed chapter, never a silent «—».
Metering (count + dollar) is enforced at the API layer (`api._classify_ai_allowed`).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("silk.hs_classifier")

# عتبة ثقة المُحلِّل الحتمي التي دونها نستدعي كلود — نفس عتبة `resolve_all`
# (٠٫٧). قابلة للضبط بالبيئة فقط (لا رقم مكتوب صلبًا في المنطق العام).
_DETERMINISTIC_THRESHOLD = float(
    os.environ.get("SILK_HS_MIN_CONFIDENCE", "0.7") or "0.7")
# عدد مرشّحي المرجع الذين نُرسي عليهم اقتراح كلود / نعرضهم في المنتقي اليدوي.
_CANDIDATE_N = int(os.environ.get("SILK_HS_CLASSIFIER_CANDIDATES", "8") or "8")

# الرسالة الموحّدة عند تعذّر التصنيف (مُختبَرة بالمطابقة التامة) — عقد عدم اختلاق.
MANUAL_MSG = "تعذّر التصنيف — اختر الرمز يدوياً"


def enabled() -> bool:
    """صمّام المالك — **فشل-آمن: مفعّل افتراضياً** (نفس عائلة `unresolved-hs-
    silent-spend`، اللائحة ٣٥/`silk_hs_confirm.gate_enabled`): تركُ هذا
    مطفأً افتراضياً كان يعني أن المُصنِّف العام — المُصلَح فعلياً وممتَحَناً
    ليتعرَّف على الرمز الصحيح لمنتجٍ متعدِّد الصفات (لا الصفة الثانوية
    العارضة فقط، عائلة `lookup-table-ceiling`) عند توفّر مفتاح كلود — لا
    يعمل أبداً في الإنتاج ما لم يتذكَّر أحدٌ ضبط متغيّر بيئةٍ غامض؛ فيعود
    النظام صامتاً لسقف جدول البحث الجزئي (نفس الحادثة المتكرّرة). النداء
    الفعلي محكومٌ أصلاً بمفتاح كلود
    (`_classify_general_allow_claude`) وبسقف الإنفاق اليومي المشترك
    (`_reserve_llm_call`) — تعطيل هذا الصمّام تحديداً يزيل حمايةً لا تحمي
    شيئاً إضافياً، فيبقى مُفعَّلاً ما لم يُطفَأ صراحةً
    (`SILK_HS_CLASSIFIER=0/false/no/off`)."""
    raw = os.environ.get("SILK_HS_CLASSIFIER", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


# ── المرشّحون الحتميّون (مصدر الإرساء والمنتقي اليدوي) ────────────────────────

def _candidates(product: str, top_n: int) -> list:
    """مرشّحو HS المرتَّبون من مرجع سِلك — ranked candidate DataPoints."""
    from silk_hs_resolver import resolve_all
    return resolve_all(product, top_n=max(1, top_n)) or []


def _best_confident(product: str):
    """أفضل مرشّح حتمي **فوق العتبة**، أو None (فشل/ضعف ثقة)."""
    for dp in _candidates(product, 1):
        if dp.value is not None and dp.confidence >= _DETERMINISTIC_THRESHOLD:
            return dp
    return None


def needs_classifier(product: str) -> bool:
    """هل يحتاج المنتجُ نداءَ كلود؟ — المُحلِّل الحتمي فشل أو ثقته دون العتبة.

    رخيصٌ وحتمي (بلا شبكة/كلود) — يُستعمَل في نقطة النهاية للقرار **قبل** أيّ
    حجز، فلا تُستهلك تفعيلةٌ مدفوعة حين يكفي المُحلِّل الحتمي.
    """
    return _best_confident((product or "").strip()) is None


# ── بناء الاقتراح الموحّد ─────────────────────────────────────────────────────

def _proposal(hs6, confidence, rationale_ar, alternates, source,
              status: str = "ok", message: str = "") -> dict:
    """شكل الاقتراح الموحّد الذي تقرؤه الواجهة — the one proposal shape."""
    return {
        "status": status,
        "source": source,
        "hs6": hs6,
        "confidence": round(float(confidence or 0.0), 2),
        "rationale_ar": rationale_ar,
        "alternates": alternates or [],
        "message": message,
    }


def _alt(dp) -> dict:
    return {"hs6": dp.value, "label": dp.note,
            "confidence": round(dp.confidence, 2)}


def _candidate_rows(product: str, n: int = _CANDIDATE_N) -> list[dict]:
    """أقربُ صفوف مرجع HS لاسم المنتج — top-N reference rows {hs6,label,confidence}.

    مصدر الإرساء والمنتقي اليدوي: يُرتّب مرجع سِلك بنفس مُسجِّل `silk_hs_resolver`
    ويُعيد رموزًا **حقيقية من المرجع** (حتى تحت العتبة) — كي يملك كلود مرشّحين
    فعليّين يختار منهم، ويملك المنتقي اليدوي بدائلَ. الفصولُ المستبعَدة (٢٧)
    تُصفّى فلا تُعرَض كخيارٍ قابل. صفر رمز مكتوب صلبًا — كلّه من المرجع.
    """
    from silk_hs_resolver import load_hs_codes, _score, exclusion_note
    rows = load_hs_codes()
    if not rows:
        return []
    scored = sorted(((_score(product, r), r) for r in rows),
                    key=lambda t: t[0], reverse=True)
    out: list[dict] = []
    for sc, r in scored:
        code = str(r.get("hs_code") or "").strip()
        if not code or exclusion_note(code):
            continue
        out.append({"hs6": code,
                    "label": r.get("name_ar") or r.get("name_en") or "",
                    "confidence": round(float(sc), 2)})
        if len(out) >= max(1, n):
            break
    return out


def _deterministic_proposal(product: str):
    """اقتراحٌ من المُحلِّل الحتمي حين ثقته كافية — لا نداء كلود."""
    cands = _candidates(product, _CANDIDATE_N)
    confident = [dp for dp in cands if dp.value is not None]
    if not confident or confident[0].confidence < _DETERMINISTIC_THRESHOLD:
        return None
    top = confident[0]
    alts = [_alt(dp) for dp in confident[1:4]]
    return _proposal(top.value, top.confidence,
                     f"طابقه المُحلِّل الحتمي: {top.note}", alts, "deterministic")


def manual(product: str, note: str = "") -> dict:
    """تعذّر التصنيف — منتقٍ يدويّ بلا اختلاق رمز.

    نُعيد المرشّحين الحتميّين (إن وُجِدوا بقيمة) كخيارات للمنتقي — لا نخترع
    رمزًا. `hs6=None`، `status="manual"`، والرسالة الموحّدة `MANUAL_MSG`.
    """
    # المنتقي اليدوي سطحٌ يراه المستخدم أيضاً — نفسُ نقطة الاختناق، فلا
    # يُعرَض فيه نصُّ بذرةٍ يناقض المرجع (كان `_candidate_rows` يُخرِج
    # `name_ar`/`name_en` من البذرة مباشرةً).
    import silk_hs_dialog
    _seed_rows = _candidate_rows((product or "").strip())
    _scores = {r["hs6"]: r.get("confidence") for r in _seed_rows}
    # شكلُ `alternates` يبقى حرفياً `{hs6, label, confidence}` (تقرؤه الواجهة
    # في `showHsManual` عبر `a.label`) — المتغيّرُ هو **مصدرُ النصّ** فقط:
    # الوصفُ الرسميّ بدل `name_ar` من البذرة.
    alts = [{"hs6": r["hs6"],
             "label": r["description_ar"],
             "confidence": _scores.get(r["hs6"])}
            for r in silk_hs_dialog.build_candidates(
                product, [r["hs6"] for r in _seed_rows])]
    return _proposal(None, 0.0, note or MANUAL_MSG, alts, "manual",
                     status="manual", message=MANUAL_MSG)


def classify(product: str, ingredients: list | None = None,
             category: str | None = None, allow_claude: bool = False,
             instruction: str = "") -> dict:
    """اقترح HS6 لمنتج — proposal dict (لا يرفع استثناءً، لا يختلق أبدًا).

    المسار: مُحلِّل حتمي واثق => اقتراح حتمي (بلا كلود). وإلا: إن `allow_claude`
    نداءٌ واحدٌ مقيسٌ لكلود مُرسًى على المرجع؛ وإلا/وعند فشله => منتقٍ يدوي.
    """
    product = (product or "").strip()
    if not product:
        return manual(product)
    det = _deterministic_proposal(product)
    if det is not None:
        return det
    if not allow_claude:
        return manual(product)
    prop = _claude_classify(product, ingredients, category, instruction)
    return prop if prop is not None else manual(product)


# ── نداء كلود المُرسى على المرجع + عقد عدم الاختلاق ───────────────────────────

def _grounding_lines(product: str) -> list[str]:
    """أسطر إرساءٍ من مرجع HS — candidate rows the model must choose from/near."""
    return [f"- {c['hs6']}: {c['label']}" for c in _candidate_rows(product)]


def _claude_classify(product: str, ingredients, category, instruction: str):
    """نداءٌ واحدٌ لكلود — grounded HS6 proposal, or None (declared gap)."""
    from silk_ai_judge import (available, _call, _isolate, _extract_json,
                               _user_steer, _FAST_MODEL, _PRINCIPLE)
    if not available():
        return None
    lines = _grounding_lines(product)
    if not lines:
        return None                          # لا مرجع للإرساء => منتقٍ يدوي
    grounding = "\n".join(lines)
    extra = ""
    if ingredients:
        joined = "، ".join(str(i) for i in list(ingredients)[:20] if str(i).strip())
        if joined:
            extra += "المكوّنات/العناصر المستخلَصة: " + _isolate(joined) + "\n"
    if category:
        extra += "الفئة المقترحة: " + _isolate(str(category)) + "\n"
    user = (
        f"المنتج: {_isolate(product)}.\n" + extra +
        "مرشّحو رموز HS6 من مرجع سِلك (اختَر منها أو أقربَها لطبيعة المنتج — "
        "لا تخترع رمزًا خارج هذا المرجع):\n" + _isolate(grounding) + "\n\n"
        "صنّف المنتج إلى **رمز HS6 واحد** من القائمة أعلاه (أو أقربها منطقيًا)، "
        "واذكر ٢–٣ بدائل معقولة منها. إن تعذّر التصنيف بثقة قُل ذلك في "
        "rationale_ar ولا تُلفّق. أعِد JSON فقط بالشكل: "
        '{"hs6":"NNNNNN","confidence":0.NN,"rationale_ar":"لماذا هذا الرمز",'
        '"alternates":[{"hs6":"NNNNNN","label":"وصف"}]}.'
    ) + _user_steer("hs_classifier", instruction)
    raw = _call(_PRINCIPLE, user, max_tokens=500, model=_FAST_MODEL, timeout=25)
    if not raw:
        return None
    obj = _extract_json(raw)   # noqa: BLE001 — رد غير-JSON = لا اقتراح، لا اختلاق
    if not isinstance(obj, dict):
        return None
    return _validate(obj)


def _clean_code(v) -> str:
    """أبقِ الأرقام فقط — يحذف الفواصل/النقاط من رمز النموذج (تطبيع الرمز)."""
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _valid_ref_codes() -> set:
    from silk_hs_resolver import load_hs_codes
    return {str(r.get("hs_code") or "").strip()
            for r in load_hs_codes() if r.get("hs_code")}


def _validate(obj: dict):
    """عقد عدم الاختلاق: رمزٌ خارج المرجع أو في فصلٍ مستبعَد => رفض (None).

    يُرسي اقتراح النموذج على مرجع HS الفعلي — رمزٌ لا يوجد في `load_hs_codes`
    أو في `EXCLUDED_HS_CHAPTERS` يُرفَض بالكامل فيسقط المستخدمُ للمنتقي
    اليدوي بدل قبول فصلٍ مختلَق. البدائل تُصفّى بنفس الشرط.
    """
    from silk_hs_resolver import exclusion_note
    codes = _valid_ref_codes()
    hs6 = _clean_code(obj.get("hs6"))
    if not hs6 or hs6 not in codes or exclusion_note(hs6):
        return None
    try:
        conf = float(obj.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    rationale = (str(obj.get("rationale_ar") or "").strip()
                 or "تصنيف مُقترَح من كلود مبنيّ على مرجع HS")
    alts: list[dict] = []
    for a in (obj.get("alternates") or [])[:3]:
        if not isinstance(a, dict):
            continue
        ac = _clean_code(a.get("hs6"))
        if ac and ac in codes and not exclusion_note(ac):
            alts.append({"hs6": ac, "label": str(a.get("label") or "").strip(),
                         "confidence": None})
    return _proposal(hs6, conf, rationale, alts, "claude")


# ══════════════ الموجة ٣ — المصنّف العام (systemic fix) ═══════════════════
#
# البذرة الحتمية جزئيةٌ (نطاقها موصوفٌ في docstring silk_hs_resolver) — أيّ
# منتجٍ ضعيف التمثيل فيها (مياه ورد، شيبس بنكهة، عود معطر…) يُطابَق بأقرب
# صفٍّ لفظياً **حتى لو كانت فئته خاطئة تماماً** (كلمة ثانوية عارضة تفوز على
# الصفة المميّزة — عائلة `hardcoded-lookup-ceiling`).
# التصنيف العام لا يُقيَّد بمرشّحي بذرتنا: يسأل النموذج مباشرةً عن أفضل ثلاثة
# مرشّحين من معرفته الكاملة بنظام HS، ثم **بوابة تحقّقٍ حتميّة واحدة** (سلامة
# الفصل + تداخل الصفات المميّزة) تفحص كل مرشّح بمعزلٍ عن مصدره — لا رمز يمرّ
# لمجرّد أن النموذج اقترحه. ثلاثُ درجاتِ نتيجة: تلقائي (صارم) / مرشّحون
# (اختيار المستخدم) / يدوي (لا شيء دفاعي).

_AUTO_MIN_OVERLAP = float(
    os.environ.get("SILK_HS_AUTO_MIN_OVERLAP", "0.8") or "0.8")
_CANDIDATE_MIN_OVERLAP = float(
    os.environ.get("SILK_HS_CANDIDATE_MIN_OVERLAP", "0.3") or "0.3")
# هامشُ تفرّدٍ لتلقائي: أفضل مرشّح يجب أن يتفوّق بوضوح على الثاني، وإلا
# التباس حقيقي بين احتمالين => نسأل لا نُخمِّن (الأمر: «صارمٌ — عند الشك اسأل»).
_AUTO_MARGIN = float(os.environ.get("SILK_HS_AUTO_MARGIN", "0.15") or "0.15")


def _clean_hs6(v) -> str:
    digits = "".join(ch for ch in str(v or "") if ch.isdigit())
    return digits if len(digits) == 6 else ""


def _validated_candidate(product: str, hs6: str, model_desc: str = "",
                         reason_ar: str = "", model_confidence: float = 0.0,
                         source: str = "llm") -> dict | None:
    """صادِق على مرشّحٍ واحد عبر البوابة الحتمية — سلامة الفصل (بنية WCO
    الكاملة، لا بذرتنا الجزئية) ثم تداخل الصفات المميّزة (ضد وصف بذرتنا إن
    وُجد الرمز فيها، وإلا ضد الوصف الذي قدّمه النموذج نفسه — عقد عدم
    الاختلاق: لا نثق برمزٍ لمجرّد الادّعاء، بل نقيس التداخل فعلياً).

    يعيد `None` لمرشّحٍ مرفوضٍ بنيوياً (فصلٌ غير موجود/فصلٌ مستبعَد نطاقياً/
    رمزٌ مشوَّه) — لا يصل قائمة المرشّحين المعروضة إطلاقاً، بمعزلٍ عن أيّ
    ثقةٍ ادّعاها النموذج."""
    from silk_hs_resolver import chapter_valid, exclusion_note
    hs6 = _clean_hs6(hs6)
    if not hs6 or not chapter_valid(hs6) or exclusion_note(hs6):
        return None
    from silk_hs_confirm import confirm_hs, confirm_against_description, _find_row
    ref_row = _find_row(hs6)
    verified = ref_row is not None
    conf = confirm_hs(product, hs6) if verified else None
    # بلاغ حي (تجربة تصنيف منتجٍ غذائيٍّ مُركَّب): صفوفٌ من بذرتنا بلا
    # ترجمةٍ عربية (`name_ar=""`، إنجليزية فقط) تُطأطئ التداخل صفراً ضد
    # صفاتٍ عربية حتى لو كان الرمز صحيحاً تماماً — سكريبتان مختلفان لا
    # يتطابقان لفظياً. كذلك الوصف الرسمي وحده كثيراً ما يستعمل لغة جمركية
    # شكلية لا تعيد ذِكر صفة المنتج العامية («شيبس») — بينما **سبب** النموذج
    # (`reason_ar`) عادةً ما يربط الاثنين صراحةً؛ يُضمّ إلى نصّ المطابقة.
    # حين يقدّم النموذج وصفاً/سبباً، **الأفضل من المصدرين يفوز** لا مصدرٌ
    # واحد مقفَل: `verified` يبقى صحيحاً (الرمز فعلاً في مرجعنا — حقيقةٌ
    # بنيوية) بمعزلٍ عن أيّ وصفٍ حسم المطابقة فعلياً.
    model_text = " ".join(t for t in (model_desc, reason_ar) if t).strip()
    if model_text:
        conf_model = confirm_against_description(product, hs6, model_text)
        if conf is None or (conf_model.get("overlap") or 0.0) > (
                conf.get("overlap") or 0.0):
            conf = conf_model
    if conf is None:
        conf = confirm_against_description(product, hs6, model_text)
    return {
        "hs6": hs6,
        "code_desc": conf.get("code_desc") or model_desc,
        "reason_ar": reason_ar or conf.get("reason") or "",
        "overlap": conf.get("overlap"),
        "confirmed": conf.get("confirmed"),
        "verified": verified,        # الرمز موجودٌ فعلاً في مرجعنا (حقيقةٌ بنيوية)
        "model_confidence": round(float(model_confidence or 0.0), 2),
        "source": source,
    }


def _rank_key(c: dict) -> tuple:
    """ترتيب المرشّحين — الصدارة (والفحص التلقائي `_clearly_auto`) للمرشّح
    الأصحّ فعلياً، لا لمن كان حاضراً في جدول بحثٍ جزئي مصادفةً.

    بلاغ حي («ONE FIX» — المُشرِف): لاحقٌ حتميٌّ **مرفوضٌ فعلاً** (تداخلٌ دون
    عتبة الحسم) قد يبقى «مُتحقَّقاً» (موجوداً في بذرتنا الجزئية) فيتصدّر —
    بمجرّد وجوده في جدولٍ محليّ — مرشّحاً **صحيحاً** اقترحه النموذج بعد أن
    استُدعي **لأن اللاحق الحتمي وحده رُفِض فعلاً**؛ إعادة عرض المرفوض بصفته
    الخيار الأساسي تُبقي التاجر عالقاً بين تأكيد رمزٍ خاطئ وإدخال رمزٍ يجهله.

    الترتيب: (١) عبور عتبة التداخل الأدنى (`_CANDIDATE_MIN_OVERLAP`) —
    مرشّحٌ دفاعيٌّ فعلياً يتصدّر من لم يعبرها بمعزلٍ عن مصدره؛ (٢) بين
    العابرين، مرشّح النموذج (`source="llm"`) — استُدعي تحديداً لأن الحتمي
    وحده قصَّر — يتصدّر على مرشّحٍ حتميٍّ بمعزلٍ عن تعادل التداخل اللفظي
    (تصادفٌ حرفيٌّ عارضٌ لا يهزم استنتاجاً دلالياً فحصته نفس بوابة التحقّق)؛
    (٣) التداخل الفعليّ؛ (٤) ثقة النموذج. `_clearly_auto` يبقى الحارس
    الصارم الوحيد لدرجة «تلقائي» (يشترط `verified` صراحةً بمعزلٍ عن هذا
    الترتيب) — هذا الترتيب يقرّر فقط **مَن يُعرَض أولاً** ضمن مرشّحي تأكيدٍ
    بنقرة، لا مَن يمرّ بلا تأكيد."""
    passes = (c.get("overlap") or 0.0) >= _CANDIDATE_MIN_OVERLAP
    from_llm = c.get("source") == "llm"
    return (passes, from_llm, c.get("overlap") or 0.0,
           c.get("model_confidence") or 0.0)


def _deterministic_validated_candidates(product: str, top_n: int) -> list[dict]:
    """مرشّحون حتميّون (بذرتنا) مصادَقٌ عليهم بنفس بوابة التحقّق — رخيصون
    (بلا كلود) ويدخلون نفس مسار الترتيب/الفرز الموحّد مع مرشّحي النموذج."""
    out = []
    for dp in _candidates(product, top_n):
        if dp.value is None:
            continue
        v = _validated_candidate(product, dp.value, model_desc=dp.note,
                                 model_confidence=dp.confidence,
                                 source="deterministic")
        if v is not None:
            out.append(v)
    return out


def _dedupe_candidates(cands: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for c in cands:
        prev = seen.get(c["hs6"])
        if prev is None or _rank_key(c) > _rank_key(prev):
            seen[c["hs6"]] = c
    return sorted(seen.values(), key=_rank_key, reverse=True)


def classify_general(product: str, hs_code: str | None = None,
                     ingredients: list | None = None,
                     category: str | None = None,
                     allow_claude: bool = False,
                     instruction: str = "") -> dict:
    """التصنيفُ العام — عقدُ الموجة ٣: مرشّحون من معرفة النموذج الكاملة
    بنظام HS (لا بذرتنا الجزئية وحدها)، مصادَقٌ عليهم ببوابةٍ حتمية واحدة،
    بثلاث درجات نتيجة صريحة.

    يعيد dict: {tier: "auto"|"candidates"|"manual", hs6, confidence,
    candidates: [...] (حتى ٣، كلٌّ منها {hs6, code_desc, reason_ar, overlap,
    verified, source})، message, source, used_llm}.

    `tier="auto"` **صارمٌ**: أفضل مرشّح مؤكَّدٌ (مرسًى على بذرتنا) بتداخلٍ
    ≥ `SILK_HS_AUTO_MIN_OVERLAP` (٠٫٨ افتراضياً) **ويتفوّق بهامشٍ واضح** على
    الثاني — التباسٌ حقيقي بين مرشّحين لا يمرّ تلقائياً أبداً (الأمر: «عند
    الشك اسأل»). لا نداء كلود إن كفى المُحلِّل الحتمي وحده."""
    product = (product or "").strip()
    if not product:
        return {"tier": "manual", "hs6": None, "confidence": 0.0,
               "candidates": [], "message": MANUAL_MSG, "source": "manual",
               "used_llm": False}

    candidates: list[dict] = []
    # (١) المرشّح المُعطى صراحةً (رمزٌ محسومٌ سابقاً) يدخل السباق أيضاً —
    # قد يفوز تلقائياً بلا أيّ نداء إضافي إن كان فعلاً جيداً.
    if hs_code:
        given = _validated_candidate(product, hs_code, source="given")
        if given is not None:
            candidates.append(given)
    # (٢) مرشّحو بذرتنا الحتميّون — رخيصون، دائماً.
    candidates.extend(_deterministic_validated_candidates(product, _CANDIDATE_N))
    candidates = _dedupe_candidates(candidates)

    def _clearly_auto(cands: list[dict]) -> dict | None:
        if not cands:
            return None
        top = cands[0]
        if not (top.get("verified") and (top.get("overlap") or 0.0)
                >= _AUTO_MIN_OVERLAP):
            return None
        if len(cands) > 1:
            second = cands[1].get("overlap") or 0.0
            if (top.get("overlap") or 0.0) - second < _AUTO_MARGIN:
                return None            # التباسٌ حقيقي — لا تلقائي
        return top

    used_llm = False
    top = _clearly_auto(candidates)
    if top is None and allow_claude and enabled():
        cache_key = product if not (ingredients or category) else (
            product + "|" + "|".join(sorted(str(i) for i in (ingredients or [])))
            + "|" + str(category or ""))
        cached = _cached_general(cache_key)
        if cached is not None:
            llm_raw = cached
        elif _reserve_llm_call():
            llm_raw = _claude_classify_general(product, ingredients, category,
                                               instruction)
            used_llm = llm_raw is not None
            if llm_raw is not None:
                _store_general_cache(cache_key, llm_raw)
        else:
            llm_raw = None
        if llm_raw:
            for c in llm_raw[:3]:
                v = _validated_candidate(
                    product, c.get("hs6"), model_desc=c.get("description_ar", ""),
                    reason_ar=c.get("reason_ar", ""),
                    model_confidence=c.get("confidence", 0.0), source="llm")
                if v is not None:
                    candidates.append(v)
            candidates = _dedupe_candidates(candidates)
            top = _clearly_auto(candidates)

    if top is not None:
        return {"tier": "auto", "hs6": top["hs6"],
               "confidence": top.get("overlap") or 0.0,
               "candidates": _public_candidates(candidates[:3], product),
               "message": "✓ صُنّف تلقائياً", "source": top["source"],
               "used_llm": used_llm}

    plausible = [c for c in candidates
                if (c.get("overlap") or 0.0) >= _CANDIDATE_MIN_OVERLAP]
    if plausible:
        return {"tier": "candidates", "hs6": None, "confidence": 0.0,
               "candidates": _public_candidates(plausible[:3], product),
               "message": "رمزٌ غير محسوم بثقة — اختر من المرشّحين أو أدخل "
                          "رمزاً يدوياً.",
               "source": "llm_general" if used_llm else "deterministic",
               "used_llm": used_llm}

    # لا شيء دفاعي — منتقٍ يدويٌّ خالص، لا اختلاق. نعرض مرشّحي بذرتنا الخام
    # (حتى تحت العتبة) كنقطة انطلاقٍ للمنتقي اليدوي فقط، لا كاقتراحٍ واثق.
    # المنتقي اليدوي سطحٌ يراه المستخدم كذلك — نفسُ نقطة الاختناق. كان هذا
    # الفرعُ يُخرِج `_candidate_rows` (نصَّ بذرةٍ خاماً) مباشرةً: **المُنتِجُ
    # الرابع** للنصّ المعروض، وهو تحديداً الشكلُ الذي تعود منه هذه العائلة.
    import silk_hs_dialog
    _rows = _candidate_rows(product)
    return {"tier": "manual", "hs6": None, "confidence": 0.0,
           "candidates": silk_hs_dialog.build_candidates(
               product, [r["hs6"] for r in _rows]),
           "message": MANUAL_MSG, "source": "manual", "used_llm": used_llm}


def _public_candidates(cands: list[dict], product: str = "") -> list[dict]:
    """شكلٌ نظيفٌ للواجهة/الـAPI — عبر **نقطة الاختناق الوحيدة** للعرض.

    كان هذا يُخرِج `code_desc` كما هو: نصّاً قد يكون بذرةً محلّية أو **نثرَ
    نموذجٍ وقتَ الطلب** (أيُّهما سجّل تداخلاً لفظياً أعلى) — فعُرِضت للتاجر
    حدودٌ تناقض المرجع الرسميّ، ورمزٌ لا وجود له فيه، وأشقّاءُ ناقصون.
    الآن كلُّ نصٍّ يُصيَّر من الوصف الرسميّ عبر `silk_hs_dialog`، وتُضَمّ
    أشقّاءُ المحور الرقميّ كاملةً. الثقةُ المحسوبة تُنقَل كما هي (حسابٌ لا
    نصّ)، ولا يُمَسّ منطقُ القبول/الرفض إطلاقاً."""
    import silk_hs_dialog
    scores = {c["hs6"]: (c.get("overlap") if c.get("overlap") is not None
                         else c.get("model_confidence")) for c in cands}
    reasons = {c["hs6"]: (c.get("reason_ar") or "") for c in cands}
    rows = silk_hs_dialog.build_candidates(
        product, [c["hs6"] for c in cands], reasons=reasons)
    for r in rows:
        r["confidence"] = scores.get(r["hs6"])
    return rows


def _reserve_llm_call() -> bool:
    """احجز نداءً واحداً مقيساً (عدّاً + دولاراً) قبل نداء التصنيف العام
    الفعلي — ذرّياً، تماماً كنقطة `/classify_hs` (api.py)، لكن هنا **داخل
    الوحدة نفسها** كي يعمل `classify_general` سواءً استُدعي من نقطة النهاية
    أو من `preflight_block` (كلا مساري /analyze و/research) بلا ازدواج
    منطق حجزٍ عند كل مستدعٍ. لا يُستدعى إلا حين ثبت فعلاً أن اللاحق حتميٌّ
    غير كافٍ ولا إصابة ذاكرة — لا حجزَ استكشافيّ."""
    import silk_usage
    if not silk_usage.try_reserve_paid_calls(1):
        return False
    expected = float(
        os.environ.get("SILK_HS_CLASSIFY_EXPECTED_USD", "0.02") or "0.02")
    return silk_usage.try_reserve_usd(expected)


def _cached_general(cache_key: str):
    try:
        import silk_store
        payload = silk_store.get_cached_hs_classification(cache_key)
        return payload.get("candidates") if isinstance(payload, dict) else None
    except Exception as e:  # noqa: BLE001 — الذاكرة تحسين لا شرط تصنيف
        log.debug("hs classify cache read skipped: %s", e)
        return None


def _store_general_cache(cache_key: str, candidates: list[dict]) -> None:
    try:
        import silk_store
        silk_store.cache_hs_classification(cache_key, {"candidates": candidates})
    except Exception as e:  # noqa: BLE001
        log.debug("hs classify cache write skipped: %s", e)


def _claude_classify_general(product: str, ingredients, category,
                             instruction: str = "") -> list[dict] | None:
    """نداءٌ واحدٌ لكلود — أفضل ٣ مرشّحين من معرفته الكاملة بنظام HS، **لا**
    مُرسًى على بذرتنا (على النقيض من `_claude_classify` أعلاه). كل مرشّح
    يحمل وصفه الرسمي (كما يراه النموذج) وسبباً — البوابة الحتمية
    (`_validated_candidate`) تفحصهم لاحقاً بمعزلٍ عن هذا النداء."""
    from silk_ai_judge import (available, _call, _isolate, _extract_json,
                               _user_steer, _FAST_MODEL, _PRINCIPLE)
    if not available():
        return None
    extra = ""
    if ingredients:
        joined = "، ".join(str(i) for i in list(ingredients)[:20] if str(i).strip())
        if joined:
            extra += "المكوّنات/العناصر المستخلَصة: " + _isolate(joined) + "\n"
    if category:
        extra += "الفئة المقترحة: " + _isolate(str(category)) + "\n"
    user = (
        f"المنتج: {_isolate(product)}.\n" + extra +
        "أنت خبيرٌ بنظام التصنيف الجمركي المنسّق (HS) الدولي الكامل بكل "
        "فصوله (٠١–٩٧). اقترح أفضل ثلاثة رموز HS6 مرشّحة لهذا المنتج من "
        "معرفتك الكاملة بالنظام — لا تقتصر على أيّ قائمةٍ مرفقة. لكل مرشّح: "
        "الرمز (٦ أرقام)، وصفه الرسمي الدقيق (عربي موجز)، وسببٌ من سطرٍ واحد "
        "لماذا يناسب هذا المنتج تحديداً (لا صفةً ثانوية عارضة). رتّبها من "
        "الأنسب. إن كان المنتج غامضاً جداً أو لا يقع تحت أيّ فصلٍ واضح قُل "
        "ذلك في السبب ولا تخترع رمزاً. أعِد JSON فقط بالشكل: "
        '{"candidates":[{"hs6":"NNNNNN","description_ar":"وصف رسمي موجز",'
        '"reason_ar":"لماذا هذا الرمز","confidence":0.NN}, ...]}'
    ) + _user_steer("hs_classifier_general", instruction)
    raw = _call(_PRINCIPLE, user, max_tokens=700, model=_FAST_MODEL, timeout=25)
    if not raw:
        return None
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return None
    cands = obj.get("candidates")
    if not isinstance(cands, list):
        return None
    out = []
    for c in cands[:3]:
        if not isinstance(c, dict) or not _clean_hs6(c.get("hs6")):
            continue
        try:
            conf = float(c.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append({"hs6": _clean_hs6(c.get("hs6")),
                    "description_ar": str(c.get("description_ar") or "").strip(),
                    "reason_ar": str(c.get("reason_ar") or "").strip(),
                    "confidence": max(0.0, min(1.0, conf))})
    return out or None
