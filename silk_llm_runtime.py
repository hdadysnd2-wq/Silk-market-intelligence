"""زمن تشغيل وكيل كلود بالأدوات لسِلك — Silk Claude tool-use agent runtime.

الموجة ١ من التكليف الختامي: طبقة ٢ جديدة — وكلاء كلود يقرّرون أي أداة
يستدعون (بدل مسار محدَّد سلفاً)، ضمن ميزانية بحث محدودة وقائمة أدوات
مسموحة لكل مهمة (`silk_missions.MISSIONS`، الموجة ٢). كل أداة تُغلّف دالة
حقيقية من الطبقة ١ (Comtrade/World Bank/WITS/Trends/FAOSTAT/بحث ويب/GDELT/
مرجع ثابت) وتُعيد DataPoint موسومة — **لا اختلاق**: أداة فاشلة تعيد
DataPoint(None) موسوماً بالسبب، لا استثناءً صامتاً ولا رقماً مخترعاً.

مخرَج الوكيل الإلزامي JSON: findings[] (كل بند يستشهد بمعرّفات نقاط بيانات
عادت من نداء أداة فعلي) + gaps[] + summary. بند يستشهد بمعرّفٍ غائبٍ من
سجل الجلسة **يُسقَط ويُسجَّل تحذيراً** — لا رقم بلا استشهاد قابل للتتبع.

الحلقة: نظام (`_PRINCIPLE` + تعليمات المهمة) + رسالة مستخدم -> جولات
tool_use/tool_result حتى نص نهائي أو استنفاد الميزانية (افتراضي ٨ نداءات
أداة و~٦٠٠٠ رمز مخرَج للوكيل الواحد — قسم «الميزانية والأمان» بالتكليف؛
السقف الكلي عبر التحليل بأكمله يُطبَّق في الموجة ٢ عند تشغيل ١٢ وكيلاً
معاً). يُستهلك عبر `_call_tools` (امتداد صرف لأدوات نداء `silk_ai_judge`،
لا عميل جديد) و`_isolate` (نفس وسمَي العزل — كل نص خارجي من نتائج الأدوات
معزول قبل إرساله لكلود).

كل وكيل مهمة يُغلَّف `BaseAgent` (`LLMMissionAgent`) فيرث مجاناً حارسَي
`/deepen`/التعطيل واستحالة الفشل الصامت.
"""
from __future__ import annotations

import datetime
import functools
import json
import logging
import os
import re

from silk_agents import AgentReport, BaseAgent
from silk_ai_judge import _FAST_MODEL, _MODEL, _PRINCIPLE, _call_tools, _isolate

# E2 (SPEC-v2، انحدار التكلفة): بعثات الاستخلاص/التنسيق الاثنتا عشرة تعمل
# على النموذج السريع (Haiku) افتراضياً — استخلاص أدوات وتنسيق حقائق لا
# يتطلّب النموذج الذكي (Opus). النموذج الذكي محجوز للتحليل الشامل والكاتب
# (استدلال ثقيل)، والمراجع أصلاً على السريع. قابل للضبط/الرجوع بمتغيّر واحد.
_MISSION_MODEL = os.environ.get("SILK_MISSION_MODEL", "").strip() or _FAST_MODEL
from silk_ai_judge import failure_reason as _ai_failure_reason
from silk_data_layer import (
    DataPoint, comtrade_trade, comtrade_trade_mirror_total, primary_qty,
    primary_value, world_bank)
from silk_market_resolver import MarketRef

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))

# ميزانية افتراضية لوكيل واحد — per-agent default (run_llm_agent's `budget`
# arg overrides). السقف الكلي عبر التحليل (SILK_RESEARCH_MAX_LLM_CALLS/
# _MAX_TOOL_CALLS) مسؤولية المنسّق في الموجة ٢، لا هذا الملف.
_DEFAULT_BUDGET = {"tool_calls": 8, "max_output_tokens": 6000}

# مؤشرات البنك الدولي المتاحة للوكلاء — أسماء وصفية مضبوطة بدل رموز WB خام
# (يمنع كلود من تخمين رمز مؤشر غير موجود). Curated, not free-text WB codes.
_WB_INDICATORS = {
    "population": "SP.POP.TOTL",
    "income_per_capita": "NY.GDP.PCAP.CD",
    "ppp_per_capita": "NY.GDP.PCAP.PP.CD",
    "political_stability": "PV.EST",
    "regulatory_quality": "RQ.EST",
    "rule_of_law": "RL.EST",
    "logistics_lpi": "LP.LPI.OVRL.XQ",
    # سعر الصرف الرسمي (LCU/USD) — الموجة ١٠: لم يكن مؤشر الصرف مُتاحاً
    # لأداة worldbank_indicator إطلاقاً، فتقلّب العملة المطلوب في تعليمات
    # risk_news كان بلا مصدر بيانات فعلي. نداءات متعددة بسنوات مختلفة
    # (نفس نمط demand_trends) تعطي سلسلة يُحسب منها التقلّب.
    "exchange_rate": "PA.NUS.FCRF",
    # نسبة السكان في سنّ العمل ١٥-٦٤ — ترقية المرحلة ٢ب: تعليمات
    # demographics_economy كانت تطلب "نسبة الشباب إن أمكن" بلا أي مؤشر
    # يوفّرها فعلياً (فجوة ميتة بلا web_search بديل لهذه المهمة) رغم أن
    # البنك الدولي يوفّرها مجاناً عبر نفس الأداة المستَخدَمة أصلاً.
    "youth_population_pct": "SP.POP.1564.TO.ZS",
}

_REF_TABLES = {
    "demographics": os.path.join(_HERE, "data", "demographics_l1.csv"),
    "ports": os.path.join(_HERE, "data", "ports_l1.csv"),
    "agreements": os.path.join(_HERE, "data", "agreements_l1.csv"),
    # لغة/عملة/متاجر السوق (R1) — يُغذّي البحث بلغة السوق ونطاقه ومنصّاته
    # كي يبحث النظام كمستهلك محلي بدل التخمين. جدول توجيه بحث لا مصدر أرقام.
    "locale": os.path.join(_HERE, "data", "market_locale.csv"),
}


def _today() -> str:
    return datetime.date.today().isoformat()


def _recent_years(n: int = 3) -> list[int]:
    """آخر n سنة على الأرجح مكتملة — Comtrade عادة يتأخر سنة عن الحالية."""
    last = datetime.date.today().year - 1
    return list(range(last - n + 1, last + 1))


@functools.lru_cache(maxsize=8)
def _load_csv(path: str) -> tuple[dict, ...]:
    """اقرأ CSV مرجعي — يتجاهل أسطر تعليق '#' التوثيقية في مقدّمة الملف.

    بلاغ حي (الموجة ٨): `demographics_l1.csv`/`agreements_l1.csv` يبدآن
    بسطر (أو سطرين) '# ...' يوثّقان المصدر (tools/fetch_demographics.py،
    tools/fetch_agreements.py) — `csv.DictReader` بلا تصفية كان يعامل أول
    سطر تعليق كصف رؤوس الأعمدة، فتنزاح كل الصفوف صفاً واحداً ويفشل حقل
    'iso3' للجميع (لا لسوق واحد — لكل استعلام على هذين الجدولين). فُقِدت
    نسبة مسلمي هولندا (وكل سوق آخر) بهذا الخلل تحديداً، لا لغياب البيانات
    (الصف موجود فعلاً في الملف — تحقّق مباشر، لا تخمين).
    """
    import csv
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.lstrip().startswith("#")]
        return tuple(csv.DictReader(lines))
    except Exception as e:  # noqa: BLE001 — مرجع غائب يُعامَل كفجوة، لا عطل
        log.warning("reference CSV unavailable (%s): %s", path, e)
        return ()


def _market_locale(ctx: dict) -> dict:
    """صف لغة/عملة/متاجر السوق من market_locale.csv — {} إن غاب السوق.

    R1: يمكّن البحث من التصرّف كمستهلك محلي (لغة/نطاق/منصّات مشتقّة من
    السوق لا مُخمَّنة). سوق بلا صف => {} فيتراجع البحث للسلوك العام بلا كسر.
    """
    market = ctx.get("market")
    iso3 = getattr(market, "iso3", "") if market is not None else ""
    if not iso3:
        return {}
    for r in _load_csv(_REF_TABLES["locale"]):
        if (r.get("iso3") or "").strip().upper() == iso3:
            return dict(r)
    return {}


def _locale_gl(ctx: dict) -> str:
    """نطاق الدولة (gl، ISO 3166-1 alpha-2) للسوق من مرجع locale — '' إن غاب."""
    return (_market_locale(ctx).get("gl") or "").strip().lower()


def _locale_hl(ctx: dict) -> str:
    """لغة الواجهة (hl) للسوق من مرجع locale = لغته الأساسية — '' إن غاب."""
    return (_market_locale(ctx).get("lang_primary") or "").strip().lower()


# ── الأدوات · tool implementations (args from Claude, ctx from the run) ─────

def _tool_comtrade_imports(args: dict, ctx: dict) -> list[DataPoint]:
    hs, market = ctx.get("hs_code"), ctx["market"]
    if not hs:
        return [DataPoint(None, "UN Comtrade", 0.0,
                          "لا رمز HS مرتبط بهذه المهمة", _today())]
    years = [int(y) for y in (args.get("years") or _recent_years(3))]
    out: list[DataPoint] = []
    for year in years:
        recs = comtrade_trade(hs, market.m49, year, flow="M", partner=0)
        if recs is None:
            out.append(DataPoint(
                None, "UN Comtrade", 0.0,
                f"HS{hs} استيراد {market.name_en} {year}: تعذّر الجلب",
                _today(), status="fetch_failed"))
            continue
        vals = [v for v in (primary_value(r) for r in recs) if v is not None]
        if not vals:
            # ترقية المرحلة ٢ج (خيار A — إحصاءات المرآة): الاستعلام
            # المباشر نجح لكن أعاد سجلاً فارغاً — قد تكون السوق فعلاً
            # بلا استيراد لهذا الرمز، أو قد تكون لا تُبلِغ كومتريد عن
            # نفسها إطلاقاً (شائع لأسواق نامية كثيرة). بدل إعلان فجوة
            # صامتة مباشرة، اسأل شركاءها التجاريين "كم صدّرتم لها؟" —
            # لا محاولة عند فشل الجلب الفعلي (fetch_failed أعلاه)، فقط
            # عند غياب سجل حقيقي في ردّ ناجح.
            mirror_total = comtrade_trade_mirror_total(hs, market.m49, year,
                                                        flow="M")
            if mirror_total is not None:
                out.append(DataPoint(
                    round(mirror_total, 2), "UN Comtrade (مرآة)", 0.6,
                    f"HS{hs} تقدير استيراد {market.name_en} {year} من "
                    "مرآة تصريحات تصدير الشركاء (السوق لا تُبلِغ كومتريد "
                    "مباشرة لهذه السنة/الرمز) — أقل يقيناً من تصريح "
                    "مباشر، تقدير احتياطي لا بديل كامل",
                    _today(), status="mirrored", data_year=year))
                continue
            out.append(DataPoint(
                None, "UN Comtrade", 0.0,
                f"HS{hs} استيراد {market.name_en} {year}: لا سجل (ولا مرآة)",
                _today(), status="no_record"))
            continue
        out.append(DataPoint(
            sum(vals), "UN Comtrade", 0.9,
            f"HS{hs} إجمالي استيراد {market.name_en} من العالم {year}, USD",
            _today(), data_year=year))
        # ترقية المرحلة ٢ب: متوسط سعر استيراد مرجعي (القيمة/الوزن الصافي)
        # — حقل netWgt يعود مع نفس السجلات المُستجلَبة أعلاه أصلاً بلا
        # نداء إضافي؛ لم يكن يُستخرَج. ثقة أدنى من إجمالي الاستيراد لأنه
        # متوسط عبر مزيج منتجات داخل رمز HS قد يشمل درجات جودة مختلفة —
        # نطاق جملة مرجعي واسع، لا سعر تجزئة فعلياً (راجع تعليمات
        # pricing_scout لصيغة العرض للعميل).
        qtys = [q for q in (primary_qty(r) for r in recs) if q is not None]
        total_qty = sum(qtys)
        if total_qty > 0:
            out.append(DataPoint(
                round(sum(vals) / total_qty, 4), "UN Comtrade", 0.7,
                f"HS{hs} متوسط سعر استيراد {market.name_en} {year} "
                "(القيمة الإجمالية ÷ الوزن الصافي بالكجم) — نطاق جملة "
                "مرجعي من كومتريد، لا سعر تجزئة فعلياً",
                _today()))
        else:
            # HF4.4 (بلاغ قطر — فجوةُ الوزن): كومتريد يُرجِع عمودَ netWgt مع
            # السجلات ضمن مجموعة الأعمدة الافتراضية (لا نُسقِطه ولا نمتنع عن
            # طلبه — راجع `silk_data_layer.comtrade_trade`)؛ فغيابُه هنا يعني
            # أنّ **المُبلِّغ لا يودع بيانات الوزن** لهذا البند/السنة، لا أنّنا
            # لم نطلبها. فجوةٌ حقيقيةٌ في إيداع المُبلِّغ — تُصرَّح بدقّةٍ لا
            # تُخمَّن، فلا يُقرأ «لم نطلب» مكان «المصدر لا يودع».
            out.append(DataPoint(
                None, "UN Comtrade", 0.0,
                f"HS{hs} كميات الوزن (طن/كجم) لواردات {market.name_en} {year}: "
                "المُبلِّغ لا يودع بيانات الوزن لدى كومتريد (قِيَمٌ بالدولار فقط) "
                "— يتعذّر اشتقاق سعر وحدةٍ مرجعيّ منها",
                _today(), status="no_record"))
    return out


def _tool_comtrade_competitors(args: dict, ctx: dict) -> list[DataPoint]:
    """مورّدو السوق بالبلد (كومتريد ثنائي الأطراف) + تركّز HHI — بلاغ حي
    (الموجة ١٠/١١: تشغيلة إسبانيا أظهرت 'المنافسون' بتغطية 0.0 رغم توفر
    بيانات كومتريد الثنائية دوماً). comtrade_imports يعيد إجمالي العالم فقط
    (partner=0) — هذه الأداة تستدعي partner='all' فتعيد حصص كل دولة مورّدة
    بالاسم الحقيقي (silk_data_layer.partner_name، الموجة ١٠) — لا تعتمد على
    بحث الويب لصورة تنافسية أساسية."""
    from silk_data_layer_v2 import market_competitors, market_competitors_mirror
    hs, market = ctx.get("hs_code"), ctx["market"]
    if not hs:
        return [DataPoint(None, "UN Comtrade", 0.0,
                          "لا رمز HS مرتبط بهذه المهمة", _today())]
    year = args.get("year")
    top_n = min(max(int(args.get("top_n") or 10), 1), 20)
    y = int(year) if year else _recent_years(1)[0]
    comps = market_competitors(hs, market.m49, y)
    mirrored = False
    if not comps:
        # ترقية المرحلة ٢ج (خيار A — إحصاءات المرآة): الاستعلام المباشر
        # يتطلب أن تُبلِغ السوق الهدف عن نفسها لكومتريد (reporter=السوق)
        # — أسواق كثيرة لا تُبلِغ إطلاقاً رغم أن شركاءها التجاريين
        # يُبلِغون عن تصديرهم إليها. احتياط فقط، لا استبدال للاستعلام
        # المباشر.
        comps = market_competitors_mirror(hs, market.m49, y)
        mirrored = bool(comps)
    if not comps:
        return [DataPoint(
            None, "UN Comtrade", 0.0,
            f"HS{hs} مورّدو {market.name_en} {y}: لا سجل ثنائي/تعذّر الجلب "
            "(ولا مرآة)",
            _today())]
    top = comps[:top_n]
    # رقم صحيح على مقياس 0-10000 — عشرية واحدة كانت وهم دقّة ترصده بوابة
    # الجودة (`hhi_false_precision`) على كل تقرير يقتبس هذا الملخّص.
    hhi = int(round(sum((c.value.get("share") or 0.0) ** 2 for c in comps)))
    mirror_note = (" — تقدير مرآة من تصريحات تصدير الشركاء (السوق لا تُبلِغ "
                   "كومتريد مباشرة)" if mirrored else "")
    summary = DataPoint(
        {"year": y, "hhi": hhi, "supplier_count": len(comps),
         "top_suppliers": [{"partner": c.value["partner"],
                            "share": c.value["share"]} for c in top]},
        "UN Comtrade (مرآة)" if mirrored else "UN Comtrade",
        0.6 if mirrored else 0.9,
        f"HS{hs} مورّدو {market.name_en} {y}: {len(comps)} دولة مرصودة، "
        f"مؤشر تركّز HHI={hhi} (>2500 مركّز جداً، 1500-2500 معتدل، <1500 "
        f"مجزَّأ){mirror_note}",
        _today())
    return [summary, *top]


def _tool_worldbank_indicator(args: dict, ctx: dict) -> list[DataPoint]:
    market = ctx["market"]
    key = str(args.get("indicator") or "").strip()
    code = _WB_INDICATORS.get(key)
    if not code:
        return [DataPoint(None, "World Bank", 0.0,
                          f"مؤشر غير معروف: {key!r} — يجب أن يكون أحد "
                          f"{sorted(_WB_INDICATORS)}", _today())]
    year = args.get("year")
    return [world_bank(market.iso3, code, int(year) if year else None)]


def _tool_wits_tariff(args: dict, ctx: dict) -> list[DataPoint]:
    # سلسلة التراجع (الموجة: دمج مصادر جديدة): WTO TTD → WITS → فجوة معلنة —
    # WTO TTD يسدّ فجوة التعريفة الثنائية المزمنة في WITS للأسواق الأوروبية.
    from silk_tariffs_agent import tariff_with_fallback
    hs, market = ctx.get("hs_code"), ctx["market"]
    if not hs:
        return [DataPoint(None, "World Bank WITS", 0.0,
                          "لا رمز HS مرتبط بهذه المهمة", _today())]
    partner = str(args.get("partner_iso3") or "SAU").upper()
    year = args.get("year")
    return [tariff_with_fallback(hs, market.iso3, partner_iso3=partner,
                                 year=int(year) if year else None)]


def _tool_imf_indicator(args: dict, ctx: dict) -> list[DataPoint]:
    """مؤشر اقتصاد كلي من IMF WEO (نمو/تضخم/حساب جارٍ) — يثري المخاطر/الاقتصاد
    الكلي بجانب صرف البنك الدولي (الموجة: دمج مصادر جديدة). فجوة معلنة عند الفشل."""
    from silk_imf_agent import imf_indicator
    market = ctx["market"]
    metric = str(args.get("indicator") or "").strip()
    year = args.get("year")
    return [imf_indicator(market.iso3, metric, int(year) if year else None)]


def _tool_trends_interest(args: dict, ctx: dict) -> list[DataPoint]:
    """سلسلة الصمود WS11.1 لا النداء العاري — بلاغ Nadec/اليمن #7: البعثة
    كانت تسقط على 429 إلى فجوة فوراً بينما لقطة مخزَّنة صالحة موجودة."""
    from silk_trends_agent import trends_interest_resilient
    term = str(args.get("term") or ctx.get("product") or "").strip()
    if not term:
        return [DataPoint(None, "Google Trends", 0.0, "لا كلمة بحث", _today())]
    market = ctx["market"]
    timeframe = str(args.get("timeframe") or "today 12-m")
    return [trends_interest_resilient(term, geo=(market.iso2 or None),
                                      timeframe=timeframe)]


def _tool_trends_context(args: dict, ctx: dict) -> list[DataPoint]:
    """R3: سياق طلب أغنى — استعلامات مرتبطة (شائعة/صاعدة)، مواضيع صاعدة،
    وتوزيع إقليمي. كل بند نقطة بيانات قابلة للاستشهاد؛ لا شيء => فجوة معلنة."""
    from silk_trends_agent import trends_context
    term = str(args.get("term") or ctx.get("product") or "").strip()
    if not term:
        return [DataPoint(None, "Google Trends", 0.0, "لا كلمة بحث", _today())]
    market = ctx["market"]
    geo = market.iso2 or None
    timeframe = str(args.get("timeframe") or "today 12-m")
    data = trends_context(term, geo=geo, timeframe=timeframe)
    conf = float(data.get("confidence") or 0.6)
    geo_txt = market.iso2 or "WW"
    dps: list[DataPoint] = []
    for it in data.get("related_top", []):
        dps.append(DataPoint({"related_query": it["label"], "interest": it["value"]},
                             "Google Trends", conf,
                             f"استعلام مرتبط شائع بـ'{term}' (geo={geo_txt})", _today()))
    for it in data.get("related_rising", []):
        dps.append(DataPoint({"rising_query": it["label"], "growth": it["value"]},
                             "Google Trends", conf,
                             f"استعلام مرتبط صاعد بـ'{term}' (geo={geo_txt})", _today()))
    for it in data.get("topics_rising", []):
        dps.append(DataPoint({"rising_topic": it["label"], "growth": it["value"]},
                             "Google Trends", conf,
                             f"موضوع صاعد مرتبط بـ'{term}' (geo={geo_txt})", _today()))
    for it in data.get("regions", []):
        dps.append(DataPoint({"region": it["label"], "interest": it["value"]},
                             "Google Trends", conf,
                             f"توزيع إقليمي لاهتمام '{term}' (geo={geo_txt})", _today()))
    if not dps:
        return [DataPoint(None, "Google Trends", 0.0,
                          data.get("note") or "لا سياق اتجاهات مرتبط", _today())]
    return dps


def _tool_faostat_supply(args: dict, ctx: dict) -> list[DataPoint]:
    from silk_faostat_agent import per_capita_supply
    item = str(args.get("item") or ctx.get("product") or "").strip()
    if not item:
        return [DataPoint(None, "FAOSTAT", 0.0, "لا اسم سلعة غذائية", _today())]
    market = ctx["market"]
    year = args.get("year")
    return [per_capita_supply(market.iso3, item, year=int(year) if year else None)]


def _preferred_domains(ctx: dict) -> list[str]:
    """النطاقات المُفضَّلة لبعثة هذا السياق (Wave 2) — من silk_missions،
    استيراد كسول (missions يستورد هذا الملف، فالاستيراد على مستوى الوحدة دورة)."""
    key = str(ctx.get("mission_key") or "").strip()
    if not key:
        return []
    try:
        from silk_missions import PREFERRED_DOMAINS
    except Exception:  # noqa: BLE001 — غياب الخريطة لا يكسر البحث
        return []
    return list(PREFERRED_DOMAINS.get(key) or [])


def _tool_web_search(args: dict, ctx: dict) -> list[DataPoint]:
    from silk_websearch_agent import web_search, web_search_prioritized
    query = str(args.get("query") or "").strip()
    if not query:
        return [DataPoint(None, "Web Search", 0.0, "استعلام فارغ", _today())]
    num = int(args.get("num") or 5)
    # R1: نطاق الدولة/لغة الواجهة — من وسيط كلود إن مرّره، وإلا من مرجع locale
    # للسوق (gl/hl مشتقّان من السوق لا مُخمَّنان). فارغ => بحث عام كالسابق.
    gl = str(args.get("gl") or "").strip() or _locale_gl(ctx)
    hl = str(args.get("hl") or "").strip() or _locale_hl(ctx)
    n = min(max(num, 1), 10)
    # Wave 2 (دمج مصادر جديدة): بعثة لها نطاقات مُفضَّلة => انحياز مُقيَّد
    # site: يُرتّب نتائجها أولاً موسومة دليلاً ثانوياً ◐؛ غيرها => بحث عام.
    domains = _preferred_domains(ctx)
    if domains:
        return web_search_prioritized(query, num=n, gl=gl or None,
                                      hl=hl or None, preferred_domains=domains)
    return web_search(query, num=n, gl=gl or None, hl=hl or None)


def _tool_gdelt_news(args: dict, ctx: dict) -> list[DataPoint]:
    # WS8: سلسلة تعطيلٍ نظيفة GDELT → Google News RSS → Serper — فشل GDELT
    # (429/حجب IP سحابي/لا نتيجة) لم يعد يسقط الخط إلى فجوة مباشرةً؛ التِير
    # المجاني بلا مفتاح (Google News RSS) يتوسّط قبل Serper، والفجوة تُعلَن
    # فقط بعد استنفاد السلسلة كاملةً (لا اختلاق).
    from silk_google_news_agent import news_with_fallback
    query = str(args.get("query") or "").strip()
    if not query:
        return [DataPoint(None, "GDELT", 0.0, "استعلام فارغ", _today())]
    market = ctx["market"]
    months = int(args.get("months") or 12)
    return news_with_fallback(query, market=market.name_en, months=months,
                              gl=_locale_gl(ctx), hl=_locale_hl(ctx))


def _tool_openalex_search(args: dict, ctx: dict) -> list[DataPoint]:
    from silk_openalex_agent import openalex_search
    query = str(args.get("query") or "").strip()
    if not query:
        return [DataPoint(None, "OpenAlex", 0.0, "استعلام فارغ", _today())]
    return openalex_search(query, max_records=int(args.get("max_records") or 5))


def _tool_channels_importers(args: dict, ctx: dict) -> list[DataPoint]:
    """قنوات التوزيع والمستوردون المرشَّحون — reuses the existing free-web
    DistributionChannelsAgent/ImportersAgent logic (§مهمة channels_importers)
    instead of duplicating their web-search-candidate discipline."""
    market = ctx["market"]
    product = str(args.get("product") or ctx.get("product") or "").strip()
    if not product:
        return [DataPoint(None, "Web Search", 0.0, "لا اسم منتج", _today())]
    which = str(args.get("which") or "both").strip().lower()
    task = {"product": product, "market": market.name_en,
           "num": int(args.get("num") or 3)}
    out: list[DataPoint] = []
    if which in ("channels", "both"):
        from silk_channels_agent import DistributionChannelsAgent
        out.extend(DistributionChannelsAgent().run(task).findings)
    if which in ("importers", "both"):
        from silk_importers_agent import ImportersAgent
        out.extend(ImportersAgent().run(task).findings)
    return out or [DataPoint(None, "Web Search", 0.0,
                             f"لا مرشّحين لـ{product} في {market.name_en}",
                             _today())]


def _tool_eurostat_eu_signals(args: dict, ctx: dict) -> list[DataPoint]:
    """إشارات يوروستات الإضافية (المرحلة ٢ج، خيار B) — حصة إنفاق الغذاء من
    مسح ميزانية الأسرة، وعدد السكان المولودين خارج السوق. **أسواق الاتحاد
    الأوروبي/EFTA فقط** — امتناع معلن تلقائي خارجها (راجع
    silk_eurostat_agent للتفاصيل والقيود)."""
    from silk_eurostat_agent import (
        foreign_born_population_count, household_food_expenditure_share)
    market = ctx["market"]
    which = str(args.get("which") or "both").strip().lower()
    year = args.get("year")
    y = int(year) if year else None
    out: list[DataPoint] = []
    if which in ("household_expenditure", "both"):
        out.append(household_food_expenditure_share(market.iso3, market.iso2, y))
    if which in ("foreign_born", "both"):
        out.append(foreign_born_population_count(market.iso3, market.iso2, y))
    return out


def _tool_lookup_reference(args: dict, ctx: dict) -> list[DataPoint]:
    table = str(args.get("table") or "").strip().lower()
    market = ctx["market"]
    if table == "requirements":
        from silk_requirements_agent import RequirementsAgent
        report = RequirementsAgent().run(
            {"market_iso3": market.iso3, "hs_code": ctx.get("hs_code")})
        return report.findings
    path = _REF_TABLES.get(table)
    if not path:
        return [DataPoint(None, "Silk L1 reference", 0.0,
                          f"جدول غير معروف: {table!r} — يجب أن يكون أحد "
                          f"{sorted(_REF_TABLES) + ['requirements']}", _today())]
    rows = _load_csv(path)
    matched = [r for r in rows if (r.get("iso3") or "").strip().upper() == market.iso3]
    if not matched:
        return [DataPoint(None, "Silk L1 reference", 0.0,
                          f"{table}: لا صف لِ {market.iso3} ({market.name_en}) "
                          "— فجوة مرجعية معلنة لهذا السوق", _today())]
    return [DataPoint(dict(r), r.get("source") or "Silk L1 reference",
                      float(r.get("confidence") or 0.7),
                      r.get("note") or f"مرجع {table}", _today())
           for r in matched]


TOOLS: dict[str, dict] = {
    "comtrade_imports": {
        "fn": _tool_comtrade_imports,
        "spec": {
            "name": "comtrade_imports",
            "description": ("حجم استيراد السوق المستهدف لرمز HS هذه المهمة عبر "
                            "سنوات محدَّدة (UN Comtrade، حقيقي لا تقديري). "
                            "Import volume of the mission's HS code into the "
                            "target market, by year."),
            "input_schema": {"type": "object", "properties": {
                "years": {"type": "array", "items": {"type": "integer"},
                          "description": "calendar years (default: last 3)"}}},
        },
    },
    "comtrade_competitors": {
        "fn": _tool_comtrade_competitors,
        "spec": {
            "name": "comtrade_competitors",
            "description": ("الدول المورّدة لرمز HS هذه المهمة إلى السوق "
                            "المستهدف بالاسم والحصة ومؤشر تركّز HHI (UN "
                            "Comtrade ثنائي الأطراف، حقيقي دوماً — لا يعتمد "
                            "على بحث الويب). استدعها أولاً في بعثة المنافسين "
                            "قبل بحث أسماء الشركات. Country-level supplier "
                            "shares + HHI concentration for the mission's "
                            "HS code into the target market."),
            "input_schema": {"type": "object", "properties": {
                "year": {"type": "integer"},
                "top_n": {"type": "integer",
                         "description": "1-20, default 10"}}},
        },
    },
    "worldbank_indicator": {
        "fn": _tool_worldbank_indicator,
        "spec": {
            "name": "worldbank_indicator",
            "description": "مؤشر اقتصادي/حوكمي من البنك الدولي للسوق المستهدف. "
                           "A World Bank indicator for the target market.",
            "input_schema": {"type": "object", "properties": {
                "indicator": {"type": "string", "enum": sorted(_WB_INDICATORS)},
                "year": {"type": "integer"}}, "required": ["indicator"]},
        },
    },
    "wits_tariff": {
        "fn": _tool_wits_tariff,
        "spec": {
            "name": "wits_tariff",
            "description": "التعريفة الجمركية المطبَّقة (WITS) لرمز HS هذه "
                           "المهمة من شريك (افتراضياً السعودية) للسوق المستهدف.",
            "input_schema": {"type": "object", "properties": {
                "partner_iso3": {"type": "string",
                                 "description": "default 'SAU'"},
                "year": {"type": "integer"}}},
        },
    },
    "imf_indicator": {
        "fn": _tool_imf_indicator,
        "spec": {
            "name": "imf_indicator",
            "description": "مؤشر اقتصاد كلي من صندوق النقد الدولي (IMF WEO) "
                           "للسوق المستهدف: نمو الناتج الحقيقي/التضخم/رصيد "
                           "الحساب الجاري — يثري المخاطر والاقتصاد الكلي بجانب "
                           "بيانات صرف البنك الدولي. كل قيمة موسومة بمصدرها "
                           "وسنتها؛ الفشل فجوة معلنة لا اختلاق.",
            "input_schema": {"type": "object", "properties": {
                "indicator": {"type": "string",
                              "enum": ["gdp_growth", "inflation",
                                       "current_account"]},
                "year": {"type": "integer"}}, "required": ["indicator"]},
        },
    },
    "trends_interest": {
        "fn": _tool_trends_interest,
        "spec": {
            "name": "trends_interest",
            "description": "متوسط اهتمام بحث جوجل تريندز (0-100) لكلمة في "
                           "السوق المستهدف — استدعها عدة مرات بمصطلحات/مديات "
                           "زمنية مختلفة (لا نداء واحد سطحي): timeframe="
                           "'today 5-y' لاتجاه خمس سنوات، 'today 12-m' "
                           "(الافتراضي) لموسمية العام الأخير.",
            "input_schema": {"type": "object", "properties": {
                "term": {"type": "string"},
                "timeframe": {"type": "string",
                             "description": "e.g. 'today 12-m' (default, "
                                            "seasonality) or 'today 5-y' "
                                            "(long-run trend)"}}},
        },
    },
    "trends_context": {
        "fn": _tool_trends_context,
        "spec": {
            "name": "trends_context",
            "description": "سياق طلب أغنى من جوجل تريندز للسوق المستهدف: "
                           "الاستعلامات المرتبطة (الشائعة والصاعدة)، المواضيع "
                           "الصاعدة، والتوزيع الإقليمي للاهتمام — لفهم ماذا "
                           "يبحث المستهلك المحلي فعلاً حول الفئة (لا مجرد رقم "
                           "اهتمام واحد). نداء واحد يعيد الحزمة كاملة؛ ما لا "
                           "يتوفّر يُعلَن فجوة لا يُختلَق.",
            "input_schema": {"type": "object", "properties": {
                "term": {"type": "string"},
                "timeframe": {"type": "string",
                             "description": "e.g. 'today 12-m' (default) or "
                                            "'today 5-y'"}}},
        },
    },
    "faostat_supply": {
        "fn": _tool_faostat_supply,
        "spec": {
            "name": "faostat_supply",
            "description": "نصيب الفرد من سلعة غذائية (كجم/سنة، FAOSTAT) في "
                           "السوق المستهدف — للمنتجات الغذائية فقط.",
            "input_schema": {"type": "object", "properties": {
                "item": {"type": "string",
                         "description": "FAOSTAT item name, e.g. 'Dates'"},
                "year": {"type": "integer"}}},
        },
    },
    "web_search": {
        "fn": _tool_web_search,
        "spec": {
            "name": "web_search",
            "description": "بحث ويب عام (نتائج عضوية) — استخدمه بلغة السوق "
                           "المستهدف. النطاق (gl) ولغة السوق (hl) يُطبَّقان "
                           "تلقائياً من مرجع السوق؛ لا حاجة لتمريرهما إلا "
                           "لتجاوزٍ مقصود.",
            "input_schema": {"type": "object", "properties": {
                "query": {"type": "string"},
                "num": {"type": "integer", "description": "1-10, default 5"},
                "gl": {"type": "string", "description": "تجاوز اختياري لنطاق "
                       "الدولة ISO 3166-1 alpha-2 (يُشتق تلقائياً من السوق)"},
                "hl": {"type": "string", "description": "تجاوز اختياري للغة "
                       "الواجهة (تُشتق تلقائياً من لغة السوق)"}},
                "required": ["query"]},
        },
    },
    "gdelt_news": {
        "fn": _tool_gdelt_news,
        "spec": {
            "name": "gdelt_news",
            "description": "عناوين أخبار حقيقية (GDELT) متعلقة بالاستعلام "
                           "والسوق المستهدف خلال الأشهر الأخيرة.",
            "input_schema": {"type": "object", "properties": {
                "query": {"type": "string"},
                "months": {"type": "integer", "description": "1-24, default 12"}},
                "required": ["query"]},
        },
    },
    "openalex_search": {
        "fn": _tool_openalex_search,
        "spec": {
            "name": "openalex_search",
            "description": "بحث في أدبيات أكاديمية/صناعية حقيقية (OpenAlex، "
                           "بديل Scopus المجاني) — عنوان/سنة/مصدر/ملخّص/DOI "
                           "لسند إضافي على استهلاك/سوق/مخاطر القطاع.",
            "input_schema": {"type": "object", "properties": {
                "query": {"type": "string"},
                "max_records": {"type": "integer",
                                "description": "1-25, default 5"}},
                "required": ["query"]},
        },
    },
    "channels_importers": {
        "fn": _tool_channels_importers,
        "spec": {
            "name": "channels_importers",
            "description": "قنوات توزيع ومستوردون مرشَّحون (فعلي+رقمي) من "
                           "بحث الويب — مرشَّحات غير موثَّقة، تحتاج تأكيداً.",
            "input_schema": {"type": "object", "properties": {
                "which": {"type": "string",
                         "enum": ["channels", "importers", "both"]},
                "num": {"type": "integer", "description": "per lens, default 3"}}},
        },
    },
    "eurostat_eu_signals": {
        "fn": _tool_eurostat_eu_signals,
        "spec": {
            "name": "eurostat_eu_signals",
            "description": ("إشارات استهلاك/هجرة إضافية من يوروستات — حصة "
                            "إنفاق الغذاء من مسح ميزانية الأسرة، وعدد "
                            "السكان المولودين خارج السوق. **أسواق الاتحاد "
                            "الأوروبي/EFTA فقط** — امتناع معلن تلقائي "
                            "لغيرها، لا تستدعِها لسوق خارج أوروبا."),
            "input_schema": {"type": "object", "properties": {
                "which": {"type": "string",
                         "enum": ["household_expenditure", "foreign_born",
                                 "both"]},
                "year": {"type": "integer"}}},
        },
    },
    "lookup_reference": {
        "fn": _tool_lookup_reference,
        "spec": {
            "name": "lookup_reference",
            "description": "اقرأ مرجعاً ثابتاً للسوق المستهدف من جداول سِلك "
                           "(demographics/ports/agreements/requirements) — لا "
                           "شبكة، بيانات مسبقة التوثيق.",
            "input_schema": {"type": "object", "properties": {
                "table": {"type": "string",
                         "enum": sorted(list(_REF_TABLES) + ["requirements"])}},
                "required": ["table"]},
        },
    },
}


def _isolate_external(v):
    """اعزل حقل بيانات أداة خارجي — isolate a tool-result field before it
    reaches Claude, regardless of shape (str/dict/list). أرقام صرفة
    (int/float بلا نص) تُترك كما هي — لا نص فيها يحمل حقناً محتملاً، وعزلها
    يمنع كلود من استخدامها حسابياً بلا داعٍ. كل ما عداها (نص، أو بنية تحمل
    نصاً كعناوين نتائج البحث) يُحوَّل لنص ويُعزل — الثغرة التي غطّاها هذا
    الإصلاح: `note` وحده كان يُعزل سابقاً بينما `value`/`source` يمران خاماً.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return _isolate(str(v))
    return v


def _execute_tool(name: str, args: dict, ctx: dict) -> list[DataPoint]:
    entry = TOOLS.get(name)
    if not entry:
        return [DataPoint(None, "tool", 0.0, f"أداة غير معروفة: {name!r}",
                          _today())]
    try:
        return entry["fn"](args or {}, ctx) or []
    except Exception as e:  # noqa: BLE001 — أداة فاشلة = فجوة موسومة لا عطل
        note = f"{name} tool error: {type(e).__name__}: {e}"
        log.warning(note)
        return [DataPoint(None, name, 0.0, note, _today())]


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.S | re.I)


def _truncate_at_word(text: str, max_len: int) -> str:
    """قصّ عند حدّ كلمة كاملة — بلاغ حي (الموجة ٩): نقاط سرد كانت تنتهي
    منتصف كلمة ("لا تتوفر من أد") بسبب قصّ حرفي [:N] لملاحظة الاستشهاد/
    الملخّص هنا. لا يقصّ أبداً منتصف كلمة؛ يتراجع لآخر مسافة قبل الحد."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    sp = cut.rfind(" ")
    if sp > max_len * 0.5:
        cut = cut[:sp]
    return cut.rstrip() + "…"


def _json_candidates(text: str) -> list[str]:
    """مرشّحو نص JSON من رد كلود، بترتيب الأولوية — بلاغ حي (الموجة ٨):
    ردود مسيّجة بـ```json...``` كانت تُفقَد كاملةً (pricing_scout/
    opportunity_gaps) لأن آخر '}' في **النص كله** يقع أحياناً بعد السياج
    (تعليق ختامي بأقواس معقوفة، أو سياج توضيحي ثانٍ) فيمتد المقطع
    المُستخرَج فوق حدود JSON الحقيقي فيفشل التفسير بالكامل.

    الآن: أول محاولة داخل كل سياج ```...``` على حدة (المحتوى المعزول لا
    يمكن أن يحوي ما بعد السياج) — وإن غاب السياج أو فشل تفسيره، احتياط
    السلوك القديم (أول '{' لآخر '}' في النص كاملاً)."""
    candidates = [m.group(1).strip() for m in _FENCE_RE.finditer(text)
                 if m.group(1).strip()]
    candidates.append(text)
    return candidates


_FINAL_ANSWER_KEYS = ("findings", "gaps", "summary")

# توجيه الإنهاء القسري (الموجة ٨) — جولة واحدة فقط، بلا أدوات، حين تُستنفد
# الميزانية أو يتوقف كلود مبكراً برد لا يشبه الصيغة النهائية المطلوبة.
_FINALIZE_NUDGE = (
    "توقّف — لا مزيد من نداءات الأدوات متاحة الآن. اكتب ردّك النهائي فوراً "
    "بصيغة JSON فقط (لا نص خارجها، لا سؤال توضيحي) من النتائج التي جمعتها "
    "حتى الآن: "
    '{"findings":[{"claim":"...","datapoint_ids":["dp1"],'
    '"confidence":0.0-1.0}],"gaps":["ما لم تستطع تأكيده"],"summary":"..."}. '
    "إن لم تجد شيئاً مؤكَّداً بعد، أعد findings فارغة وفصّل السبب في gaps — "
    "لا تخترع بياناً لتملأ الحقل.")

# بلاغ حي إنتاجي (opportunity_gaps + الطبقة ٣ SWOT، تمور/هولندا): فشل تفسير
# JSON نهائي بعد الإنهاء القسري كان يستسلم فوراً بفجوة معلنة بلا أي محاولة
# تصحيح — رغم أن الجولة الإنهائية القسرية تنجح غالباً في انتزاع رد "يشبه"
# JSON نهائياً، السياج/نصّ زائد لاحق أحياناً يفسد التفسير رغم استراتيجيات
# _json_candidates المتعددة. محاولة إصلاح واحدة فقط (ليست حلقة — نفس انضباط
# _FINALIZE_NUDGE أحادي الطلقة، §mission-tuning-and-evals) تقتبس سبب الفشل
# صراحة وتُذكِّر بالصيغة الدقيقة. فشلها أيضاً = فجوة معلنة كالسابق، لا اختلاق.
_JSON_PARSE_FAILURE_GAP = "رد كلود غير قابل للتفسير كـ JSON"
_JSON_REPAIR_NUDGE = (
    "ردّك السابق تعذّر تفسيره كـJSON صالح. أعد الإجابة الآن — **بصيغة JSON "
    "فقط، لا نص قبلها أو بعدها، لا سياج ```، لا تعليق توضيحي** — مطابقة "
    "تماماً لهذا الشكل: "
    '{"findings":[{"claim":"...","datapoint_ids":["dp1"],'
    '"confidence":0.0-1.0}],"gaps":["..."],"summary":"..."}. '
    "إن تعذّر توثيق أي بند بمعرّف نقطة بيانات صالح، أعد findings فارغة "
    "وفصّل السبب في gaps — لا تخترع بياناً لتملأ الحقل.")


def _looks_like_final_answer(text: str) -> bool:
    """هل يشبه هذا النص رداً نهائياً صالحاً (JSON بمفاتيح findings/gaps/
    summary)؟ — فحص رخيص يعيد استخدام _json_candidates (يدعم السياج نفسه)
    لتقرير: أنُرسل جولة إنهاء قسرية أم نقبل هذا الرد كما هو."""
    for cand in _json_candidates(text or ""):
        start, end = cand.find("{"), cand.rfind("}")
        if start < 0 or end < start:
            continue
        try:
            obj = json.loads(cand[start:end + 1])
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict) and any(k in obj for k in _FINAL_ANSWER_KEYS):
            return True
    return False


def _parse_output(text: str | None, registry: dict[str, DataPoint]) -> dict:
    """فسِّر الرد النهائي — validate findings against the cited-datapoint
    registry; an uncited/mis-cited claim is dropped + logged (never kept)."""
    if not text or not text.strip():
        return {"findings": [], "gaps": ["لا رد نهائي من كلود"], "summary": "",
                "dropped": []}
    obj: dict | None = None
    for cand in _json_candidates(text):
        start, end = cand.find("{"), cand.rfind("}")
        if start < 0 or end < start:
            continue
        try:
            parsed = json.loads(cand[start:end + 1])
        except Exception:  # noqa: BLE001 — مرشّح فاشل، جرّب التالي
            continue
        if isinstance(parsed, dict):
            obj = parsed
            break
    if obj is None:
        # بلاغ حي (risk_news): رد لا يفسَّر كـJSON إطلاقاً كان يضع النص
        # الخام كـsummary — لو كان هذا النص نفسه يشبه JSON مشوَّهاً
        # (يبدأ بـ{/[), يتسرّب حرفياً للواجهة/"حدود البحث". لا نص خام
        # يشبه JSON يُعرَض أبداً؛ نص نثري عادي فشل تفسيره يبقى كتلميح
        # تشخيصي قصير فقط.
        stripped = text.strip()
        safe_summary = "" if stripped[:1] in "{[" else stripped[:300]
        return {"findings": [], "gaps": ["رد كلود غير قابل للتفسير كـ JSON"],
                "summary": safe_summary, "dropped": []}
    if not any(k in obj for k in _FINAL_ANSWER_KEYS) and "claim" in obj:
        # رد مشوَّه: بند واحد بلا الغلاف المطلوب ({"findings":[...],...})
        # — بلاغ حي (risk_news): كان يُرفَض بالكامل ويتسرّب حرفياً كـ
        # summary رغم كونه JSON صالحاً. الآن يُعامَل كبند وحيد بنفس مسار
        # الاستخراج القياسي أدناه — يُقبل إن استُشهِد بمعرّف صالح، وإلا
        # يُسقَط بسبب معلن في dropped (لا اختلاق، ولا تسريب صيغة خام).
        obj = {"findings": [obj], "gaps": [], "summary": ""}

    kept: list[dict] = []
    dropped: list[dict] = []
    zero_conf_gaps: list[str] = []
    for it in obj.get("findings") or []:
        if not isinstance(it, dict):
            continue
        claim = str(it.get("claim") or "").strip()
        raw_ids = [i for i in (it.get("datapoint_ids") or []) if isinstance(i, str)]
        valid_ids = [i for i in raw_ids if i in registry]
        if not claim or not valid_ids:
            log.warning("LLM agent finding dropped (uncited datapoint): %r "
                       "(cited=%s)", claim, raw_ids)
            dropped.append({"claim": claim, "cited": raw_ids,
                            "reason": "no valid cited datapoint_id"})
            continue
        try:
            conf = float(it.get("confidence"))
        except (TypeError, ValueError):
            conf = min(registry[i].confidence for i in valid_ids)
        conf = round(max(0.0, min(1.0, conf)), 2)
        if conf <= 0.0:
            # عقد عدم الاختلاق (بلاغ حي — حارس المراقبة، demand_trends):
            # قيمة غير فارغة بثقة 0.0 زوج متناقض؛ يحدث حين يصرّح النموذج
            # بثقة صفرية أو حين تُورَث `min()` من نقطة فجوة مستشهَد بها
            # (ثقتها 0.0 بحكم العقد). ادعاء بلا ثقة ليس بنداً — فجوة تُعلَن.
            log.warning("LLM agent zero-confidence claim declared as gap: %r",
                        claim)
            dropped.append({"claim": claim, "cited": raw_ids,
                            "reason": "zero-confidence claim -> declared gap"})
            zero_conf_gaps.append(claim)
            continue
        kept.append({"claim": claim, "datapoint_ids": valid_ids,
                    "confidence": conf,
                    "category": str(it.get("category") or "").strip()})

    gaps = [str(g).strip() for g in (obj.get("gaps") or []) if str(g).strip()]
    gaps.extend(g for g in zero_conf_gaps if g not in gaps)
    return {"findings": kept, "gaps": gaps,
            "summary": str(obj.get("summary") or "").strip(), "dropped": dropped}


def _mark_cache_boundary(messages: list[dict]) -> None:
    """علّم آخر رسالة في `messages` بـ`cache_control` (ephemeral) — نقطة
    التخزين المؤقت تتقدّم مع كل جولة فتُخزَّن الجولات السابقة كاملة (المرحلة ٠).

    يزيل الوسم من كل الرسائل أولاً (بما فيها ما عُلِّم في جولة سابقة) قبل
    وضعه على الأخيرة فقط — Anthropic يسمح بأربع نقاط تخزين كحد أقصى لكل نداء
    (system + tools + هذه)، وترك وسوم قديمة متراكمة عبر الجولات يتجاوز الحد."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    if not messages:
        return
    last = messages[-1]
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content,
                            "cache_control": {"type": "ephemeral"}}]
    elif isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}


def _run_loop(mission: dict, ctx: dict, budget: dict,
              timeout: float | None = None, model: str | None = None) -> dict:
    """الحلقة المحكومة بالميزانية — tool_use/tool_result rounds until a final
    JSON answer or budget exhaustion (then one forced tools-off round).

    كل جولة (نداء كلود + كل نداء أداة) تُسجَّل عبر silk_trace.record_event
    إن كان التتبّع مفعَّلاً (silk_trace.trace_context) — no-op بلا تكلفة
    خارج تشغيلة تنقيح صريحة (§docs/TUNING.md، الموجة ٦).

    `timeout`: مهلة نداء كلود لكل جولة — None يترك `_call_tools` يستعمل
    مهلته الافتراضية (بعثات الأدوات القياسية الاثنتا عشرة). المحلل الشامل
    (`silk_market_analyst`) يمرّر `silk_ai_judge._LONG_TIMEOUT` صراحة — بلاغ
    حي إنتاجي: مدخله (نتائج البعثات كاملة) يتجاوز المهلة القياسية بانتظام.
    """
    import time as _time

    import silk_context
    import silk_trace

    mission_key = mission.get("key") or mission.get("name") or "?"
    t_mission_start = _time.monotonic()

    allowed = mission.get("allowed_tools") or []
    # None لا [] حين لا أدوات (بعثة المحلل الشامل، allowed_tools=[]) — بلاغ
    # حي (الموجة ٩): مصفوفة tools فارغة صراحة قد تُفسَّر مختلفاً عن غيابها
    # كلياً في واجهات LLM؛ الغياب الصريح أوضح دلالياً ولا مجازفة.
    tool_specs = [TOOLS[k]["spec"] for k in allowed if k in TOOLS] or None
    system = f"{_PRINCIPLE}\n\n{mission.get('instructions', '')}"
    market: MarketRef = ctx["market"]
    hs = ctx.get("hs_code") or ""

    registry: dict[str, DataPoint] = {}
    next_id = [1]
    tool_calls_used = [0]

    def _register(dp: DataPoint) -> str:
        did = f"dp{next_id[0]}"
        next_id[0] += 1
        registry[did] = dp
        return did

    # نتائج الوكلاء السابقين (opportunity_gaps، الوكيل ١٢ بلا أدوات خاصة به —
    # §الموجة ٢): تُسجَّل في نفس سجل نقاط البيانات **قبل** بدء الحلقة، فيصير
    # الاستشهاد بها بمعرّف dpN مطابقاً لأي استشهاد بنتيجة أداة حية — لا مسار
    # تحقق موازٍ. Prior findings are pre-registered as real DataPoints so the
    # single citation rule (claim -> real registry id) stays uniform.
    prior_block = ""
    for dp in (ctx.get("extra_findings") or []):
        did = _register(dp)
        prior_block += f"[{did}] {dp.value} — المصدر: {dp.source} — {dp.note}\n"

    user_intro = (
        f"المهمة: {_isolate(str(mission.get('name') or mission.get('key') or ''))}\n"
        f"المنتج: {_isolate(str(ctx.get('product') or ''))}\n"
        f"السوق: {_isolate(f'{market.name_en} ({market.iso3})')}\n"
        + (f"رمز HS: {_isolate(str(hs))}\n" if hs else "")
        + (f"نتائج الوكلاء السابقين (حلّلها ولا تُعِد جمعها — استشهد "
           f"بمعرّفاتها dpN كأي نقطة بيانات):\n{_isolate(prior_block)}\n"
          if prior_block else "")
        + (f"سياق إضافي غير قابل للاستشهاد المباشر (خيوط تقاطع محسوبة "
           f"سابقاً — للاستئناس السردي فقط):\n{_isolate(str(ctx['extra_context']))}\n"
          if ctx.get("extra_context") else "")
        + "استخدم الأدوات المتاحة لجمع حقائق حقيقية، ثم أعد النتيجة النهائية "
        "بصيغة JSON فقط (لا نص خارجها): "
        '{"findings":[{"claim":"...","datapoint_ids":["dp1"],'
        '"confidence":0.0-1.0,"category":"..."(اختياري)}],"gaps":["..."],'
        '"summary":"..."}. '
        "كل claim يجب أن يستشهد بمعرّف نقطة بيانات (datapoint_ids) عاد فعلاً "
        "من نداء أداة أو من نتائج الوكلاء السابقين — بند بلا استشهاد صحيح يُسقَط.")
    messages: list[dict] = [{"role": "user", "content": user_intro}]

    tool_budget = int(budget.get("tool_calls", _DEFAULT_BUDGET["tool_calls"]))
    max_tokens = int(budget.get("max_output_tokens",
                                _DEFAULT_BUDGET["max_output_tokens"]))
    max_rounds = tool_budget + 2  # هامش أمان: نص نهائي + جولة إجبار بلا أدوات
    final_text: str | None = None
    global_cap_hit = False
    # بلاغ حي (الموجة ٨): consumer_culture/customs_requirements استنفدا
    # الميزانية بلا رد نهائي — الجولة الأخيرة كانت تُحذف الأدوات فقط دون
    # توجيه صريح، فيواصل كلود سرداً/أسئلة توضيحية بدل JSON. الآن: جولة
    # إنهاء قسرية واحدة فقط (لا أكثر) تحمل توجيهاً صريحاً "أجب الآن نهائياً"
    # — تُرسَل إما عند نفاد الميزانية، أو حين يتوقف كلود مبكراً برد لا يشبه
    # الصيغة النهائية المطلوبة (JSON بمفتاح findings/gaps/summary).
    forced_finalization_sent = False
    # بعثات بلا أدوات إطلاقاً (المحلل الشامل، allowed_tools=[]) لم "تنفد"
    # ميزانيتها — لم تكن تملك أدوات أصلاً، فلا داعي لتوجيه إنهاء قسري في
    # الجولة صفر (كان سيُرسَل قبل أي فرصة فعلية للتحليل — بلاغ حي، الموجة ٩).
    had_tools = tool_specs is not None

    for _round in range(max_rounds):
        # السقف الكلي عبر التحليل بأكمله (١٢ بعثة معاً — قسم «الميزانية
        # والأمان» بالتكليف): يقرأ عدّاد data_economics المشترك (نفس
        # القاموس يُشارَك بين خيوط silk_missions.run_all_missions بعد نسخ
        # السياق — راجع تعليق copy_context هناك)؛ تجاوزه = إنهاء رشيق
        # (جولة أخيرة بلا أدوات) لا كسر — نفس آلية استنفاد الميزانية المحلية.
        counter = silk_context.data_counter()
        if counter is not None and not global_cap_hit:
            llm_cap = int(os.environ.get("SILK_RESEARCH_MAX_LLM_CALLS", "40"))
            tool_cap = int(os.environ.get("SILK_RESEARCH_MAX_TOOL_CALLS", "100"))
            if counter["llm_calls"] >= llm_cap or counter["tool_calls"] >= tool_cap:
                global_cap_hit = True
        offer_tools = (tool_specs if tool_calls_used[0] < tool_budget
                      and not global_cap_hit else None)
        if (offer_tools is None and had_tools
                and not forced_finalization_sent):
            messages.append({"role": "user", "content": _FINALIZE_NUDGE})
            forced_finalization_sent = True
        _mark_cache_boundary(messages)
        t_round = _time.monotonic()
        resp = _call_tools(system, messages, tools=offer_tools,
                           max_tokens=max_tokens, model=(model or _MODEL), timeout=timeout)
        silk_context.count_data("llm_calls")
        elapsed_ms = round((_time.monotonic() - t_round) * 1000)
        if resp is None:
            reason = _ai_failure_reason()
            silk_trace.record_event(
                kind="llm_call", mission=mission_key, round=_round,
                tools_offered=bool(offer_tools), elapsed_ms=elapsed_ms,
                result=f"no_response ({reason})")
            return {"findings": [], "gaps": [f"تعذّر نداء كلود ({reason})"],
                    "summary": "", "dropped": [], "registry": registry}
        content = resp.get("content") or []
        silk_trace.record_event(
            kind="llm_call", mission=mission_key, round=_round,
            tools_offered=bool(offer_tools), elapsed_ms=elapsed_ms,
            stop_reason=resp.get("stop_reason"),
            system_prompt=system, last_user_message=messages[-1].get("content"))
        messages.append({"role": "assistant", "content": content})
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses or resp.get("stop_reason") != "tool_use":
            candidate_text = "".join(b.get("text", "") for b in content
                                     if b.get("type") == "text")
            # توقّف مبكر (ميزانية أدوات لم تُستنفد بعد) لكن الرد لا يشبه
            # الصيغة النهائية — فرصة إنهاء قسرية واحدة قبل الاستسلام، بدل
            # قبول رد غير مكتمل صامتاً (مطابقة السلوك عند نفاد الميزانية).
            if (not _looks_like_final_answer(candidate_text)
                    and not forced_finalization_sent
                    and _round < max_rounds - 1):
                messages.append({"role": "user", "content": _FINALIZE_NUDGE})
                forced_finalization_sent = True
                continue
            final_text = candidate_text
            break

        tool_results = []
        for block in tool_uses:
            name = block.get("name")
            t_tool = _time.monotonic()
            dps = _execute_tool(name, block.get("input") or {}, ctx)
            silk_context.count_data("tool_calls")
            tool_calls_used[0] += 1
            ids = [_register(dp) for dp in dps]
            silk_trace.record_event(
                kind="tool_call", mission=mission_key, round=_round,
                tool=name, input=block.get("input") or {},
                output=[{"id": did, "value": dp.value, "source": dp.source,
                        "confidence": dp.confidence} for did, dp in
                       zip(ids, dps)],
                elapsed_ms=round((_time.monotonic() - t_tool) * 1000))
            payload = {"data": [
                {"id": did, "value": _isolate_external(dp.value),
                 "source": _isolate_external(dp.source),
                 "confidence": dp.confidence, "note": _isolate(str(dp.note))}
                for did, dp in zip(ids, dps)]}
            tool_results.append({
                "type": "tool_result", "tool_use_id": block.get("id"),
                "content": json.dumps(payload, ensure_ascii=False, default=str)})
        messages.append({"role": "user", "content": tool_results})

    parsed = _parse_output(final_text, registry)
    # محاولة إصلاح واحدة فقط — فقط عند فشل تفسير JSON تام (لا بنود مُسقَطة
    # لعدم استشهاد صحيح، تلك فجوة أصيلة لا عطل تقني)، وبشرط عدم بلوغ السقف
    # العام (لا تجاوز ميزانية النداءات لمحاولة إصلاح واحدة).
    if parsed["gaps"] == [_JSON_PARSE_FAILURE_GAP] and not global_cap_hit:
        messages.append({"role": "user", "content": _JSON_REPAIR_NUDGE})
        _mark_cache_boundary(messages)
        t_repair = _time.monotonic()
        resp2 = _call_tools(system, messages, tools=None, max_tokens=max_tokens,
                            model=(model or _MODEL), timeout=timeout)
        silk_context.count_data("llm_calls")
        elapsed_ms2 = round((_time.monotonic() - t_repair) * 1000)
        if resp2 is None:
            silk_trace.record_event(
                kind="llm_call", mission=mission_key, round="json_repair",
                tools_offered=False, elapsed_ms=elapsed_ms2,
                result=f"no_response ({_ai_failure_reason()})")
        else:
            content2 = resp2.get("content") or []
            repair_text = "".join(b.get("text", "") for b in content2
                                  if b.get("type") == "text")
            silk_trace.record_event(
                kind="llm_call", mission=mission_key, round="json_repair",
                tools_offered=False, elapsed_ms=elapsed_ms2,
                stop_reason=resp2.get("stop_reason"))
            repaired = _parse_output(repair_text, registry)
            if repaired["gaps"] != [_JSON_PARSE_FAILURE_GAP]:
                parsed = repaired  # الإصلاح نجح — لا محاولة ثانية بأي حال
    if global_cap_hit:
        parsed["gaps"] = list(parsed.get("gaps") or []) + [
            "السقف الكلي لنداءات كلود/الأدوات عبر هذا التحليل بأكمله "
            "(SILK_RESEARCH_MAX_LLM_CALLS/_MAX_TOOL_CALLS) بلغ حدّه — "
            "إنهاء رشيق مبكر لهذا الوكيل"]
    silk_trace.record_event(
        kind="finish", mission=mission_key,
        elapsed_ms=round((_time.monotonic() - t_mission_start) * 1000),
        tool_calls_used=tool_calls_used[0],
        findings_kept=len(parsed["findings"]), dropped=parsed["dropped"],
        gaps=parsed["gaps"], summary=parsed["summary"])
    parsed["registry"] = registry
    parsed["tool_calls_used"] = tool_calls_used[0]
    return parsed


def run_llm_agent(mission: dict, market: MarketRef, product: str = "",
                  hs_code: str | None = None, budget: dict | None = None,
                  instruction: str = "",
                  extra_findings: list[DataPoint] | None = None,
                  extra_context: str = "",
                  timeout: float | None = None,
                  model: str | None = None) -> AgentReport:
    """شغّل وكيل مهمة كلود — the mission-driven tool-use loop as an AgentReport.

    `mission`: {"key","name","instructions","allowed_tools":[...]} — شكل
    `silk_missions.MISSIONS[key]` (الموجة ٢)؛ يُمرَّر صراحةً هنا لأن سجل
    المهام لم يُبنَ بعد (يبقي هذه الموجة قابلة للاختبار مستقلةً).

    `extra_findings`: نتائج وكلاء سابقين (الوكيل ١٢ opportunity_gaps بلا
    أدوات خاصة به — يقرأ فقط) — تُسجَّل كنقاط بيانات قابلة للاستشهاد بها.
    `extra_context`: سياق سردي إضافي غير قابل للاستشهاد المباشر (خيوط
    تقاطع محسوبة سابقاً من correlation.py — الموجة ٣) — يُعزَل ويُلحَق
    للاستئناس فقط، لا يفتح مسار استشهاد ثانياً.
    `timeout`: مهلة نداء كلود صريحة (None = افتراضي `_call_tools`) — راجع
    تعليق `_run_loop`.
    """
    eff_budget = {**_DEFAULT_BUDGET, **(budget or {})}
    # mission_key يصل الأدوات (خاصةً web_search) كي تطبّق النطاقات المُفضَّلة
    # لكل بعثة (الموجة: دمج مصادر جديدة، Wave 2) — لا يفتح مسار استشهاد جديداً.
    ctx = {"market": market, "product": product, "hs_code": hs_code,
          "extra_findings": extra_findings or [], "extra_context": extra_context,
          "mission_key": mission.get("key", "")}
    eff_mission = dict(mission)
    if instruction:
        eff_mission["instructions"] = (
            f"{eff_mission.get('instructions', '')}\n"
            f"توجيه المستخدم (وجّه التركيز فقط — لا تخترع بيانات): "
            f"{_isolate(instruction)}")

    # إسناد التكلفة لكل بعثة (Part C): يسِم كل نداء كلود يجري داخل _run_loop
    # باسم هذه البعثة — silk_context.record_llm_usage يقرأه فيُراكم في
    # data_counter()["mission_usage"][key] فوق الإجمالي القائم. آمن تحت
    # ThreadPoolExecutor (كل خيط بعثة موازٍ يملك نسخة سياق مستقلة عبر
    # copy_context()، فلا تختلط وسوم بعثتين متزامنتين).
    # E2: بعثة على النموذج السريع افتراضياً؛ المستدعي (المحلل الشامل) يمرّر
    # النموذج الذكي صراحةً. `model` صريح يتجاوز الافتراضي.
    eff_model = model or _MISSION_MODEL
    import silk_context
    with silk_context.mission_context(eff_mission.get("key")):
        result = _run_loop(eff_mission, ctx, eff_budget, timeout=timeout,
                           model=eff_model)
    today = _today()
    label = eff_mission.get("name") or eff_mission.get("key") or "LLM agent"
    registry = result.get("registry", {})

    findings: list[DataPoint] = []
    for f in result["findings"]:
        cited = [registry[i] for i in f["datapoint_ids"] if i in registry]
        cited_notes = "؛ ".join(str(c.note) for c in cited)
        # §2/§6 (أمر العمل الرئيس — سجل الأدلة للمدققين): عمود «المصدر» يحمل
        # المصدر العمومي الحقيقي للنقاط المستشهَد بها (UN Comtrade, World
        # Bank, Google Trends, OpenAlex, …) لا اسم البعثة الداخلي ولا وسم
        # «(Claude tool-use)». يُشتقّ من `.source` للنقاط المُسجَّلة فعلاً
        # (registry) — بلا اختلاق: إن غاب مصدر عمومي يبقى اسم البعثة العربي.
        pub_sources = list(dict.fromkeys(
            str(getattr(c, "source", "") or "").strip() for c in cited
            if str(getattr(c, "source", "") or "").strip()))
        # HF1 (بلاغ إسناد مركّب — تقرير قطر): **لا تدمج** المصادر في سلسلةٍ
        # واحدة. كان «، ».join يُنتج معرّفاً مركّباً («IMF WEO، World Bank»)
        # يُعامَل لاحقاً كمعرّفٍ ذرّيٍّ واحد، فيُخطئ إسنادَ الرابط (يوجّه بيانات
        # البنك الدولي لرابط IMF) ويُكرِّر المصدرَ في المراجع. الآن: `source`
        # يبقى ذرّياً (المصدر الأساسيّ الأول)، والقائمةُ الكاملةُ تُحفَظ في
        # `source_ids` ليسطّحها المُصدِّر فيُسنِد كلَّ مصدرٍ لرابطه الصحيح.
        primary_source = pub_sources[0] if pub_sources else label
        prefix = f"[{f['category']}] " if f.get("category") else ""
        findings.append(DataPoint(
            f["claim"], primary_source, f["confidence"],
            _truncate_at_word(f"{prefix}مبني على: {cited_notes}", 500), today,
            source_ids=tuple(pub_sources)))

    failed = not findings
    summary = result.get("summary") or ("لا نتائج مبنية على استشهاد — "
                                        "no grounded findings" if failed else "")
    if result.get("gaps"):
        summary = _truncate_at_word(
            f"{summary} | فجوات: {'؛ '.join(result['gaps'])}", 500)
    if result.get("dropped"):
        summary = _truncate_at_word(
            f"{summary} | أُسقطت {len(result['dropped'])} بند(ود) بلا استشهاد", 600)
    # نداءات الأداة تُلحَق دوماً — لوحة التتبّع (الموجة ٦) تستخرجها من هنا
    # بدل تمديد عقد AgentReport (البقية لا يحملن هذا الحقل، لا داعٍ لسمة جديدة).
    summary = _truncate_at_word(
        f"{summary} | نداءات أدوات: {result.get('tool_calls_used', 0)}", 700)
    return AgentReport(f"LLMAgent:{eff_mission.get('key', label)}", findings,
                       failed, summary)


class LLMMissionAgent(BaseAgent):
    """وكيل مهمة كلود عام — a Claude tool-use agent driven by a mission spec.

    نسخة واحدة لكل مهمة (لا صنف لكل مهمة): `PREF_KEY`/`SOURCE` يُضبطان على
    مستوى النسخة في __init__ — BaseAgent.run() يقرأهما كسمتَي نسخة فتُطبَّق
    حراسة اللوحة (تعطيل/توجيه) طبيعياً بلا تعديل على BaseAgent نفسه.
    """

    PAID = False

    def __init__(self, mission: dict) -> None:
        super().__init__(f"LLMMissionAgent:{mission.get('key', '?')}")
        self.mission = mission
        self.SOURCE = mission.get("name", self.name)
        self.PREF_KEY = mission.get("key", "")

    def _execute(self, task: dict) -> AgentReport:
        market = task.get("market")
        if not isinstance(market, MarketRef):
            return AgentReport(self.name, [], True,
                               "لا MarketRef صالح — market must be a resolved "
                               "MarketRef, not a raw string")
        return run_llm_agent(
            self.mission, market, product=task.get("product", ""),
            hs_code=task.get("hs_code"), budget=task.get("budget"),
            instruction=task.get("instruction", ""),
            extra_findings=task.get("extra_findings"),
            extra_context=task.get("extra_context", ""))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from silk_market_resolver import resolve_market
    ref, _ = resolve_market("Nigeria")
    demo_mission = {"key": "demo", "name": "عرض توضيحي",
                    "instructions": "اجمع حقائق تجارية أساسية عن هذا المنتج.",
                    "allowed_tools": ["comtrade_imports", "worldbank_indicator"]}
    report = run_llm_agent(demo_mission, ref, product="تمور", hs_code="080410")
    print(f"[{'FAILED' if report.failed else 'ok'}] {report.agent_name}: "
          f"{report.summary}")
    for dp in report.findings:
        print(f"  - {dp.value} (ثقة {dp.confidence}) — {dp.note}")
