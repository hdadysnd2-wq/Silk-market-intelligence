"""القالب الموحّد لسِلك — Silk unified render template (wave 4, vision §10.1).

قالب واحد «أصل» والباقي مشتقات: `build_view()` يبني نموذج العرض القانوني
الوحيد من نتيجة المحرّك، وكل المخرجات تشتق منه:
  - اللوحة (الواجهة تستهلك `result["view"]` من الـ API)
  - نص الطرفية (`render_text` — يحل محل جسد format_result القديم)
  - أداة المطوّر Streamlit (`tools/dev_console.py` تقرأ الجدول من النموذج نفسه)
  - سطور المختصر (`view["brief"]` — القرار + الموقع التنافسي بسطرين)

المبرر (vision §10.1): خلل «التحليل الأجوف» كان خلل ربط عرض — مسارات
عرض منفصلة = فرص متعددة لنفس الخطأ؛ مسار مشترك = الخطأ يقع مرة ويُصلح مرة.

منطق عرض صرف: صفر شبكة، صفر تعديل على الأرقام — قراءة وتشكيل فقط.
"""
from __future__ import annotations

import json
import logging
import os
import re

log = logging.getLogger(__name__)


def _dp(obj: object) -> dict:
    """طبّع DataPoint/dict — normalize a DataPoint or dict to a plain dict."""
    if isinstance(obj, dict):
        return obj
    return {"value": getattr(obj, "value", None),
            "source": getattr(obj, "source", ""),
            "confidence": getattr(obj, "confidence", 0.0),
            "note": getattr(obj, "note", ""),
            "retrieved_at": getattr(obj, "retrieved_at", ""),
            "status": getattr(obj, "status", ""),
            # الحقل البنيويّ لسنة البيانات (الدرس ٣٣) يُحفَظ في التطبيع كي
            # يقرأه silk_staleness.fact_year في طبقة العرض/التصدير.
            "data_year": getattr(obj, "data_year", None),
            # HF1: قائمةُ المعرّفات الذرّية تُحفَظ في التطبيع كي يسطّحها بناءُ
            # المراجع/المنهجية فيُسنِد كلُّ مصدرٍ لرابطه (لا معرّفٌ مركّب).
            "source_ids": tuple(getattr(obj, "source_ids", ()) or ())}


def _decision(top: dict | None) -> dict:
    """القرار أولاً (vision §10.2) — verdict + confidence + one-line why."""
    if not top:
        return {"verdict": None, "confidence": None,
                "why": "لا أسواق مرتّبة — لا بيانات كافية"}
    jury = top.get("jury") or {}
    ai = jury.get("ai") or {}
    # WP-1: الحكم المعروض من المرحلة الحتمية حصراً (authoritative_verdict) —
    # ai.verdict قراءة استشارية داخلية، لم يعد يتقدّم على الحكم الحتمي.
    from silk_narrative import authoritative_verdict
    verdict, confidence = authoritative_verdict(jury)
    # أسماء أصناف الوكلاء الداخلية (TradeFlowAgent...) لا تصل وجه المستخدم —
    # تُعرَّب في المصدر هنا كي يرث كل مستهلك (نص/docx/markdown) الترجمة
    # نفسها، بدل ترقيعها في مستهلك واحد فقط (كانت docx وحدها تُعرِّبها).
    from silk_narrative import internal_ar
    gaps_ar = ", ".join(internal_ar(g) for g in jury.get("data_gaps", [])) or "لا شيء"
    why = (ai.get("reasoning")
           or f"تغطية الوكلاء {jury.get('agents_with_data', 0)}/"
              f"{jury.get('agents_total', 0)} وفجوات: {gaps_ar}")
    return {"verdict": verdict, "confidence": confidence,
            "why": (why or "")[:280], "market": top.get("country"),
            "stage": jury.get("synthesis_stage"),
            # سدّ تسريب (الطبقة ٦): تصنيف الشارة محسوب هنا — لوحة الويب
            # تستهلكه بدل حساب تصنيفها الخاص من الرمز الخام (نفس الإصلاح
            # المطبَّق على شارة البحث العميق).
            "tone": _verdict_tone(verdict)}


def _competitive_position(top: dict | None) -> dict:
    """قسم "موقعك التنافسي" — the correlation section, or an honest absence."""
    cp = (top or {}).get("competitive_position")
    if not cp or "error" in (cp or {}):
        return {"available": False,
                "note": (cp or {}).get("error")
                or "أضف بطاقة منتجك (product_card) للحصول على موقعك التنافسي"}
    feas = cp.get("feasibility_threads") or []
    best = max(feas, key=lambda f: f.get("margin_at_match_pct", -9e9),
               default=None)
    doors = (cp.get("entry_thread") or {}).get("doors") or []
    realistic = next((d for d in doors if str(d.get("assessment", ""))
                      .startswith("واقعية")), doors[0] if doors else None)
    return {
        "available": True,
        "market": (top or {}).get("country"),
        "coverage": cp.get("coverage"),
        "competitor_threads": cp.get("competitor_threads"),
        "feasibility_threads": feas,
        "entry_thread": cp.get("entry_thread"),
        "contacts_thread": cp.get("contacts_thread"),
        "nearest_beatable": best,
        "best_door": realistic,
        "note": cp.get("note"),
    }


def _brief(decision: dict, cp: dict) -> list[str]:
    """المختصر — سطران للموقع التنافسي فوق سطر القرار (vision §6, §10.4).

    P1 (طبقة السرد): رمز الحكم الآلي (CONDITIONAL-GO) والكسر العشري الخام
    وأسماء أعلام الكود (with_localprice) لا تصل وجه المستخدم — تُترجم عبر
    silk_narrative. القيم نفسها بلا تغيير.
    """
    import silk_narrative as N
    market = N.country_ar(decision.get("market"), decision.get("market"))
    lines = [f"التوصية: {N.verdict_ar(decision.get('verdict'))} — "
             f"سوق {market} (ثقة {N.confidence_phrase(decision.get('confidence'))})"]
    if cp.get("available"):
        best = cp.get("nearest_beatable")
        lines.append(
            f"أقرب منافس قابل للمنافسة: {best['competitor']} — هامشك عند "
            f"مضاهاته {best['margin_at_match_pct']}%" if best else
            "أسعار المنافسين على الرفّ لم تُجمع بعد — تتوافر مع الدراسة العميقة")
        door = cp.get("best_door")
        lines.append(f"أفضل باب دخول مرصود: {door['name']} ({door['assessment']})"
                     if door else "قنوات الدخول التفصيلية تتوافر مع الدراسة العميقة")
    else:
        lines.append(cp.get("note", ""))
    return lines


def _deep_research_brief(dr_view: dict) -> list[str]:
    """مختصر البحث العميق — القرار + أرقام حاسمة + الموقع التنافسي (الموجة ٤).

    نفس فلسفة `_brief` (§10.4: سطر جوال) لكن على شكل view["deep_research"]
    (١٢ بعثة + محلل، لا قائمة أسواق مرتّبة)."""
    from silk_narrative import authoritative_verdict, verdict_ar
    verdict = dr_view.get("verdict") or {}
    v_raw, _ = authoritative_verdict(verdict)   # WP-1: الحتمي أولاً
    v = verdict_ar(v_raw) if v_raw else "تعذّر إصدار توصية"
    market = ((dr_view.get("market") or {}).get("name_ar")
             or (dr_view.get("market") or {}).get("name_en") or "؟")
    lines = [f"التوصية: {v} — سوق {market} (بحث عميق شامل)"]
    demand = (dr_view.get("analyst") or {}).get("by_category", {}).get("demand") or []
    if demand:
        lines.append(f"الطلب الفعلي المقدَّر: {demand[0].get('value')}")
    entry_door = (dr_view.get("analyst") or {}).get("by_category", {}).get(
        "entry_door") or []
    if entry_door:
        lines.append(f"أفضل باب دخول: {entry_door[0].get('value')}")
    if dr_view.get("next_step"):
        lines.append(dr_view["next_step"])
    return lines


def _completeness(markets: list) -> dict:
    """مؤشر اكتمال الدراسة — how much of the study is OBSERVED vs. declared gaps.

    يعدّ المكوّنات المرصودة (`value is not None`) عبر كل الأسواق ويعطي نسبة
    مئوية + تفصيلاً لكل مكوّن. لا يعدّل رقماً — قراءة فقط؛ يبني ثقة المستخدم
    بإظهار «كم% من الدراسة مرصود فعلاً» بدل إيحاء زائف بالاكتمال (المبدأ
    التأسيسي: الفجوات معلنة). Pure read-only; observed/total across markets.
    """
    total = observed = 0
    by_component: dict[str, dict] = {}
    for row in markets:
        for name, c in (row.get("components") or {}).items():
            present = _dp(c).get("value") is not None
            total += 1
            observed += 1 if present else 0
            b = by_component.setdefault(name, {"observed": 0, "total": 0})
            b["total"] += 1
            b["observed"] += 1 if present else 0
    pct = round(100.0 * observed / total, 1) if total else 0.0
    if pct >= 75:
        label = "دراسة شبه مكتملة — most components observed"
    elif pct >= 40:
        label = "دراسة جزئية — الفجوات معلنة، partial with declared gaps"
    else:
        label = "بيانات ضعيفة — thin data, gaps dominate"
    return {"observed": observed, "total": total, "pct": pct,
            "gap_count": total - observed, "by_component": by_component,
            "label": label}


def _fval(f: object) -> object:
    """قيمة نتيجة — .value whether DataPoint, dict, or a plain value."""
    if isinstance(f, dict):
        return f.get("value")
    return getattr(f, "value", f)


def _real_list(x: object) -> list:
    """قيم مرصودة فقط — real values from a DataPoint-or-list field ([] if none)."""
    if x is None:
        return []
    items = x if isinstance(x, list) else [x]
    out = []
    for f in items:
        v = _fval(f)
        if v is not None:
            out.append(v)
    return out


# Wave 3.1 (تدقيق زبدة الفول السوداني/اليمن — صفوف أسعار بلا وزن): سبب غياب
# السعر/كجم لكل صفّ يُصرَّح صراحةً (وزن غير مذكور / وحدة غامضة) بدل خانة فارغة،
# وسطر الفتح الوحيد «بطاقة منتج: التكلفة/كجم» يُذكَر مرة واحدة في قسم التسعير.
_PER_KG_RE = re.compile(
    r"(?:/|\bلكل\b|\bper\b)?\s*(?:كجم|كيلو|كغ|للكيلو|kg|كيلوغرام)"
    r"|(?:كجم|كيلو|كغ|kg)\s*/?\s*(?:€|\$|£|دولار|يورو)")
_CURRENCY_RE = re.compile(r"€|\$|£|دولار|يورو|ريال|درهم|\d")
_WEIGHT_RE = re.compile(
    r"\d+\s*(?:غ|جم|جرام|غرام|كجم|كيلو|كغ|kg|g|مل|لتر|ml|l|أونصة|oz)")

PRICE_UNLOCK_LINE = ("لحساب موقعك السعري الدقيق: بطاقة منتجك (التكلفة/كجم) هي "
                     "المُدخَل الناقص الوحيد.")


def _price_row_reason(text: object) -> str:
    """سبب تعذّر حساب السعر/كجم لصفّ سعر — «» إن كان قابلاً للحساب.

    - يحوي سعراً لكل كيلوغرام/وحدة => «» (قابل للحساب).
    - سعرٌ بلا وزن مذكور => «وزن غير مذكور».
    - بلا سعر واضح أصلاً => «وحدة غامضة». حتمي، لا اختلاق."""
    s = str(text or "").strip()
    if not s:
        return "وحدة غامضة"
    if _PER_KG_RE.search(s):
        return ""  # سعر/كجم مرصود مباشرة
    has_currency = bool(_CURRENCY_RE.search(s))
    has_weight = bool(_WEIGHT_RE.search(s))
    if has_currency and has_weight:
        return ""  # سعر + وزن => قابل للاشتقاق
    if has_currency and not has_weight:
        return "وزن غير مذكور"
    return "وحدة غامضة"


def _prices(row: dict) -> list:
    """أسعار السوق المرصودة — observed retail listings (localprice layer)."""
    out = []
    for v in _real_list(row.get("localprice")):
        if isinstance(v, dict) and v.get("price") is not None:
            out.append({"title": v.get("title"), "price": v.get("price"),
                        "currency": v.get("currency"), "store": v.get("store")})
    return out


def _named_competitors(row: dict) -> list:
    """منافسون بالاسم — named-competitor candidates (web layer)."""
    out = []
    for v in _real_list(row.get("competitors_named")):
        name = (v.get("title") or v.get("name")) if isinstance(v, dict) else v
        if name:
            out.append(str(name))
    return out


def _suppliers(row: dict) -> list:
    """موردون/أعمال بالاسم — named businesses (maps/volza/explee)."""
    out = []
    for key, src in (("maps", "Google Maps"), ("volza", "Volza"),
                     ("explee", "explee")):
        for v in _real_list(row.get(key)):
            name = v.get("name") if isinstance(v, dict) else v
            if name:
                out.append({"name": str(name), "source": src})
    return out


def _culture(result: dict) -> list:
    """روابطُ بحثِ الويب الخام — raw web headlines (fallback only, links kept as citations)."""
    out = []
    for v in _real_list(result.get("websearch")):
        if isinstance(v, dict):
            title = v.get("title") or v.get("snippet")
            if title:
                out.append({"title": str(title), "link": v.get("link")})
        elif v:
            out.append({"title": str(v), "link": None})
    return out


def _consumer_culture(result: dict) -> dict:
    """ثقافةُ المستهلك المستخلَصة — Layer-3 extracted insights over the raw headlines.

    بلاغ المالك «ترسل روابط = أنت قوقل»: القسم يعرض رؤًى مبنيّة (كلود) لا روابطَ خام.
    يعيد {"insights":[{point, evidence}], "note", "raw": [عناوين للاستشهاد]}. إن غاب
    الاستخلاص (بلا مفتاح كلود) يبقى raw فقط ويُعلَن أنه لم يُحلَّل بعد — لا يُدَّعى تحليلٌ.
    """
    cc = result.get("consumer_culture")
    raw = _culture(result)
    if isinstance(cc, dict) and cc.get("insights"):
        return {"insights": _sanitize_points(cc.get("insights")),
                "note": _strip_internal_plumbing(cc.get("note", "")),
                "grounded": True, "raw": raw}
    return {"insights": [], "note": "", "grounded": False, "raw": raw}


def _t_today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ── Stage 2A: تغطية المصادر لكل قسم + ملحق الأثر — coverage & provenance ──────

_SECTION_FIELDS = {
    "market_size": ("components",),                # سيُفصَّل داخلياً
    "regulatory": ("requirements", "tariff"),
    "competitors": ("competitors", "competitors_named", "maps"),
    "pricing": ("prices", "localprice"),
    "demand": ("faostat",),
    "risk": ("risk",),
    # إصلاح مراجعة Stage 5 (ثغرة ٣): حقائق Google Trends تُحسب لقسم الاتجاه —
    # كانت «الاتجاه 0/0» بينما Trends أسهمت فعلاً لأن خط السنوات dict بلا
    # حقل value مباشر وطبقة trends كانت محسوبة على الطلب.
    "trend": ("trends",),
}


def _section_dps(row: dict, sec: str) -> list[dict]:
    """نقاط بيانات قسم واحد — the ONE fact-to-section extractor (تُستخدم في
    التغطية والبوابة معاً كي يستحيل اختلافهما)."""
    dps: list[dict] = []
    if sec == "market_size":
        comps = row.get("components") or {}
        for k in ("market_size", "saudi_position", "competition"):
            _walk_dps(comps.get(k), dps)
    elif sec == "demand":
        comps = row.get("components") or {}
        _walk_dps(comps.get("demand_capacity"), dps)
        _walk_dps(row.get("faostat"), dps)
    elif sec == "trend":
        # سلسلة الاتجاه متعدد السنوات: dict بسنوات مرصودة/فجوات — كل سنة حقيقة.
        tr = row.get("trend") or {}
        for pt in tr.get("series") or []:
            dps.append({"source": tr.get("source", "UN Comtrade"),
                        "value": pt.get("value"),
                        "note": f"سنة {pt.get('year')} من خط الاتجاه"})
        _walk_dps(row.get("trends"), dps)      # إشارة Google Trends
    elif sec == "pricing":
        for f in _SECTION_FIELDS.get(sec, ()):
            _walk_dps(row.get(f), dps)
        # إصلاح مراجعة المالك («هل الوكلاء يعملون؟»): الطبقة الحدودية المجانية
        # لوكيل pricing (border_unit_value_usd_kg من كومتريد، §4b) كانت تُحسب
        # فعلاً لكن لا تُقرأ هنا أبداً — فتُعرض «تسعير 0/0» رغم نجاح الوكيل،
        # بنفس علّة قسم trend المُصلَحة أعلاه (تعليق سطر ٢٢٢). "prices"/
        # "localprice" وحدهما (طبقة التجزئة المدفوعة) لا يكفيان على المسار
        # المجاني إذ يبقيان فارغَين بنيوياً خارج /deepen.
        research = row.get("research") or {}
        pricing_agent = (research.get("agents") or {}).get("pricing") or {}
        _walk_dps(pricing_agent.get("findings"), dps)
    else:
        for f in _SECTION_FIELDS.get(sec, ()):
            _walk_dps(row.get(f), dps)
    return dps


def _walk_dps(obj, out):
    """اجمع كل نقاط البيانات (dict أو DataPoint) — collect every datapoint-shaped node.

    إصلاح مراجعة التشغيل الحي: اكتشافات حزمة البحث (Stage 3، §4b) تحمل
    `sources[]` جمعاً لا `source` مفرداً فتغيب عن ملحق الأثر الإجمالي —
    Serper/Maps/مرآة السعودية كانت تُسهم فعلياً دون أن يظهر ذلك في الملحق.
    كل مصدر في sources[] يُسجَّل هنا مساهماً بقيمة الاكتشاف نفسها (المخطط
    يفرض sources غير فارغة فقط عند نجاح القيمة). محاولات §4b الفاشلة تبقى
    نصاً حراً في gaps[] لا نقاط بيانات مفردة — تُقرأ من قسم الفجوات مباشرة
    لا من هذا الملحق (قيد معروف، لا فشل صامتاً داخل قسمها الخاص).
    """
    if isinstance(obj, dict):
        if "source" in obj and "value" in obj:
            out.append(obj)
        elif "metric" in obj and isinstance(obj.get("sources"), list):
            for s in obj["sources"]:
                if isinstance(s, dict) and s.get("source"):
                    out.append({"source": s["source"], "value": obj.get("value"),
                               "note": obj.get("note", "")})
        for v in obj.values():
            _walk_dps(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_dps(v, out)
    elif hasattr(obj, "source") and hasattr(obj, "value"):
        out.append({"source": obj.source, "value": obj.value,
                    "note": getattr(obj, "note", "")})


def _provenance(result: dict) -> list[dict]:
    """ملحق الأثر (Stage 2A) — لكل مصدر: المحاولات، المُسهم، وأمثلة أسباب الفشل.
    لا فشل صامتاً: كل نداء فاشل يظهر هنا بملاحظته الموسومة."""
    dps: list[dict] = []
    _walk_dps(result, dps)
    by: dict[str, dict] = {}
    for d in dps:
        src = str(d.get("source") or "?")
        b = by.setdefault(src, {"source": src, "attempted": 0,
                                "contributed": 0, "failures": []})
        b["attempted"] += 1
        if d.get("value") is not None:
            b["contributed"] += 1
        elif len(b["failures"]) < 3 and d.get("note"):
            # سدّ تسريب: ملاحظة DataPoint فاشلة خام (مثل "PV.EST fetch
            # failed for CHN: HTTPSConnectionPool(...)") كانت تصل ملحق
            # الأثر — أضمن ملحق ظهوراً بنيوياً (كل DataPoint فاشل في شجرة
            # النتيجة كلها يمرّ هنا) فتُعرَّب عند الجمع لا عند كل مستهلك.
            failure = _strip_internal_plumbing(str(d.get("note")))
            b["failures"].append(failure[:140])
    return sorted(by.values(), key=lambda b: -b["contributed"])


def _section_coverage(row: dict) -> dict:
    """درجة تغطية لكل قسم — {section: {attempted, contributed, score, single_source,
    low_confidence}}. قسم بمصدر واحد فقط يُعلَّم منخفض الثقة (قاعدة 2A)."""
    out: dict[str, dict] = {}
    for section in _SECTION_FIELDS:
        dps = _section_dps(row, section)
        att = len(dps)
        con = sum(1 for d in dps if d.get("value") is not None)
        srcs = {str(d.get("source")) for d in dps if d.get("value") is not None}
        out[section] = {
            "attempted": att, "contributed": con,
            "score": round(con / att, 2) if att else 0.0,
            "single_source": len(srcs) == 1 and con > 0,
            "low_confidence": (len(srcs) <= 1),
        }
    return out


# ── Stage 2B: بوابة الخصوصية — per-section specificity gate ──────────────────
# العتبات المقترحة (قابلة للضبط): أدنى عدد حقائق سوقية حقيقية ليُعرض القسم كنثر؛
# دونها يُعرض «بيانات غير كافية» + قائمة المصادر المُحاوَلة — لا نثر عام أبداً.
SECTION_THRESHOLDS = {
    "market_size": 2,   # الحجم + (حصة أو تركّز) — رقم واحد لا يصنع قسم سوق
    "demand": 1,
    "regulatory": 2,    # بند اشتراطات + التعريفة (بند خروج عام وحده لا يكفي)
    "competitors": 2,
    "pricing": 1,
    "risk": 2,
    "trend": 2,         # سنتان على الأقل لخط اتجاه
}


def _section_status(row: dict) -> dict:
    """حالة كل قسم بعد بوابة العتبة — {section: {status, contributed, threshold,
    sources_attempted}}. status: ok | insufficient."""
    cov = _section_coverage(row)
    out: dict[str, dict] = {}
    for sec, c in cov.items():
        thr = SECTION_THRESHOLDS.get(sec, 1)
        # نفس المستخرج الواحد — يستحيل اختلاف البوابة عن التغطية (ثغرة ٣).
        dps = _section_dps(row, sec)
        attempted_sources = sorted({str(d.get("source")) for d in dps
                                    if d.get("source")})
        out[sec] = {
            "status": "ok" if c["contributed"] >= thr else "insufficient",
            "contributed": c["contributed"], "threshold": thr,
            "sources_attempted": attempted_sources,
        }
    return out


def insufficient_line(sec_ar: str, st: dict) -> str:
    """جملة النقص الوحيدة المسموح بها (2B-ب) — the only allowed insufficiency text."""
    srcs = "، ".join(st.get("sources_attempted") or []) or "لا مصادر مُحاوَلة"
    return (f"بيانات غير كافية لقسم «{sec_ar}» "
            f"({st['contributed']}/{st['threshold']} حقائق سوقية) — "
            f"المصادر المُحاوَلة: {srcs}")


# ── Stage 5: مشتقات حزمة البحث (§7) — SWOT قاعدي، شرائح، دليل مورّدين ─────────

def _rmetric(research: dict | None, agent: str, metric: str):
    """قيمة مقياس من حزمة §4b — value of a metric from the research bundle."""
    for f in ((research or {}).get("agents", {}).get(agent, {})
              .get("findings") or []):
        if f.get("metric") == metric and f.get("value") is not None:
            return f.get("value")
    return None


def _swot(research: dict | None) -> dict:
    """SWOT قاعدي (§7-5) — كل خلية من حقيقة مرصودة بدليلها؛ الفارغ يُعلَن.

    اشتقاق عرض صرف: قواعد معلنة فوق حقائق حزمة البحث — لا نثر حر ولا تخمين.
    """
    from silk_narrative import internal_ar
    S, W, O, T = [], [], [], []
    if not research or not research.get("agents"):
        return {"S": S, "W": W, "O": O, "T": T,
                "note": "يتطلب حزمة وكلاء البحث (with_research)"}
    sau = _rmetric(research, "competitor", "saudi_share_pct")
    if sau:
        S.append({"text": f"حضور سعودي قائم بحصة {sau}% من واردات السوق",
                  "evidence": f"UN Comtrade — {internal_ar('saudi_share_pct')}"})
    uv = _rmetric(research, "pricing", "border_unit_value_usd_kg")
    suv = _rmetric(research, "pricing", "saudi_border_unit_value_usd_kg")
    if uv and suv and suv <= uv:
        S.append({"text": f"سعر حدودي سعودي منافس ({suv}$ مقابل متوسط {uv}$/kg)",
                  "evidence": "UN Comtrade — قيم الوحدة"})
    for g in (research.get("agents", {}).get("pricing", {}).get("gaps") or []):
        if "بطاقة" in g or "margin" in g:
            W.append({"text": "الهامش غير محسوب — بطاقة المنتج غير مكتملة",
                      "evidence": _humanize_gap_note(g[:120])})
            break
    gate = _rmetric(research, "regulatory", "eligibility_gate")
    if gate:
        W.append({"text": "بوابة أهلية أوروبية مفتوحة (منشأة معتمدة EU 2017/625)",
                  "evidence": f"مرجع L1 — {internal_ar('eligibility_gate')}"})
    cagr = _rmetric(research, "market_size", "import_cagr_pct")
    if cagr is not None and cagr > 5:
        O.append({"text": f"واردات السوق تنمو {cagr}% سنوياً مركّباً",
                  "evidence": f"UN Comtrade — {internal_ar('import_cagr_pct')}"})
    hhi = _rmetric(research, "competitor", "hhi")
    if hhi is not None and hhi < 0.15:
        O.append({"text": f"سوق مفتّت (HHI {hhi}) — لا مورّد مهيمناً",
                  "evidence": f"UN Comtrade — {internal_ar('hhi')}"})
    rr = _rmetric(research, "consumer_demand", "ramadan_seasonality")
    if rr and "مرجّحة" in str(rr):
        O.append({"text": "موسمية رمضان/العيدين فرصة ذروة طلب",
                  "evidence": "قاعدة معلنة فوق مرجع Pew"})
    top = _rmetric(research, "competitor", "top_supplier_share_pct")
    if top is not None and top > 50:
        T.append({"text": f"مورّد مهيمن بحصة {top}% — حرب أسعار محتملة",
                  "evidence": f"UN Comtrade — {internal_ar('top_supplier_share_pct')}"})
    tariff = _rmetric(research, "regulatory", "tariff_applied_pct")
    if tariff is not None and tariff > 10:
        T.append({"text": f"تعريفة مطبّقة مرتفعة {tariff}%",
                  "evidence": f"WITS — {internal_ar('tariff_applied_pct')}"})
    fx = _rmetric(research, "risk", "fx_volatility_pct")
    if fx is not None and fx > 5:
        T.append({"text": f"تقلب عملة {fx}% (معامل اختلاف)",
                  "evidence": f"World Bank — {internal_ar('PA.NUS.FCRF')}"})
    if _rmetric(research, "risk", "critical_risk"):
        T.append({"text": "خطر سياسي حرج (WGI دون −1.5)",
                  "evidence": f"World Bank — {internal_ar('PV.EST')}"})
    return {"S": S, "W": W, "O": O, "T": T,
            "note": "خلايا مشتقة من الحقائق المتاحة — الخلية الفارغة تعني "
                    "غياب البيانات، لا سلامة الجانب"}


def _segments(research: dict | None) -> list[dict]:
    """شرائح العملاء (§7-8) — دخل × ثقافة استهلاك، بقواعد معلنة وفجوات مصرّحة."""
    if not research or not research.get("agents"):
        return []
    out = []
    gdp = _rmetric(research, "consumer_demand", "gdp_per_capita_usd")
    if gdp is not None:
        tier = ("مرتفع" if gdp > 25_000 else
                "متوسط" if gdp > 8_000 else "منخفض")
        out.append({"segment": f"شريحة الدخل: {tier}",
                    "basis": f"نصيب الفرد {round(gdp):,}$ (World Bank) — "
                             "عتبات معلنة 8k/25k"})
    ms = _rmetric(research, "consumer_demand", "muslim_share_pct")
    if ms is not None:
        out.append({"segment": f"شريحة الحلال/رمضان: {ms}% من السكان",
                    "basis": "مرجع Pew الساكن — muslim_share_pct"})
    si = _rmetric(research, "consumer_demand", "search_interest")
    if si is not None:
        out.append({"segment": f"اهتمام البحث بالمنتج: {si}/100",
                    "basis": "Google Trends — search_interest"})
    return out


def _supplier_directory(research: dict | None) -> dict:
    """دليل المورّدين (§7 بتوجيه المالك) — مرشّحون موسومون غير موثَّقين."""
    return {"saudi": _rmetric(research, "supplier", "saudi_suppliers") or [],
            "target": _rmetric(research, "supplier", "target_distributors")
                      or [],
            # بلا كسر ثقة خام ولا اسم مسار API داخلي على وجه التقرير
            # (تسريب سباكة): الشارة الثلاثية بدل "(ثقة 0.4)"، و«خدمة
            # التعميق المدفوعة» بدل "/deepen".
            "note": "مرشّحون غير موثَّقين (○ غير متحقق) — أكّدهم قبل "
                    "التعاقد؛ الترقية الموثّقة عبر خدمة التعميق المدفوعة"}


def _report_fields(rep: object) -> dict:
    """طبّع AgentReport/dict — a live AgentReport dataclass OR a dict reloaded
    from storage (json_blob)، نفس نمط `_dp` أعلاه."""
    if isinstance(rep, dict):
        return {"agent_name": rep.get("agent_name"),
               "findings": rep.get("findings") or [],
               "failed": bool(rep.get("failed")), "summary": rep.get("summary") or ""}
    return {"agent_name": getattr(rep, "agent_name", None),
           "findings": getattr(rep, "findings", None) or [],
           "failed": bool(getattr(rep, "failed", False)),
           "summary": getattr(rep, "summary", "") or ""}


_TOOL_CALLS_RE = re.compile(r"نداءات أدوات:\s*(\d+)")
_DROPPED_RE = re.compile(r"أُسقطت\s*(\d+)\s*بند")
_GAPS_RE = re.compile(r"فجوات:\s*([^|]*)")

# بلاغ منتج من المالك: التقرير المعروض للعميل كان يكشف السباكة الداخلية
# ("LLMAgent:tariffs_agreements"، وسوم استشهاد خام مثل "dp7") — كلود
# (الكاتب أو بعثة) يستشهد أحياناً حرفياً بوسوم رآها في مدخلاته الخام بدل
# تلخيصها بلغة تجارية. الإصلاح تطبيع حتمي في طبقة العرض، لا تعديل على
# الأرقام: راجع _mission_label/_strip_internal_plumbing تحت.
# `\s*` بعد النقطتين: كلود يكتب أحياناً "LLMMissionAgent: pricing_scout"
# بمسافة (تسريب مُثبَت في المختصر) — بلا `\s*` كان يفلت (تدقيق، النمط A).
_INTERNAL_AGENT_RE = re.compile(r"LLM(?:Mission)?Agent:\s*([A-Za-z_]+)")
_DP_TAG_RE = re.compile(r"\[?dp\d+\]?")
# HF2 (بلاغ أقواسٍ فارغة — تقرير قطر ٢٠٢٦-٠٧-٢٣): كان `_DP_TAG_RE` يحذف نصَّ
# الوسم «dp7» ويترك قوسَه «()» هيكلاً فارغاً («)/(»، «()»، «)///(»). العلاج
# (نفسُ مبدأ WS4: لا قوسٌ حول محذوف): احذفِ **المجموعةَ كاملةً بقوسها** أولاً،
# ثمّ الوسمَ المفردَ الباقي، ثمّ اطوِ أيَّ قوسٍ فارغٍ متبقٍّ. «/／» ضمن الفواصل
# لأنّ خلايا markdown تستبدل «|»→«/» (وملء fullwidth «／»).
_DP_GROUP_RE = re.compile(
    r"[\(（]\s*\[?dp\d+\]?(?:\s*[،,;/／\s]\s*\[?dp\d+\]?)*\s*[\)）]")
_EMPTY_CITATION_GROUP_RE = re.compile(r"[\(（]\s*[/／،,;\s]*[\)）]")
# HF4.1 (تسريب سلسلةٍ إنجليزيةٍ داخلية إلى §5 — تقرير قطر): ملاحظةُ الحكم
# المبدئيّ ثنائيةُ اللغة («Preliminary only; missing sources flagged, not
# estimated. تنبيه: …») — النصفُ الإنجليزيّ داخليٌّ لا يصل العميل. يُزال
# النصفُ الإنجليزيّ فقط (يبدأ بـPreliminary وينتهي بـestimated)، والعربيّ يبقى.
_PRELIM_EN_NOTE_RE = re.compile(
    r"Preliminary[^.\n]*?(?:estimated|flagged)[^.\n]*\.\s*", re.I)
_WHOLE_JSON_RE = re.compile(r"^\s*[{\[].*[}\]]\s*$", re.S)
# بلاغ حي إنتاجي (تمور/هولندا HS080410): وصلت الواجهةَ أشكالُ JSON خام لم
# يلتقطها _WHOLE_JSON_RE المُرسَّى: (أ) سياج شيفرة "```json {...}" أو "json
# {...}"؛ (ب) JSON مضمَّن خلف بادئة نصية ("التوصية: {\"verdict\":...}")؛
# (ج) لاحقة عدّ أدوات داخلية ("... | tool calls: 2"). تُطهَّر كلها هنا.
_JSON_FENCE_RE = re.compile(r"`{3,}|(?<![A-Za-z؀-ۿ])json(?=\s*[{\[])",
                            re.I)
# الشكل الإنجليزي فقط ("| tool calls: N") — هو ما تسرَّب للعميل. الشكل
# العربي ("نداءات أدوات: N") تِلِمتري مشغّل مشروع يُحلّله _mission_trace_summary
# لعدّ نداءات الأدوات، فلا يُجرَّد هنا (بلاغ حي: تجريده صفّر العدّ في اللوحة).
_TOOL_CALLS_SUFFIX_RE = re.compile(
    r"\s*\|\s*tool calls\s*:?\s*\d+\s*$", re.I)
# تدقيق v2 (تسريب المشرف #7، متابعة مستقلّة): الشكل العربي «نداءات أدوات: N»
# (أرقام لاتينية أو عربية-هندية) تِلِمتري تتبّعٍ مشروع — يُقرأ من الملخّص الخام
# قبل العرض؛ لكنه على أسطح العميل (report.md/ask/brief) عبر `_strip_internal_plumbing`
# سباكةٌ داخلية تُسرَّب. يُجرَّد للعرض فقط، والتتبّع يقرأ الخام فلا يتصفّر عدّ اللوحة.
_AR_TOOL_CALLS_RE = re.compile(
    r"\s*[|]?\s*نداء(?:ات)?\s+أدوات?\s*[:：]\s*[0-9٠-٩۰-۹]+")
# علامات بنية JSON داخلية للنموذج — وجود أيّها يعني تسريب سباكة لا نثر عميل.
# تشمل مفاتيح الحكم بصيغتها الإنجليزية الخام وصيغتها المُعرَّبة (كان
# _EN_FIELD_RE يحوّل verdict/confidence داخل JSON مسرَّب قبل التقاطه، فيظهر
# "{\"الحكم\":...}" على الواجهة — نلتقط الصيغتين).
_INTERNAL_JSON_MARKERS = ('"datapoint_ids"', '"findings"', '"claim"',
                          '"reasoning"', '"verdict"', '"confidence"',
                          # تسريب حي مؤكَّد (المُشرِف): JSON مضمَّن بمفاتيح
                          # score/summary (مخرَج بعثة/محلل) فات علامات البنية
                          # القديمة فوصل مضمَّناً خلف بادئة نصية.
                          '"score"', '"summary"',
                          '"الحكم"', '"درجة الثقة"')

# ريبر DataPoint(...) مسرَّب في نصّ معروض (تسريب حي مؤكَّد، المُشرِف): الكاتب
# يردّد أحياناً تمثيل نقطة بيانات خاماً كما رآه في مدخلاته. المُطهِّر القديم
# كان **ينصف-يترجم** الريبر (يحوّل confidence→«درجة الثقة» ويُبقي الغلاف
# DataPoint(value=…, source=…, …)) فيخرج فرانكنشتاين. الحلّ: يُحيَّد الريبر
# **كاملاً** قبل أي ترجمة حقول — تُستخرَج القيمة المقروءة (value) أو تُعلَن
# فجوة، ولا يبقى اسم الصنف ولا أيّ حقل خام. البنية المعروفة للريبر مُثبَّتة
# (قيم مُقتبَسة تحتمل فواصل/أقواس داخلها فلا تكسر non-greedy).
_DATAPOINT_REPR_RE = re.compile(
    # مرن (بلاغ المشرف الحي): يمسك أي ريبر DataPoint يبدأ بـ value= مهما كان
    # عدد الحقول بعده أو ترتيبها — الصيغة السداسية الكاملة والمختصرة معاً.
    # علامات التنصيص داخل الحقول (بما فيها أقواس داخل note مقتبسة) مسموحة.
    r"DataPoint\(\s*value=(?P<v>'[^']*'|\"[^\"]*\"|[^,)]+?)\s*"
    r"(?:,(?:'[^']*'|\"[^\"]*\"|[^)])*)?\)")
# شبكة أمان (بلاغ المشرف): أي DataPoint(...) لم يلتقطه النمط أعلاه (ترتيب
# حقول شاذ، بلا value=) يُستبدَل كاملاً بفجوة معلنة — نصف الترجمة أسوأ من الخام.
_DATAPOINT_ANY_RE = re.compile(r"DataPoint\((?:'[^']*'|\"[^\"]*\"|[^)])*\)")
# تسريب حقول داخلية إنجليزية في نص معروض (بلاغ مالك: "verdict" و
# "confidence 0.64" وصلا جدولاً في متن تقرير العميل) — الكاتب يردّد أحياناً
# أسماء حقول رآها في مدخلاته. القيمة العشرية بعد confidence تُصاغ بشرياً
# (confidence_phrase) والوسمان يُعرَّبان؛ لا تعديل على أي رقم آخر.
# سدّ تسريب (الطبقة ٥): الفاصل الأصلي [|:：] يطابق خلية جدول ("| confidence
# | 0.64 |") لكن ليس نثراً حرّاً بفاصلة فراغ ("confidence 0.64") — الشكل
# الذي ظهر فعلياً في جواب الدردشة السياقية الحرّ (سطح جديد لهذا المُطهِّر).
_EN_CONF_VALUE_RE = re.compile(r"\bconfidence\b(\s*[|:：]?\s*)(\d?\.\d{1,4})")
# تدقيق v2 (الموجة ١، تسريب المشرف #3): الصيغة العربية الخام «ثقة=٠٫٦٤» (كلمة
# «ثقة» + أرقام عربية-هندية + فاصلة عربية ٫) كانت تنجو من `_EN_CONF_VALUE_RE`
# (إنجليزي فقط). تُلتقَط بأرقامٍ عربية أو لاتينية وتُصاغ بشرياً كنظيرتها.
_AR_DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_AR_DIGIT_FOLD.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})
_AR_CONF_RE = re.compile(
    r"ثقة\s*[=:：]\s*([0-9٠-٩۰-۹]+[.,٫][0-9٠-٩۰-۹]+)")
# تدقيق v2 (تسريب المشرف #6): بادئة مفتاح بعثة مرقّمة «m3_pricing_scout» —
# البادئة الرقمية «mN_» أمام مفتاحٍ لاتيني تُزال، فيبقى المفتاح ليُترجَم لاسمه
# العربي عبر `_map_mission_keys` (لا مفتاح داخلي مرقّم في المُسلَّم).
_MISSION_NUM_PREFIX_RE = re.compile(r"\bm\d+_(?=[a-z])")
_EN_FIELD_RE = re.compile(r"\b(verdict|confidence)\b")
_EN_FIELD_AR = {"verdict": "الحكم", "confidence": "درجة الثقة"}
# رمز حكم آلة خام (GO/WATCH/NO-GO/CONDITIONAL-GO) داخل نثر حرّ كتبه الكاتب
# نفسه (بلاغ اختبار: "الحكم WATCH — مراقبة قبل الدخول مبني على...") — لا
# حقل مُهيكَل يلتقطه verdict_ar عند مصدره هنا، فالتقاط نصّي مباشر داخل
# السرد. الأطول أولاً (CONDITIONAL-GO/NO-GO قبل GO المجرّدة) كي لا يتبقّى
# "-GO" يتيماً بعد الاستبدال.
# تسريب حي مؤكَّد (المُشرِف): رمز الحكم كان يُطابَق بالحالة الكبيرة فقط، فأيّ
# صيغة أخرى (go/Watch/no-go) تبقى خاماً في المُسلَّم. الآن حساسية-حالة مُلغاة
# (re.I) + توحيد للكبيرة عند التمرير لـ verdict_ar (يوحّدها داخلياً أصلاً).
_RAW_VERDICT_RE = re.compile(r"\b(CONDITIONAL-GO|NO-GO|GO|WATCH)\b", re.I)

# §2.6 (أمر العمل الرئيس): عبارة تُلمِّح إلى «قائمة حقائق» داخلية معطاة
# للنموذج («بين الحقائق المتاحة/المعطاة») تُعاد صياغتها بلغة موجَّهة للقارئ.
_FACTS_LIST_RE = re.compile(
    r"بين\s+الحقائق(?:\s+(?:المتاحة|المعطاة|المتوفّرة|المتوفرة))?")
# §2.7 (أمر العمل الرئيس): سرد فشل الأداة («فشل استعلام WITS مرتين بسبب
# انقطاع الاتصال») يُعاد صياغته كتصريح فجوة بيانات — الرقم لم يتوفّر من
# المصدر الرسمي وقت الإعداد، لا سرد لأعطال تقنية داخلية.
_TOOL_FAILURE_RE = re.compile(
    r"فشل\s+استعلام\s+([A-Za-z؀-ۿ/]+)[^.؛،\n]*?"
    r"(?:بسبب\s+)?(?:انقطاع|فشل|تعذّر|تعذر|توقّف|توقف)\s+الاتصال[^.؛\n]*")


_RAW_JSON_GAP = "تعذّرت قراءة هذا البند — بيانات غير مقروءة من المصدر"

# §2 (أمر العمل الرئيس — صفر ذكر لـ«كلود»/Claude في المُسلَّم): أي ذكر صريح
# للأداة يُصاغ بلغة محايدة موجَّهة للقارئ. تُطبَّق في طبقة العرض على متن
# البحث العميق (سرد/ملخّصات/حدود) فلا يصل الاسم الداخلي إلى المُسلَّم.
_CLAUDE_JSON_FAIL_RE = re.compile(r"رد\s+كلود\s+غير\s+قابل\s+للتفسير[^.،؛\n]*")
_CLAUDE_WORD_RE = re.compile(r"\bClaude\b|كلود")
# §7: كلمة (٣ أحرف فأكثر) تكرّرت فوراً — تُطوى إلى واحدة («التوصية التوصية»).
_DUP_WORD_RE = re.compile(r"(?<!\S)([^\W\d_]{3,})\s+\1(?!\S)")

# البند ١ (تدقيق «تحليل #1» DZA — silk_quality_gate.markdown_artifacts):
# أسوار كود عشوائية («```» بمحتواها الكامل) وتنسيق «**» شارد قد تتسرّب من
# مقطع مصدر مقتبَس حرفياً أو من صياغة الكاتب — تُزالان. **لا تُمَسّ** عناوين
# "## "/"### " البنيوية: هذه إلزامية (silk_ai_judge._REPORT_SECTIONS) وتُقرَأ
# عناوين Word فعلية في silk_reports._docx_deep_research (`line.startswith
# ("## ")`)، وتبقى تُبلَّغ WARN متوقَّعة في بوابة الجودة (test_quality_gate_
# stays_warn_for_ordinary_repairable_findings) — هذا الإصلاح يعالج التسريب
# الفعلي الإضافي (الأسوار/التنسيق الشارد) لا العناوين المطلوبة.
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```\n?")
_SANCTIONED_BOLD = "**ماذا يعني هذا لقرارك:**"
_STRAY_BOLD_RE = re.compile(r"\*\*([^\n*]{1,200}?)\*\*")


def _strip_stray_markdown(text: str) -> str:
    """أزل تنسيق «**» شارد خارج العبارة المرخَّصة الوحيدة (راجع تعليق الثوابت
    أعلاه) — لا يمسّ عناوين "## "/"### ". أسوار الكود («```») تُزال أبكر في
    `_strip_internal_plumbing` (قبل معالجة تسريب JSON) — راجع تعليقها هناك."""
    if not text:
        return text
    return _STRAY_BOLD_RE.sub(
        lambda m: m.group(0) if m.group(0) == _SANCTIONED_BOLD else m.group(1),
        text)


# البند ٢ (تدقيق «تحليل #1» DZA — silk_quality_gate.raw_confidence): رقم ثقة
# عربي خام «ثقة 0.x» متسرّب في السرد رغم حظر عقد الكاتب له صراحة (silk_ai_judge
# deep_report prompt) — شبكة أمان أخيرة، بنفس منطق _EN_CONF_VALUE_RE أعلاه
# للإنجليزية لكن للعربية؛ يستبدل الرقم الخام بعبارة لغوية عبر
# silk_narrative.confidence_phrase (نفس القيمة، عبارة مقروءة — لا اختلاق).
_AR_RAW_CONF_RE = re.compile(r"ثقة\s*[:=]?\s*\(?(0\.\d{1,4})\)?")


def _ar_conf_repl(m: "re.Match") -> str:
    from silk_narrative import confidence_phrase
    try:
        c = float(m.group(1))
    except ValueError:
        return m.group(0)
    return f"ثقة {confidence_phrase(c)}"


# البند ٥ (تدقيق «تحليل #1» DZA — silk_quality_gate.currency_label_mismatch):
# عمود سعر يَعِد بعملة غير التي رُصدت فعلاً ("السعر/كجم بالدولار" بينما
# الصفوف تحمل €/يورو) — وعدُ تحويلٍ لم يُجرَ، بلاغٌ حيّ حقيقي (لا تسريب
# سرّية). الإصلاح يُعنوِن العمود بالعملة **المرصودة فعلاً** بدل حذف الصفّ أو
# اختلاق تحويل (لا سعر صرف بين الحقائق).
_PRICE_HEADER_CUR_RE = re.compile(r"(السعر[^\n|]{0,20}?)(بالدولار|\bUSD\b)")
_OTHER_CUR_RELABEL = (
    ("باليورو", re.compile(r"باليورو|\bEUR\b|€|يورو")),
    ("بالجنيه الإسترليني", re.compile(r"بالجنيه|\bGBP\b|£|جنيه إسترليني")),
)


def _fix_price_column_currency_label(text: str) -> str:
    """عنوِن عمود السعر بالعملة المرصودة فعلاً في متن التقرير نفسه، لا
    باليورو/الدولار حسب الترويسة وحدها. إن لم تظهر عملة أخرى غير الموعودة في
    الترويسة، لا تغيير (لا مؤشّر مطابَق سلباً — نفس منطق الاكتشاف في
    silk_quality_gate._check_currency_label_mismatch).

    البحث عن العملة الأخرى **يقتصر على نافذة الجدول نفسه** (من الترويسة حتى
    أول سطر فارغ) — لا كامل المستند. بلاغ حي (Master Prompt Part 2، تدقيق
    عيّنة تقرير العميل): بحثٌ على كامل النص كان يُعنوِن عمود سعرٍ مطبَّعٍ
    بالدولار عمداً بـ«باليورو» لمجرّد أنّ قسماً آخر تماماً (نقاش خطر صرف
    العملة، «اليورو هو عملة السوق نفسها») يذكر اليورو — نفس مبدأ نافذة
    الجدول في silk_quality_gate._check_currency_label_mismatch (LESSONS ٤٢)
    لم يكن مطبَّقاً هنا في دالة الإصلاح الشقيقة."""
    if not text:
        return text
    m = _PRICE_HEADER_CUR_RE.search(text)
    if not m:
        return text
    block_end = text.find("\n\n", m.end())
    block_end = block_end if block_end != -1 else len(text)
    block = text[m.start():block_end]
    for label, pat in _OTHER_CUR_RELABEL:
        if pat.search(block):
            return text[:m.start()] + m.group(1) + label + text[m.end():]
    return text


def _extract_or_gap(blob: str) -> str:
    """استخرج قيمة مفتاح مقروء من تفريغ JSON، وإلا فجوة معلنة — لا JSON خام
    يُعرَض إطلاقاً. `reasoning` أولاً (تعليل الحكم المسرَّب)، ثم مفاتيح البعثة
    الشائعة (claim/summary/value/note)."""
    try:
        obj = json.loads(blob)
    except Exception:  # noqa: BLE001 — تفريغ مشوَّه أيضاً غير قابل للعرض خاماً
        return _RAW_JSON_GAP
    if isinstance(obj, dict):
        for key in ("reasoning", "claim", "summary", "value", "note"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return _RAW_JSON_GAP


def _neutralize_datapoint_repr(m: "re.Match") -> str:
    """استبدل ريبر DataPoint(...) كاملاً بقيمته المقروءة، أو فجوة معلنة إن
    كانت None/فارغة (عقد عدم الاختلاق — لا نخترع قيمة لنقطة بلا قيمة)."""
    v = m.group("v").strip()
    if (v[:1] == "'" and v[-1:] == "'") or (v[:1] == '"' and v[-1:] == '"'):
        v = v[1:-1]
    v = v.strip()
    if not v or v == "None":
        return _RAW_JSON_GAP
    return v


def _strip_raw_json_leak(text: str | None) -> str | None:
    """استبدل تفريغ JSON خام بنص عربي مقروء أو فجوة معلنة — بلاغ حي
    (بعثة risk_news أعادت `{"claim": "..."}` حرفياً كملخّص، وحكم كلود
    وصل الواجهةَ كـ`{"verdict":...}` مسيَّجاً بـ"json" أو مضمَّناً خلف بادئة).
    يعالج ثلاثة أشكال فاتت الإصدار المُرسَّى القديم: سياج شيفرة، JSON
    مضمَّن، ولاحقة عدّ أدوات داخلية. نص عادي لا يحمل بنية JSON يمر كما هو."""
    if not text:
        return text
    # (١) أزل لاحقة عدّ الأدوات الداخلية ("... | tool calls: 2").
    out = _TOOL_CALLS_SUFFIX_RE.sub("", text)
    # (٢) أزل سياج الشيفرة (```json / json) قبل كائن JSON إن وُجد.
    if _JSON_FENCE_RE.search(out):
        out = _JSON_FENCE_RE.sub("", out).strip()
    # (٣) النص كلّه JSON — استخرج قيمة مقروءة أو أعلن فجوة (السلوك القائم).
    if _WHOLE_JSON_RE.match(out):
        return _extract_or_gap(out)
    # (٤) JSON مضمَّن خلف/أمام نص، يحمل علامة بنية داخلية — استبدل مجاله
    #     { .. } بقيمة مقروءة/فجوة مع الحفاظ على أي نص عربي سليم حوله.
    if any(m in out for m in _INTERNAL_JSON_MARKERS):
        i, j = out.find("{"), out.rfind("}")
        if i != -1 and j > i:
            out = (out[:i] + _extract_or_gap(out[i:j + 1]) + out[j + 1:]).strip()
    return out


def _mission_label(key: str) -> str:
    """اسم البعثة التجاري بالعربية — نفس الاسم الذي تعرضه لوحة إعدادات
    الوكلاء (silk_missions.MISSIONS[key]['name']) بدل المفتاح snake_case
    الخام أو agent_name الداخلي ("LLMAgent:<key>")."""
    try:
        from silk_missions import MISSIONS
        row = MISSIONS.get(key)
        if row and row.get("name"):
            return row["name"]
    except Exception:  # noqa: BLE001 — تسمية تجميلية لا شرط عرض
        pass
    return key.replace("_", " ")


_MISSION_KEYS_RE = None


def _map_mission_keys(text: str) -> str:
    """استبدل أي مفتاح بعثة داخلي (snake_case) ظاهر في المتن باسمه العربي
    (§2.3) — «(consumer_culture)» → «(ثقافة المستهلك)». يُبنى النمط كسولاً
    من سجل البعثات الواحد (silk_missions.MISSIONS) فلا قائمة يدوية تتقادم؛
    فشل الاستيراد يُعيد النص كما هو (تجميلي لا شرط عرض)."""
    global _MISSION_KEYS_RE
    if _MISSION_KEYS_RE is None:
        try:
            from silk_missions import MISSIONS
            keys = sorted((k for k in MISSIONS if "_" in k), key=len,
                          reverse=True)
            _MISSION_KEYS_RE = (re.compile(
                r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b")
                if keys else re.compile(r"(?!x)x"))
        except Exception:  # noqa: BLE001
            _MISSION_KEYS_RE = re.compile(r"(?!x)x")
    return _MISSION_KEYS_RE.sub(lambda m: _mission_label(m.group(1)), text)


def _category_label(key: str) -> str:
    """اسم تقاطع المحلل الشامل التجاري بالعربية — نفس معجم
    silk_market_analyst._CATEGORY_LABELS المستعمَل في بوابة الجودة
    (silk_quality_gate._check_intersection_insufficiency)، بدل مفتاح
    إنجليزي خام ("entry_cost") في حدّ معروض للعميل."""
    try:
        from silk_market_analyst import _CATEGORY_LABELS
        if key in _CATEGORY_LABELS:
            return _CATEGORY_LABELS[key]
    except Exception:  # noqa: BLE001 — تسمية تجميلية لا شرط عرض
        pass
    return key.replace("_", " ")


def _humanize_gap_note(text: object) -> str:
    """عرّب ملاحظات الحُرّاس/الفجوات الداخلية في سطر حدّ معروض للعميل —
    تفويض للمترجم القانوني الواحد (silk_narrative.translate_gaps /
    INTERNAL_AR): العقود الإنجليزية تبقى كما هي في طبقة البيانات؛
    الترجمة للعرض فقط، لا إعادة صياغة ولا مسّ بالأرقام."""
    from silk_narrative import translate_gaps
    return translate_gaps([text])[0]


_MISSION_KEY_PREFIX_RE = re.compile(r"^([a-z][a-z_]*)(:\s*)")


def _strip_mission_key_prefix(text: str) -> str:
    """بادئة مفتاح بعثة خام أول السطر ("pricing_scout: ...") → الاسم
    التجاري العربي — بلاغ تدقيق: انهيار خيط بعثة يبني الملخّص بـ
    `f"{key}: خطأ غير متوقع: ..."` (silk_missions.py) وهذه البادئة لا
    يلتقطها `_INTERNAL_AGENT_RE` (يطابق "LLMAgent:key" فقط، لا "key:" مجرّدة)."""
    m = _MISSION_KEY_PREFIX_RE.match(text)
    if not m:
        return text
    try:
        from silk_missions import MISSIONS
        if m.group(1) in MISSIONS:
            return _mission_label(m.group(1)) + m.group(2) + text[m.end():]
    except Exception:  # noqa: BLE001 — تسمية تجميلية لا شرط عرض
        pass
    return text


# WP-2 §2 — سقالة «إذن ماذا؟» الحرفية: كانت تعليمة المحلل تفرض ختم كل بند
# بأثره فكتب النموذج العبارةَ نفسها حرفياً داخل القيم فوصلت تقارير عملاء
# مُسلَّمة («إذن ماذا؟ يجب…» ×١٠). التعليمة أُعيدت صياغتها (silk_market_
# analyst) وهذا المُنظِّف شبكة الأمان الحتمية، وبوابة الجودة تُفشِل أي بقايا.
_SO_WHAT_SCAFFOLD_RE = re.compile(
    r"[«\"'()\[]*\s*(?:إذن\s*،?\s*ماذا|So\s+what)\s*[؟?]?\s*[»\"')\]]*\s*[:،—-]*\s*",
    re.I)


def _strip_internal_plumbing(text: str | None) -> str | None:
    """أزل تسريبات السباكة الداخلية من نص معروض للعميل (تقرير مكتوب/حدود
    بحث/ملخّص بعثة) — تفريغ JSON خام كامل يُستبدَل بنص مقروء أو فجوة
    معلنة (`_strip_raw_json_leak`)، "LLMAgent:<key>"/"LLMMissionAgent:
    <key>" وبادئة "<key>: " المجرّدة تُستبدَلان باسم البعثة العربي، ووسوم
    استشهاد خام "dp7"/"[dp7]" تُحذَف، وأسماء الحقول الداخلية الإنجليزية
    (verdict/confidence مع قيمتها العشرية الخامة) تُعرَّب وتُصاغ بشرياً،
    ثم يمرّ النص على `silk_narrative.humanize_technical_note` (نقطة
    التعريب المركزية) لالتقاط أي استثناء بايثون/خطأ HTTP/قالب مصدر متبقٍّ
    لم تلتقطه الأنماط أعلاه. None/فارغ يمر كما هو."""
    if not text:
        return text
    # البند ١ (تدقيق «تحليل #1» DZA): أسوار كود عشوائية («```...```») تُزال
    # **قبل** معالجة تسريب JSON أدناه — وإلا تسبق `_strip_raw_json_leak`
    # فتُزيل أسوار الكود وحدها (فرع (٢) فيها، عام لأي سياج لا JSON فقط)
    # وتترك محتوى الكتلة الخام (مقطع مصدر مقتبَس حرفياً) عارياً كفقرة زائدة.
    text = _CODE_FENCE_RE.sub("", text)
    text = _strip_raw_json_leak(text)
    # لاحقة عدّ نداءات الأدوات العربية (تِلِمتري) — تُجرَّد لسطح العرض؛ التتبّع
    # يقرأ الخام قبل هنا (build_view) فلا يتأثّر عدّ اللوحة (تسريب المشرف #7).
    text = _AR_TOOL_CALLS_RE.sub("", text)
    # حيِّد أيّ ريبر DataPoint(...) **كاملاً** قبل ترجمة الحقول (وإلا نصف-ترجمة):
    # تُستخرَج القيمة المقروءة، أو تُعلَن فجوة إن كانت None/فارغة (لا اختلاق).
    text = _DATAPOINT_REPR_RE.sub(_neutralize_datapoint_repr, text)
    # شبكة أمان: أي DataPoint(...) شاذ نجا من النمط المرن → فجوة معلنة كاملة.
    text = _DATAPOINT_ANY_RE.sub(_RAW_JSON_GAP, text)
    text = _strip_mission_key_prefix(text)
    # تدقيق v2 (تسريب المشرف #6): بادئة «mN_» المرقّمة أمام مفتاح بعثة تُزال
    # قبل تعيين المفاتيح، فيُترجَم المفتاح الباقي لاسمه العربي أدناه.
    text = _MISSION_NUM_PREFIX_RE.sub("", text)
    text = _INTERNAL_AGENT_RE.sub(lambda m: _mission_label(m.group(1)), text)
    # HF2: احذفِ مجموعةَ الاستشهاد «(dp1، dp2)» **كاملةً** قبل الوسم المفرد —
    # وإلّا يبقى «()»/«(/)» هيكلاً فارغاً على وجه العميل (بلاغ قطر). ثمّ اطوِ
    # أيَّ قوسٍ فارغٍ متبقٍّ (نفسُ عائلة النائب الفارغ التي عالجها WS4).
    text = _DP_GROUP_RE.sub("", text)
    text = _DP_TAG_RE.sub("", text)
    text = _EMPTY_CITATION_GROUP_RE.sub("", text)
    # §٢ (تدقيق «تحليل #1» DZA): تنسيق «**» شارد + رقم ثقة عربي خام — راجع
    # تعليقات الثوابت أعلاه لماذا لا يُمَسّ "## "/"### ".
    text = _strip_stray_markdown(text)
    text = _AR_RAW_CONF_RE.sub(_ar_conf_repl, text)
    # §2.3 (أمر العمل الرئيس): مفتاح بعثة داخلي (snake_case) تسرَّب في المتن
    # أو جدول الحكم («(consumer_culture)») يُستبدَل باسمه العربي المعروض.
    text = _map_mission_keys(text)
    # §2.6: «بين الحقائق المتاحة/المعطاة» → لغة موجَّهة للقارئ.
    text = _FACTS_LIST_RE.sub("من المصادر المتاحة", text)
    # §2.7: سرد فشل الأداة → تصريح فجوة بيانات.
    text = _TOOL_FAILURE_RE.sub(
        lambda m: f"لم تتوفّر بيانات {m.group(1)} من المصدر الرسمي وقت "
                  "إعداد التقرير", text)
    # §2: لا ذكر لـ«كلود»/Claude في المُسلَّم.
    text = _CLAUDE_JSON_FAIL_RE.sub("تعذّرت قراءة بيانات هذا البند", text)
    text = _CLAUDE_WORD_RE.sub("التحليل الآلي", text)
    # HF4.1: أزِلِ النصفَ الإنجليزيَّ الداخليَّ من ملاحظة الحكم المبدئيّ (يبقى
    # نظيرُه العربيّ) — لا سلسلةٌ إنجليزيةٌ داخليةٌ تصل متن العميل (§5).
    text = _PRELIM_EN_NOTE_RE.sub("", text)
    # WP-2 §2: سقالة «إذن ماذا؟»/"So what" الحرفية (من تعليمة المحلل
    # القديمة) تُنزَع — الأثر يبقى نثراً مدمجاً؛ العنوان السقالي يُحذَف.
    text = _SO_WHAT_SCAFFOLD_RE.sub("", text)

    def _conf_value(m: "re.Match") -> str:
        from silk_narrative import confidence_phrase
        return f"درجة الثقة{m.group(1)}{confidence_phrase(float(m.group(2)))}"
    text = _EN_CONF_VALUE_RE.sub(_conf_value, text)

    def _ar_conf_value(m: "re.Match") -> str:
        from silk_narrative import confidence_phrase
        raw = m.group(1).translate(_AR_DIGIT_FOLD).replace("٫", ".").replace(",", ".")
        try:
            return f"درجة الثقة {confidence_phrase(float(raw))}"
        except ValueError:
            return "درجة الثقة"
    text = _AR_CONF_RE.sub(_ar_conf_value, text)
    text = _EN_FIELD_RE.sub(lambda m: _EN_FIELD_AR[m.group(1)], text)
    from silk_narrative import humanize_technical_note, verdict_ar
    text = _RAW_VERDICT_RE.sub(lambda m: verdict_ar(m.group(1).upper()), text)
    text = humanize_technical_note(text)
    # §7 (أمر العمل الرئيس — تصحيح التلصيقات المشوَّهة): توسيع رمز الحكم قد
    # يُنتِج تكرار كلمة فوراً («التوصية GO» → «التوصية التوصية بالدخول»).
    # اطوِ أي كلمة (٣ أحرف فأكثر) تكرّرت فوراً بعدها نفسُها.
    text = _DUP_WORD_RE.sub(r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def _sanitize_points(items: list, extra_key: str | None = None) -> list:
    """طهّر قائمة {point, evidence, [extra_key]} — استخلاصات كلود الحرّة
    (ثقافة المستهلك P1، ديناميكيات السوق P2-8) لم تكن تمرّ عبر
    `_strip_internal_plumbing` إطلاقاً رغم أنها نفس نوع النص الحرّ الذي
    قد يردّد وسماً داخلياً رآه كلود في مدخلاته (تسريب تدقيق). `evidence`
    عناوين ويب خارجية مقتبَسة حرفياً — لا تُعدَّل، ليست سباكة داخلية."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        row = dict(it)
        if "point" in row:
            row["point"] = _strip_internal_plumbing(row.get("point"))
        if extra_key and extra_key in row:
            row[extra_key] = _strip_internal_plumbing(row.get(extra_key))
        out.append(row)
    return out


def _sanitized_dynamics(dynamics: object) -> dict | None:
    """ديناميكيات السوق (P2-8، `silk_ai_judge.classify_dynamics`) مطهَّرة —
    نفس القصور الذي كان في _consumer_culture: قوائم point/evidence حرّة لم
    تمرّ عبر `_strip_internal_plumbing` قط في هذا المسار."""
    if dynamics is None:
        return None
    d = _dp(dynamics)
    v = d.get("value")
    if isinstance(v, dict):
        v = dict(v)
        for key in ("drivers", "restraints", "opportunities", "threats"):
            if key in v:
                v[key] = _sanitize_points(v.get(key))
        if "porter" in v:
            v["porter"] = _sanitize_points(v.get("porter"), extra_key="force")
        if "pestel" in v:
            v["pestel"] = _sanitize_points(v.get("pestel"), extra_key="dimension")
        if v.get("note"):
            v["note"] = _strip_internal_plumbing(v["note"])
        d = {**d, "value": v}
    return d


def _mission_trace_summary(failed: bool, summary: str) -> dict:
    """لوحة تتبّع بلمحة (الموجة ٦، §docs/TUNING.md) — حالة/نداءات أداة/
    بنود مُسقَطة/فجوات، مُستخرَجة من نص ملخّص البعثة (لا تمديد على عقد
    AgentReport — راجع تعليق التصميم في silk_llm_runtime.run_llm_agent)."""
    skipped = "معطّل" in summary
    status = "skipped" if skipped else ("failed" if failed else "succeeded")
    tool_m = _TOOL_CALLS_RE.search(summary)
    dropped_m = _DROPPED_RE.search(summary)
    gaps_m = _GAPS_RE.search(summary)
    gaps_n = len([g for g in (gaps_m.group(1).split("؛") if gaps_m else [])
                 if g.strip()])
    return {"status": status, "tool_calls": int(tool_m.group(1)) if tool_m else 0,
           "dropped": int(dropped_m.group(1)) if dropped_m else 0,
           "gaps": gaps_n}


def _reconcile_numeric_conflicts(missions: dict, hs_flagged: bool) -> list[dict]:
    """WP-3 §2 — ممرّ مصالحة رقمية قبل العرض، يعمل على بنود نموذج العرض
    (يُعدِّل الوسوم فقط — القيم لا تُمَسّ أبداً، عقد عدم الاختلاق):

    (أ) **قيمة قانونية واحدة لكل رقم**: قيمتان رقميتان كبيرتان (≥ 10000)
        متقاربتان جداً (فرق نسبي ≤ 0.5%) وغير متطابقتين — بلاغ التدقيق:
        6,733,369 مقابل 6,733,376 لواردات 2023 معاً في تقرير واحد — تُحسمان
        لقيمة قانونية (الأعلى ثقةً، فالأولى وروداً)؛ الباقي يُوسَم
        «متعارض — مستبعد» في سجلّ الأدلة، والتعارض يُفصَح عنه مرة واحدة
        (قائمة conflicts المعادة). التقارب الرقمي تقريبٌ مُعلَن لهوية
        (المؤشر، السنة) — لا مطابقة مواضيع بنيوية متاحة عبر البعثات.
    (ب) عند تعليم رمز HS (غير مؤكَّد): بند كومتريد الرقمي يُوسَم «مؤشر
        سياقي» فلا يعرض «✓ موثّق» بينما السرد نفسه يرفضه/يعيد تأطيره.
    (ج) بند جمعه وكيل بحث (وسم tool-use) تسانده قيمة مطابقة من جامعٍ رسمي
        مباشر => `corroborated` (يرفع سقف شارته — silk_narrative)."""
    from silk_narrative import RECONCILED_OUT_TAG, is_agent_gathered
    entries: list[dict] = []
    for m in missions.values():
        for f in (m.get("findings") or []):
            v = f.get("value")
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            entries.append(f)
            if hs_flagged and "comtrade" in str(f.get("source") or "").lower():
                f.setdefault("evidence_tag", "مؤشر سياقي — رمز غير مؤكَّد")
    official_vals = [float(f["value"]) for f in entries
                     if not is_agent_gathered(f.get("source"))]
    for f in entries:
        if is_agent_gathered(f.get("source")):
            fv = float(f["value"])
            if any(abs(fv - ov) <= max(abs(ov), 1.0) * 0.005
                   for ov in official_vals):
                f["corroborated"] = True
    # مراجعة شيفرة PR #147: التقارب الرقمي وحده كان يخاطر بضمّ رقمين لا
    # علاقة بينهما (واردات 6.70م$ وعدد سكان 6.72م مثلاً) فيُستبعَد رقم
    # صحيح بإفصاح تعارضٍ كاذب. تُشترط الآن **هوية سنة معلومة ومتطابقة**
    # (data_year البنيوي أو سنة صريحة في الملاحظة) — بند بلا سنة قابلة
    # للاشتقاق لا يدخل المصالحة أصلاً (تحفّظ: لا حسم بلا هوية).
    def _entry_year(f: dict) -> "int | None":
        dy = f.get("data_year")
        if isinstance(dy, int):
            return dy
        m = re.search(r"(?<!\d)(19\d\d|20\d\d)(?!\d)",
                      f"{f.get('note') or ''}")
        return int(m.group(1)) if m else None

    by_year: dict[int, list[dict]] = {}
    for f in entries:
        if abs(float(f["value"])) < 10000:
            continue
        yr = _entry_year(f)
        if yr is not None:
            by_year.setdefault(yr, []).append(f)
    conflicts: list[dict] = []
    for _yr in sorted(by_year):
        _cluster_year_group(by_year[_yr], conflicts)
    return conflicts


def _cluster_year_group(group: "list[dict]", conflicts: "list[dict]") -> None:
    """عنقدة قيم سنةٍ واحدة بالتقارب النسبي (≤0.5%) — جزء ممرّ المصالحة."""
    from silk_narrative import RECONCILED_OUT_TAG
    big = sorted(group, key=lambda f: float(f["value"]))
    i = 0
    while i < len(big):
        base = float(big[i]["value"])
        cluster = [big[i]]
        j = i + 1
        while j < len(big) and \
                abs(float(big[j]["value"]) - base) <= abs(base) * 0.005:
            cluster.append(big[j])
            j += 1
        distinct = sorted({float(f["value"]) for f in cluster})
        if len(distinct) > 1:
            canonical = max(
                cluster, key=lambda f: float(f.get("confidence") or 0.0))
            cv = float(canonical["value"])
            for f in cluster:
                if float(f["value"]) != cv:
                    f["evidence_tag"] = (
                        f"{RECONCILED_OUT_TAG} — القيمة القانونية المعتمدة "
                        f"{canonical['value']}")
            conflicts.append({
                "canonical_value": canonical["value"],
                "canonical_source": canonical.get("source"),
                "rejected_values": [v for v in distinct if v != cv],
                "note": ("رُصدت قيمتان متقاربتان غير متطابقتين لما يبدو "
                         f"المؤشر نفسه؛ اعتُمدت {canonical['value']} "
                         "(الأعلى ثقة) واستُبعد الباقي موسوماً "
                         f"«{RECONCILED_OUT_TAG}».")})
        i = j


def _mission_gap_lines(name: str, summary: str) -> list[str]:
    """فجوات بعثة معلنة داخل ملخّصها — كل بعثة، لا الفاشلة (صفر نتائج) فقط.

    بعثة قد "تنجح" (نتائج مبنية على استشهاد ≥١) وتُصرّح بفجوات جزئية داخل
    نفس الملخّص («فجوات: لا بيانات أسعار؛ لا بيانات مخاطر») — كانت هذه
    الفجوات غير مرئية لقسم «حدود التقرير» لأن التجميع القديم فحص `failed`
    فقط. إصلاح مراجعة حية: أي فجوة مُعلَنة في أي مكان يجب أن تظهر هنا.
    """
    m = _GAPS_RE.search(summary or "")
    if not m:
        return []
    return [f"{name}: {g.strip()}" for g in m.group(1).split("؛") if g.strip()]


# ── PART B1: مصالحة حدود البعثة مع الحقائق النهائية + قصّ آمن للجملة ────────
_FIRST_CLAUSE_RE = re.compile(r"^(.*?[.؟!،؛])\s")
# مواضيع فجوات البعثات القابلة للحسم بحقيقة بعثة أخرى — كل موضوع:
#   gap_keywords: كلمات تُميّز سطر الحدّ (لأيّ موضوع ينتمي).
#   need_kw_in_fact: هل يلزم أن يحمل بند الحقيقة كلمةَ الموضوع نفسها بجانب
#     الدليل (للحصص/التعريفة نعم — تفادياً لأن يحسم أيّ % عائم فجوةَ حصص).
#   evidence_re: نمط الدليل الرقمي الذي يجب أن يظهر في **بندٍ واحد**.
# تحفّظ صارم: لا يُحسَم حدٌّ إلا بدليل صريح مطابق الموضوع (عقد عدم الاختلاق).
#
# تشديد C-1 (تدقيق 2026-07-20، عائلة البند ١٢): موضوع «الأسعار» كان
# need_kw_in_fact=False بنمطٍ يلتقط **العملةَ وحدَها** (رقم+$/€/دولار)، على
# افتراضٍ خاطئ أن «العملة تُعرّف السعر بلا كلمة سعر». لكنّ العملةَ وحدَها لا
# تُميّز سعرَ تجزئةٍ عن قيمةٍ تجاريّة (حجم سوق/واردات/قيمة طلب كلّها بالعملة)،
# فبندُ «$129.6 مليون واردات» كان يحسم فجوةَ «تسعير المنافسين غير مرصود»
# كذباً — إخفاء فجوةٍ حقيقية على سطر حدٍّ للعميل. الذي يُعرّف سعرَ التجزئة هو
# **العملة + وحدةُ سعرٍ** (‏/كجم، للكيلو، €/kg)؛ فصار الدليلُ يشترط اجتماعهما.
_PRICE_MONEY = r"(?:€|\$|£|ريال|يورو|دولار|درهم)"
_PRICE_PER_UNIT = r"(?:كجم|كغم|كغ|كيلوغرام|كيلو|للكيلو|لتر|وحدة|عبوة|kg|g|l)"
_LIMIT_TOPICS = [
    {"name": "حصص",  # حصص المورّدين وتركّزهم (الحالة الحيّة: 3.39%/55.28%/HHI)
     "gap_keywords": ("حصص", "حصة", "مورّد", "مورد", "موردين", "الموردين",
                      "شركاء", "المصدّرين", "hhi", "تركّز"),
     "need_kw_in_fact": True,
     "evidence_re": re.compile(r"\d+(?:[.,]\d+)?\s*%|\bHHI\b", re.I)},
    {"name": "أسعار",  # سعر المنافسين/التجزئة = عملة **+ وحدة سعر** (لا عملة وحدها)
     "gap_keywords": ("سعر", "أسعار", "تسعير", "التجزئة"),
     "need_kw_in_fact": False,
     "evidence_re": re.compile(
         # رقم … عملة [/] وحدة  (9.96 يورو/كجم، 6.20–9.80 يورو/كغم، 3.20 دولار للكيلو)
         r"\d[\d.,–—\s-]*" + _PRICE_MONEY + r"\s*/?\s*" + _PRICE_PER_UNIT
         # عملة رقم / وحدة  (€3.49/kg، £2.40 / kg)
         + r"|" + _PRICE_MONEY + r"\s*\d[\d.,]*\s*/\s*" + _PRICE_PER_UNIT,
         re.I)},
    {"name": "تعريفة",  # التعريفة الجمركية (نسبة + كلمة تعريفة/رسوم)
     "gap_keywords": ("تعريفة", "جمرك", "رسوم", "tariff"),
     "need_kw_in_fact": True,
     "evidence_re": re.compile(r"\d+(?:[.,]\d+)?\s*%")},
]


def _first_clause(text: str, max_len: int = 180) -> str:
    """أول جملة/شبه-جملة من نصّ — يمنع تضمين ملخّص طويل (≤٧٠٠ محرف) في سطر
    حدٍّ تعيد طبقة docx قصّه عند ٣٠٠ منتصفَ جملة بـ«…». يقطع عند أول علامة
    وقف؛ وإلا عند حدّ متحفّظ بحدود الكلمة (بلا «…» وسط جملة)."""
    s = str(text or "").strip()
    if not s:
        return s
    m = _FIRST_CLAUSE_RE.match(s + " ")
    if m and len(m.group(1)) <= max_len + 40:
        return m.group(1).strip()
    if len(s) <= max_len:
        return s
    cut = s[:max_len].rsplit(" ", 1)[0].strip()
    return cut or s[:max_len].strip()


def _final_fact_texts(missions: dict, by_category: dict) -> list[str]:
    """قائمة نصوص البنود النهائية (قيمة+ملاحظة كل بند) من كل البعثات
    والتقاطعات — كل عنصر بندٌ واحد كي يُشترَط اجتماع الموضوع والرقم فيه."""
    out: list[str] = []
    for v in (missions or {}).values():
        if not isinstance(v, dict):
            continue
        for f in (v.get("findings") or []):
            out.append(f"{f.get('value')} {f.get('note') or ''}")
    for dps in (by_category or {}).values():
        for f in (dps or []):
            out.append(f"{f.get('value')} {f.get('note') or ''}")
    return out


def _topic_resolved(gap_line: str, fact_texts: list[str]) -> "str | None":
    """اسم الموضوع إن كان سطر الحدّ محسوماً بدليل رقمي فعلي، وإلا None.
    الحسم: سطر الحدّ ينتمي لموضوع، وبندُ حقيقةٍ يحمل دليل ذلك الموضوع
    (نمطه الرقمي، ومعه كلمة الموضوع حين need_kw_in_fact). متحفّظ عمداً."""
    low = gap_line.lower()
    for topic in _LIMIT_TOPICS:
        kws = topic["gap_keywords"]
        if not any(k in low for k in kws):
            continue
        for t in fact_texts:
            if not topic["evidence_re"].search(t):
                continue
            if topic["need_kw_in_fact"] and not any(k in t.lower() for k in kws):
                continue
            return topic["name"]
    return None


def _reconcile_mission_limits(lines: list[str],
                              fact_texts: list[str]) -> list[str]:
    """PART B1: أعد وسم كل سطر حدٍّ مشتقٍّ من بعثة حُسم لاحقاً بدليل رقمي
    فعلي في الحقائق النهائية «حُسمت لاحقاً: …»، وأبقِ الباقي حرفياً (لا
    إخفاء فجوة حقيقية). عقد عدم الاختلاق: لا يُحسَم إلا ما له دليل صريح."""
    out: list[str] = []
    for line in lines:
        if _topic_resolved(line, fact_texts):
            out.append(f"حُسمت لاحقاً (وردت في الحقائق المرصودة): {line}")
        else:
            out.append(line)
    return out


# تصنيف لون/تسمية شارة الحكم — مصدر واحد يستهلكه ثلاثة عارضين (لوحة
# الويب، غلاف docx، خلاصة docx التنفيذية) بدل تكرار نفس المنطق بايثون +
# JS بمعيارين قد يختلفان لنفس الرمز (سدّ تسريب الطبقة ٦: كانت لوحة الويب
# تحسب تصنيفها الخاص من رمز الحكم الإنجليزي الخام وتعرض الرمز نفسه كنص
# ظاهر — silk_reports._verdict_tone/_VERDICT_LABELS_AR كانتا نسخة موازية).
_NEGATIVE_ENTRY_HINT_RE = re.compile(
    r"(?:لا|غير|عدم|تأجيل|تجنّب|تجنب)[^\n]{0,15}دخول")
def _verdict_tone(vtxt: object) -> str:
    """تصنيف لون شارة الحكم — go (أخضر)/conditional (مشروط، أخضر مزرقّ)/
    watch (كهرماني)/nogo (أحمر)/unknown (رمادي).

    بلاغ حي (مراجعة المالك على نموذج تقرير العميل): CONDITIONAL-GO كان
    ينهار إلى tone=watch فتعرض الشارة «مراقبة السوق» بينما متن التقرير
    يقول «دخول مشروط» — تناقض على الصفحة الأولى. صار للحكم المشروط tone
    مستقل بتسميته الخاصة («دخول مشروط»، مطابقة لـsilk_narrative.VERDICT_AR)
    فتتّفق الشارة مع المتن. CONDITIONAL قبل GO (يحوي الرمز كليهما) وقبل
    WATCH (لا يحوي WATCH أصلاً)."""
    t = str(vtxt or "").upper()
    if "NO-GO" in t or "NO GO" in t:
        return "nogo"
    # «مبدئي وناقص البيانات» حكمٌ حتميٌّ صادر فعلاً، لا غيابَ حكم (بلاغ
    # المالك: كتلة SWOT تحمل توصيةً كاملة والشارة تقول «تعذّر إصدار توصية»).
    # `JuryCommittee.evaluate` تُصدر «PRELIMINARY / INCONCLUSIVE» كلّما فشل
    # وكيلٌ/بعثةٌ واحدة مع بقاء نتائج حقيقية — ولم يكن لها فرعٌ هنا فتنهار
    # إلى unknown. فشلُ نداءٍ واحد يُنقِص التغطية؛ لا يمحو الحكم.
    if "INCONCLUSIVE" in t:
        return "inconclusive"
    # PR A §A2 (بلاغ تحليل ٧): «PRELIMINARY GO» نغمةٌ مستقلّة لا تنهار إلى go —
    # وإلا عرض الغلاف «التوصية بالدخول» بينما الكاتب (verdict_ar) استلم «توصية
    # أولية بالدخول»، فتناقضت الصفحة الأولى مع المتن. قرار المالك: «توصية
    # أولية بالدخول» على كل سطح. تُفحَص قبل فرع «GO» المجرّد (الرمز يحوي GO).
    if "PRELIMINARY" in t and "GO" in t and "NO-GO" not in t and "NO GO" not in t:
        return "preliminary"
    if "CONDITIONAL" in t:
        return "conditional"
    if "WATCH" in t:
        return "watch"
    if "GO" in t:
        return "go"
    # Master Prompt Part 2 §B: بعض مسارات الحكم (نداء كلود المرحلة الثانية،
    # أو مدوّناتٌ يضبطها مستدعٍ) قد تضع التسمية **العربية** مباشرةً بدل
    # الرمز الإنجليزي (`ai["verdict"] = "دخول مشروط"` لا "CONDITIONAL-GO") —
    # كانت تنهار سابقاً إلى "unknown" فتعرض الشارة «تعذّر إصدار توصية» بينما
    # المتن/الجدول يذكران التسمية العربية الصحيحة، وهو بالضبط تناقض الشارة/
    # المتن الذي صُمِّمت هذه الدالة أصلاً لمنعه (بلاغ ٢٠٢٦-٠٧-٢١ أعلاه).
    # نفس ترتيب الفحص (الأخصّ أولاً): «مشروط» قبل «الدخول» المجرّدة لأن
    # «دخول مشروط» تحوي كلمة «دخول» أيضاً.
    s = str(vtxt or "")
    if "عدم الدخول" in s:
        return "nogo"
    if "غير محسوم" in s or "غير محسومة" in s:
        return "inconclusive"
    if "مشروط" in s:
        return "conditional"
    # PR A §A2: تسميةُ «توصية أولية بالدخول» العربية مباشرةً (مسارٌ يضع التسمية
    # بدل الرمز) — تُصنَّف preliminary لا go، فلا تنهار «أولية» فيتناقض الغلاف.
    if "أولية" in s and ("دخول" in s):
        return "preliminary"
    if "مراقبة" in s:
        return "watch"
    # مراجعة الشيفرة: «دخول» المجرّدة بلا سياق نفي تُصنَّف go افتراضياً —
    # لكن نفياً/تأجيلاً بصياغةٍ غير «عدم الدخول» الحرفية («لا يُنصح بالدخول»،
    # «تأجيل الدخول») كان سيُقلَب زوراً إلى go. نمطٌ إضافي يلتقط ألفاظ النفي
    # الشائعة قبل «دخول» ضمن نافذة قصيرة قبل الرجوع لـgo.
    if _NEGATIVE_ENTRY_HINT_RE.search(s):
        return "nogo"
    if "الدخول" in s or "دخول" in s:
        return "go"
    return "unknown"


# تسميات الحكم بالعربية مصنَّفةً بالـtone — مطابقة لـsilk_narrative.VERDICT_AR
# (المترجم القانوني الواحد): conditional=«دخول مشروط» تحديداً، لا «مراقبة
# السوق» (بلاغ مراجعة المالك: الشارة كانت تخالف المتن).
_VERDICT_LABELS_AR = {"go": "التوصية بالدخول", "conditional": "دخول مشروط",
                      # PR A §A2: حكمٌ إيجابيٌّ مبدئيّ بتغطيةٍ ناقصة — تسميةٌ
                      # مستقلّة يتّفق عليها الغلاف والكاتب (قرار المالك).
                      "preliminary": "توصية أولية بالدخول",
                      "watch": "مراقبة السوق", "nogo": "عدم الدخول حالياً",
                      # حكمٌ حتميٌّ صادر بتغطيةٍ ناقصة — ليس غيابَ حكم.
                      "inconclusive": "نتيجة مبدئية — غير محسومة",
                      # «unknown» محجوزةٌ الآن لغيابِ الحكم الحتمي كلياً فقط.
                      "unknown": "تعذّر إصدار توصية"}


# §1 (أمر العمل الرئيس): أنماط توحيد العملة — العملة بالدولار حصراً.
#   _SAR_PAREN_RE: مقابل ريالي مُقوَّس («(نحو 228.8 مليون ريال بسعر الربط 3.75)»
#     أو أي «(... ريال ...)» رقمي) — يُزال بالكامل، لا تحويل عملة في التقرير.
#   _USD_SHORT_*_RE: الاختزال «61م$» / «2.1 مليار$» → الصيغة الكاملة بالدولار.
_SAR_PAREN_RE = re.compile(
    r"\s*\((?:نحو|حوالي|قرابة|~|≈)?\s*[\d.,]+\s*(?:مليون|مليار|ألف|الف)?\s*"
    r"ريال[^)]*\)")
_USD_SHORT_MLN_RE = re.compile(r"(\d[\d.,]*)\s*م\s*\$")
_USD_SHORT_BLN_RE = re.compile(r"(\d[\d.,]*)\s*مليار\s*\$")

# §F-2 (حزمة الفكس v2.1) — بلاغ حي: كلا التعريبين «غوغل» و«قوقل» شُحنا في
# نفس التقرير (الكاتب يستعمل أيّهما بلا اتساق). تعريبٌ واحد قياسي — «قوقل»
# (المستعمَل فعلاً في كل شيفرة المشروع، silk_gmaps.py وأخواتها).
_GOOGLE_TRANSLIT_RE = re.compile(r"غوغل")

# §D-5 (حزمة الفكس v2.1) — بلاغ حي: «بنسبة .%68» (نقطة فاصلة قبل علامة
# النسبة قبل الرقم — ترتيبٌ معكوس). الصيغة الصحيحة دوماً رقمٌ ثم «%» ثم
# (اختيارياً) نقطة ختام جملة. أيّ نقطة ملاصقة لـ«%» **قبلها** بلا رقمٍ
# بينهما، أو «%» متبوعة بنقطة فرقمٍ آخر، خطأ تنسيقٍ لا صيغة شرعية.
_STRAY_PERCENT_RE = re.compile(r"\.\s*%\s*(\d+(?:\.\d+)?)")


def _fix_stray_percent_punctuation(text: str) -> str:
    """أصلح ترتيب «نقطة-نسبة-رقم» المعكوس إلى «رقم-نسبة-نقطة» الصحيح."""
    return _STRAY_PERCENT_RE.sub(r"\1%.", text)


# PR B §B9 (بلاغ تحليل ٧): HHI يُعرَض «7743.7» بدقّة عشرية مختلَقة رغم أن
# المقياس (0-10000 بعد الضرب) رقمٌ صحيح. القيمةُ المخزَّنة صحيحةٌ (نسبة 0.774
# أو عدد صحيح 7743)، لكنّ الكاتب يُعيد اشتقاقها من حصص المورّدين الخام فيُنتِج
# عشريةً وهميّة. حارس البوابة `hhi_false_precision` كان يرصدها بلا إصلاح؛ هذا
# مُصلِحُ عرضٍ حتميّ يقرّبها إلى صحيحٍ قبل وصول النص، فتُصبح النتيجةُ قابلةً
# للإصلاح فعلاً (نفس نمط `_fix_price_column_currency_label`).
_HHI_DECIMAL_FIX_RE = re.compile(r"(HHI[^0-9\n]{0,10})(\d{3,5}\.\d+)")


def _fix_hhi_false_precision(text: str) -> str:
    """قرِّب أيّ «HHI … NNNN.d» إلى عددٍ صحيح (المقياس 0-10000 لا يحمل عشرية)."""
    if not text:
        return text

    def _round(m: "re.Match") -> str:
        try:
            return m.group(1) + str(round(float(m.group(2))))
        except ValueError:
            return m.group(0)

    return _HHI_DECIMAL_FIX_RE.sub(_round, text)


# §D-1 (حزمة الفكس v2.1) — «CAGR (متوسط النمو السنوي المركب) — معدل نمو
# سنوي مركب»: الفحص القديم اكتفى بحرف "(" الفوري، ففاته شرح الكاتب بشرطة
# ("CAGR — معدل نمو سنوي مركب") فحقن تعريفاً ثانياً مكرَّراً بالمعنى فوراً.
_AR_DIACRITICS_RE = re.compile(r"[ً-ْٰ]")
_AR_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.U)


def _ar_norm_word(w: str) -> str:
    """تطبيع خفيف لمقارنة الجذر: نزع التشكيل + أداة التعريف «ال» البادئة —
    كافٍ لمطابقة «النمو» بـ«نمو» و«المركّب» بـ«مركب» بلا محلّل صرفي كامل."""
    w = _AR_DIACRITICS_RE.sub("", w)
    if w.startswith("ال") and len(w) > 4:
        w = w[2:]
    return w


def _already_explained_nearby(s: str, end: int, gloss: str) -> bool:
    """هل الكاتب شرح المصطلح فعلاً قرب أول ورود له — قوسٌ فوري (الصيغة
    القديمة الوحيدة المفحوصة) **أو** شرطة/نقطتان متبوعة بعبارة تتقاطع
    معنوياً مع تعريفنا (≥٢ جذر مشترك) — لا شكل ترقيم بعينه فقط."""
    window = s[end:end + 60]
    if window.lstrip().startswith("("):
        return True
    m = re.match(r"\s*[—\-:]\s*(.{0,50})", window)
    if not m:
        return False
    following_words = {_ar_norm_word(w) for w in _AR_WORD_RE.findall(m.group(1))}
    gloss_words = {_ar_norm_word(w) for w in _AR_WORD_RE.findall(gloss)}
    return len(following_words & gloss_words) >= 2


def _apply_merchant_language(text: "str | None") -> "tuple[str, list]":
    """B1 (SPEC-v2): نفّذ عقد لغة التاجر حتمياً على سرد التقرير في النموذج
    الواحد. يعيد (النص المشروح، قائمة المسرد) فيرثهما كل مخرَج (md/docx)
    من مصدر واحد: (١) شرح كل مصطلح تقني عند **أول** ورود بين قوسين بالعربية
    (إن لم يشرحه الكاتب)، (٢) توحيد صياغة العملة بالدولار.

    §1 (أمر العمل الرئيس — تحديث تسليم التقرير): العملة تبقى بالدولار كما
    وردت من المصادر بالضبط — **لا تحويل إلى الريال ولا أي مقابل مُقوَّس**
    (يُلغى تسييق B1 الريالي السابق). أي تسييق ريالي كتبه النموذج يُزال،
    والاختزال «م$» يُوحَّد إلى الصيغة الكاملة «مليون دولار». المسرد يُعاد
    **بنية** (لا نصّاً مُذيَّلاً) كي يعرضه كل مُصدِّر صراحةً. يشرح ويوحّد
    الصياغة فقط — لا يغيّر أي رقم ولا يخترع قيمة (عقد عدم الاختلاق)."""
    import re
    from silk_style_contract import GLOSSARY_ORDER
    s = str(text or "")
    if not s.strip():
        return s, []
    # §1: أزل أي مقابل ريالي مُقوَّس (سعر الربط) — العملة تبقى دولاراً حصراً.
    s = _SAR_PAREN_RE.sub("", s)
    # §1: وحِّد الاختزال «م$»/«مليار$» إلى الصيغة الكاملة بالدولار.
    s = _USD_SHORT_MLN_RE.sub(r"\1 مليون دولار", s)
    s = _USD_SHORT_BLN_RE.sub(r"\1 مليار دولار", s)
    # §D-5: أصلح ترتيب «نقطة-نسبة-رقم» المعكوس («بنسبة .%68» → «بنسبة 68%.»).
    s = _fix_stray_percent_punctuation(s)
    # §F-2: تعريبٌ واحد قياسي لـ«Google» — «قوقل» في كل موضع.
    s = _GOOGLE_TRANSLIT_RE.sub("قوقل", s)
    used: list = []
    for term, gloss in GLOSSARY_ORDER:
        m = re.search(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])", s)
        if not m:
            continue
        used.append((term, gloss))
        if _already_explained_nearby(s, m.end(), gloss):
            continue  # الكاتب شرحه فعلاً (قوس أو شرطة) — لا تكرار (§D-1)
        s = s[:m.end()] + f" ({gloss})" + s[m.end():]
    s = re.sub(r"[ \t]{2,}", " ", s)

    seen: dict = {}
    for term, gloss in used:
        seen.setdefault(term, gloss)
    glossary = [{"term": t, "gloss": g}
                for t, g in sorted(seen.items(), key=lambda kv: s.find(kv[0]))]
    return s, glossary


# القاعدة العامة (قرار المالك — التقادُم من المصدر لا النثر، مراجعة الشيفرة
# #1/#2/#3/#5): الآلية **الأساسية** توسيمُ سنوات الحقائق المتقادِمة المعروفة من
# البيانات (`silk_staleness.stale_fact_years`) أينما وردت، فلا تفلت أيّ صياغة
# («في 2013»/«2013م»/…) ولا يُوسَم رمزُ HS (2008) لأنه ليس سنة حقيقة. التعبير
# النمطي أدناه **شبكة أمان أخيرة** فقط: سياق بيانات صريح (بين قوسين، أو كلمة
# زمنية مسبوقة بفاصل حتى لا تُطابَق داخل كلمة مثل «الطعام» — مراجعة الشيفرة #1).
_DATA_YEAR_CTX_RE = re.compile(
    r"\((19\d\d|20\d\d)\)"                                       # بين قوسين: (2013)
    r"|(?:^|[\s(،؛:])(?:عام|سنة|لعام|منذ)\s+(19\d\d|20\d\d)(?![\d/])")  # كلمة زمنية بفاصل، بحدّ يمينيّ (لا بادئة رقمٍ أطول)
_STALE_TAG = "الأحدث المتاح"


def _stale_years_threshold() -> int:
    """سنة العتبة — أقدم من (السنة الحالية − SILK_STALE_DATA_YEARS). مصدر واحد
    عبر silk_staleness كي لا تتشعّب النافذة."""
    from silk_staleness import stale_threshold_year
    return stale_threshold_year()


# Wave 6.1 (تدقيق زبدة الفول السوداني/اليمن): حين يكون الحكم «مراقبة»/«مشروط»،
# يجب تسمية **شرطَي قلب الحكم** كحقلين مهيكلين (لا نثر حظّ): بيانات استيراد
# موثوقة تحت الرمز الصحيح (إن كان الرمز مُعلَّماً)، وموزّع محلي مؤكَّد تعاقدياً
# بالاسم. كل شرط يحمل خطوة الإغلاق التي تُقفله فتربطه خارطة الـ٩٠ يوماً. مبنيّ
# على البيانات (لا قائمة منتج صلبة) — يُستهلَك في العرض/المُصدِّرات/المختصر.
FLIP_CONDITIONS_HEADING = "شرطا قلب الحكم"


# قيم حشو تُعامَل كغياب جهة اتصال (لا تُثبِت موزّعاً مؤكَّداً — مراجعة الشيفرة #4).
_FILLER_CONTACTS = frozenset({"", "-", "—", "–", "n/a", "na", "غير متاح",
                              "غير متوفر", "لا يوجد", "none", "null"})


def _real_contact(v: object) -> bool:
    """هل قيمة جهة الاتصال حقيقية (لا حشو/شرطة/«غير متاح حالياً»)؟ — عقد عدم
    الاختلاق: شرط «موزّع مؤكَّد» لا يتحقّق بعلامة حشو. جهةٌ حقيقية = بريدٌ
    (يحوي @ ونقطة) أو هاتفٌ (٥ أرقام فأكثر)؛ فالعبارات النصّية المطوّلة مثل
    «غير متاح حالياً»/«لا يوجد رقم» تُرفَض لأنها بلا @ ولا أرقام كافية
    (مراجعة الشيفرة #4 — الرفض بالبنية لا بمطابقة نصّية حرفية)."""
    s = str(v or "").strip()
    if not s or s.lower() in _FILLER_CONTACTS:
        return False
    if "@" in s and "." in s:                          # بريد إلكتروني
        return True
    return sum(ch.isdigit() for ch in s) >= 5          # هاتف (أرقام كافية)


def _flip_conditions(verdict_tone: str, hs_flagged: bool,
                     importer_leads: dict, market_ar: str) -> list[dict]:
    """اشتقّ شرطَي قلب الحكم المهيكلين — يُفعَّل فقط للحكم watch/conditional.

    كل شرط: {condition, closes_via, met}. `met=True` حين يوجد دليل مرصود
    يُغلقه فعلاً (لا اختلاق) — موزّع بجهة اتصال مؤكَّدة => شرط الموزّع محقَّق."""
    if verdict_tone not in ("watch", "conditional"):
        return []
    conds: list[dict] = []
    if hs_flagged:
        conds.append({
            "condition": "توفّر بيانات استيراد موثوقة تحت رمز HS الصحيح",
            "closes_via": "إعادة تصنيف الرمز ثم سحب واردات كومتريد تحته",
            "met": False})
    leads = (importer_leads or {}).get("leads") or []
    has_confirmed = any(
        isinstance(l, dict)
        and (_real_contact(l.get("phone")) or _real_contact(l.get("email")))
        and (l.get("title") or l.get("name")) for l in leads)
    conds.append({
        "condition": f"التعاقد مع موزّع محلي مؤكَّد بالاسم في {market_ar or 'السوق'}",
        "closes_via": "خدمة تحقّق جهات الاتصال المدفوعة ثم عقد موزّع",
        "met": bool(has_confirmed)})
    return conds


def _has_seasonality_gap(missions: dict) -> bool:
    """هل رصدت بعثة فجوةَ موسمية (قيمة None + ملاحظة تخصّ الموسمية/رمضان)؟

    يلتقط شكلَي الملاحظة: الجديد (silk_trends_agent.SEASONALITY_GAP_CLOSURE)
    والقديم المخزَّن ('no series for seasonality of ...')."""
    for m in (missions or {}).values():
        if not isinstance(m, dict):
            continue
        for f in (m.get("findings") or []):
            d = _dp(f)
            if d.get("value") is not None:
                continue
            note = str(d.get("note") or "")
            if ("موسمي" in note or "رمضان" in note
                    or "seasonalit" in note.lower()):
                return True
    return False


# §D-2 (حزمة الفكس v2.1) — بلاغ حي: 2019/2021 وُسِمَتا «الأحدث المتاح» داخل
# فقرة سلسلتها الخاصة تمتدّ إلى 2023-2024 فعلياً («من 8% في 2019 إلى 12% في
# 2023»). سنةٌ ذُكِرت في نفس الجملة مع سنةٍ أحدث = مقارنة/مسار نمو صريح، لا
# ادّعاء أن هذه السنة القديمة هي «أحدث بيانات متاحة».
_SENTENCE_BOUND_RE = re.compile(r"[.!؟\n]")


def _year_in_growth_span(s: str, start: int, end: int, yr: int) -> bool:
    """هل تذكر جملة هذه السنة سنةً أخرى **أحدث** معها — مقارنة/مسار نمو
    صريح لا يجوز وسمه «الأحدث المتاح» (§D-2)؟"""
    lo = 0
    for m in _SENTENCE_BOUND_RE.finditer(s[:start]):
        lo = m.end()
    m2 = _SENTENCE_BOUND_RE.search(s, end)
    hi = m2.start() if m2 else len(s)
    sentence = s[lo:hi]
    for ym in re.finditer(r"(?<![\d/])(19\d\d|20\d\d)(?![\d/])", sentence):
        try:
            y2 = int(ym.group(1))
        except ValueError:
            continue
        if y2 > yr:
            return True
    return False


def _tag_stale_years(text: "str | None",
                     stale_fact_years: "set[int] | frozenset[int]" = frozenset()) -> str:
    """وسم سنوات البيانات المتقادِمة «الأحدث المتاح» — **أساسه قائمة الحقائق
    المتقادِمة** (provenance) لا تحليل النثر (قرار المالك).

    - **أساسي:** كل سنة في `stale_fact_years` (معروفة متقادِمةً من الحقائق)
      تُوسَم أينما وردت (بحدود آمنة: ليست ملاصقة لرقم/شرطة، فلا يُوسَم رمز HS
      مثل 200811 ولا لائحة 2017/625) — مستقلّةً عن الصياغة («في 2013»/«2013م»).
    - **شبكة أمان:** سنةٌ متقادِمة (≤ العتبة) في سياق بيانات صريح (قوسين/كلمة
      زمنية) حتى لو لم تُمرَّر قائمةٌ — احتياطٌ أخير محافظ.

    عقد عدم الاختلاق: إفصاح فقط، لا يغيّر رقماً؛ أول ورودٍ لكل سنة، ولا تكرار
    إن وسَمها الكاتب أصلاً (نافذة ٤٠ محرفاً)."""
    s = str(text or "")
    if not s.strip():
        return s
    threshold = _stale_years_threshold()
    # جمع كل المرشّحين (موضع، نهاية، سنة): الأساسي من القائمة + الاحتياطي النمطي.
    cands: list[tuple[int, int, int]] = []
    for yr in (stale_fact_years or set()):
        for m in re.finditer(rf"(?<![\d/]){int(yr)}(?![\d/])", s):
            cands.append((m.start(), m.end(), int(yr)))
    for m in _DATA_YEAR_CTX_RE.finditer(s):
        g = m.group(1) or m.group(2)
        try:
            y = int(g)
        except (TypeError, ValueError):
            continue
        if y <= threshold:
            cands.append((m.start(), m.end(), y))
    if not cands:
        return s
    cands.sort()
    tagged: set[int] = set()
    out: list[str] = []
    last = 0
    for start, end, yr in cands:
        if yr in tagged or end < last:
            continue
        if _year_in_growth_span(s, start, end, yr):
            continue  # §D-2: مقارنة/مسار نمو صريح مع سنةٍ أحدث في نفس الجملة
        # ضمّ لاحقة سنةٍ ميلادية/هجرية **مفردة عند حدّ كلمة فقط** (م/هـ/ـ) كي
        # لا تتدلّى بعد الوسم — دون ابتلاع أوّل حرفٍ من كلمةٍ ملاصقة مثل «مايو»
        # (مراجعة الشيفرة #4: «2013مايو» تبقى «مايو» سليمة).
        _era_end = end
        while _era_end < len(s) and s[_era_end] in "مهـ":
            _era_end += 1
        if _era_end > end and (_era_end >= len(s) or not s[_era_end].isalpha()):
            end = _era_end
        # لا تُكرِّر إن كان الوسم مكتوباً أصلاً بعد السنة (نافذة ٤٠ محرفاً).
        if _STALE_TAG in s[end:end + 40]:
            tagged.add(yr)
            continue
        tagged.add(yr)
        out.append(s[last:end])
        out.append(f" — بيانات {yr} ({_STALE_TAG})")
        last = end
    out.append(s[last:])
    return "".join(out)


def _hs_provenance(result: dict) -> dict | None:
    """إفصاحُ مصدر رمز HS حين حُسِم آلياً بالسمة الرقمية — أو `None`.

    بلاغ المُشرِف (`silk_hs_attributes`): بنودُ الترويسة الواحدة تتمايز
    بعتبةٍ رقمية، فيُقاس الرقمُ (بطاقةُ العبوة/مصدرُ ويب) بدل سؤال التاجر
    عنه. **لا يُعرَض رمزٌ حُسِم آلياً بلا ذكرِ دليله** — القارئُ يرى كيف
    اختير البندُ لا مجرّد نتيجته (نفس منطق «سطرُ مصدرٍ لكل رقم»). حسمٌ
    بمسارٍ غير معروف => `None` (لا سطرَ إفصاحٍ مختلَق)."""
    prov = result.get("hs_provenance")
    if not isinstance(prov, dict) or not prov.get("hs6"):
        return None
    src = prov.get("resolved_from")
    label = prov.get("label_ar") or prov.get("attribute") or ""
    measured, unit = prov.get("value"), (prov.get("unit") or "")
    measured_ar = "" if measured is None else f" ({label} {measured}{unit})"
    if src == "image":
        line = (f"الرمز محدَّد من صورة العبوة{measured_ar} — البند "
                f"{prov.get('hs6')} اختير بمطابقة القياس المقروء على نطاقات "
                "بنود الترويسة.")
    elif src == "web":
        line = (f"الرمز محدَّد من مصدر ويب: {prov.get('source_url') or ''}"
                f"{measured_ar} — البند {prov.get('hs6')} اختير بمطابقة هذا "
                "القياس على نطاقات بنود الترويسة (استشهادٌ ثانويّ برابط، "
                "يُراجَع قبل الاعتماد النهائي).")
    else:
        return None
    return {"resolved_from": src, "hs6": prov.get("hs6"),
            "attribute": prov.get("attribute"), "label_ar": label,
            "value": measured, "unit": unit,
            "source_url": prov.get("source_url"),
            "confidence": prov.get("confidence"), "note_ar": line}


def _deep_research_view(result: dict) -> dict | None:
    """قسم البحث العميق (الموجة ٤، V5) — إضافي بحت، لا يمسّ أي مفتاح قائم.

    **تنبيه تسمية مهم**: هذا المفتاح `view["deep_research"]` مختلف تماماً عن
    `row["research"]` الموجود أصلاً (حزمة وكلاء البحث الثمانية الحتمية،
    Stage 3 §4b) — تعمّد اختيار اسم مختلف لتفادي تصادم دلالي، لا تكرار خطأ.
    None عند غياب `result["deep_research"]` (تحليل /analyze عادي — لا أثر).
    """
    dr = result.get("deep_research")
    if not dr:
        return None
    missions = {}
    for key, rep in (dr.get("missions") or {}).items():
        f = _report_fields(rep)
        # بلاغ حي (risk_news): بعثة قد تعيد JSON خام كملخّص عند فشل تفسير
        # ردّها (silk_llm_runtime._parse_output) — يُطبَّع هنا مرة واحدة
        # فيصل نظيفاً كل مستهلك (جدول الأدلة الخام، حدود البحث، ملخّص
        # التتبّع أدناه).
        clean_summary = _strip_internal_plumbing(f["summary"])
        missions[key] = {
            "name": f["agent_name"], "failed": f["failed"],
            # الاسم التجاري العربي للبعثة — كل مستهلك (جدول docx، لوحة
            # الويب، الملحق التقني) يعرضه بدل مفتاح snake_case الخام
            # (بلاغ مالك: "pricing_scout"/"risk_news" ظهرت حرفياً للعميل).
            "label": _mission_label(key),
            "summary": clean_summary,
            # مراجعة شيفرة PR #147: نسخة سطحية لكل بند — `_dp` تعيد dict
            # المدوّنة **بالمرجع**، وممرّ المصالحة (WP-3) يكتب وسوم عرضٍ
            # (evidence_tag/corroborated) على بنود العرض؛ بلا النسخ كانت
            # الوسوم تتسرّب إلى بنود السجل الخام وتُحفَظ مع أي save_analysis
            # لاحق (مسار regenerate/enrich) — طبقة العرض لا تلمس المخزون.
            "findings": [dict(_dp(x)) for x in f["findings"]],
            # التتبّع يُستخرَج من الملخّص **الخام** (لا المُطهَّر): تِلِمتري عدّ
            # نداءات الأدوات يُجرَّد الآن من سطح العرض (LESSON 57، تسريب المشرف #7).
            "trace": _mission_trace_summary(f["failed"], f["summary"]),
        }
    analyst = dr.get("analyst") or {}
    analyst_report = _report_fields(analyst.get("report"))
    # سدّ تسريب: ملخّص المحلل الشامل نص كلود حرّ فوق نفس الحقائق المعزولة
    # التي يقرأها ملخّص كل بعثة — كان الأخير وحده يمرّ عبر التطهير، تاركاً
    # ثغرة مطابقة (نفس نوع النص، لا سبب لتمييزه).
    analyst_report = {**analyst_report,
                      "summary": _strip_internal_plumbing(analyst_report["summary"])}
    # P2: شارة أدلة ثلاثية (✓/◐/○) محسوبة هنا مرة واحدة في النموذج القانوني —
    # لا رقم ثقة خام يصل الواجهة، ولا منطق تصنيف مكرَّر في JS العميل.
    from silk_narrative import evidence_badge
    # سدّ تسريب (الطبقة ٩): ملاحظة اكتشاف المحلل الشامل تحمل أحياناً وسم
    # تقاطع خام بادئاً ("[entry_cost] تعريفة مطبّقة") — وسم تصنيف داخلي
    # للمحلل نفسه، لا معلومة تفيد القارئ (التقاطع معروف أصلاً من عنوان
    # القسم الذي يُدرَج تحته). يُزال، لا يُترجَم — تكرار لا قيمة إضافية له.
    _cat_tag_re = re.compile(
        r"^\[(?:demand|price_competitiveness|entry_cost|entry_door|swot)\]\s*")
    def _with_badge(x):
        d = _dp(x)
        note = d.get("note")
        if isinstance(note, str) and _cat_tag_re.match(note):
            note = _cat_tag_re.sub("", note)
        # H2 (تدقيق): قيمة/ملاحظة تقاطع المحلل كانتا تصلان /brief و/ask
        # خامّتين — الملخّص وحده كان يُطهَّر. تُطهَّران هنا في المصدر فيرثهما
        # كل مستهلك للـview (اللوحة، المختصر، سياق الدردشة). أرقام/غير-نصّ
        # تُترك كما هي (لا سباكة فيها).
        val = d.get("value")
        d = {**d,
             "value": _strip_internal_plumbing(val) if isinstance(val, str) else val,
             "note": _strip_internal_plumbing(note) if isinstance(note, str) else note}
        # WP-3: الشارة الواعية بالمنشأ — بند جمعه وكيل بحث يُسقَف درجةً.
        from silk_narrative import evidence_badge_for
        return {**d, "confidence_badge": evidence_badge_for(d)}
    by_category = {cat: [_with_badge(x) for x in (dps or [])]
                  for cat, dps in (analyst.get("by_category") or {}).items()}
    report_out = dr.get("report") or {}
    verdict = dr.get("verdict") or {}
    # سدّ تسريب (الطبقة ٦): تعليل حكم كلود (ai.reasoning) نص حرّ — قد يردّد
    # رمز حكم خام أو مصطلحاً داخلياً رآه في مدخلاته (نفس خطر ai.reasoning
    # المذكور في _stage2)، وكان يصل خاماً لكل من لوحة الويب وخلاصة docx
    # التنفيذية بلا أي مُطهِّر. تعقيم هنا مرة واحدة في النموذج القانوني —
    # بقية حقول verdict (الرمز الخام، الثقة) تبقى كما هي لأن تصنيف الشارة
    # (_verdict_tone) يحتاج الرمز الإنجليزي الخام تحديداً.
    if isinstance(verdict.get("ai"), dict) and verdict["ai"].get("reasoning"):
        verdict = {**verdict,
                  "ai": {**verdict["ai"],
                        "reasoning": _strip_internal_plumbing(
                            verdict["ai"]["reasoning"])}}
    # سدّ تسريب: ملاحظات المراجعة غير المحلولة نص كلود حرّ (المراجِع) —
    # كانت تصل limits وview["deep_research"]["report"] خامة تماماً؛ وسبب
    # فشل التقرير (failure_reason) يحمل تفصيل استثناء/HTTP خام متعمَّد
    # لأغراض تشخيص المطوّرين (silk_ai_judge.failure_reason) لكن كان يصل
    # العميل حرفياً بما فيه توجيه تشغيلي ("راجع سجلّات الخادم") — العقد
    # الخام يبقى في `report_out` كما هو؛ التطهير هنا للعرض فقط.
    clean_unresolved = [_strip_internal_plumbing(n)
                        for n in (report_out.get("unresolved_notes") or [])]
    clean_failure_reason = (_strip_internal_plumbing(report_out.get("failure_reason"))
                            if report_out.get("failure_reason") else "")
    # PART B1 (أمر العمل الرئيس — «حدود هذا البحث» تناقض المتن): البعثات
    # تُعلن فجواتها معزولةً وقت تشغيلها المتوازي، فتبقى فجوة بعثة «حصص
    # الموردين غير متاحة» في الحدود حتى لو رصدت بعثة المنافسين لاحقاً
    # 55.28%/HHI. مصالحة متحفّظة: سطر حدٍّ **مشتقّ من بعثة** (فاشلة أو فجوة
    # جزئية) يُعاد وسمه «حُسمت لاحقاً» فقط إن حمل موضوعاً وُجد له دليل رقمي
    # فعلي في الحقائق النهائية (نفس الموضوع + رقم في بند واحد) — وإلا يبقى
    # حرفياً (لا إخفاء فجوة حقيقية، عقد عدم الاختلاق). حدود المحلل/المراجع/
    # الفشل/HS/كلود لا تُمَسّ (ليست فجوات بعثة قابلة للحسم بحقائق بعثة أخرى).
    _fact_texts = _final_fact_texts(missions, by_category)
    # v["summary"] مُطبَّع أصلاً أعلاه (clean_summary) — لا حاجة لإعادة التنظيف.
    # سطر البعثة الفاشلة يحمل الجملة الأولى فقط من الملخّص لا الملخّص كاملاً
    # (كان ≤٧٠٠ محرفاً فتعيد طبقة docx قصّه عند ٣٠٠ منتصفَ جملة بـ"…").
    mission_limits = _reconcile_mission_limits(
        [f"فرصة {_mission_label(k)} بلا نتائج مبنية على استشهاد: "
         f"{_first_clause(v['summary'])}"
         for k, v in missions.items() if v["failed"]]
        # فجوات جزئية داخل بعثات "ناجحة" (نتائج ≥١ لكن ببنود ناقصة معلنة).
        + [g for k, v in missions.items()
           for g in _mission_gap_lines(_mission_label(k), v["summary"])],
        _fact_texts)
    limits = (mission_limits
             # سدّ تسريب (الطبقة ٩): مفتاح تقاطع خام إنجليزي ("entry_cost")
             # كان يصل حدّاً معروضاً للعميل حرفياً — الاسم التجاري العربي
             # (نفس معجم silk_market_analyst._CATEGORY_LABELS المستعمَل في
             # بوابة الجودة) يحل محله.
             + [f"تقاطع المحلل بلا أدلة كافية: {_category_label(c)}"
               for c in (analyst.get("missing_categories") or [])]
             + [f"ملاحظة مراجع لم تُعالَج: {n}" for n in clean_unresolved])
    if not report_out.get("report") and clean_failure_reason:
        limits.append(f"التقرير الكامل غائب: {clean_failure_reason}")
    if result.get("hs_resolution_note"):
        limits.append(f"تصنيف HS: {_humanize_gap_note(result['hs_resolution_note'])}")
    # وسمُ إعادة التحقّق (resume / إعادة توليد التقرير): رمزٌ أُعيد من سجلٍّ
    # سابق ولم يعد يوافق حُكمَ التصنيف اليوم — يمرّ ويُعلَن، لا يُحجَب ولا يُخفى.
    _reval = result.get("hs_revalidation")
    if isinstance(_reval, dict) and _reval.get("message"):
        limits.insert(0, _reval["message"])
    if result.get("ai_extras_note"):
        limits.append(f"تحليل إضافي: {_humanize_gap_note(result['ai_extras_note'])}")
    if verdict.get("ai_note"):
        limits.append(f"ملاحظة على التوصية: "
                      f"{_strip_internal_plumbing(verdict['ai_note'])}")
    # Wave 2.2 (تدقيق زبدة الفول السوداني/اليمن — لا بيانات موسمية من Trends):
    # فجوة الموسمية تُعلَن **مرة واحدة** في «ما لم يكتمل» مع خطوة الإغلاق
    # العملية (بحث ميداني/مقابلات موزّعين) — النمط القائم للفجوات، مُوسَّعاً.
    if _has_seasonality_gap(missions) and not any(
            "الموسمية" in l for l in limits):
        from silk_trends_agent import SEASONALITY_GAP_CLOSURE
        limits.append(SEASONALITY_GAP_CLOSURE)
    # WP-1: الحكم المعروض (الشارة/التسمية) من المرحلة الحتمية حصراً.
    from silk_narrative import authoritative_verdict
    v_raw, _ = authoritative_verdict(verdict)
    verdict_tone = _verdict_tone(v_raw)
    # Wave 1.3/3.2/4.1 (تدقيق زبدة الفول السوداني/اليمن): حين يُعلَّم رمز HS
    # غير مؤكَّد (صفة المنتج المميّزة غائبة عن وصف الرمز، silk_hs_confirm)،
    # كل رقم مشتقّ من كومتريد (حجم السوق/HHI/حصص/CAGR) يُعاد تأطيره «مؤشر
    # سياقي لا مقياس فعلي» بملاحظة **منهجية واحدة** (لا تكرار في كل قسم،
    # 4.1)، وثقة الحكم تُسقَف (1.3)، وHHI يخرج من مدخلات تسجيل الحكم (3.2).
    # العقد يُحسَب مرة إن غاب من المدوّنة (نتائج مخزّنة قديمة) — لا اختلاق:
    # confirmed=None (غير قابل للتأكيد) لا يُعامَل تعليماً.
    from silk_hs_confirm import (confirm_hs, is_flagged, CONTEXTUAL_TAG,
                                 cap_confidence_for_flagged_hs)
    hs_conf = result.get("hs_confirmation")
    if not isinstance(hs_conf, dict) and result.get("hs_code"):
        try:
            hs_conf = confirm_hs(str(result.get("product") or ""),
                                 str(result.get("hs_code") or ""))
        except Exception:
            hs_conf = None
    hs_flagged = is_flagged(hs_conf)
    if hs_flagged:
        # PR A §A1: نفس المُسقِّف الواحد المستعمَل قبل الكاتب (المسار الرئيسي +
        # إعادة التوليد) — idempotent هنا: لو مرّ الكاتب على قيمةٍ مسقوفة أصلاً
        # لم يتغيّر شيء، وإلا سُقِّف الغلاف كما كان. لا نسخة سقفٍ محلّية تتباعد.
        verdict = cap_confidence_for_flagged_hs(verdict, hs_conf)
        # ملاحظة منهجية واحدة (4.1) — تُضاف مرة واحدة إلى الحدود، لا في كل قسم.
        _missing = "، ".join((hs_conf or {}).get("missing_terms") or [])
        limits.insert(0, f"{CONTEXTUAL_TAG}: رمز HS {hs_conf.get('hs_code')} "
                      f"(«{hs_conf.get('code_desc')}») لا يشمل صفة المنتج المميّزة"
                      + (f" ({_missing})" if _missing else "")
                      + " — تُقرأ أرقام الاستيراد والتركّز والحصص كمؤشر سياقي "
                      "حتى تأكيد الرمز الصحيح.")
    # إفصاحُ مصدر الرمز حين حُسِم آلياً بالسمة الرقمية — سطرٌ واحدٌ مشترك
    # يُبنى في `_hs_provenance` ويُحقَن هنا وفي `build_view` معاً (مسارا
    # /research و/analyze) بلا نسختين قابلتين للانحراف.
    _hs_prov_view = _hs_provenance(result)
    if _hs_prov_view:
        limits.insert(0, _hs_prov_view["note_ar"])
    # WP-3 §2/§3: ممرّ المصالحة الرقمية (يوسم البنود المستبعدة/السياقية/
    # المسانَدة) + إعلان المصادر التي فشل جمعها كلياً — مصدرٌ كل بنوده
    # أخطاء (value=None) يُستبعَد من سطر «اعتمد هذا التقرير على مصادر…»
    # (silk_reports._client_methodology_paragraph) ويُذكَر هنا في الحدود فقط.
    _conflicts = _reconcile_numeric_conflicts(missions, bool(hs_flagged))
    _src_ok: dict[str, bool] = {}
    from silk_narrative import TOOLUSE_MARK_RE as _TUM
    for _m in missions.values():
        for _f in (_m.get("findings") or []):
            _lbl = _TUM.sub("", str(_f.get("source") or "")).strip(" ،-—")
            if not _lbl:
                continue
            _src_ok[_lbl] = _src_ok.get(_lbl, False) or (
                _f.get("value") is not None)
    for _lbl, _ok in sorted(_src_ok.items()):
        if not _ok and not any(_lbl in l for l in limits):
            limits.append(f"المصدر «{_lbl}» تعذّر جلب بياناته في هذه "
                          "التشغيلة — لم يُعتمد عليه ولا يُدرَج ضمن مصادر "
                          "التقرير.")
    _report_text_glossed, _glossary = _apply_merchant_language(
        _strip_internal_plumbing(report_out.get("report")))
    # البند ٥ (تدقيق «تحليل #1» DZA): عنوِن عمود السعر بالعملة المرصودة
    # فعلاً قبل التخزين في النموذج القانوني — راجع تعليق الدالة أعلاه.
    _report_text_glossed = _fix_price_column_currency_label(_report_text_glossed)
    # PR B §B9: قرِّب دقّةَ HHI العشرية الوهميّة إلى صحيح قبل التخزين/العرض.
    _report_text_glossed = _fix_hhi_false_precision(_report_text_glossed)
    # القاعدة العامة (قرار المالك): سنوات الحقائق المتقادِمة تُحسَب من **مصدرها
    # البنيوي** (silk_staleness) لا من النثر، فتُوسَم أينما وردت بأيّ صياغة، ولا
    # يُوسَم رمزُ HS. ثم تحقّقٌ: أيّ سنة حقيقة متقادِمة بلا وسمٍ في السرد
    # تُبلَّغ حدًّا (لا تُصحَّح صامتةً — عقد عدم الاختلاق).
    # القاعدة العامة (قرار المالك): سنوات الحقائق المتقادِمة تُحسَب من الحقل
    # البنيويّ `data_year` (silk_staleness) لا من النثر، فتُوسَم أينما وردت بأيّ
    # صياغة، ولا يُوسَم رمزُ HS. التوسيمُ حتميٌّ شاملٌ لكلّ سنةٍ في القائمة —
    # لا حاجة لمتحقّقٍ لاحق (كان `_stale_tag_misses` غيرَ قابلٍ للإطلاق عملياً،
    # مراجعة الشيفرة #5 — حُذف).
    from silk_staleness import stale_fact_years as _stale_fact_years
    _all_findings = [f for v in missions.values()
                     for f in (v.get("findings") or [])]
    _stale_set = _stale_fact_years(_all_findings)
    _report_text_glossed = _tag_stale_years(_report_text_glossed, _stale_set)
    return {
        "market": result.get("market"),
        # Wave 2: اسم المنتج المدروس يصل عرض البحث كي يشتقّ منه المُصدِّرُ سطرَ
        # إخلاء المسؤولية بارامتريًّا (لا «التمور السعودية» مثبَّتة) وفلترةَ الجغرافيا.
        "product": result.get("product"),
        "trace_id": dr.get("trace_id"),
        # لافتة التدهور (بلاغ حي، بوابة ما قبل التشغيل api.py) — تصل هنا كي
        # يحملها كل مشتق (docx/مختصر/طرفية/لوحة) لا سطر ملاحظة وحيد مدفون.
        "degraded": bool(result.get("degraded")),
        "degraded_reason": result.get("degraded_reason") or "",
        "missions": missions,
        "analyst": {"summary": analyst_report["summary"],
                   "missing_categories": analyst.get("missing_categories") or [],
                   "by_category": by_category,
                   # PART B2: التشخيص الذاتي (عدّاد الخام مقابل المُصنَّف +
                   # سبب «كل التقاطعات فارغة») يصل المدوّنة والواجهة فيُقرأ من
                   # GET /analyses/{id} مباشرة — الحادثة القادمة تُشخِّص نفسها.
                   "diagnostics": analyst.get("diagnostics") or {}},
        # سدّ تسريب (الطبقة ٦): تصنيف/تسمية الحكم مُحسَّبان هنا مرة واحدة —
        # لوحة الويب تستهلكهما بدل حساب تصنيفها الخاص من الرمز الخام
        # وعرض الرمز نفسه كنص ظاهر (كان "CONDITIONAL-GO"/"WATCH" يظهر
        # حرفياً على شارة الغلاف).
        "verdict_tone": verdict_tone,
        "verdict_label": _VERDICT_LABELS_AR[verdict_tone],
        "verdict": verdict,
        # فصلٌ بنيويّ بين الحكم والسرد (بلاغ المالك): **الحكم** يُشتقّ من
        # التوليف الحتمي (`silk_synthesis` — المرحلة ١ لا تُطفأ أبداً)، و**السرد**
        # طبقةُ نثرٍ لغويةٍ اختيارية. فشلُ نداء الكاتب يُعلَّم هنا وحده — ولا
        # يمسّ `verdict_tone`/`verdict_label` بحال. الواجهة/المُصدِّرات تعرض
        # «السرد غير متاح» في مكانه بدل تخفيض الشارة.
        "narrative": {
            "available": bool(report_out.get("report")),
            "reason": clean_failure_reason if not report_out.get("report") else "",
            "source": "llm",
        },
        # Wave 1.3: عقد تأكيد رمز HS — يعرضه كل مُصدِّر/لوحة كي يعيد تأطير
        # أرقام كومتريد «مؤشر سياقي» عند التعليم. None/غير مؤكَّد لا يُطأطئ شيئاً.
        "hs_confirmation": hs_conf or {},
        "hs_flagged": bool(hs_flagged),
        # WP-3 §2: تعارضات رقمية حُسمت — تُفصَح مرة واحدة في سجل الأدلة.
        "reconciliation": {"conflicts": _conflicts},
        # أسلوب التقرير المخزَّن (إعادة توليد أكاديمية) — تقرؤه التصديرات
        # لتختار القالب الافتراضي.
        "report_style": str(dr.get("report_style") or ""),
        # مراجعة شيفرة PR #147: نثر الصياغة التجارية المُخزَّن (WP-2 §3،
        # يكتبه مسار التصدير مرة واحدة عبر save_analysis) يُعاد حمله هنا
        # فلا يُعاد دفع نداءاته مع كل تصدير — مُطهَّراً كأي نص معروض.
        "client_fallback_prose": {
            str(k): _strip_internal_plumbing(str(v))
            for k, v in (dr.get("client_fallback_prose") or {}).items()
            if str(v or "").strip()},
        # Wave 3.1: سبب غياب السعر/كجم لكل صفّ سعر مرصود + سطر الفتح الوحيد.
        "price_rows": [
            {"value": (_dp(x).get("value")),
             "store": _strip_internal_plumbing(str(_dp(x).get("note") or "")),
             "reason": _price_row_reason(_dp(x).get("value"))}
            for x in ((missions.get("pricing_scout") or {}).get("findings") or [])],
        "price_unlock": PRICE_UNLOCK_LINE,
        # Wave 3.2: عند تعليم الرمز، التركّز (HHI) سياقٌ فقط لا إشارة تسجيل
        # للحكم لهذا المنتج — الشارة تستهلكها المُصدِّرات.
        "concentration_context_only": bool(hs_flagged),
        # Wave 6.1: شرطا قلب الحكم المهيكلان (حكم مراقبة/مشروط) — يعرضهما كل
        # مُصدِّر «شرطا قلب الحكم»، وتربط خارطة الـ٩٠ يوماً كل خطوة بأيّهما تُغلق.
        "flip_conditions": _flip_conditions(
            verdict_tone, hs_flagged,
            dr.get("importer_leads") or {},
            (result.get("market") or {}).get("name_ar") or ""),
        "report": {"text": _report_text_glossed,
                  "review_cycles": report_out.get("review_cycles", 0),
                  "unresolved_notes": clean_unresolved,
                  "failure_reason": clean_failure_reason},
        # B1 (SPEC-v2): مسرد المصطلحات المستعملة فعلاً — بنية يعرضها كل
        # مُصدِّر صراحةً (md/docx المدقّق/docx العميل).
        "glossary": _glossary,
        # C5 (SPEC-v2): قائمة مستوردين/موزعين قابلين للتواصل — بنية يعرضها
        # كل مُصدِّر كجدول في قسم الدخول (خرائط قوقل/Places + مرشّحو ويب).
        "importer_leads": dr.get("importer_leads") or {"leads": [], "path": "gap"},
        # مصدرُ الرمز حين حُسِم آلياً — يصل **عرضَ البحث العميق** لا الحدودَ
        # وحدها: تقريرُ العميل (المُسلَّم الفعليّ) يبني أقسامَه من
        # `deep_research` لا من `limits`، فوضعُه في الحدود وحدها أخرجه من
        # المستند الذي يراه العميل فعلاً. التقطه فحصُ «سلسلةُ الإفصاح في
        # تقريرٍ مُصيَّرٍ فعلاً» — لا اختبارُ وحدةٍ على العرض.
        "hs_provenance": _hs_prov_view,
        "limits": limits,
        # عقد المالك (بلاغ UK الحي): لا يُسمّى مزوّد داخلي (Volza/Explee/…) على
        # أيّ سطح عميل — لغة أعمال عامة فقط. السطح التشغيلي (?internal=1) قد
        # يبقي التفصيل. الحارس النهائي: _CLIENT_VENDOR_NAMES في silk_reports.
        "next_step": ("فعّل خدمة التعميق المدفوعة للتحقق من المستوردين "
                     "وجهات الاتصال قبل الالتزام"
                     if str(verdict.get("verdict") or
                           (verdict.get("ai") or {}).get("verdict") or "")
                        .upper().startswith(("GO", "PRELIMINARY GO")) else None),
    }


def build_view(result: dict) -> dict:
    """ابنِ نموذج العرض القانوني — the ONE canonical view-model (vision §10.1).

    كل المخرجات (لوحة/طرفية/Streamlit/مختصر) تشتق من هذا النموذج حصراً.
    """
    markets = result.get("markets") or []
    top = markets[0] if markets else None
    decision = _decision(top)
    # حكم واحد لا حكمان (إصلاح مراجعة Stage 5): عند وجود قرار المحرك الموزون
    # (§8) الصالح فهو **الحكم الوحيد** في كل التقرير — هيئة المحلفين تتحول إلى
    # سطر كفاية بيانات بلا كلمة حكم (خطة §8a: الجورية بوابة كفاية لا قرار).
    ed_top = (top or {}).get("decision") or {}
    if ed_top.get("schema") and not ed_top.get("error"):
        jury = (top or {}).get("jury") or {}
        from silk_narrative import internal_ar
        gaps_ar = ("، ".join(internal_ar(g) for g in jury.get("data_gaps", []))
                  or "لا شيء")
        decision = {
            "verdict": ed_top.get("verdict"),
            "confidence": ed_top.get("confidence"),
            "score": ed_top.get("score"),
            "why": ed_top.get("why"),
            "market": (top or {}).get("country"),
            "stage": "silk.decision/v1 — المحرك الموزون (الحكم الوحيد)",
            "sufficiency": (f"بوابة كفاية البيانات: {jury.get('agents_with_data', 0)}/"
                            f"{jury.get('agents_total', 0)} وكلاء أساسيون لديهم "
                            f"بيانات؛ فجوات: {gaps_ar}"),
            # سدّ تسريب (الطبقة ٦): نفس تصنيف الشارة المحسوب لمسار الجورية
            # الاحتياطي أعلاه — هذا الفرع (محرك §8) هو الشائع فعلياً.
            "tone": _verdict_tone(ed_top.get("verdict")),
        }
    cp = _competitive_position(top)
    view_markets = []
    for row in markets:
        comps = row.get("components") or {}
        present = sum(1 for c in comps.values() if _dp(c).get("value") is not None)
        view_markets.append({
            "country": row.get("country"), "iso3": row.get("iso3"),
            "score": row.get("total_score"), "confidence": row.get("confidence"),
            "components_present": f"{present}/{len(comps) or 4}",
            # §10.3: سطر مصدر تحت كل رقم — مبني في القالب نفسه فيستحيل
            # بنيوياً ظهور رقم بلا نسب في أي مشتق (docx/نص/لوحة).
            "components_detail": [
                {"name": name, "value": _dp(c).get("value"),
                 "source": _dp(c).get("source"),
                 "confidence": _dp(c).get("confidence"),
                 "retrieved_at": _dp(c).get("retrieved_at", ""),
                 # سدّ تسريب: ملاحظة DataPoint خام (نجاح إنجليزي مثل "HS…
                 # total World… USD" أو فشل يضمّ استثناء) لم تكن تمرّ عبر
                 # أي مُطهِّر رغم وصولها مباشرة لهذا الحقل في نموذج العرض
                 # القانوني — أي مستهلك مستقبلي (JSON خام، ودجت جديد) يرث
                 # النص المُعرَّب الآن، لا الخام.
                 "note": _strip_internal_plumbing(_dp(c).get("note", "")),
                 "status": _dp(c).get("status", "")}
                for name, c in comps.items()],
            "recommendation": row.get("recommendation"),
            "quality_flags": row.get("quality_flags") or [],
            "has_competitive_position": "competitive_position" in row,
            # §سنوات الدراسة: خط الاتجاه متعدد السنوات إن فُعِّل (with_trend)،
            # وإلا None — الواجهة تعرضه أو تعلن «يتطلب تفعيل مدى السنوات».
            "trend": row.get("trend"),
            # طبقات الإثراء المرصودة (أسعار/منافسون/موردون) — يعرضها التقرير
            # والواجهة؛ الفارغ يُعلن «غير مرصود» لا يُخترع.
            "prices": _prices(row),
            "named_competitors": _named_competitors(row),
            "supplier_countries": row.get("competitors") or [],
            "suppliers": _suppliers(row),
            # Stage 2A: مخاطر (WGI/LPI/FX) + درجة تغطية المصادر لكل قسم
            "risk": [_dp(f) for f in (row.get("risk") or [])],
            "section_coverage": _section_coverage(row),
            "section_status": _section_status(row),   # بوابة 2B
            # Stage 5: حزمة §4b كما تحقق منها المنسّق + قرار §8 + مشتقاتها
            # القاعدية (SWOT/شرائح/دليل مورّدين) — اشتقاق عرض صرف.
            "research": row.get("research"),
            "entry_decision": row.get("decision"),
            "swot": _swot(row.get("research")),
            "segments": _segments(row.get("research")),
            "supplier_directory": _supplier_directory(row.get("research")),
        })
    # إفصاحُ مصدر الرمز يظهر على مسار /analyze أيضاً لا على /research وحده
    # (لا إصلاحَ على مسارٍ واحد — الدرسان ٣٥/٣٧). عند وجود بحثٍ عميق يكون
    # السطرُ محقوناً سلفاً في حدوده، فيُمنَع التكرار أدناه.
    hs_prov = _hs_provenance(result)
    limits = [f"{m['country']}: {_humanize_gap_note(f)}" for m in markets[:5]
              for f in (m.get("quality_flags") or [])]
    if not result.get("classified"):
        limits.insert(0, _humanize_gap_note(result.get("hs_note"))
                     if result.get("hs_note") else "تعذّر التصنيف")
    # قسم البحث العميق (الموجة ٤، V5) — إضافي بحت؛ None لتحليل /analyze عادي.
    dr_view = _deep_research_view(result)
    if dr_view:
        # HF3: حارسُ المعقولية عبر المصادر — يقارن المقاديرَ المكشوطة (حجم سوقٍ)
        # بمرتكزات التشغيلة المُتحقَّقة (واردات/سكان) قبل التصيير، فيُسجّل العلاماتِ
        # في المانيفست ويُضيف تحفّظَ نطاقٍ للعميل بدل تسريب رقمٍ متعارضٍ صامتاً.
        # فشلٌ آمنٌ مفتوح: حارسٌ تشخيصيّ لا شرطُ تنفيذ.
        try:
            import silk_plausibility
            _pflags = silk_plausibility.annotate(result)
            if _pflags:
                dr_view["plausibility_flags"] = _pflags
                dr_view["limits"] = (silk_plausibility.caveat_lines(_pflags)
                                     + list(dr_view.get("limits") or []))
            # G4.1: إعفاءُ الإنتاج المحليّ — مرئيٌّ للمراجع في العرض (لا تحفّظَ
            # عميل، لا يدخل limits). فيرى المراجعُ أنّ الحارسَ وقف جانباً ولماذا.
            _pexempt = (result.get("deep_research") or {}).get(
                "plausibility_exemptions")
            if _pexempt:
                dr_view["plausibility_exemptions"] = _pexempt
        except Exception:  # noqa: BLE001
            pass
        limits = dr_view["limits"] + limits
    elif hs_prov:
        limits.insert(0, hs_prov["note_ar"])
    # ترويسة 2B: التغطية الإجمالية % = مُسهم/مُحاوَل عبر أقسام السوق الأعلى.
    top_cov = _section_coverage(markets[0]) if markets else {}
    att = sum(c["attempted"] for c in top_cov.values())
    con = sum(c["contributed"] for c in top_cov.values())
    dr_market = (dr_view or {}).get("market") or {}
    header = {
        "product": result.get("product"), "hs_code": result.get("hs_code"),
        "origin": "SAU",
        "target_market": ((markets[0].get("country") or markets[0].get("iso3"))
                          if markets else
                          (dr_market.get("name_ar") or dr_market.get("name_en"))),
        "date": _t_today(),
        "coverage_pct": round(100 * con / att, 1) if att else 0.0,
    }
    view = {
        # راية التشغيل البرهاني: العواذف تضبط SILK_HERMETIC — كل المشتقات تطبع
        # لافتة TEST RUN؛ وفي الإنتاج يرفض المولّد أي أثر برهاني (silk_reports).
        "test_run": bool(os.environ.get("SILK_HERMETIC")),
        # لافتة التدهور (بلاغ حي) — top-level لتظهر في كل مشتق يقرأ
        # view["degraded"] مباشرة، بلا حاجة لفتح deep_research أولاً.
        "degraded": bool((dr_view or {}).get("degraded")),
        "degraded_reason": (dr_view or {}).get("degraded_reason") or "",
        "header": header,
        "product": result.get("product"), "hs_code": result.get("hs_code"),
        "hs_confidence": result.get("hs_confidence"),
        # مصدرُ الرمز حين حُسِم آلياً (صورةُ عبوة/رابطُ ويب) — `None` حين
        # حُسِم بالمسار العادي؛ لا حقلَ صامتاً يُفسَّر خطأً.
        "hs_provenance": hs_prov,
        "year": result.get("year"), "preliminary": True,
        "data_year": result.get("data_year", result.get("year")),
        "year_fell_back": bool(result.get("year_fell_back")),
        "classified": result.get("classified", False),
        "decision": decision,
        "dynamics": _sanitized_dynamics(result.get("dynamics")),
        "competitive_position": cp,
        "completeness": _completeness(markets),
        "markets": view_markets,
        "culture": _culture(result),          # روابط خام (تراجُع/استشهاد)
        "consumer_culture": _consumer_culture(result),  # ثقافة المستهلك المستخلَصة (كلود)
        "brief": (_deep_research_brief(dr_view) if dr_view
                 else _brief(decision, cp)),
        "limits": limits,
        "provenance": _provenance(result),   # Stage 2A: لا فشل صامتاً
        # اقتصاد البيانات (persist-5): عدّاد مرصود — مخزن/ذاكرة مقابل جلب حي.
        "data_economics": result.get("data_economics"),
        # HF4.1: ملاحظةُ التشغيلة تمرّ عبر مُطهِّر المتن (كملخّصات البعثات/المحلل
        # أصلاً) — فلا يتسرّب نصفُها الإنجليزيُّ الداخليّ لأيّ سطحِ عميل (§5).
        "note": (_strip_internal_plumbing(result.get("note"))
                 if isinstance(result.get("note"), str) else result.get("note")),
        # التحليل الاحترافي (silk_ai_judge.ai_report) — يحلّ محل الخلاصة
        # الحتمية (exec_summary) في التقرير المصدَّر حين يتوفر؛ None = غياب
        # مفتاح/فشل النداء (ظاهر لا محذوف)، والقالب يرجع حينها لـ exec_summary.
        "ai_report": result.get("report"),
        "ai_report_note": result.get("report_note"),
        # الموجة ٤ (V5): مختلف عن row["research"] القائم — راجع تنبيه التسمية
        # أعلى _deep_research_view. None لتحليل /analyze العادي (لا أثر).
        "deep_research": dr_view,
    }
    return view


def render_text(view: dict) -> str:
    """نص الطرفية من القالب — terminal rendering derived from the view only."""
    L = ["═" * 60]
    if view.get("test_run"):
        L.append("⚠ TEST RUN — تشغيل برهاني ببدائل موسومة، ليس تقريراً إنتاجياً")
    L.append(f"المنتج / Product : {view.get('product')}")
    if not view.get("classified"):
        L += ["الحالة: تعذّر التصنيف — could not classify",
              *(f"  حد: {x}" for x in view.get("limits", [])[:3]), "═" * 60]
        return "\n".join(L)
    d = view["decision"]
    h = view.get("header") or {}
    L.append(f"المنتج: {h.get('product')} | HS: {h.get('hs_code')} | "
             f"السوق: {h.get('target_market')} | {h.get('date')} | "
             f"تغطية: {h.get('coverage_pct')}%")
    st0 = (view.get("markets") or [{}])[0].get("section_status") or {}
    for sec, st in st0.items():
        if st.get("status") == "insufficient":
            L.append("  " + insufficient_line(sec, st))
    cov0 = (view.get("markets") or [{}])[0].get("section_coverage") or {}
    if cov0:
        L.append("تغطية الأقسام: " + " | ".join(
            f"{k}:{c['contributed']}/{c['attempted']}" for k, c in cov0.items()))
    prov = view.get("provenance") or []
    if prov:
        L.append("أثر المصادر: " + " ، ".join(
            f"{b['source']}={b['contributed']}/{b['attempted']}"
            for b in prov[:6]))
    from silk_narrative import confidence_phrase, verdict_ar
    L += [f"رمز HS: {view['hs_code']} (ثقة {view['hs_confidence']}) | "
          f"سنة {view['year']} | مبدئي",
          f"القرار: {verdict_ar(d.get('verdict'))} "
          f"(ثقة {confidence_phrase(d.get('confidence'))}) — {d.get('market')}",
          f"لماذا: {d.get('why')}", "─" * 60]
    ed = (view.get("markets") or [{}])[0].get("entry_decision") or {}
    if ed.get("schema"):
        L.append(f"قرار الدخول (المحرك الموزون): {verdict_ar(ed.get('verdict'))} "
                 f"— النقاط {ed.get('score')} — الثقة "
                 f"{confidence_phrase(ed.get('confidence'))} — {ed.get('why')}")
        for c in (ed.get("conditions") or [])[:3]:
            L.append(f"  شرط: {c}")
    cp = view["competitive_position"]
    L.append("موقعك التنافسي:")
    if cp.get("available"):
        L.append(f"  التغطية: {cp.get('coverage')}")
        for f in cp.get("feasibility_threads") or []:
            L.append(f"  ضد {f['competitor'][:40]}: سعر مرصود "
                     f"{f['observed_price']} — هامشك عند المضاهاة "
                     f"{f['margin_at_match_pct']}% وعند البيع أقل 10% "
                     f"{f['margin_at_10pct_below']}%")
        for t in cp.get("competitor_threads") or []:
            if not t.get("observed_price"):
                # خيوط بحث الويب مراجع لا كيانات (إصلاح مراجعة Stage 5، ثغرة ٢).
                L.append(f"  مرجع ويب للمراجعة: {t['name'][:40]} — "
                         f"{t['price_flag']} "
                         f"(اكتمال الخيط {t['thread_completeness']})")
    else:
        L.append(f"  {cp.get('note')}")
    L.append("─" * 60)
    L.append("الأسواق (الأفضل أولاً):")
    for i, m in enumerate(view["markets"], 1):
        L.append(f"  {i:>2}. {m['country']:<22} score={m['score']:.3f} "
                 f"conf={m['confidence']} ({m['components_present']})")
    if view.get("limits"):
        L.append("حدود هذا التقرير:")
        L += [f"  - {x}" for x in view["limits"][:6]]
    if (view.get("data_economics") or {}).get("note"):
        L.append(f"اقتصاد البيانات: {view['data_economics']['note']}")
    L += ["المختصر:", *(f"  {x}" for x in view["brief"]), "═" * 60]
    return "\n".join(L)


def analysis_context(result: dict, max_chars: int = 6000) -> str:
    """سياق نصي مضغوط لتحليل قائم (10b) — للدردشة السياقية فوق النتيجة.

    يقرأ نتيجة المحرّك المخزّنة حصراً — صفر شبكة، صفر إعادة تشغيل وكلاء.
    كل رقم يُذكر بمصدره؛ الفجوات تُذكر كما هي كي يجيب كلود «غير متوفر في
    هذا التحليل» بدل الاختلاق.
    """
    # سدّ تسريب: هذا السياق يُغذّى مباشرة لبرومبت الدردشة السياقية
    # (silk_ai_judge.answer_about_analysis) — كلود مُطالَب بالاستشهاد حرفياً
    # من هذا النص، فأي مفتاح داخلي خام هنا (اسم مكوّن snake_case، مفتاح وكيل)
    # قابل للظهور حرفياً في جواب يصل العميل مباشرة.
    from silk_narrative import internal_ar
    view = result.get("view") if isinstance(result.get("view"), dict) else None
    view = view or build_view(result)
    L: list[str] = []
    h = view.get("header") or {}
    L.append(f"المنتج: {h.get('product')} (HS {h.get('hs_code')}) — "
             f"السوق الأول: {h.get('target_market')} — سنة البيانات: "
             f"{view.get('data_year', view.get('year'))}")
    for b in view.get("brief") or []:
        L.append(f"الخلاصة: {b}")
    # R2 (تفعيل الدردشة فوق الدراسة العميقة): analysis_context كان يقرأ شكل
    # /analyze حصراً (top فارغ للدراسة العميقة إذ markets=[])، فتُجيب دردشة
    # «اسأل عن الدراسة» من سياق شبه فارغ للدراسات الرئيسية. هنا نضيف تأريض
    # البحث العميق — حقائق البعثات بمصادرها، تقاطعات المحلل، الحكم، والتقرير
    # المكتوب — كي تُؤسَّس الإجابة على كامل الدراسة لا العنوان وحده. لا اختلاق:
    # كل رقم بمصدره، وما ليس هنا يقال «غير متوفر» في برومبت الإجابة نفسه.
    dr = view.get("deep_research")
    if isinstance(dr, dict):
        if dr.get("verdict_label"):
            L.append(f"حكم الدراسة: {dr['verdict_label']}")
        for key, m in (dr.get("missions") or {}).items():
            if not isinstance(m, dict) or m.get("failed"):
                continue
            label = m.get("label") or key
            for f in (m.get("findings") or [])[:3]:
                val = f.get("value")
                if val is None or isinstance(val, (list, dict)):
                    continue
                src = f.get("source") or ""
                L.append(f"{label}: {val}"
                         + (f" [المصدر: {src}]" if src else ""))
        an = dr.get("analyst") or {}
        for cat, dps in (an.get("by_category") or {}).items():
            for d in (dps or [])[:2]:
                val = d.get("value")
                if val is None or isinstance(val, (list, dict)):
                    continue
                L.append(f"تقاطع {_category_label(cat)}: {val}")
        report_text = (dr.get("report") or {}).get("text")
        if report_text:
            L.append("التقرير المكتوب للدراسة:\n" + report_text)
    top = (view.get("markets") or [{}])[0]
    for c in top.get("components_detail") or []:
        name_ar = internal_ar(c.get("name"))
        if c.get("value") is not None:
            L.append(f"{name_ar} = {c['value']} [المصدر: {c.get('source')}]")
        else:
            why = ("تعذّر الجلب — أعد المحاولة"
                   if c.get("status") == "fetch_failed" else "غير متوفر")
            L.append(f"{name_ar}: {why}")
    for sc in (top.get("supplier_countries") or [])[:6]:
        L.append(f"مورّد: {sc.get('partner')} — حصة {sc.get('share')}% "
                 f"({sc.get('value_usd')}$) [UN Comtrade]")
    ag = ((top.get("research") or {}).get("agents")) or {}
    for k, a in ag.items():
        k_ar = internal_ar(k)
        for f in (a.get("findings") or [])[:4]:
            if f.get("value") is None or isinstance(f.get("value"),
                                                    (list, dict)):
                continue
            srcs = "، ".join(str(x.get("source")) for x in
                             (f.get("sources") or []) if isinstance(x, dict))
            L.append(f"{k_ar} — {internal_ar(f.get('metric'))} = {f['value']}"
                     f"{(' ' + f['unit']) if f.get('unit') else ''}"
                     f" [المصدر: {srcs or '؟'}]")
        for g in (a.get("gaps") or [])[:2]:
            L.append(f"فجوة {k_ar}: {_humanize_gap_note(g)}")
    ed = top.get("entry_decision") or {}
    for cnd in (ed.get("conditions") or [])[:4]:
        L.append(f"شرط مفتوح: {cnd}")
    for x in (view.get("limits") or [])[:6]:
        L.append(f"حدّ معلن: {x}")
    out = "\n".join(L)
    return out[:max_chars]
