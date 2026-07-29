"""بوابة الجودة قبل التسليم لسِلك — Silk pre-delivery quality gate (الموجة ١٠).

تشغَّل تلقائياً في نهاية كل `/research`، **قبل** أن يُعرَض DOCX — فحوصات
حتمية (لا كلود) على `view["deep_research"]` النهائي: لا رموز شركاء خامة،
لا تقطيع منتصف كلمة، لا تسريب Markdown/JSON خام، لا أرقام ثقة خامة في
المتن، لا تسريب سباكة داخلية (LLMAgent:*/وسوم dp)، تغطية الملحق التقني،
عدم إعلان "دليل غير كافٍ" حين توجد أدلة كافية، ترتيب/اكتمال الأقسام
الأحد عشر (§10.3)، وصحة البعثات (بعثة بلا نتائج مستشهَد بها). حكم PASS /
PASS-WITH-WARNINGS / FAIL؛ النتائج القابلة للإصلاح (Markdown/ثقة خام/
تقطيع/سباكة داخلية) تُصلَح آلياً بالفعل في طبقة العرض
(`silk_reports._strip_inline_markdown`/`_evidence_badge`/`_truncate_at_word`،
`silk_render._strip_internal_plumbing`) — هذه البوابة حارس انحدار يتأكد
أنها فعلاً أُصلحت، لا مصلح مستقل. النتائج غير القابلة للإصلاح (بنيوية/
بيانات) تُبنى كملاحظات تُعرَض داخل قسم "منهجية البحث ونطاقه" (٢) — لا
لافتة تحذير على الغلاف، ولا صمت.

منطق فحص صرف: صفر شبكة، صفر تعديل على الأرقام — قراءة وتشكيل فقط، مثل
`silk_render.py` تماماً.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

PASS, WARN, FAIL = "PASS", "PASS-WITH-WARNINGS", "FAIL"

_MARKDOWN_RE = re.compile(r"(^#{1,6}\s)|(```)|(\*\*)", re.M)
# مفتاح JSON بأي حروف (لا اللاتينية فقط) — بلاغ حي: حكم مسرَّب عُرِّبت
# مفاتيحه ("{\"الحكم\":...}") فأفلت من [a-zA-Z_]+؛ [^"\s]+ يلتقط الصيغتين.
_RAW_JSON_RE = re.compile(r'[{]\s*"[^"\s]+"\s*:', re.M)
# §8 (قرار المُشرِف): نمطُ ثقةٍ **سياقيّ** — كلمةٌ مفتاحية (ثقة/confidence) +
# كسرٌ عشريّ. لا صيدَ كسورٍ مجرّدة: «0.6 مليون» ومقاديرُ البيانات مشروعة.
_RAW_CONFIDENCE_RE = re.compile(r"(?:ثقة|confidence)\s*[:=]?\s*0\.\d", re.I)
_TERMINAL_PUNCT = ".!?:؛،؟…\"'”)"
# بلاغ منتج من المالك: التقرير المعروض للعميل كشف السباكة الداخلية
# ("LLMAgent:tariffs_agreements"، وسوم استشهاد خام "dp7") — كلود يستشهد
# أحياناً حرفياً بوسوم رآها في مدخلاته. طبقة العرض تُصلح هذا فعلاً
# (silk_render._strip_internal_plumbing)؛ هذا الفحص حارس انحدار.
_INTERNAL_PLUMBING_RE = re.compile(r"LLM(?:Mission)?Agent:[A-Za-z_]+|\[?dp\d+\]?")
# بلاغ مالك (تسريب سباكة ٢): أسماء حقول داخلية إنجليزية ("verdict"،
# "confidence 0.64") ومفاتيح بعثات snake_case خام ("pricing_scout") ظهرت في
# نص معروض للعميل. طبقة العرض تُصلح فعلاً (_strip_internal_plumbing يعرّب
# الحقول، وlabel العربي يحل محل المفتاح) — هذان حارسا انحدار حتميان.
_EN_FIELD_LEAK_RE = re.compile(r"\b(?:verdict|confidence)\b")


def _check_markdown_and_raw_json(text: str) -> list[dict]:
    findings = []
    if not text:
        return findings
    if _MARKDOWN_RE.search(text):
        findings.append({"check": "markdown_artifacts", "repairable": True,
                         "note": "تسريب رموز Markdown (#/```/**) في النص المصدَر"})
    if _RAW_JSON_RE.search(text):
        findings.append({"check": "raw_json", "repairable": True,
                         "note": "كتلة JSON خام مسرَّبة في النص المصدَر"})
    return findings


def _check_raw_confidence(text: str) -> list[dict]:
    if text and _RAW_CONFIDENCE_RE.search(text):
        return [{"check": "raw_confidence", "repairable": True,
                 "note": "رقم ثقة خام '(ثقة 0.x)' مسرَّب في النص المصدَر"}]
    return []


# PR A §A1 (بلاغ تحليل ٧): تعارض قيمة الثقة — الملخّص «ثقة منخفضة (50%)»
# والقسم ٤ «الثقة متوسطة (73%)». رقمٌ واحد (`verdict["confidence"]`) سُقِّف في
# طبقة العرض (سقف رمز HS المُعلَّم) **بعد** أن جمّد الكاتب النسخة غير المسقوفة
# في المتن. الفكس الجذري تمريرُ القيمة المسقوفة نفسها للكاتب؛ هذه بوابة حتمية
# تُفشِل حين تفلت نسبتا ثقة مختلفتان إلى المتن — تلتقط نسب الثقة حصراً (شكل
# «عالية/متوسطة/منخفضة (NN%)» أو نسبة تجاور لفظَ ثقة)، لا نسب الحصص/النمو.
# الشكل ١: تسمية نطاقٍ + نسبة («منخفضة (50%)») — مخرَج `confidence_phrase`
# القياسيّ، يغطّي كلّ عرضٍ مشروع للثقة. الشكل ٢: لفظُ ثقةٍ **ملاصقٌ** للنسبة
# («درجة الثقة 73%») — نافذةٌ ضيّقة (فاصل/قوس فقط) كي لا تُلتقَط نسبةُ حصّة/
# نموٍّ تصادف قربَ كلمة «ثقة» في جملةٍ أخرى (إيجابٌ كاذب).
_CONF_PCT_RES = [
    re.compile(r"(?:عالية|متوسطة|منخفضة)\s*\(\s*(\d{1,3})\s*%\s*\)"),
    re.compile(r"(?:درجة\s+الثقة|الثقة|بثقة|ثقة)\s*[:(]?\s*(\d{1,3})\s*%"),
]


def _check_confidence_value_conflict(text: str) -> list[dict]:
    """PR A §A1 — نسبتا ثقة مختلفتان في التقرير نفسه = تعارض حاجب. الثقة
    قيمةٌ واحدة تُشتقّ من مصدر واحد (`silk_narrative.confidence_phrase` فوق
    `verdict["confidence"]` المسقوفة)؛ ظهور رقمين مختلفين يعني أن الكاتب حمل
    نسخةً غير مسقوفة بينما سقّفت طبقة العرض الغلاف — يجب توحيدهما قبل التسليم."""
    if not text:
        return []
    pcts: set[int] = set()
    for rex in _CONF_PCT_RES:
        for m in rex.finditer(text):
            try:
                pcts.add(int(m.group(1)))
            except ValueError:
                continue
    if len(pcts) >= 2:
        shown = "، ".join(f"{p}%" for p in sorted(pcts))
        return [{"check": "confidence_value_conflict", "repairable": False,
                 "note": (f"نسبتا ثقة مختلفتان في التقرير ({shown}) — درجة "
                          "الثقة قيمةٌ واحدة من مصدر واحد؛ التعارض يعني أن "
                          "الكاتب حمل نسخة غير مسقوفة بينما سُقِّف الغلاف. "
                          "وحِّد الثقة من مصدرها الواحد قبل التسليم")}]
    return []


def _check_mid_word_truncation(text: str) -> list[dict]:
    """تقطيع منتصف كلمة — آخر سطر في كل **فقرة** (كتلة أسطر متتالية بين
    سطرين فارغين) ينتهي بحرف/رقم بلا علامة ترقيم ختامية، مع طول كافٍ
    يستبعد عناوين/فواصل قصيرة عادية (بلاغ حي: "لا تتوفر من أد"). يفحص
    آخر سطر في الفقرة فقط لا كل سطر — نثر مُلفوف يدوياً عبر أسطر متعددة
    (تنسيق شائع للمصدر) لا يجب أن يُبلَّغ سطراً سطراً كتقطيع مزيَّف؛
    التقطيع الحقيقي يظهر في نهاية الوحدة المولَّدة لا وسطها."""
    if not text:
        return []
    findings = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        s = lines[-1]
        if s.startswith(("#", "|", "-", "*")):
            continue
        if len(s) < 25:
            continue
        if s[-1] not in _TERMINAL_PUNCT and not s.endswith("**"):
            findings.append({"check": "mid_word_truncation", "repairable": True,
                             "note": f"فقرة تنتهي بلا علامة ترقيم ختامية: "
                                     f"'...{s[-40:]}'"})
    return findings


def _check_trailing_ellipsis(text: str) -> list[dict]:
    """§5/§6 (أمر العمل الرئيس) — لا فقرة/حقيقة تنتهي بنقاط حذف «…»/«...»
    (بتر غير نظيف). حارس انحدار: القصّ النظيف (silk_reports._trim_sentence)
    يقطع عند حدّ جملة بلا نقاط حذف؛ ظهورها يعني بتراً وصل المُسلَّم."""
    if not text:
        return []
    findings = []
    for block in re.split(r"\n\s*\n", text):
        s = block.strip()
        # WP-2 §6(ب): الاقتباس الحرفي (كتلة > أو نصّ داخل «») يُستثنى — فقرة
        # غير اقتباسية تنتهي بنقاط حذف = بتر يصل العميل => FAIL لا تحذير.
        if s.startswith(">"):
            continue
        if s.endswith("…") or s.endswith("..."):
            findings.append({"check": "trailing_ellipsis", "repairable": False,
                             "note": "نصّ ينتهي بنقاط حذف «…» — بتر غير نظيف "
                                     "(§5): يجب القصّ عند حدّ جملة أو العرض كاملاً"})
    return findings


# §B-3 (حزمة الفكس v2.1) — شظية حرف/حرفين عربية يتيمة في آخر سطر فقرة، بلا
# علامة ترقيم ختامية بعدها: أثر بتر منتصف كلمة نجا من فحص علامة الترقيم
# (بلاغ حي: «تحققا ت» — «تحققات» انقطعت فبقيت شظيتان). لا يلتقط أدوات الربط
# أحادية الحرف المشروعة («و»/«ف»/«ب») حين تكون الفقرة كلها قصيرة أصلاً —
# نشترط طولاً كافياً قبل الشظية كي لا يكون التنبيه كاذباً على فقرة قصيرة عادية.
_ORPHAN_TOKEN_RE = re.compile(r"(?:^|\s)[ء-ي]{1,2}\s*$")


def _check_orphan_short_token(text: str) -> list[dict]:
    """§B-3 — شظية 1-2 حرف عربية يتيمة تختم فقرة بلا علامة ترقيم: أثر بترٍ
    غير نظيف نجا من `_check_mid_word_truncation` (ذاك يفحص غياب الترقيم
    فقط، لا شكل الشظية نفسها)."""
    if not text:
        return []
    findings = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        s = lines[-1].strip()
        if not s or s[-1] in _TERMINAL_PUNCT:
            continue
        m = _ORPHAN_TOKEN_RE.search(s)
        if m and len(s) > len(m.group(0)) + 3:
            findings.append({
                "check": "orphan_short_token", "repairable": False,
                "note": f"شظية حرف/حرفين عربية يتيمة تختم فقرة — أثر بتر "
                       f"منتصف كلمة: '...{s[-25:]}'"})
    return findings


# §B-4 — إحالة معلَّقة: النص يعد بملاحظة/قسم («انظر الملاحظة المنهجية»، أو
# «انظر «عنوان بين قوسين»») لا وجود له فعلياً في التقرير.
_METHOD_NOTE_REF_RE = re.compile(r"انظر\s+الملاحظة\s+المنهجية")
_QUOTED_SECTION_REF_RE = re.compile(r"(?:انظر|راجع)\s+[^.\n]{0,20}«([^»]+)»")
_HEADING_RE = re.compile(r"^#{2,3}\s+(?:\d+\.\s*)?(.+?)\s*$", re.M)


def _check_dangling_cross_reference(text: str) -> list[dict]:
    """§B-4 — كل عبارة إحالة («انظر»/«راجع») يجب أن تُشير إلى قسم/ملاحظة
    موجودة فعلياً في نفس التقرير، لا وعداً معلَّقاً."""
    if not text:
        return []
    findings = []
    if _METHOD_NOTE_REF_RE.search(text) and "ملاحظة منهجية" not in text \
            and "قسم المنهجية" not in text:
        findings.append({
            "check": "dangling_cross_reference", "repairable": False,
            "note": "النص يحيل إلى «الملاحظة المنهجية» لكن لا ملاحظة/قسم "
                   "بهذا المضمون موجود فعلياً في التقرير"})
    headings = _HEADING_RE.findall(text)
    for m in _QUOTED_SECTION_REF_RE.finditer(text):
        ref = m.group(1).strip()
        if not any(ref == h or ref in h or h in ref for h in headings):
            findings.append({
                "check": "dangling_cross_reference", "repairable": False,
                "note": f"إحالة معلَّقة إلى «{ref}» — لا عنوان قسم بهذا "
                       "الاسم موجود في التقرير"})
    return findings


# WP-2 §6 — سقالة «إذن ماذا؟»/"So what" الحرفية والنصوص النائبة التقنية:
# كلتاهما وصلت تقارير عملاء مُسلَّمة فعلاً (تدقيق 2026-07-22). FAIL لا تحذير.
_SO_WHAT_LEAK_RE = re.compile(r"إذن\s*،?\s*ماذا|So\s+what", re.I)
# مراجعة شيفرة PR #147: الإبرة العارية «أثر التتبع» كانت (أ) تُطابِق نثراً
# مشروعاً («أثر التتبع الرقمي…») و(ب) **تفوّت هدفها الفعلي** — النص النائب
# الحقيقي مُشكَّل («أثر التتبّع» بالشدّة) فلا يطابق الإبرة غير المشكَّلة.
# الفكس: عبارات مميِّزة كاملة + مقارنة بعد تجريد التشكيل من الطرفين.
_AR_DIACRITICS_STRIP_RE = re.compile("[ً-ْٰ]")


def _strip_ar_diacritics(s: str) -> str:
    """جرّد التشكيل العربي للمقارنة النصية فقط — لا يغيّر نصاً معروضاً."""
    return _AR_DIACRITICS_STRIP_RE.sub("", s or "")


_PLACEHOLDER_STRINGS = (
    "بند تقني غير قابل للعرض المباشر",
    "التفاصيل في أثر التتبع",
    "التفاصيل الكاملة في أثر التتبع",
    "التحليل السردي التفصيلي لهذا القسم غير متاح",
)


_GAPS_TRIGGER_RE = re.compile(r"فجوة بيانات|(?<![ء-ي])فجوات\s*:")


def _check_gaps_closing_contradiction(dr: dict) -> list[dict]:
    """WP-4 §3 — تناقض الختام مع المتن: القسم الختامي سيطبع «لا فجوة
    جوهرية…» (كل مدخلات الفجوات الأربعة خالية — نفس المصدر الواحد
    `silk_reports._client_gap_inputs`) بينما نص التقرير يعلن «فجوة بيانات»
    صراحةً. الحالة المُسلَّمة فعلاً (2026-07-22): الختام نفى الفجوات بينما
    قسم المخاطر عدّد ثلاثاً (حوكمة البنك الدولي/الموسمية/سعر الصرف)."""
    text = ((dr.get("report") or {}).get("text") or "")
    summaries = " ".join(str((m or {}).get("summary") or "")
                         for m in (dr.get("missions") or {}).values())
    combined = text + "\n" + summaries
    # مراجعة شيفرة PR #147: «فجوات:» العارية كانت تطابق «الفجوات:» داخل
    # سردٍ سليم («الفجوات: لا توجد فجوات جوهرية») فتُفشِل تقريراً صحيحاً —
    # المُشغِّل الآن كلمة مستقلة (لا يسبقها حرف عربي) أو «فجوة بيانات».
    if not _GAPS_TRIGGER_RE.search(combined):
        return []
    try:
        from silk_reports import _client_gap_inputs
        critical, informational = _client_gap_inputs(dr)
    except Exception:  # noqa: BLE001 — فحص إضافي لا يكسر البوابة
        return []
    if critical or informational:
        return []   # الختام لن يطبع النفي — لا تناقض
    return [{"check": "gaps_closing_contradiction", "repairable": False,
             "note": "التقرير يعلن «فجوة بيانات» في متنه بينما القسم "
                    "الختامي سيطبع «لا فجوة جوهرية تمنع اتخاذ القرار» — "
                    "تناقض فجوات حاجب للتسليم"}]


def _check_client_scaffold_leak(text: str) -> list[dict]:
    """WP-2 §6(أ) — العبارة السقالية الحرفية «إذن ماذا»/"So what" في نص
    يواجه العميل: أثر تعليمة المحلل القديمة، نُزِعت في المصدر والمُنظِّف —
    ظهورها هنا انحدار حاجب."""
    if text and _SO_WHAT_LEAK_RE.search(text):
        return [{"check": "client_scaffold_leak", "repairable": False,
                 "note": "العبارة السقالية الحرفية «إذن ماذا»/So what "
                        "ظهرت في نص التقرير — تُصاغ الآثار نثراً مدمجاً، "
                        "لا سقالة تعليمات تصل العميل"}]
    return []


def _check_placeholder_leak(text: str) -> list[dict]:
    """WP-2 §6(ج) — نصّ نائب تقني («بند تقني غير قابل للعرض المباشر»/«أثر
    التتبع»/سطر عدم التوفّر العام) في نص يواجه العميل = فشل توليد سُلِّم
    بدل أن يُعاد أو يُحجَب — FAIL."""
    findings = []
    plain = _strip_ar_diacritics(text or "")
    for ph in _PLACEHOLDER_STRINGS:
        if plain and _strip_ar_diacritics(ph) in plain:
            findings.append({
                "check": "placeholder_leak", "repairable": False,
                "note": f"نصّ نائب تقني وصل نص التقرير: «{ph}» — فشل "
                       "التوليد يُعاد أو يُحجَب التسليم، لا يُسلَّم نائب"})
    return findings


# §B-2 — بدل نصّ عام ثابت («التحليل السردي التفصيلي لهذا القسم غير متاح…»)
# حين يخلو قسم عميل من سرد الكاتب: FAIL يمنع التسليم (§0) بدل نصّ عام دائم
# يوهم بتحليل لم يحدث فعلياً. WP-2 §3: حقائق التقاطع الخام لم تعد تكفي
# وحدها (كانت تُسرَد نقاطاً حرفية بسقالة «إذن ماذا» وبتر) — القسم بلا سرد
# كاتب يمرّ فقط إن حمل نثر الصياغة التجارية المُحضَّر
# (`dr["client_fallback_prose"]`، نداء كاتب مصغّر قبل البوابة).


def _check_client_section_would_be_placeholder(dr: dict) -> list[dict]:
    """يُعيد استعمال منطق تجميع أقسام العميل الفعلي (`silk_reports`) للتحقّق
    مسبقاً: هل سيُصادف أيّ قسم من الأقسام الخمسة النهائية غياب سرد الكاتب
    **و** غياب حقائق تقاطع مهيكلة معاً؟ تلك هي بالضبط الحالة التي كانت
    تُغطّى بنصّ عام ثابت بدل تحليل حقيقي (البند §B-2)."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []  # فشل الكاتب كاملاً محكوم عبر analyst_layer_failed/agent_failed
    try:
        from silk_reports import (_CLIENT_SECTION_MAP, _CLIENT_SECTION_ORDER,
                                  _parse_writer_sections, _split_at_roadmap)
    except Exception:  # noqa: BLE001 — فحص إضافي، لا يكسر البوابة
        return []
    sections = _parse_writer_sections(text)
    buckets: dict[str, list[list[str]]] = {c: [] for c in _CLIENT_SECTION_ORDER}
    for title, body in sections:
        if title == "التوصيات الاستراتيجية":
            decision_part, roadmap_part = _split_at_roadmap(body)
            buckets["القرار وأساسه"].append(decision_part)
            if roadmap_part:
                buckets["مسار الدخول والمتطلبات"].append(roadmap_part)
            continue
        head = _CLIENT_SECTION_MAP.get(title)
        if head:
            buckets[head].append(body)
    prose_map = dr.get("client_fallback_prose") or {}
    findings = []
    for head in _CLIENT_SECTION_ORDER:
        has_body = any(any(str(ln).strip() for ln in body)
                      for body in buckets[head])
        if has_body:
            continue
        # WP-2 §3: القسم بلا سرد كاتب يمرّ فقط بنثر الصياغة التجارية
        # المُحضَّر — لا تكفي حقائق التقاطع الخام (كانت تُسرَد نقاطاً حرفية).
        if str(prose_map.get(head) or "").strip():
            continue
        findings.append({
            "check": "client_section_placeholder", "repairable": False,
            "note": f"قسم «{head}» سيُعرَض للعميل بنصٍّ عام ثابت بدل "
                   "تحليل حقيقي — لا سرد كاتب ولا نثر صياغة تجارية "
                   "مُحضَّر له في هذه التشغيلة"})
    return findings


# §D-5 (حزمة الفكس v2.1) — بلاغ حي: «بنسبة .%68» (نقطة قبل علامة النسبة
# قبل الرقم). حارس انحدار: `silk_render._fix_stray_percent_punctuation`
# تُصلح هذا فعلاً؛ ظهوره هنا يعني ثغرة في التطبيع لا حالة طبيعية.
_STRAY_PERCENT_DOT_BEFORE_RE = re.compile(r"\.\s*%")
_STRAY_PERCENT_DOT_AFTER_DIGIT_RE = re.compile(r"%\s*\.\d")


def _check_stray_percent_punctuation(text: str) -> list[dict]:
    """§D-5 — ترقيمٌ ملتصقٌ خاطئ حول علامة النسبة (بلاغ حي: «بنسبة .%68»)."""
    if not text:
        return []
    if _STRAY_PERCENT_DOT_BEFORE_RE.search(text) or \
            _STRAY_PERCENT_DOT_AFTER_DIGIT_RE.search(text):
        return [{"check": "stray_percent_punctuation", "repairable": True,
                 "note": "ترقيمٌ ملتصقٌ خاطئ حول علامة النسبة «%» "
                        "(نقطة في موضع الرقم) — أثر تنسيقٍ غير مُصلَح"}]
    return []


# §F-1 (حزمة الفكس v2.1) — سجلّ كيانات لكل تقرير: اسمان لاتينيان متعدّدا
# الكلمات بنفس مجموعة الكلمات بترتيب مختلف ("Taste of Nature" مقابل "Nature
# of Taste") على الأرجح نفس الكيان مكتوباً بصيغتين — WARN لا FAIL (خطر
# إيجابٍ كاذبٍ حقيقي على شركات مختلفة تتشارك كلمات شائعة).
_LATIN_ENTITY_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?:\s+(?:of|de|&|and|the)?\s*[A-Z][a-zA-Z]+){1,3}\b")
_ENTITY_STOPWORDS = {"of", "de", "and", "the", "for"}


def _check_entity_near_duplicates(text: str) -> list[dict]:
    """§F-1 — اسمان يتشاركان نفس مجموعة الكلمات بترتيبٍ مختلف: على الأرجح
    نفس الكيان مكتوباً بصيغتين لم تُوحَّدا (سجلّ كيانات واحد لكل تقرير)."""
    if not text:
        return []
    seen: dict = {}
    findings = []
    for m in _LATIN_ENTITY_RE.finditer(text):
        name = m.group(0).strip()
        words = frozenset(w.lower() for w in re.findall(r"[A-Za-z]+", name)
                          if w.lower() not in _ENTITY_STOPWORDS)
        if len(words) < 2:
            continue
        prior = seen.get(words)
        if prior and prior != name:
            findings.append({
                "check": "entity_near_duplicate", "repairable": False,
                "note": f"اسمان متقاربان على الأرجح لنفس الكيان بترتيب "
                       f"كلمات مختلف: «{prior}» و«{name}» — وحِّدهما في "
                       "سجلّ كيانات واحد لكل تقرير"})
        else:
            seen.setdefault(words, name)
    return findings


# §F-3 (حزمة الفكس v2.1) — بلاغ حي: «ثقة عالية (68%)» بجانب 90%/75% بلا
# مقياس متّسق. النطاقات المعتمدة (silk_narrative.confidence_phrase): عالية
# ≥80% / متوسطة 60-79% / منخفضة <60%. حارس انحدار مستقلّ لا يعتمد على أن
# كل مكان في الكود يستدعي confidence_phrase فعلياً.
_CONFIDENCE_BAND_RE = re.compile(r"(عالية|متوسطة|منخفضة)\s*\((\d{1,3})%\)")


def _check_confidence_band_label(text: str) -> list[dict]:
    """§F-3 — كل تسمية «عالية/متوسطة/منخفضة» تُطابِق نطاقها الرقمي المعتمد."""
    if not text:
        return []
    findings = []
    for m in _CONFIDENCE_BAND_RE.finditer(text):
        label, pct_s = m.group(1), m.group(2)
        try:
            pct = int(pct_s)
        except ValueError:
            continue
        # WP-1 §4: العتبات من سُلَّم المعايرة الواحد — لا نسخة محلية.
        from silk_style_contract import confidence_band_label
        expected = confidence_band_label(pct)
        if label != expected:
            findings.append({
                "check": "confidence_band_mismatch", "repairable": False,
                "note": f"تسمية ثقة «{label} ({pct}%)» لا تطابق النطاق "
                       f"المعتمد (عالية ≥80% / متوسطة 60-79% / منخفضة "
                       f"<60%) — المتوقَّع «{expected}»"})
    return findings


# §G-1 (حزمة الفكس v2.1) — بلاغ حي: «LPI 3.2 لعام 2022» — لا نسخة LPI لعام
# 2022 فعلياً (نسخ مؤشر أداء اللوجستيات للبنك الدولي: 2007/2010/2012/2014/
# 2016/2018/2023 فقط؛ الأعوام بين نسخة وأخرى لا نسخة منشورة لها). حارس
# حتمي: سنة مذكورة مباشرة مع «LPI» ضمن إحدى الفجوات المعروفة بين نسخ حقيقية.
_LPI_INVALID_EDITION_YEARS = {"2019", "2020", "2021", "2022", "2024"}
# نافذة قصيرة لا تعبر سطراً؛ تسمح بالنقاط العشرية («3.2») بين «LPI» والسنة
# لكنها قصيرة (≤25 محرفاً) فلا تقفز جملةً كاملة.
_LPI_YEAR_NEAR_RE = re.compile(
    r"LPI[^\n]{0,25}?(19\d\d|20\d\d)|(19\d\d|20\d\d)[^\n]{0,25}?LPI")


def _check_lpi_edition_year(text: str) -> list[dict]:
    """§G-1 — سنةٌ مذكورة مع LPI ضمن فجوة معروفة بين نسخ حقيقية منشورة."""
    if not text:
        return []
    findings = []
    for m in _LPI_YEAR_NEAR_RE.finditer(text):
        yr = m.group(1) or m.group(2)
        if yr in _LPI_INVALID_EDITION_YEARS:
            findings.append({
                "check": "lpi_invalid_edition_year", "repairable": False,
                "note": f"سنة {yr} مذكورة مع LPI لكن لا نسخة LPI منشورة "
                       "لهذا العام فعلياً (نسخ البنك الدولي المنشورة: "
                       "2007/2010/2012/2014/2016/2018/2023) — تحقّق من "
                       "السنة الصحيحة قبل الاستشهاد"})
    return findings


# §H-2 (حزمة الفكس v2.1) — بلاغ حي: شُحن «التوصية بالدخول» (تسمية درجة
# «دخول قوي») بجانب «يتحول إلى دخول قوي إذا تحقق شرطان» بينما الحكم
# القانوني الفعلي «دخول مشروط» — سلّم الدرجات مُعرَّف مرّة واحدة
# (`silk_render._VERDICT_LABELS_AR`)؛ هذا حارس انحدار: أيّ ذكرٍ لتسمية درجة
# **أعلى** من الدرجة الفعلية في متن التقرير يجب أن يُصاغ شرطاً مستقبلياً،
# لا حكماً حالياً.
def _check_recommendation_tier_label_consistency(dr: dict) -> list[dict]:
    """§H-2 — الحكم الفعلي «دخول مشروط» لكن المتن يذكر تسمية «دخول قوي»
    («التوصية بالدخول») بلا تأطيرها كشرطٍ مستقبلي."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    try:
        from silk_render import _verdict_tone
    except Exception:  # noqa: BLE001 — فحص إضافي، لا يكسر البوابة
        return []
    verdict = dr.get("verdict") or {}
    # مراجعة شيفرة PR #147: الحكم من المصدر الواحد (الحتمي أولاً) — القراءة
    # القديمة (ai أولاً) كانت تُفشِل تقريراً صحيحاً أو تتخطّى خطأً حقيقياً
    # كلما اختلفت قراءة كلود عن الحكم الحتمي المعروض.
    from silk_narrative import authoritative_verdict
    v_raw, _ = authoritative_verdict(verdict)
    if _verdict_tone(v_raw or "") != "conditional":
        return []
    if "التوصية بالدخول" in text:
        return [{
            "check": "recommendation_tier_mislabel", "repairable": False,
            "note": "الحكم القانوني الحالي «دخول مشروط» لكن المتن يذكر "
                   "تسمية درجة أعلى «التوصية بالدخول» — صف الترقية كشرطٍ "
                   "مستقبلي («يتحول إلى X إذا تحقق كذا») لا حكماً حالياً"}]
    return []


# PR A §A2 (بلاغ تحليل ٧): تعارض تسمية الحكم — الغلاف/§5 «التوصية بالدخول»
# بينما §4 «توصية أولية بالدخول» تُعامَل حالةً مستقبلية. سلّم التسميات مُوحَّد
# الآن (`silk_render._VERDICT_LABELS_AR`، نغمة `preliminary` مستقلة يتّفق
# عليها الكاتب والغلاف)؛ هذه بوابة انحدار: تسميتا حكمٍ حاسمتان مختلفتان
# مذكورتان **إثباتاً** (لا كشرط قلبٍ مستقبليّ) في نفس المتن = تعارض حاجب.
# جذرُ «تحوّل» يلتقط كل تصريفاته (يتحوّل/تتحوّل/التحوّل) — البلاغ الحيّ في
# نموذج إسبانيا كان «تتحوّل … إلى التوصية بالدخول» (مؤنّث) فأفلت من «يتحول».
_VERDICT_FLIP_MARKER_RE = re.compile(
    r"تحوّل|تحول|إذا|إن\s|لو\s|بشرط|شرط|حين|متى|سيناريو|في\s+حال|احتمال")


def _check_verdict_label_conflict(dr: dict) -> list[dict]:
    """PR A §A2 — تسميتا حكمٍ حاسمتان مختلفتان مذكورتان إثباتاً في المتن.
    كلّ تسمية يسبقها ضمن نافذة قصيرة مؤشّرُ شرطٍ مستقبليّ («يتحوّل إلى…
    إذا…») تُستثنى (شرط قلبٍ مشروع لا تعارض). ≥٢ تسمية حاسمة مؤكَّدة معاً =
    التقرير يعرض حكمين — يجب حكمٌ واحد من مصدر واحد."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    from silk_render import _VERDICT_LABELS_AR
    # التسميات الحاسمة فقط (لا «تعذّر إصدار توصية»/«غير محسومة» — قد تتعايش
    # مع تسميةٍ حاسمة كإعلان تغطيةٍ ناقصة لا كحكمٍ ثانٍ متعارض).
    decisive = {_VERDICT_LABELS_AR[k] for k in
                ("go", "preliminary", "conditional", "watch", "nogo")
                if k in _VERDICT_LABELS_AR}
    asserted: set[str] = set()
    for label in decisive:
        start = 0
        while True:
            idx = text.find(label, start)
            if idx < 0:
                break
            start = idx + len(label)
            pre = text[max(0, idx - 45):idx]
            if _VERDICT_FLIP_MARKER_RE.search(pre):
                continue   # شرط قلبٍ مستقبليّ صريح — لا إثبات
            asserted.add(label)
    if len(asserted) >= 2:
        shown = "» و«".join(sorted(asserted))
        return [{"check": "verdict_label_conflict", "repairable": False,
                 "note": (f"تسميتا حكمٍ حاسمتان متعارضتان مذكورتان إثباتاً: "
                          f"«{shown}» — التقرير يعرض حكمين بينما الحكم قيمةٌ "
                          "واحدة من مصدر واحد؛ أيّ ترقية/بديل يُصاغ شرطاً "
                          "مستقبلياً صريحاً، لا حكماً حالياً موازياً")}]
    return []


# §C (حزمة الفكس v2.1) — مدقّق الاتساق الرقمي: أرقامٌ يُفترَض أنها **نفس
# المؤشر** لكنها اختُلفت بمقدار ضئيل يستحيل تفسيره إحصائياً (بلاغ حي: واردات
# 2023 شُحنت 6,733,369 في موضع و6,733,376 في آخر — فارق تحريف/خطأ حساب لا
# مصدرين مختلفين شرعاً). لا يلتقط أرقاماً متقاربة صدفةً بمصادر مختلفة
# (فارقٌ نسبي ≤0.5% فقط، وأكبر من صفر — التطابق التامّ ليس تناقضاً).
_LARGE_NUMBER_RE = re.compile(r"\b\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?\b")


def _check_near_duplicate_figures(text: str) -> list[dict]:
    """§C-3 — رقمان كبيران متقاربان جداً (≤0.5% فارقاً نسبياً) في نفس
    التقرير على الأرجح نفس المؤشر بقيمتين متضاربتين، لا مصدرين مختلفين."""
    if not text:
        return []
    nums = []
    for m in _LARGE_NUMBER_RE.finditer(text):
        try:
            v = float(m.group(0).replace(",", ""))
        except ValueError:
            continue
        nums.append(v)
    findings = []
    seen_pairs = set()
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if a == b or a <= 0 or b <= 0:
                continue
            rel = abs(a - b) / max(a, b)
            if 0 < rel <= 0.005:
                key = (round(min(a, b)), round(max(a, b)))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                findings.append({
                    "check": "near_duplicate_figure", "repairable": False,
                    "note": f"رقمان كبيران متقاربان جداً ({a:,.0f} و{b:,.0f}، "
                           f"فارق {rel*100:.3f}%) على الأرجح نفس المؤشر بقيمة "
                           "واحدة قانونية لا قيمتين متضاربتين — وحِّدهما"})
    return findings


# §C-1 (حزمة الفكس v2.1) — بلاغ حي: HHI شُحن بدقّة عشرية مختلَقة («2184.7»)
# رغم أن المقياس معياريّاً رقمٌ صحيح بعد الضرب ×10000 (0-10000). دقّةٌ عشرية
# على HHI = وهم دقّة لم يُحسَب فعلياً بهذا التفصيل.
_HHI_DECIMAL_RE = re.compile(r"HHI[^0-9]{0,10}(\d{3,5}\.\d+)")


def _check_hhi_false_precision(text: str) -> list[dict]:
    """§C-1 — قيمة HHI (مقياس 0-10000 بعد الضرب) بدقّة عشرية مختلَقة.

    PR B §B9: صار **قابلاً للإصلاح** — `silk_render._fix_hhi_false_precision`
    يقرّبها إلى صحيحٍ قبل وصول النص (نفس نطاق regex هذا الفحص)؛ فظهورها هنا
    يعني فشلَ الإصلاح تحديداً في هذه التشغيلة (وصلت الدقّة الوهميّة المُسلَّم)
    — لذا هي ضمن `_REGRESSION_GUARD_FIRED` تُفشِل الحكم لا مجرّد تحذير."""
    if not text:
        return []
    findings = []
    for m in _HHI_DECIMAL_RE.finditer(text):
        findings.append({
            "check": "hhi_false_precision", "repairable": True,
            "note": f"قيمة HHI «{m.group(1)}» بدقّة عشرية على مقياس 0-10000 "
                   "— يجب أن تكون رقماً صحيحاً مقرَّباً (وهم دقّة غير محسوب "
                   "فعلياً بهذا التفصيل)"})
    return findings


# §C-2 (حزمة الفكس v2.1) — بلاغ حي: شُحنت مراتب موردين #1،#2،#5،#6 متخطّية
# #3،#4 — جدول موردين يجب أن يكون متصلاً (top-N كاملاً) لا صفوفاً منتقاة.
_SUPPLIER_RANK_RE = re.compile(r"#(\d{1,2})\b")


def _check_supplier_rank_contiguity(text: str) -> list[dict]:
    """§C-2 — مراتب موردين مذكورة بترقيم «#N» يجب أن تكون متصلة من ١."""
    if not text:
        return []
    ranks = sorted({int(m.group(1)) for m in _SUPPLIER_RANK_RE.finditer(text)})
    if len(ranks) < 2:
        return []
    expected = list(range(ranks[0], ranks[-1] + 1))
    if ranks != expected:
        missing = sorted(set(expected) - set(ranks))
        return [{
            "check": "supplier_rank_gap", "repairable": False,
            "note": f"مراتب موردين مذكورة بترقيم غير متصل ({ranks}) — "
                   f"مراتب مفقودة {missing}؛ جدول أعلى الموردين يجب أن يكون "
                   "متصلاً (top-N كاملاً) لا صفوفاً منتقاة"}]
    return []


def _check_internal_plumbing_leak(text: str) -> list[dict]:
    """تسريب سباكة داخلية (اسم وكيل خام/وسم استشهاد dp) في نص التقرير
    المصدَر — بلاغ منتج من المالك. حارس انحدار: طبقة العرض
    (`silk_render._strip_internal_plumbing`) تُصلح هذا فعلاً قبل وصول
    النص هنا؛ ظهوره يعني ثغرة في التطبيع لا حالة طبيعية."""
    if not text:
        return []
    if _INTERNAL_PLUMBING_RE.search(text):
        return [{"check": "internal_plumbing_leak", "repairable": True,
                 "note": "تسريب سباكة داخلية (اسم وكيل/وسم استشهاد خام) "
                        "في نص التقرير المصدَر"}]
    return []


def _check_english_field_and_mission_key_leak(text: str) -> list[dict]:
    """حقول داخلية إنجليزية (verdict/confidence) أو مفاتيح بعثات snake_case
    خام (pricing_scout وأخواتها) في نص معروض للعميل — حارس انحدار: طبقة
    العرض تعرّب الحقول (`_strip_internal_plumbing`) وتستبدل المفتاح بالاسم
    العربي (`label` في النموذج القانوني)؛ ظهور أيٍّ منها يعني ثغرة تطبيع.
    مفاتيح البعثات تُستورد كسولاً من السجل الواحد (silk_missions.MISSIONS)
    — لا قائمة يدوية تتقادم؛ فشل الاستيراد يمرّر فحص الحقول وحده."""
    findings = []
    if not text:
        return findings
    if _EN_FIELD_LEAK_RE.search(text):
        findings.append({"check": "english_field_leak", "repairable": True,
                         "note": "اسم حقل داخلي إنجليزي (verdict/confidence) "
                                "مسرَّب في النص المصدَر"})
    try:
        from silk_missions import MISSIONS
        keys_re = re.compile(
            r"\b(?:" + "|".join(re.escape(k) for k in MISSIONS) + r")\b")
        if keys_re.search(text):
            findings.append({"check": "mission_key_leak", "repairable": True,
                             "note": "مفتاح بعثة داخلي خام (snake_case) "
                                    "مسرَّب في النص المصدَر"})
    except Exception:  # noqa: BLE001 — حارس ثانوي، لا يعطّل البوابة
        pass
    return findings


# §2 (أمر العمل الرئيس — سرّية: صفر سباكة داخلية في المُسلَّم): محفّزات
# تُفشِل البوابة حتمياً إن ظهرت في نصّ التقرير أو ملخّصات المصادر. طبقة
# العرض (silk_render._strip_internal_plumbing) تُحيّدها فعلاً قبل وصول النص
# هنا — ظهور أيٍّ منها = ثغرة تطبيع، لا حالة طبيعية (حارس انحدار حتمي).
#   لا تُطبَع القيمة المطابَقة في الملاحظة كي لا تُعيد البوابة تسريبها بنفسها.
_CONFIDENTIALITY_LEAK_PATTERNS = [
    ("tool_use_leak", re.compile(r"tool[-\s]?use", re.I), "وسم استخدام أداة"),
    ("claude_mention", re.compile(r"\bClaude\b|كلود"), "ذكر صريح للأداة (كلود)"),
    ("env_var_leak", re.compile(r"SILK_[A-Z_]+"), "اسم متغيّر بيئة داخلي"),
    ("research_track_leak", re.compile(r"مسار(?:ات)?\s+(?:ال)?بحث"),
     "نسبة الحقائق لمسار بحث داخلي"),
    ("facts_list_leak", re.compile(r"بين\s+الحقائق"),
     "تلميح لقائمة حقائق داخلية"),
    ("ops_warning_leak", re.compile(r"⚠"), "رمز تحذير تشغيلي"),
]


# §8 (أمر العمل الرئيس — بوابة الأسلوب الحتمية): جودة العربية التجارية.
#   FAIL: «م$» (اختزال عملة)، «(1)» ترقيم إنجليزي داخل فقرة، «بين الحقائق».
#   WARN: «من ناحية» > مرّتين (سقف رابط)، رقم مفتاحي مميَّز مكرَّر > مرّتين.
_MSHORT_STYLE_RE = re.compile(r"\d\s*م\$")
_INLINE_ENUM_RE = re.compile(r"(?<![\n(])\s\(\d\)")   # «(1)» وسط سطر لا بدايته
# §8 (قرار المُشرِف): قائمةُ أدوات الربط الموسَّعة — عباراتٌ متعدّدةُ الكلمات
# (خطرُ إيجابٍ كاذبٍ ضئيل). تدرّجٌ لكلّ أداة: ≤٢ تمرّ، ٣–٤ WARN، ≥٥ FAIL.
_CONNECTORS = ("من ناحية", "علاوة على ذلك", "بالإضافة إلى",
               "من جهة أخرى", "إضافة إلى ذلك")
# رقم مفتاحي مميَّز: نسبة بكسر عشري («55.28%») أو رقم بفواصل آلاف («61,000,000»)
# أو قيمة HHI مجاورة للفظها — عادةً لا يتكرّر طبيعياً، فتكراره >مرّتين حشو.
_KEYFIG_RES = [
    re.compile(r"\d{1,3}\.\d+\s*%"),
    re.compile(r"\d{1,3}(?:,\d{3}){2,}"),
    re.compile(r"HHI[^0-9]{0,8}\d{3,5}"),
]


def style_digest(text: str) -> dict:
    """عدّادُ أدوات الربط والأرقام المفتاحية (§8) — عدٌّ فقط، لا حكم. يُطبَع
    **دائمًا** في CI (كمبدأ §4: الأخضر/التحذير مفحوصٌ لا مُستنتَج)."""
    text = text or ""
    connectors = {c: len(re.findall(re.escape(c), text)) for c in _CONNECTORS}
    connectors = {c: n for c, n in connectors.items() if n}
    figures: dict = {}
    for rex in _KEYFIG_RES:
        for m in rex.finditer(text):
            tok = re.sub(r"\s+", "", m.group(0))
            figures[tok] = figures.get(tok, 0) + 1
    figures = {t: n for t, n in figures.items() if n}
    return {"connectors": connectors, "key_figures": figures}


def _style_tier(n: int) -> str:
    """تدرّجُ الأسلوب: ≥٥ FAIL، ٣–٤ WARN، وإلا ok."""
    return "FAIL" if n >= 5 else "WARN" if n >= 3 else "ok"


def format_style_digest(text: str) -> str:
    """خُلاصةُ الأسلوب القابلة للفحص — تُطبَع دائمًا في CI (قرار المُشرِف §8)."""
    d = style_digest(text)
    out = ["----- §8 style digest (connectors / key-figures) -----"]
    if not d["connectors"] and not d["key_figures"]:
        out.append("  (none over threshold-tracked patterns)")
    for c, n in sorted(d["connectors"].items(), key=lambda kv: -kv[1]):
        out.append(f"  connector «{c}» ×{n}  [{_style_tier(n)}]")
    for t, n in sorted(d["key_figures"].items(), key=lambda kv: -kv[1]):
        out.append(f"  key-figure «{t}» ×{n}  [{_style_tier(n)}]")
    return "\n".join(out)


def _check_style(text: str) -> list[dict]:
    """§8 — جودة الأسلوب الحتمية (بلا كلود). FAIL على اختزال العملة/الترقيم
    الإنجليزي داخل الفقرة؛ وتدرّجٌ لأدوات الربط والأرقام المفتاحية (٣–٤ WARN،
    ≥٥ FAIL) — قرار المُشرِف §8: أسلوبٌ لا تسريب، فالتصعيد عند الإفراط فقط."""
    findings = []
    if not text:
        return findings
    if _MSHORT_STYLE_RE.search(text):
        findings.append({"check": "style_currency_shorthand", "repairable": True,
                         "note": "اختزال العملة «م$» — اكتب «مليون دولار» كاملةً"})
    if _INLINE_ENUM_RE.search(text):
        findings.append({"check": "style_inline_enumeration", "repairable": False,
                         "note": "ترقيم إنجليزي «(1)…(2)» داخل فقرة — استعمل "
                                 "أولاً/ثانياً أو قائمة مرقّمة"})
    dg = style_digest(text)
    for c, n in dg["connectors"].items():
        if n >= 5:
            findings.append({"check": "style_connector_excess", "repairable": False,
                             "note": f"أداة الربط «{c}» تكرّرت {n} مرّات "
                                     "(≥٥ = حشوٌ أسلوبيّ يُفشِل) — نوّع أدوات الربط"})
        elif n >= 3:
            findings.append({"check": "style_connector_overuse", "repairable": False,
                             "note": f"أداة الربط «{c}» تكرّرت {n} مرّات "
                                     "(الحدّ المريح مرّتان) — نوّع أدوات الربط"})
    for tok, n in dg["key_figures"].items():
        if n >= 5:
            findings.append({
                "check": "style_repeated_key_figure_excess", "repairable": False,
                "note": f"رقم مفتاحي «{tok}» تكرّر {n} مرّات في المتن "
                        "(≥٥ = حشوٌ يُفشِل) — اذكره كاملاً مرّة ثم أحِل إليه"})
        elif n >= 3:
            findings.append({
                "check": "style_repeated_key_figure", "repairable": False,
                "note": f"رقم مفتاحي «{tok}» تكرّر {n} مرّات في المتن "
                        "(الحدّ مرّتان) — اذكره كاملاً مرّة ثم أحِل إليه"})
    return findings


def _check_confidentiality_leaks(text: str) -> list[dict]:
    """§2 — تسريب سرّية في المُسلَّم (اسم أداة/متغيّر بيئة/مسار بحث/…). حارس
    انحدار: يُفشِل البوابة إن أفلت أيّ محفّز من طبقة التطهير."""
    findings = []
    for check, pat, human in _CONFIDENTIALITY_LEAK_PATTERNS:
        if pat.search(text or ""):
            findings.append({
                "check": check, "repairable": True,
                "note": f"تسريب سرّية داخلي في نصّ التقرير ({human}) — "
                        "يجب تحييده قبل التسليم"})
    return findings


def _check_bare_partner_codes(dr: dict) -> list[dict]:
    """رمز شريك خام بدل اسم — حارس انحدار دائم لإصلاح ١٠.٢أ
    (`silk_data_layer.partner_name`) لا فحصاً أولياً؛ يُتوقَّع نظافته دوماً
    الآن لكنه يبقى يرصد أي تسرّب مستقبلي (مصدر بيانات جديد لا يمرّ عبر
    partner_name).

    سدّ تسريب (الطبقة ٧ — مفارقة البوابة): كانت ملاحظة هذا الفحص نفسها
    تحمل مفتاح البعثة الخام (snake_case) وتنسيق repr بايثون الخام
    (`{p!r}` → `'042'` بعلامات اقتباس بايثونية) — وهذه الملاحظة
    (`repairable: False`) تُحقَن مباشرة في قسم "منهجية البحث ونطاقه"
    المعروض للعميل عبر `methodology_notes`؛ أي بوابة الجودة كانت تكتشف
    تسريباً ثم تُصدر تسريباً موازياً بنفسها. الاسم التجاري + بلا تنسيق
    بايثون الآن، بنفس `_mission_label` المستعمَل في بقية هذا الملف."""
    findings = []
    for key, m in (dr.get("missions") or {}).items():
        label = _mission_label(key)
        for f in (m.get("findings") or []):
            v = f.get("value")
            if isinstance(v, dict) and "partner" in v:
                p = str(v.get("partner") or "")
                if p.isdigit():
                    findings.append({
                        "check": "bare_partner_code", "repairable": False,
                        "note": f"[{label}] رمز شريك خام بلا اسم: «{p}»"})
    return findings


def _check_intersection_insufficiency(dr: dict) -> list[dict]:
    """"دليل غير كافٍ" رغم وجود ≥٢ بند ذي صلة — بلاغ حي (الموجة ٩-١٠)."""
    from silk_market_analyst import _CATEGORY_LABELS
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    by_cat = (dr.get("analyst") or {}).get("by_category") or {}
    findings = []
    for cat, label in _CATEGORY_LABELS.items():
        items = by_cat.get(cat) or []
        if len(items) < 2:
            continue
        idx = text.find(label)
        window = text[idx:idx + 400] if idx >= 0 else text
        if "دليل غير كافٍ" in window or "لا تتوفر بيانات كافية" in window:
            findings.append({
                "check": "intersection_insufficiency", "repairable": False,
                "note": f"تقاطع '{label}' يحوي {len(items)} بند(اً) ذا صلة "
                       "لكن النص يعلن 'دليل غير كافٍ' بدل الحساب الحسابي"})
    return findings


def _check_section_structure(dr: dict) -> list[dict]:
    """ترتيب/اكتمال الأقسام الأحد عشر (§10.3) — يعيد استعمال الفحص الحتمي
    الموجود أصلاً في silk_ai_judge (مصدر حقيقة واحد لا تكرار منطق)."""
    from silk_ai_judge import _section_order_issues
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    return [{"check": "section_structure", "repairable": False, "note": issue}
           for issue in _section_order_issues(text)]


def _mission_label(key: str) -> str:
    """اسم البعثة التجاري بالعربية — بلاغ منتج من المالك: ملاحظات هذه
    البوابة تصل قسم "حدود المنهجية وجودة البيانات" في التقرير المعروض
    للعميل مباشرة؛ المفتاح snake_case الخام (مثل "tariffs_agreements")
    سباكة داخلية لا لغة تجارية."""
    try:
        from silk_missions import MISSIONS
        row = MISSIONS.get(key)
        if row and row.get("name"):
            return row["name"]
    except Exception:  # noqa: BLE001 — تسمية تجميلية لا شرط فحص
        pass
    return key.replace("_", " ")


def _check_agent_health(dr: dict) -> list[dict]:
    """بعثات بلا أي نتيجة مستشهَد بها — تُسرَد صراحة، لا تُخفى داخل ملخّص.

    بعثة **فشلت فعلياً** (`failed=True`) أشد من بعثة نجحت لكن لم تجد
    جديداً (مثل `opportunity_gaps` حين تكون كل الفرص مغطّاة أصلاً في
    البعثات الأخرى) — الأولى بند `agent_failed` (تُفشِل الحكم)، الثانية
    `agent_empty` (ملاحظة منهجية فقط، لا تُفشِل الحكم وحدها)."""
    findings = []
    for key, m in (dr.get("missions") or {}).items():
        label = _mission_label(key)
        if m.get("failed"):
            findings.append({
                "check": "agent_failed", "repairable": False,
                "note": f"بعثة '{label}' فشلت بلا نتائج مستشهَد بها — "
                       f"{m.get('summary') or 'بلا ملخّص'}"})
        elif not (m.get("findings") or []):
            findings.append({
                "check": "agent_empty", "repairable": False,
                "note": f"بعثة '{label}' نجحت لكن بلا نتائج مستشهَد بها — "
                       f"{m.get('summary') or 'بلا ملخّص'}"})
    return findings


def _check_analyst_layer_failure(dr: dict) -> list[dict]:
    """فشل طبقة المحلل الشامل كاملة — بلاغ حي إنتاجي (تمور/هولندا): نداءا
    المحلل الشامل وكاتب التقرير تجاوزا مهلة ثابتة فأعادا None، فظهرت
    التقاطعات الخمسة كلها "دليل غير كافٍ" مع غياب التقرير الكامل — ومرّت
    البوابة رغم ذلك لأن كل الفحوصات أعلاه تشترط نص تقرير غير فارغ.

    هذا فحص مستقل لا يشترط وجود نص: تشغيلة بلا تقرير كامل **و** بخمس
    تقاطعات معلنة كلها ناقصة الأدلة معاً = فشل الطبقة كلها، لا نتيجة
    تحليل حقيقية — لا يجوز أن تمر بحكم PASS/PASS-WITH-WARNINGS."""
    from silk_market_analyst import REQUIRED_CATEGORIES

    text = ((dr.get("report") or {}).get("text") or "")
    if text:
        return []
    missing = set((dr.get("analyst") or {}).get("missing_categories") or [])
    if missing >= set(REQUIRED_CATEGORIES):
        return [{"check": "analyst_layer_failed", "repairable": False,
                 "note": "طبقة المحلل الشامل فشلت كاملة: التقاطعات الخمسة "
                        "كلها بلا أدلة كافية والتقرير الكامل غائب — نداء "
                        "المحلل الشامل و/أو كاتب التقرير فشل (مهلة أو خطأ "
                        "شبكة)، لا نتيجة تحليل حقيقية لهذه التشغيلة"}]
    return []


# سقف صفوف الملحق التقني — ثابت واحد مشترك مع المصدِّر
# (`silk_reports._docx_technical_appendix`) كي لا ينحرف حجم الجدول المُسلَّم
# عن رسالة البوابة. رُفع 80 ← 150 (بلاغ حي Nadec/اليمن: 102 استشهاداً
# قُصّت إلى 80 فظهر بلاغ `audit_coverage` على كل تصدير).
AUDIT_APPENDIX_CAP = 150
_AUDIT_APPENDIX_CAP = AUDIT_APPENDIX_CAP   # الاسم القديم — توافق داخلي


def _check_audit_coverage(dr: dict) -> list[dict]:
    """سقف ملحق ٨٠ صفاً — إن تجاوزه إجمالي الاستشهادات، أعلن القطع صراحة
    بدل حذف صامت (نفس مبدأ "لا سقف صامت" المتّبع في هذا المشروع)."""
    total = sum(len(m.get("findings") or [])
               for m in (dr.get("missions") or {}).values())
    if total > _AUDIT_APPENDIX_CAP:
        return [{"check": "audit_coverage", "repairable": False,
                 "note": f"{total} استشهاداً إجمالياً يتجاوز سقف الملحق "
                        f"التقني ({_AUDIT_APPENDIX_CAP}) — يُعرَض أول "
                        f"{_AUDIT_APPENDIX_CAP} فقط، معلَناً هنا لا صامتاً"}]
    return []


# Q2 (تدقيق CAGR غير متسق، تمور/هولندا): معدّل نمو سنوي مركّب واحد قد يظهر
# برقمين مختلفين على نافذتَي سنوات مختلفتين (الملخّص «13.3% (2020-2024)»
# مقابل الحكم «16.3% (2019-2023)») بلا مصالحة. نلتقط «معدّل نمو مؤطَّر بنافذة»
# = نسبة مئوية تجاور لفظَ نموٍّ ونافذةَ سنوات ضمن الجملة نفسها.
_GROWTH_KW_RE = re.compile(r"نمو|مركّب|مركب|سنوي|CAGR|معدّل النمو|compound", re.I)
_PCT_RE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")
_YEAR_WINDOW_RE = re.compile(r"(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}")


def _check_cagr_consistency(dr: dict) -> list[dict]:
    """اكشف أكثر من معدّل نمو سنوي مركّب بنوافذ سنوات مختلفة بلا مصالحة —
    نفس المقياس، سنوات أساس مختلفة، رقمان متعارضان. يمسح سرد الكاتب + تعليل
    الحكم + ملخّص المحلل (المصادر التي أظهرت التعارض فعلاً في البلاغ الحيّ)."""
    report_text = (dr.get("report") or {}).get("text") or ""
    verdict = dr.get("verdict") or {}
    reasoning = " ".join(str(x) for x in [
        (verdict.get("ai") or {}).get("reasoning"), verdict.get("note"),
        ((dr.get("analyst") or {}).get("report") or {}).get("summary")] if x)
    blob = report_text + "\n" + reasoning
    all_windows = [(m.start(), re.sub(r"\s+", "", m.group(0)))
                   for m in _YEAR_WINDOW_RE.finditer(blob)]
    # قُرب (لا تقسيم جُمَل — «.» يكسر العشري «13.3%»): لكل نسبة يجاورها لفظُ
    # نموٍّ ضمن ±45 محرفاً، نربطها بأقربِ نافذةِ سنوات إليها (أقلّ مسافة، ≤45)
    # — فلا تختطف نسبةٌ نافذةَ جملةٍ أخرى في نصٍّ قصير.
    windowed: list[tuple[str, str]] = []
    for pm in _PCT_RE.finditer(blob):
        ctx = blob[max(0, pm.start() - 45):pm.end() + 45]
        if not _GROWTH_KW_RE.search(ctx):
            continue
        near = [(abs(wp - pm.start()), w) for wp, w in all_windows
                if abs(wp - pm.start()) <= 45]
        if not near:
            continue
        windowed.append((pm.group(1), min(near)[1]))
    distinct_vals = {v for v, _ in windowed}
    distinct_wins = {w for _, w in windowed}
    if len(distinct_vals) >= 2 and len(distinct_wins) >= 2:
        pairs = "، ".join(f"{v}% ({w})" for v, w in dict.fromkeys(windowed))
        return [{
            "check": "cagr_inconsistency", "repairable": False,
            "note": ("معدّلات نمو سنوي مركّب متعارضة على نوافذ سنوات مختلفة "
                     f"بلا مصالحة: {pairs} — يجب اعتماد معدّل واحد قانوني مع "
                     "ذكر نافذته، وأيّ بديل يُذكر بنافذته صراحة")}]
    return []


# Q3 (تدقيق عملة العمود المضلِّل، تمور/هولندا): عمود «السعر/كجم بالدولار»
# يحمل قيماً باليورو مع اعتذار داخل الخليّة — وعدٌ بتحويلٍ لم يُجرَ. نكشف عمود
# عملةٍ يَعِد بعملةٍ بينما النصّ يحمل رموز عملةٍ أخرى.
_CURRENCY_LABELS = {
    "USD": re.compile(r"بالدولار|\bUSD\b|دولار"),
    "EUR": re.compile(r"باليورو|\bEUR\b|€|يورو"),
    "GBP": re.compile(r"بالجنيه|\bGBP\b|£|جنيه إسترليني"),
}


_PRICE_HEADER_CUR_RE = re.compile(
    r"السعر[^|\n]{0,20}?(بالدولار|باليورو|بالجنيه)")
_HEADER_PHRASE_TO_CUR = {"بالدولار": "USD", "باليورو": "EUR", "بالجنيه": "GBP"}


def _check_currency_label_mismatch(dr: dict) -> list[dict]:
    """اكشف عمودَ سعرٍ يَعِد بعملةٍ بينما القيم بعملةٍ أخرى (تحويل غير مُنجَز).

    البلاغ الحيّ: عنوان العمود «السعر/كجم بالدولار» بينما الخلايا يورو. البحث
    عن العملة الأخرى **يقتصر على نافذة الجدول نفسه** (من الترويسة حتى أول
    سطرٍ فارغ) — لا كامل نص التقرير: تقارير حقيقية تخلط عملات مشروعة بأقسام
    مختلفة (استيراد بالدولار دوماً §1، تجزئة بعملة الرصد §6) بلا أيّ خطأ؛
    فحصٌ على كامل النص كان يُبلِّغ تعارضاً زائفاً بين قسمين مستقلّين تماماً.
    **قابل للإصلاح** فعلياً — راجع silk_render._fix_price_column_currency_label
    (يُعنوِن العمود بالعملة المرصودة فعلاً قبل وصول النص هنا)؛ هذا الفحص
    حارس انحدار يتأكّد أنّ الإصلاح نجح فعلاً لهذه التشغيلة."""
    text = (dr.get("report") or {}).get("text") or ""
    m = _PRICE_HEADER_CUR_RE.search(text)
    if not m:
        return []
    cur = _HEADER_PHRASE_TO_CUR[m.group(1)]
    block_end = text.find("\n\n", m.end())
    block = text[m.start():block_end if block_end != -1 else len(text)]
    others = [c for c, pat in _CURRENCY_LABELS.items()
             if c != cur and pat.search(block)]
    if others:
        return [{
            "check": "currency_label_mismatch", "repairable": True,
            "note": (f"عمود السعر مُعنوَن بـ{cur} بينما جدول الأسعار نفسه يحمل "
                     f"قيماً بعملة أخرى ({'، '.join(others)}) — عنوِن العمود "
                     "بالعملة المرصودة فعلاً، ولا تَعِد بتحويلٍ لم يُجرَ")}]
    return []


# Master Prompt Part 2 §A3/§C — تناقضٌ رقميٌّ داخليّ: حقيقة في سجل الأدلة
# (findings البعثات، قيمة DataPoint خام) تخالف رقماً في متن التقرير لنفس
# المؤشر بأكثر من ٣× (المثال المكتشف: واردات 17K$ في المتن مقابل 11.88
# مليون$ في سجل الأدلة). سجل الأدلة مصدرٌ **بنيويّ** (قيمة DataPoint رقمية
# حقيقية) لا نصٌّ حرّ — فالمقارنة أضيق خطراً من CAGR/العملة (نصّ مقابل نصّ):
# طرفٌ واحد بياناتٌ مؤكَّدة. نافذة تفسيرٍ محلية (٦٠ محرفاً حول الرقم في
# المتن) تمنع علماً زائفاً حين يُفسَّر التناقض صراحةً (نفس مبدأ فئة كومتريد
# مجاورة في مدوّنة الكويت القانونية) — مطابقٌ لعقد عدم الاختلاق: كلا الرقمين
# يُحفَظان، لا يُصحَّح أحدهما صامتاً.
_RECONCILED_PHRASES = ("مؤشر سياقي", "فئة مجاورة", "فئة كومتريد مجاورة",
                       "ليس خطأً", "لا يُصلَح برقمٍ مختلَق", "تفسير التناقض",
                       "التناقض متوقَّع", "مصالحة")
_IMPORTS_KW_RE = re.compile(r"الواردات|واردات")
_USD_AMOUNT_RE = re.compile(r"(\d[\d,.]*)\s*(مليار|مليون|ألف|الف)?\s*دولار")
_USD_MAGNITUDE = {"مليار": 1_000_000_000, "مليون": 1_000_000,
                  "ألف": 1_000, "الف": 1_000}
# مراجعة الشيفرة: مذكِّرٌ نموّ/نسبة («نمو الواردات 9% سنوياً») ليس قيمة
# استيرادٍ مطلقة بالدولار حتى لو ذُكرت كلمة «واردات» في نفس الملاحظة — قيمته
# الخام (مثال: 9) تعني نسبة مئوية لا مبلغاً، فمقارنتها برقمٍ دولاريّ في المتن
# تُنتِج نسبة تناقضٍ زائفة (false positive). يُستبعَد من سجل الأدلة هنا.
_GROWTH_RATE_NOTE_RE = re.compile(r"نمو|معدّل|معدل|CAGR|%|٪", re.I)


def _usd_amount_to_float(num_str: str, mag: str) -> "float | None":
    try:
        v = float(num_str.replace(",", ""))
    except ValueError:
        return None
    return v * _USD_MAGNITUDE.get(mag, 1)


# بلاغ A3 (تحليل ٧): `_USD_AMOUNT_RE` أعلاه يشترط لفظ «دولار»، بينما الكاتب
# يكتب TAM فعلاً بصيغة رمز `$` — «TAM = 61,000,000$» (عيّنة
# `samples/research_report_latest.md` سطر ٥١) و«2,090,000$» في تحليل ٧ — فأفلتت
# تلك الصيغة وبقيت بوابة A3 **صامتة** على الحالة التي بُنيت لها. المستخلِص أدناه
# يلتقط كلّ صيغةٍ يُخرِجها الكاتب: `N,NNN,NNN$` (لاحق)، `$N` (سابق)، و«N مليون
# دولار» (لفظ). لا يمسّ `_USD_AMOUNT_RE`/`_check_evidence_body_numeric_consistency`
# (نطاقٌ أضيق مقصود) — استخلاصٌ مستقلّ لبوّابتَي TAM وتباين المرآة.
_USD_TRAIL_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(مليار|مليون|ألف|الف)?\s*(?:دولار|\$)")
_USD_LEAD_RE = re.compile(
    r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(مليار|مليون|ألف|الف)?")


def _iter_usd_amounts(text: str) -> list[tuple[int, int, float]]:
    """كلّ مبلغٍ دولاريّ في المتن بأيّ صيغة — قائمة (بداية، نهاية، قيمة).
    يشمل `$`-اللاحقة (`61,000,000$`)، و`$`-السابقة (`$61`)، واللفظية (`61 مليون
    دولار`/`17000 دولار`) — لا صيغةً واحدة كما كان الاشتراط القديم."""
    spans: list[tuple[int, int, float]] = []
    for m in _USD_TRAIL_RE.finditer(text or ""):
        v = _usd_amount_to_float(m.group(1), m.group(2) or "")
        if v is not None and v > 0:
            spans.append((m.start(), m.end(), v))
    for m in _USD_LEAD_RE.finditer(text or ""):
        v = _usd_amount_to_float(m.group(1), m.group(2) or "")
        if v is None or v <= 0:
            continue
        if any(a <= m.start() < b for a, b, _ in spans):
            continue   # لا تُكرِّر مبلغاً التقطته الصيغة اللاحقة
        spans.append((m.start(), m.end(), v))
    spans.sort()
    return spans


def _check_evidence_body_numeric_consistency(dr: dict) -> list[dict]:
    """قارن قيمة الواردات المسجَّلة في سجل الأدلة (DataPoint خام في findings
    البعثات) برقم الواردات المذكور في متن التقرير — تعارضٌ حقيقي (>٣×) بلا
    تفسيرٍ في نافذة محلية حول الرقم (لا كامل النص) => FAIL."""
    text = (dr.get("report") or {}).get("text") or ""
    if not text:
        return []
    evidence_values = []
    for m in (dr.get("missions") or {}).values():
        for f in (m.get("findings") or []):
            v = f.get("value")
            note = str(f.get("note") or "")
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and _IMPORTS_KW_RE.search(note) \
                    and not _GROWTH_RATE_NOTE_RE.search(note):
                evidence_values.append(float(v))
    if not evidence_values:
        return []
    findings = []
    seen_pairs = set()
    for pm in _USD_AMOUNT_RE.finditer(text):
        ctx = text[max(0, pm.start() - 60):pm.end() + 60]
        if not _IMPORTS_KW_RE.search(ctx):
            continue
        amt = _usd_amount_to_float(pm.group(1), pm.group(2) or "")
        if amt is None or amt <= 0:
            continue
        if any(p in ctx for p in _RECONCILED_PHRASES):
            continue
        for ev in evidence_values:
            if ev <= 0:
                continue
            ratio = max(ev, amt) / min(ev, amt)
            if ratio > 3:
                key = (round(ev), round(amt))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                findings.append({
                    "check": "evidence_body_numeric_contradiction",
                    "repairable": False,
                    "note": (f"تناقضٌ رقميٌّ داخليّ: سجل الأدلة يسجّل قيمة "
                             f"واردات {ev:,.0f}$ بينما متن التقرير يذكر "
                             f"{amt:,.0f}$ لنفس المؤشر (نسبة {ratio:.1f}× "
                             "> 3×) بلا تفسيرٍ مجاور — يجب التصالح أو "
                             "التفسير الصريح قبل التسليم")})
                break
    return findings


# PR A §A3 (بلاغ تحليل ٧): TAM أصغر من تدفّق دولة واحدة — §3.1 يضع TAM =
# 2.09M بينما يذكر تدفّق السعودية وحدها 22.14M لنفس السوق/السنة. TAM (إجمالي
# واردات السوق) يجب أن يكون ≥ تدفّق أيّ دولةٍ واحدة إليه تعريفاً؛ تدفّقٌ مفردٌ
# يتجاوز TAM المذكورة = تعارضٌ منطقيّ (تباين منظور استيراد↔تصدير المرآة، أو
# رمز HS ضيّق) — تعارضٌ حاجب يجب تفسيره/تصحيحه قبل التسليم. فحصٌ نصّيّ (يعمل
# على تقريرٍ مخزَّن/مُعاد التوليد) لا يعتمد على شكل حقلٍ بعينه.
_TAM_MARKER_RE = re.compile(
    r"TAM|إجمالي\s+(?:ال)?واردات|حجم\s+السوق|السوق\s+الكلّ?ي|الطلب\s+الكلّ?ي")
_FLOW_VERB_RE = re.compile(r"صادرات|تصدير|يصدّر|تصدّر|تدفّق|واردات\s+من")
_SINGLE_COUNTRY_RE = re.compile(
    r"وحده|وحدها|دولة\s+واحدة|مورّد\s+واحد|شريك\s+واحد|السعودية|سعودي")


def _marker_min_dist(text: str, start: int, end: int, rex, lo: int,
                     hi: int) -> "int | None":
    """أقصرُ مسافةٍ من مدى الرقم [start,end] إلى أيّ تطابقٍ لـrex ضمن [lo,hi]؛
    None إن لا تطابق (يُستعمَل لنسبِ رقمٍ للمؤشّر الأقرب حين يتجاور رقمان)."""
    best: "int | None" = None
    for m in rex.finditer(text[lo:hi]):
        ms, me = m.start() + lo, m.end() + lo
        d = 0 if (ms <= end and me >= start) else min(abs(ms - end),
                                                      abs(start - me))
        best = d if best is None else min(best, d)
    return best


def _classify_market_amounts(text: str) -> tuple[list[float], list[float]]:
    """صنّف كلّ مبلغٍ دولاريّ في المتن (بأيّ صيغة، عبر `_iter_usd_amounts`) إلى:
       - tam_amounts: مبلغٌ مجاورٌ لمؤشّر إجماليِّ سوقٍ (TAM/إجمالي واردات/حجم
         السوق) ضمن نافذة ±٧٠ محرفاً.
       - flow_amounts: مبلغٌ مجاورٌ لفعل تدفّق **و**دولةٍ واحدة (صادرات … السعودية
         وحدها) — تدفّق دولةٍ مفردةٍ/مرآةٍ.
    حين يجتمع مؤشّرا TAM والتدفّق في نافذة رقمٍ واحد (رقمان متجاوران في نفس
    الجملة)، يُنسَب الرقم للمؤشّر **الأقرب** إليه لا بأولويّةٍ ثابتة — كي لا
    تُختطَف قيمةُ تدفّقٍ مفردٍ إلى دلوِ TAM لمجرّد أنّ لفظ TAM في المدى.
    مستخلَصٌ واحد يُغذّي بوابة A3 (تعارضٌ منطقيّ: TAM < تدفّق مفرد) وبوابة تباين
    المرآة (سرد الانكماش) — مصدر تصنيفٍ واحد لا نسختان."""
    tam_amounts: list[float] = []
    flow_amounts: list[float] = []
    for start, end, amt in _iter_usd_amounts(text):
        lo, hi = max(0, start - 70), end + 70
        ctx = text[lo:hi]
        tam_hit = bool(_TAM_MARKER_RE.search(ctx))
        flow_hit = bool(_FLOW_VERB_RE.search(ctx)) and \
            bool(_SINGLE_COUNTRY_RE.search(ctx))
        if tam_hit and not flow_hit:
            tam_amounts.append(amt)
        elif flow_hit and not tam_hit:
            flow_amounts.append(amt)
        elif tam_hit and flow_hit:
            td = _marker_min_dist(text, start, end, _TAM_MARKER_RE, lo, hi)
            fds = [d for d in (
                _marker_min_dist(text, start, end, _FLOW_VERB_RE, lo, hi),
                _marker_min_dist(text, start, end, _SINGLE_COUNTRY_RE, lo, hi))
                if d is not None]
            fd = min(fds) if fds else None
            if td is not None and (fd is None or td <= fd):
                tam_amounts.append(amt)
            else:
                flow_amounts.append(amt)
    return tam_amounts, flow_amounts


def _check_tam_below_single_country_flow(dr: dict) -> list[dict]:
    """PR A §A3 — TAM مذكورة أصغر من تدفّق دولةٍ واحدة مذكور لنفس السوق."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    tam_amounts, flow_amounts = _classify_market_amounts(text)
    if not tam_amounts or not flow_amounts:
        return []
    tam = min(tam_amounts)                 # أصغر إجماليٍّ مذكور (الأكثر تحفّظاً)
    worst = max((f for f in flow_amounts if f > tam), default=None)
    if worst is None:
        return []
    return [{"check": "tam_below_single_country_flow", "repairable": False,
             "note": (f"إجمالي واردات السوق المذكور (TAM ≈ {tam:,.0f}$) أصغر "
                      f"من تدفّق دولةٍ واحدة مذكور لنفس السوق ({worst:,.0f}$) "
                      "— مستحيلٌ منطقياً (تدفّق دولةٍ واحدة ≤ إجمالي الواردات "
                      "دائماً). راجع منظور المصدر (استيراد↔مرآة تصدير) وصحّة "
                      "رمز HS، وفسّر التباين صراحةً أو صحّحه قبل التسليم")}]


# P0 (بلاغ تحليل ٧ — سردُ تباين المرآة): «انكماش ‑22.08% CAGR» محسوبٌ من سلسلة
# تصريح اليمن الجمركية التي انهار تسجيلها، ويقود السرد (تهديد رئيسي/ضعف SWOT/
# «الحفاظ على حصة في مواجهة انكماش الحجم») بينما تدفّق المرآة (تصدير السعودية
# 22.14M) يفوق التصريح المُعلَن (2.09M) ×١٠. القاعدة الكتابية: حين يفوق تدفّق
# المرآة التصريحَ مادّياً، تُعرَض القيمتان معاً، وتُسمّى المفارقة صراحةً، ولا
# يُقاد السرد بانكماشٍ مبنيٍّ على الرقم الأدنى. بوابةٌ حاجبة تُفشِل حين يجتمع
# (أ) تدفّق مرآةٍ مفردٍ يفوق إجمالي السوق المُعلَن، (ب) سردُ انكماشٍ **مؤطَّرٌ
# تهديداً/ضعفاً** (قيادةً به لا تذييلاً)، دون (ج) مصالحةٍ صريحة تسمّي أنّ
# المرآة تفوق التصريح وتعتمدها بديلاً (لا تلميح «ضعف التسجيل» عابراً وحده).
_CONTRACTION_RE = re.compile(
    r"انكماش|تقلّص|تقلص|تراجع\s+الحجم|[‑–—−-]\s*\d{1,2}(?:[.,]\d+)?\s*%")
_THREAT_FRAME_RE = re.compile(
    r"تهديد|التهديد|نقطة\s+الضعف|نقطة\s+ضعف|الضعف\b|التحدّي|التحدي|مواجهة")
# مصالحةٌ صريحة: سطرٌ يجمع منظور المرآة/التصدير بفعل اعتمادٍ/تفوّقٍ صريح — لا
# مجرّد ذكرٍ عابرٍ لـ«المرآة» ولا تلميح «ضعف التسجيل» وحده (القاعدة: قِد بالمصالحة
# لا بالانكماش). نافذةٌ قصيرة (≤٨٠ محرفاً) كي لا تقفز جملةً كاملة.
_MIRROR_RECONCILED_RE = re.compile(
    r"(?:المرآة|تصدير\s+المُصدِّر|تصدير\s+الشريك|منظور\s+التصدير)"
    r"[^\n]{0,80}?(?:يفوق|تفوق|أعلى|أكبر|نعتمد|البديل|الأقرب|الأدقّ|الأدق)")


def _check_mirror_divergence_contraction_narrative(dr: dict) -> list[dict]:
    """P0 (تحليل ٧) — سردُ انكماشٍ مؤطَّرٌ تهديداً بينما تدفّق المرآة يفوق
    التصريح المُعلَن، دون تسمية المفارقة/مصالحتها = عيبٌ حاجب. السوق قد لا
    ينكمش؛ التسجيل قد يكون هو الذي انهار — فلا يُقاد السرد بالسلسلة الأدنى."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    tam_amounts, flow_amounts = _classify_market_amounts(text)
    if not tam_amounts or not flow_amounts:
        return []
    total = min(tam_amounts)
    mirror = max((f for f in flow_amounts if f > total), default=None)
    if mirror is None:
        return []
    # (ب) قيادةٌ بالانكماش: انكماش/‑CAGR متجاورٌ لتأطير تهديدٍ/ضعفٍ (±١٢٠ محرفاً).
    led_by_contraction = any(
        _THREAT_FRAME_RE.search(text[max(0, cm.start() - 120):cm.end() + 120])
        for cm in _CONTRACTION_RE.finditer(text))
    if not led_by_contraction:
        return []
    # (ج) مصالحةٌ صريحة للمفارقة تُلغي القيادةَ بالأدنى — لا تُفشِل حينها.
    if _MIRROR_RECONCILED_RE.search(text):
        return []
    ratio = mirror / total if total else 0
    return [{"check": "mirror_divergence_contraction_narrative",
             "repairable": False,
             "note": (f"سردُ انكماشٍ (‑CAGR/«انكماش») مؤطَّرٌ تهديداً/ضعفاً بينما "
                      f"تدفّق المرآة (تصدير الشريك ≈ {mirror:,.0f}$) يفوق إجمالي "
                      f"السوق المُعلَن ({total:,.0f}$) بنحو {ratio:.0f}× — الانكماش "
                      "محسوبٌ من سلسلة تصريحٍ ضعيفةِ التسجيل. اعرض القيمتين معاً "
                      "وسمِّ مفارقة المرآة صراحةً، ولا تَقُد السرد بانكماشٍ مبنيٍّ "
                      "على الرقم الأدنى قبل مصالحة المنظورَين")}]


# تصعيدُ التقادُم (بلاغ تحليل ٧ — قاعدةٌ إضافية): آلية `silk_staleness` **تُوسِم**
# السنوات المتقادِمة (>٥ سنوات) لكنها لا تُفشِل. حين تقود سنةٌ متقادِمة استنتاجاً
# **مذكوراً** — دخل الفرد 2018 وPPP 2013 مدخلَين لاستنتاج «القدرة الشرائية تقيّد
# التسعير» — يجب أن تُفشِل لا أن تُوسَم فقط. نربط سنةً متقادِمةً (من الحقائق ذات
# القيم عبر `data_year` البنيويّ، لا نثراً) بلغةِ استنتاجٍ قوّةٍ شرائية↔تسعير في
# نافذةٍ محلية — فلا يُعلَم على ذكرٍ عابرٍ لسنةٍ قديمة بلا استنتاجٍ مبنيٍّ عليها.
_PURCHASING_POWER_RE = re.compile(
    r"القدرة\s+الشرائية|القوّة\s+الشرائية|القوة\s+الشرائية|تعادل\s+القوة|\bPPP\b")
_PRICING_CONCLUSION_RE = re.compile(
    r"تسعير|التسعير|السعر|الأسعار|تقيّد|تقيد|تحدّ|يحدّ|يقيّد")


def _stale_years_in_view(dr: dict) -> set:
    """سنوات الحقائق المتقادِمة (ذات القيم) عبر بعثات + تقاطعات المحلل — من
    الحقل البنيويّ `data_year` لا من نثر التقرير (`silk_staleness`)."""
    from silk_staleness import stale_fact_years
    findings: list = []
    for m in (dr.get("missions") or {}).values():
        findings.extend((m or {}).get("findings") or [])
    for dps in ((dr.get("analyst") or {}).get("by_category") or {}).values():
        findings.extend(dps or [])
    return stale_fact_years(findings)


def _check_stale_year_driving_conclusion(dr: dict) -> list[dict]:
    """تصعيدُ التقادُم — سنةُ حقيقةٍ أقدم من ٥ سنوات تقود استنتاجاً تسعيرياً
    مذكوراً (قوّة شرائية↔تسعير) = فشلٌ حاجب، لا مجرّد وسمٍ «الأحدث المتاح»."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    stale = _stale_years_in_view(dr)
    if not stale:
        return []
    findings: list[dict] = []
    for yr in sorted(stale):
        for m in re.finditer(rf"(?<!\d){yr}(?!\d)", text):
            win = text[max(0, m.start() - 160):m.end() + 160]
            if _PURCHASING_POWER_RE.search(win) and _PRICING_CONCLUSION_RE.search(win):
                findings.append({
                    "check": "stale_year_driving_conclusion", "repairable": False,
                    "note": (f"سنةُ بياناتٍ متقادِمة ({yr}، أقدم من ٥ سنوات) تقود "
                             "استنتاجاً مذكوراً (القدرة الشرائية تقيّد التسعير) — "
                             "بياناتٌ بهذا القِدَم لا تصلح مدخلاً حاضراً لاستنتاجٍ "
                             "تسعيريّ؛ حدِّثها أو أعلن الفجوة صراحةً، لا تُوسَم فقط")})
                break   # بندٌ واحد لكلّ سنة متقادِمة قائدة
    return findings


# بلاغ المالك (تحليل ٧ — مِجَسّ /trend الحيّ): سلسلةُ واردات اليمن السنوية
# 2018=$0.88M، 2019=$5.59M (ذروة)، 2023=$2.09M (2020–2022 بلا بيانات — انهيارُ
# التسجيل). التقريرُ ثبّت الأساسَ على ذروة 2019 فأنتج «‑22.08% انكماش»، بينما
# التثبيت على 2018 (أوّل سنةٍ مرصودة) يعطي +18.9% **نموّاً**. اختيارُ سنة الأساس
# **قلب الإشارة** فانهار سردُ «السوق المنكمش» كلّه (تهديد الغلاف/ضعف SWOT).
# القاعدة (أمر المالك): ادّعاءُ نموّ/انكماشٍ تنقلب إشارتُه بتغيير سنة الأساس
# المرصودة ضمن السلسلة نفسها = عيبٌ حاجب. حتميّ: نقرأ السلسلة من نقاط البعثات
# (كلّ سنة DataPoint من comtrade_imports بـdata_year) لا من نثر التقرير.
_DIRECTIONAL_CLAIM_RE = re.compile(
    r"انكماش|تقلّص|تقلص|نموّ|نمو|CAGR|معدّل\s+النمو|معدل\s+النمو|تراجع|توسّع|توسع")
# إفصاحٌ صحيح (§3.6): تقريرٌ يذكر «سنة الأساس» صراحةً أو يصرّح أنّ الاتجاه غير
# محسومٍ لحساسيته لسنة الأساس لم يُخفِ شيئاً — القاعدة تُفشِل التثبيتَ المُختار
# الصامت لا الإفصاحَ عن الحساسية. لا يُسكِت تحليل ٧ (لا يذكر «سنة الأساس»).
_BASE_YEAR_DISCLOSED_RE = re.compile(
    r"سنة\s+الأساس|سنةِ\s+الأساس|سنتَي\s+الأساس|حساسية[^.\n]{0,30}الأساس|"
    r"اعتماداً\s+على\s+سنة\s+الأساس|غير\s+محسوم[^.\n]{0,40}الأساس")
_IMPORT_TOTAL_NOTE_RE = re.compile(r"استيراد|واردات")
_NON_TOTAL_NOTE_RE = re.compile(r"سعر|الوزن|كميات|وحدة|price", re.I)


def _annual_import_series(dr: dict) -> dict:
    """سلسلةُ واردات السوق السنوية {سنة: قيمة} من نقاط البعثات — إجماليّاتُ
    الاستيراد فقط (لا أسعار/كميات)، بـ`data_year` بنيويّ. تشمل تقديرات المرآة
    (status=mirrored) فهي واردات أيضاً. لا تحليلَ نثر — قراءةٌ بنيوية."""
    series: dict = {}
    for m in (dr.get("missions") or {}).values():
        for f in ((m or {}).get("findings") or []):
            v = f.get("value")
            y = f.get("data_year")
            note = str(f.get("note") or "")
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if v <= 0 or not isinstance(y, int) or isinstance(y, bool):
                continue
            if not _IMPORT_TOTAL_NOTE_RE.search(note) \
                    or _NON_TOTAL_NOTE_RE.search(note):
                continue
            series[y] = float(v)   # آخر قيمةٍ لكلّ سنة إن تكرّرت
    return series


def _check_cagr_sign_flips_under_base_year(dr: dict) -> list[dict]:
    """بلاغ المالك — إشارةُ معدّل النمو المركّب تنقلب بتغيير سنة الأساس المرصودة
    في السلسلة نفسها (2018→نموّ مقابل 2019→انكماش، نفس النهاية 2023). ادّعاءُ
    اتجاهٍ واحد على سلسلةٍ كهذه يُشكِّك السردَ كلّه — عيبٌ حاجب."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text or not _DIRECTIONAL_CLAIM_RE.search(text):
        return []                       # لا ادّعاءَ اتجاهٍ في المتن — لا قلب
    if _BASE_YEAR_DISCLOSED_RE.search(text):
        return []                       # أفصح عن حساسية سنة الأساس (§3.6) — لا عقاب
    series = _annual_import_series(dr)
    if len(series) < 3:
        return []                       # نحتاج ≥٣ سنوات مرصودة لتظهر الحساسية
    years = sorted(series)
    last_y, last_v = years[-1], series[years[-1]]
    pos: list = []
    neg: list = []
    for b in years[:-1]:
        span = last_y - b
        if span <= 0:
            continue
        cagr = (last_v / series[b]) ** (1.0 / span) - 1.0
        (pos if cagr > 0 else neg if cagr < 0 else []).append((b, cagr))
    if not pos or not neg:
        return []                       # الإشارة ثابتة عبر كلّ الأسس — لا قلب
    bp, cp = max(pos, key=lambda t: t[1])   # أقوى نموّ
    bn, cn = min(neg, key=lambda t: t[1])   # أقوى انكماش
    return [{"check": "cagr_sign_flips_under_base_year", "repairable": False,
             "note": (f"إشارةُ معدّل النمو المركّب تنقلب بتغيير سنة الأساس "
                      f"المرصودة في السلسلة نفسها: أساس {bp} = {cp*100:+.1f}% "
                      f"(نموّ) مقابل أساس {bn} = {cn*100:+.1f}% (انكماش)، "
                      f"والنهايةُ {last_y}. لا يجوز بناءُ حكم/تهديدٍ على اتجاهٍ "
                      "واحدٍ مختار — ثبِّت الأساسَ على أوّل سنةٍ مرصودة، أو "
                      "اعرض كلا القراءتين صراحةً")}]


# PR B §B3 (بلاغ تحليل ٧): تلوّثُ العملة — §3.9 اقتبس سعراً بالريال العُماني
# داخل دراسةِ اليمن. الأسعار تُعرَض بالدولار (عملة الإبلاغ) أو بعملة السوق
# المرصودة؛ عملةُ دولةٍ **أخرى** (خليجية/إقليمية) في متن التقرير = تلوّثٌ
# مضلِّل. خريطةٌ محدودةٌ لأسماء العملات العربية المميِّزة → الدولة (لا «ريال»
# وحدها — مشتركةٌ بين SAR/QAR/YER/OMR؛ ولا «دينار» وحدها). USD/EUR/GBP عملاتُ
# إبلاغٍ عالمية لا تُبلَّغ. يُفحَص فقط حين تكون دولةُ السوق معروفة (لا إيجاب كاذب).
_CURRENCY_COUNTRY_PHRASES = {
    "ريال عماني": "OMN", "ريال قطري": "QAT", "ريال سعودي": "SAU",
    "ريال يمني": "YEM", "دينار كويتي": "KWT", "دينار بحريني": "BHR",
    "درهم إماراتي": "ARE", "درهم مغربي": "MAR", "دينار أردني": "JOR",
    "جنيه مصري": "EGY", "دينار عراقي": "IRQ", "ليرة تركية": "TUR",
    "دينار جزائري": "DZA", "دينار تونسي": "TUN", "دينار ليبي": "LBY",
    "ريال إيراني": "IRN", "ليرة لبنانية": "LBN",
}


# PR B §B5 (بلاغ تحليل ٧): بعثة «demand_trends» تُبلَّغ «مكتملة» (`failed=not
# findings` تَعُدّ أيّ نتيجة من أدواتها الأربع نجاحاً — faostat/openalex يُغطّيان
# غياب Trends)، بينما §3.3/§6 يعلنان بيانات Trends/الموسمية مفقودةً فتبقى ذروة
# رمضان مجهولة. «بعثةٌ بلا بيانات صالحة لغرضها لا تُعَدّ مكتملة»: نُبرِز التناقض
# ملاحظةً منهجية (لا حكماً حاجباً — كـagent_empty) حين تُعلن البعثةُ نفسها في
# ملخّصها فجوةَ الاتجاهات/الموسمية رغم عدم فشلها.
_TRENDS_GAP_RE = re.compile(
    r"(?:اتجاهات|Trends|تريندز|موسمي|رمضان|seasonal)", re.I)


def _check_trends_hollow_completion(dr: dict) -> list[dict]:
    """PR B §B5 — بعثةُ الاتجاهات/الطلب «مكتملة» لكنها تُعلن فجوةَ الاتجاهات/
    الموسمية في ملخّصها نفسه (بياناتها الأساسية غائبة) — تُبرَز لا تُخفى."""
    findings = []
    for key, m in (dr.get("missions") or {}).items():
        if not isinstance(m, dict):
            continue
        if "trend" not in key and "demand" not in key:
            continue
        if m.get("failed"):
            continue   # فشلٌ صريح محكومٌ أصلاً (agent_failed)
        summary = str(m.get("summary") or "")
        gm = _GAPS_TRIGGER_RE.search(summary) or ("فجوات" in summary)
        if gm and _TRENDS_GAP_RE.search(summary):
            findings.append({
                "check": "trends_hollow_completion", "repairable": False,
                "note": (f"بعثةُ «{_mission_label(key)}» مُبلَّغةٌ غيرَ فاشلة "
                         "لكنها تُعلن فجوةَ بيانات الاتجاهات/الموسمية في "
                         "ملخّصها — لم تُنتِج بياناتٍ صالحةً لغرضها الأساسي "
                         "(موسمية/ذروة الطلب)؛ تُقرأ كتغطيةٍ ناقصة لا اكتمالاً")})
    return findings


def _check_off_market_currency(dr: dict) -> list[dict]:
    """PR B §B3 — عملةُ دولةٍ غير سوق الدراسة (وليست عملةَ إبلاغٍ عالمية)
    مذكورةٌ في متن التقرير = تلوّثُ عملةٍ مضلِّل يجب تصحيحه قبل التسليم."""
    text = ((dr.get("report") or {}).get("text") or "")
    if not text:
        return []
    market = dr.get("market") or {}
    market_iso3 = str(market.get("iso3") or "").upper()
    if not market_iso3:
        return []   # سوق مجهول — لا فحص (تفادي إيجاب كاذب)
    findings = []
    seen: set[str] = set()
    for phrase, iso3 in _CURRENCY_COUNTRY_PHRASES.items():
        if iso3 == market_iso3:
            continue   # عملةُ السوق نفسها مشروعة (رصدٌ محليّ)
        if phrase in text and phrase not in seen:
            seen.add(phrase)
            findings.append({
                "check": "off_market_currency", "repairable": False,
                "note": (f"عملةٌ خارج السوق «{phrase}» ({iso3}) مذكورةٌ في "
                         f"دراسةِ سوقٍ مختلف ({market_iso3}) — الأسعار تُعرَض "
                         "بالدولار أو بعملة السوق المرصودة؛ صحّح العملة "
                         "المتسرّبة قبل التسليم")})
    return findings


# Master Prompt Part 2 §D — تغطية المصادر: كل مؤشرٍ يحمل مصدراً مسمّى
# حقيقياً أو وسم «تقدير استرشادي» صريح؛ عتبة القبول ≥٨٥٪. دون العتبة =
# ضيّق نطاق التقرير وأعلن الفجوة، لا تشحن مؤشرات بلا مصدر (البند ٩).
def _check_source_coverage(dr: dict) -> list[dict]:
    from silk_source_coverage import compute_source_coverage, SOURCE_COVERAGE_MIN_PCT
    cov = compute_source_coverage(dr)
    if cov["total"] == 0 or cov["pct"] >= SOURCE_COVERAGE_MIN_PCT:
        return []
    return [{
        "check": "source_coverage_below_threshold", "repairable": False,
        "note": (f"تغطية المصادر {cov['pct']:.0f}% ({cov['backed']}/"
                 f"{cov['total']} مؤشراً بمصدرٍ مسمّى) دون عتبة القبول "
                 f"{SOURCE_COVERAGE_MIN_PCT:.0f}% — ضيّق نطاق التقرير أو "
                 "أعلن الفجوة صراحةً بدل شحن مؤشرات بلا مصدرٍ مسمّى")}]


# سدّ تسريب (الطبقة ٧ — مفارقة البوابة): هذه الفحوصات مُعلَّمة repairable=True
# لأن *صنف* النتيجة يُصلَح عادة في طبقة العرض قبل أن يصل النص هنا (راجع تعليق
# الوحدة) — لكن حين تُطلِق أحدها فعلياً، فهذا يعني أن الإصلاح **فشل تحديداً في
# هذه التشغيلة**، والنص الخام وصل بالفعل إلى DOCX المُسلَّم قبل تشغيل البوابة
# (api.py._attach_quality_gate تُشغَّل بعد بناء العرض لا قبله). تخفيضها بصمت
# إلى WARN يعني أن البوابة تكتشف تسريباً فعلياً ثم تكتمه — لا يجوز أن يمرّ بحكم
# أهدأ من فشل بنيوي حقيقي (section_structure/agent_failed). ثابتٌ على مستوى
# الوحدة كي تُثبِّته الاختبارات (عقد تصعيد §8: …_excess داخله، WARN خارجه).
_REGRESSION_GUARD_FIRED = {"internal_plumbing_leak", "english_field_leak",
                           "mission_key_leak", "raw_confidence",
                           "trailing_ellipsis", "tool_use_leak",
                           "claude_mention", "env_var_leak",
                           "research_track_leak", "facts_list_leak",
                           "ops_warning_leak",
                           # §8: اختزال العملة والترقيم الإنجليزي داخل الفقرة
                           # يُفشِلان (FAIL). أدوات الربط/الأرقام المفتاحية
                           # مُدرَّجة (قرار المُشرِف): ٣–٤ WARN (خارج المجموعة)،
                           # ≥٥ FAIL (…_excess داخلها).
                           "style_currency_shorthand",
                           "style_inline_enumeration",
                           "style_connector_excess",
                           "style_repeated_key_figure_excess",
                           # البند ٥ (تدقيق «تحليل #1» DZA): وعدُ عملةٍ لم
                           # يُنجَز تحويلها بلاغٌ مضلِّل حقيقي (لا مجرّد أسلوب)
                           # — الإصلاح الفعلي في silk_render._fix_price_
                           # column_currency_label؛ ظهوره يعني فشل الإصلاح.
                           "currency_label_mismatch",
                           # PR B §B9: HHI بدقّة عشرية — يُصلَح في العرض
                           # (_fix_hhi_false_precision)؛ ظهوره = فشل الإصلاح.
                           "hhi_false_precision"}


# WP-7 §3 — النصوص النائبة الصلبة التي لا يجوز أن تبلغ **المستند النهائي
# المبني** أبداً (سطر عدم التوفّر العام مستثنى هنا: مسار التدهور المتعمَّد
# للاستدعاء المباشر؛ تسليمه عبر API محكوم بفحص القالب client_section_placeholder).
_ARTIFACT_HARD_PLACEHOLDERS = (
    "بند تقني غير قابل للعرض المباشر",
    "التفاصيل في أثر التتبع",
    "التفاصيل الكاملة في أثر التتبع",
)


def run_client_artifact_text_gate(text: str) -> list[dict]:
    """WP-7 §3 — بوابة نصّ المُنتَج النهائي: تُشغَّل على النص الكامل
    المستخرَج من مستند العميل **بعد** بنائه (docx — ومنه يُشتق الـPDF)، لا
    على القالب فقط: طبقة العرض نفسها قد تُدخِل نصاً لم يمرّ على فحوصات
    القالب. تعيد قائمة بنود؛ أي بند = رفض التسليم (RuntimeError في
    `render_client_docx`)."""
    findings: list[dict] = []
    if not text:
        return findings
    findings += _check_client_scaffold_leak(text)
    _plain = _strip_ar_diacritics(text)
    for ph in _ARTIFACT_HARD_PLACEHOLDERS:
        if _strip_ar_diacritics(ph) in _plain:
            findings.append({
                "check": "placeholder_leak", "repairable": False,
                "note": f"نصّ نائب تقني في المستند النهائي: «{ph}»"})
    # بتر «…» على مستوى السطر (نص docx المستخرَج سطرٌ لكل فقرة، لا كتل
    # منفصلة بأسطر فارغة) — الاقتباسات (»/") الخاتمة مستثناة بنيوياً لأن
    # السطر حينها لا ينتهي بالنقاط نفسها.
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">"):
            continue
        if len(s) > 25 and (s.endswith("…") or s.endswith("...")):
            findings.append({
                "check": "trailing_ellipsis", "repairable": False,
                "note": f"سطر في المستند النهائي ينتهي بنقاط حذف: "
                       f"'...{s[-40:]}'"})
    if "لا فجوة جوهرية" in text and "فجوة بيانات" in text:
        findings.append({
            "check": "gaps_closing_contradiction", "repairable": False,
            "note": "المستند النهائي يعلن «فجوة بيانات» ويطبع «لا فجوة "
                   "جوهرية» معاً — تناقض فجوات في المُنتَج المبني"})
    return findings


# البنود غير القابلة للإصلاح التي تُفشِل الحكم (FAIL لا WARN) — ثابتٌ واحد
# على مستوى الوحدة كي تُثبِّته الاختبارات وتُضاف إليه البوابات الجديدة بلا
# نسخٍ للمنطق داخل `run_quality_gate`. (البنود القابلة للإصلاح التي أفلتت
# فعلاً تُفشِل عبر `_REGRESSION_GUARD_FIRED` — مسارٌ منفصل.)
FAIL_TRIGGER_CHECKS = frozenset({
    "section_structure", "agent_failed", "analyst_layer_failed",
    "evidence_body_numeric_contradiction", "source_coverage_below_threshold",
    # §B (حزمة الفكس v2.1): بتر/إحالة معلَّقة/قسم عميل بلا محتوى فعلي.
    "orphan_short_token", "dangling_cross_reference",
    "client_section_placeholder",
    # WP-1 §4: تسمية نطاق ثقة لا تطابق رقمها = خطأ يصل وجه التقرير.
    "confidence_band_mismatch",
    # WP-2 §6: سقالة «إذن ماذا»/نصّ نائب تقني/بتر «…» غير اقتباسي.
    "client_scaffold_leak", "placeholder_leak", "trailing_ellipsis",
    # WP-4 §3: ختامٌ ينفي الفجوات بينما المتن يعلنها.
    "gaps_closing_contradiction",
    # PR A (بلاغ تحليل ٧): تعارض ثقة/تسمية حكم، وTAM أصغر من تدفّق دولة واحدة.
    "confidence_value_conflict", "verdict_label_conflict",
    "tam_below_single_country_flow",
    # P0 (تحليل ٧): سردُ انكماشٍ مبنيٍّ على سلسلةٍ ضعيفةِ التسجيل بينما المرآة
    # تفوقها، وتصعيدُ التقادُم (سنةٌ >٥ سنوات تقود استنتاجاً مذكوراً).
    "mirror_divergence_contraction_narrative",
    "stale_year_driving_conclusion",
    # بلاغ المالك (تحليل ٧): إشارةُ CAGR تنقلب بتغيير سنة الأساس المرصودة.
    "cagr_sign_flips_under_base_year",
    # PR B (بلاغ تحليل ٧): تلوّثُ عملةٍ خارج السوق يصل العميل مضلِّلاً.
    "off_market_currency",
})


def run_quality_gate(view: dict) -> dict:
    """شغّل بوابة الجودة على `view["deep_research"]` — يعيد
    {"verdict": PASS|WARN|FAIL, "findings": [...], "methodology_notes": [...]}.

    `findings`: كل بنود الفحص (قابل للإصلاح أو لا). `methodology_notes`:
    نصوص عربية جاهزة للعرض داخل قسم "منهجية البحث ونطاقه" — البنود غير
    القابلة للإصلاح فقط (القابلة للإصلاح مُصلَحة فعلاً في طبقة العرض،
    عرضها كملاحظة منهجية يكرر معلومة صحيحة الآن بلا داعٍ)."""
    dr = view.get("deep_research") if isinstance(view, dict) else None
    if not dr:
        return {"verdict": PASS, "findings": [], "methodology_notes": []}

    text = ((dr.get("report") or {}).get("text") or "")
    summaries = " ".join(str((m or {}).get("summary") or "")
                         for m in (dr.get("missions") or {}).values())
    combined_text = text + "\n" + summaries

    findings: list[dict] = []
    findings += _check_markdown_and_raw_json(combined_text)
    findings += _check_raw_confidence(combined_text)
    # ملخّصات البعثات عبارات قصيرة عمداً بلا علامة ترقيم ختامية بالاصطلاح
    # (راجع أي AgentReport.summary في المشروع) — فحص التقطيع يقتصر على نص
    # التقرير السردي الكامل (كاتب التقرير) حيث التقطيع الحقيقي مرصود فعلاً.
    findings += _check_mid_word_truncation(text)
    findings += _check_trailing_ellipsis(text)
    findings += _check_orphan_short_token(text)
    findings += _check_dangling_cross_reference(text)
    findings += _check_stray_percent_punctuation(text)
    findings += _check_entity_near_duplicates(text)
    findings += _check_confidence_band_label(text)
    findings += _check_lpi_edition_year(text)
    findings += _check_recommendation_tier_label_consistency(dr)
    findings += _check_near_duplicate_figures(text)
    findings += _check_hhi_false_precision(text)
    findings += _check_supplier_rank_contiguity(text)
    findings += _check_client_section_would_be_placeholder(dr)
    findings += _check_client_scaffold_leak(combined_text)
    findings += _check_placeholder_leak(combined_text)
    findings += _check_gaps_closing_contradiction(dr)
    findings += _check_internal_plumbing_leak(text)
    findings += _check_english_field_and_mission_key_leak(text)
    findings += _check_confidentiality_leaks(combined_text)
    findings += _check_style(text)
    findings += _check_bare_partner_codes(dr)
    findings += _check_intersection_insufficiency(dr)
    findings += _check_section_structure(dr)
    findings += _check_cagr_consistency(dr)
    findings += _check_currency_label_mismatch(dr)
    findings += _check_evidence_body_numeric_consistency(dr)
    findings += _check_source_coverage(dr)
    findings += _check_agent_health(dr)
    findings += _check_audit_coverage(dr)
    findings += _check_analyst_layer_failure(dr)
    # PR A (بلاغ تحليل ٧) — ثلاث بوابات حاجبة جديدة: تعارض قيمة الثقة (§A1)،
    # تعارض تسمية الحكم (§A2)، وTAM أصغر من تدفّق دولة واحدة (§A3).
    findings += _check_confidence_value_conflict(text)
    findings += _check_verdict_label_conflict(dr)
    findings += _check_tam_below_single_country_flow(dr)
    # P0 (تحليل ٧): سردُ تباين المرآة، وتصعيدُ التقادُم القائد لاستنتاج.
    findings += _check_mirror_divergence_contraction_narrative(dr)
    findings += _check_stale_year_driving_conclusion(dr)
    # بلاغ المالك (تحليل ٧): إشارةُ CAGR تنقلب بتغيير سنة الأساس المرصودة.
    findings += _check_cagr_sign_flips_under_base_year(dr)
    # PR B (بلاغ تحليل ٧): تلوّث عملةٍ خارج السوق (§B3، حاجب)، وبعثةُ اتجاهاتٍ
    # «مكتملة» بلا بياناتها الأساسية (§B5، ملاحظة منهجية لا حاجبة).
    findings += _check_off_market_currency(dr)
    findings += _check_trends_hollow_completion(dr)

    non_repairable = [f for f in findings if not f["repairable"]]
    guard_fired = [f for f in findings if f["check"] in _REGRESSION_GUARD_FIRED]
    severe = non_repairable + guard_fired
    if not findings:
        verdict = PASS
    elif any(f["check"] in FAIL_TRIGGER_CHECKS for f in non_repairable) \
            or guard_fired:
        verdict = FAIL
    else:
        verdict = WARN

    methodology_notes = [f["note"] for f in severe]
    return {"verdict": verdict, "findings": findings,
           "methodology_notes": methodology_notes}
