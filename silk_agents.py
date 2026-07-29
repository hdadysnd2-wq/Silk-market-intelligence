"""بنية الوكلاء المتعددة لسِلك — Silk multi-agent structure.

Manager + three research agents + a jury committee. Each agent pulls ONLY real
public data via the data layer; an agent with no real data sets failed=True and
reports it — it never invents findings (founding principle).
"""
from __future__ import annotations

import datetime
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from silk_data_layer import (
    DataPoint,
    comtrade_trade,
    gdp_per_capita,
    population,
    partner_name,
    primary_value,
)
from silk_data_layer_v2 import ppp_per_capita, market_competitors

log = logging.getLogger(__name__)


@dataclass
class AgentReport:
    """تقرير وكيل — one agent's findings plus its success flag."""

    agent_name: str
    findings: list[DataPoint] = field(default_factory=list)
    failed: bool = False
    summary: str = ""


# ── سجل الوكلاء القانوني · the ONE agent catalog (لوحة «إعدادات الوكلاء») ────
# المصدر الوحيد لصفوف اللوحة: مفتاح + اسم + دور بسطر + مدفوع/مجاني. الواجهة
# تبنيه من GET /settings/agents (لا قائمة موازية تنحرف). كل BaseAgent يحمل
# PREF_KEY يشير لصفّه هنا؛ المفتاح الواحد قد يجمع أكثر من صنف (وكيل التجارة =
# TradeFlowAgent + وكيل حجم السوق البحثي). **لا حقول مفاتيح مصادر هنا** —
# المفاتيح تُضبط في بيئة النشر (Railway env) حصراً.
AGENT_CATALOG: list[dict] = [
    {"key": "trade", "name": "وكيل التجارة",
     "role": "حجم استيراد/تصدير السوق وحجم الفرصة · UN Comtrade", "paid": False},
    {"key": "economic", "name": "الوكيل الاقتصادي",
     "role": "دخل الفرد والسكان · World Bank", "paid": False},
    {"key": "competition", "name": "وكيل المنافسة",
     "role": "الدول المورّدة وحصصها ومنافسون بالاسم · UN Comtrade", "paid": False},
    {"key": "regulatory", "name": "وكيل الاشتراطات",
     "role": "اشتراطات الدخول والتعريفة · مرجع L1 + WITS", "paid": False},
    {"key": "risk", "name": "وكيل المخاطر",
     "role": "استقرار ولوجستيات وعملة · البنك الدولي", "paid": False},
    {"key": "trends", "name": "وكيل الاتجاهات",
     "role": "اهتمام البحث والموسمية · Google Trends", "paid": False},
    {"key": "maps", "name": "وكيل الأعمال بالاسم",
     "role": "مصانع وموزّعون بالاسم · Google Maps", "paid": False},
    {"key": "channels", "name": "وكيل قنوات التوزيع",
     "role": "أبواب الدخول ومستوردون مرشّحون (الطبقة الحرة) · بحث ويب",
     "paid": False},
    {"key": "consumer", "name": "وكيل ثقافة المستهلك",
     "role": "هل المنتج مرغوب؟ · بحث + كلود", "paid": False},
    {"key": "dynamics", "name": "وكيل الديناميكيات",
     "role": "دوافع وكوابح وفرص · بحث + كلود", "paid": False},
    {"key": "synthesis", "name": "حكم التوليف",
     "role": "حكم كلود فوق حقائق الوكلاء المعزولة (المرحلة ٢)", "paid": False},
    {"key": "pricing", "name": "وكيل التسعير",
     "role": "أسعار التجزئة الفعلية · طبقة التعميق المدفوعة", "paid": True},
    {"key": "importers", "name": "وكيل المستوردين",
     "role": "مستوردون بالاسم من بوالص الشحن · Volza (تعميق)", "paid": True},
    {"key": "contacts", "name": "وكيل جهات الاتصال",
     "role": "مشترون وجهات اتصال B2B · Explee (تعميق)", "paid": True},
]


def register_agents(rows: list[dict]) -> int:
    """سجّل صفوفاً إضافية في الكتالوج — additive registration (V5 wave 2).

    `silk_missions.py` (١٢ وكيل كلود بالأدوات) يستدعيها عند الاستيراد
    لإضافة صفوفه دون أن يستورد `silk_agents` منه (يمنع الاستيراد الدائري:
    silk_missions <- silk_llm_runtime <- silk_agents). التسجيل مضافٌ لا
    استبدال — مفتاح موجود مسبقاً يُتجاهَل صفّه الجديد (الكتالوج الأصلي
    الفائز دائماً)، فتبقى AGENT_CATALOG «السجل القانوني الواحد» حرفياً
    حتى مع مصدرين للصفوف. يعيد عدد الصفوف المضافة فعلاً.
    """
    existing = {a["key"] for a in AGENT_CATALOG}
    added = 0
    for row in rows:
        if row.get("key") and row["key"] not in existing:
            AGENT_CATALOG.append(row)
            existing.add(row["key"])
            added += 1
    return added


def default_agent_settings() -> dict:
    """الإعدادات الافتراضية — المجاني مفعّل والمدفوع مطفأ، بلا توجيهات."""
    return {a["key"]: {"on": not a["paid"], "cmd": ""} for a in AGENT_CATALOG}


class Agent(ABC):
    """وكيل أساس — base research agent; name + abstract run()."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def run(self, task: dict, instruction: str = "") -> AgentReport:
        """نفّذ المهمة — execute the task, returning an AgentReport."""
        raise NotImplementedError


def _real(findings: list[DataPoint]) -> list[DataPoint]:
    """نقاط حقيقية — findings carrying an actual value (value is not None)."""
    return [f for f in findings if f.value is not None]


class BaseAgent(Agent):
    """الفئة الأساس الفارضة — enforces the protocol structurally (wave 2).

    البروتوكول الرباعي كان قواعد تُراجَع؛ هنا يصير بنيةً تُورَث:
      - `PAID = True` خارج سياق `/deepen` = **يستحيل التنفيذ** (تقرير متخطى
        موسوم، بلا أي نداء) — لا يعتمد على حارس يدوي في api.py.
      - استثناء غير متوقع من `_execute` = تقرير فاشل بـ `DataPoint(None,
        note=السبب)` تلقائياً — الفشل الصامت مستحيل بنيوياً.
    الوكلاء الجدد يرثونها ويكتبون `_execute()` فقط؛ القائمون يهاجرون تدريجياً
    (الموجة ٢ تهجّر المدفوعين الثلاثة، والبقية وكيلٌ مع كل PR لاحق).
    """

    PAID: bool = False   # تصنيف إلزامي عند التعريف — مدفوع أم مجاني
    SOURCE: str = ""     # وسم المصدر لنقاط الفشل البنيوية (افتراضه اسم الوكيل)
    PREF_KEY: str = ""   # صفّ الوكيل في «إعدادات الوكلاء» (AGENT_CATALOG) —
                         # "" = وكيل داخلي بلا صف (لا مفتاح تعطيل ولا توجيه)

    def run(self, task: dict, instruction: str = "") -> AgentReport:
        """نفّذ محروساً — guarded execution; subclasses implement _execute().

        حارسا اللوحة (إعدادات الوكلاء):
          • وكيل معطّل (`agent_enabled(PREF_KEY)` False) = **يستحيل التنفيذ**
            — تقرير متخطى موسوم بلا أي نداء، كنمط حارس PAID نفسه.
          • التوجيه النصي (`instruction` الصريح أو أمر اللوحة المحفوظ) يمرَّر
            للوكيل في task["instruction"] ويُعلن في خلاصة التقرير — **يوجّه
            العرض والتركيز فقط ولا يغيّر أي رقم مجلوب** (الثابت التأسيسي:
            القيمة الغائبة تبقى None مهما كان التوجيه).
        """
        import silk_context  # lazy: keeps import graph flat

        src = self.SOURCE or self.name
        today = datetime.date.today().isoformat()
        if self.PREF_KEY and not silk_context.agent_enabled(self.PREF_KEY):
            note = (f"{self.name}: معطّل من إعدادات الوكلاء — disabled by "
                    "user setting (skipped, no call attempted)")
            log.info(note)
            return AgentReport(
                self.name, [DataPoint(None, src, 0.0, note, today)],
                True, note)
        if self.PAID and not silk_context.deepen_active():
            note = (f"{self.name}: paid agent outside /deepen — skipped "
                    "(structural guard, no call attempted)")
            log.warning(note)
            return AgentReport(
                self.name, [DataPoint(None, src, 0.0, note, today)],
                True, note)
        steer = (instruction or (silk_context.agent_command(self.PREF_KEY)
                                 if self.PREF_KEY else "")).strip()[:500]
        if steer:
            task = dict(task)
            task["instruction"] = steer
        try:
            report = self._execute(task)
        except Exception as e:  # noqa: BLE001 — silent failure impossible
            note = f"{self.name} error: {type(e).__name__}: {e}"
            log.warning(note)
            return AgentReport(
                self.name, [DataPoint(None, src, 0.0, note, today)],
                True, note)
        if steer and not report.failed:
            # إعلان التوجيه في الخلاصة — presentation only, numbers untouched.
            report.summary = (f"{report.summary} · توجيه المستخدم (عرض "
                              f"وتركيز فقط): {steer[:120]}")
        return report

    def _execute(self, task: dict) -> AgentReport:
        """منطق الوكيل الفعلي — the agent's actual logic (subclass hook)."""
        raise NotImplementedError


class TradeFlowAgent(BaseAgent):
    """وكيل تدفّق التجارة — Comtrade import/export size of an HS code in a market."""

    PAID = False
    PREF_KEY = "trade"

    def __init__(self) -> None:
        super().__init__("TradeFlowAgent")

    @staticmethod
    def _world_row_from_store(hs: str, iso3: str, year: int, flow: str):
        """صف العالم من المخزن — the stored World-total row, or None on miss.

        قراءة من مخزن الحقائق أولاً (صفر نداء خارجي) — أي فشل في طبقة المخزن
        يسقط بأمان للمسار الحي؛ المخزن تحسين لا شرط. Store-first read.
        """
        if not iso3:
            return None
        try:
            import silk_store
            row = silk_store.get_trade_flow(hs, iso3, "WLD", int(year), flow)
        except Exception as e:  # noqa: BLE001 — المخزن تحسين لا شرط
            log.debug("world-row store read unavailable (%s %s %s %s): %s",
                      hs, iso3, year, flow, e)
            return None
        return row if (row and row.get("value_usd") is not None) else None

    def _execute(self, task: dict) -> AgentReport:
        """حجم استيراد/تصدير سلعة في سوق — total trade value from World partner.

        مخزن الحقائق أولاً (صف WLD للتدفق) — إصابة = صفر نداء خارجي، والقيمة
        تحمل مصدرها الأصلي وتاريخ جلبها («من المخزن»، لا تُعرض كجلب حي).
        الغياب = المسار الحي القائم، وعند نجاحه تُكتب النتيجة للمخزن فيستفيد
        التحليل التالي. Store-first; live+write-through on miss.
        """
        hs, market, year = task["hs_code"], task["market_m49"], task["year"]
        iso3 = task.get("iso3") or ""
        findings: list[DataPoint] = []
        for flow, label in (("M", "imports"), ("X", "exports")):
            row = self._world_row_from_store(hs, iso3, year, flow)
            if row is not None:
                import silk_store
                import silk_context
                silk_context.count_data("store_hits")
                day = (row.get("retrieved_at") or "")[:10]
                stale = silk_store.freshness(row.get("retrieved_at"),
                                             "trade") != "fresh"
                findings.append(DataPoint(
                    row["value_usd"], "UN Comtrade (مخزن الحقائق)", 0.9,
                    f"HS{hs} total {label} (World) to market {market} {year}, "
                    f"USD — من المخزن"
                    + (f"، جُلبت أصلاً {day}" if day else "")
                    + (" — أقدم من نافذة الحداثة (يحدّثها التحديث الدوري)"
                       if stale else ""),
                    row.get("retrieved_at") or
                    datetime.date.today().isoformat(),
                    status="stale" if stale else ""))
                continue
            recs = comtrade_trade(hs, market, year, flow=flow, partner=0)
            # 1b: None = تعذّر الجلب (429/شبكة — أعد المحاولة)؛ [] = ردّ ناجح
            # بلا سجلات (غياب حقيقي). كانا يُعرضان معاً «لا بيانات» فيوهم
            # المستخدم أن السوق فارغ بينما المنصة هي التي عجزت عن الجلب.
            if recs is None:
                findings.append(DataPoint(
                    None, "UN Comtrade", 0.0,
                    f"HS{hs} {label} سوق {market} {year}: تعذّر الجلب "
                    "(حد معدل/شبكة) — أعد المحاولة",
                    status="fetch_failed"))
                continue
            # سجل بلا primaryValue رقمية لا يُجمع كصفر — لا اختلاق (المبدأ التأسيسي).
            # Records lacking a numeric primaryValue are dropped, never summed as 0.
            vals = [v for v in (primary_value(r) for r in recs) if v is not None]
            dropped = len(recs) - len(vals)
            if not recs:
                findings.append(DataPoint(
                    None, "UN Comtrade", 0.0,
                    f"HS{hs} {label} for market {market} {year}: "
                    "لا سجل في كومتريد لهذه السنة",
                    status="no_record"))
            elif not vals:
                findings.append(DataPoint(
                    None, "UN Comtrade", 0.0,
                    f"HS{hs} {label} for market {market} {year}: "
                    f"سجلات بلا قيم رقمية ({dropped}) — payload records lacked "
                    "numeric primaryValue, no value to report"))
            else:
                note = f"HS{hs} total {label} (World) to market {market} {year}, USD"
                conf = 0.9
                if dropped:
                    conf = 0.7  # مجموع جزئي: سجلات بلا قيمة أُسقطت — partial sum
                    note += (f"؛ {dropped} سجل بلا قيمة رقمية أُسقط من الجمع — "
                             f"{dropped} record(s) lacked primaryValue, excluded")
                total = sum(vals)
                findings.append(DataPoint(total, "UN Comtrade", conf, note))
                # كتابة عابرة لصف X فقط (مجموع كامل، لا جزئي). صف M العالمي
                # يُكتب حصراً مع صفوف شركائه (write-through المُرتِّب/الجامع) —
                # كتابة WLD-M وحده كانت ستجعل قراءة المخزن «إصابة» بإجمالي بلا
                # شركاء فيتخطى وكيل المنافسة جلبه الحي (بيانات موجودة تُعرض غائبة).
                if iso3 and flow == "X" and not dropped:
                    try:
                        import silk_store
                        silk_store.migrate()
                        silk_store.upsert_trade_flows([{
                            "hs6": hs, "reporter_iso3": iso3,
                            "partner_iso3": "WLD", "year": int(year),
                            "flow": flow, "value_usd": total}])
                    except Exception as e:  # noqa: BLE001 — لا يكسر المسار الحي
                        log.warning("world-row write-through failed "
                                    "(%s %s %s %s): %s", hs, iso3, year, flow, e)
        real = _real(findings)
        failed = not real
        summary = ("لا توجد بيانات تجارة — no trade data available"
                   if failed else f"{len(real)} trade flow datum(s) from Comtrade")
        return AgentReport(self.name, findings, failed, summary)


class EconomicAgent(BaseAgent):
    """وكيل اقتصادي — World Bank income (GDP & PPP per capita) + population."""

    PAID = False
    PREF_KEY = "economic"

    def __init__(self) -> None:
        super().__init__("EconomicAgent")

    def _execute(self, task: dict) -> AgentReport:
        """قياس قوة السوق الاقتصادية — economic profile of the market."""
        iso3, year = task.get("iso3"), task.get("year")
        if not iso3:
            return AgentReport(self.name, [], True,
                               "لا يوجد ISO3 للسوق — no ISO3 for market, cannot query World Bank")
        findings = [gdp_per_capita(iso3, year), ppp_per_capita(iso3, year),
                    population(iso3, year)]
        real = _real(findings)
        failed = not real
        summary = ("لا توجد بيانات اقتصادية — no economic data available"
                   if failed else f"{len(real)}/3 World Bank indicator(s) returned")
        return AgentReport(self.name, findings, failed, summary)


class CompetitionAgent(BaseAgent):
    """وكيل المنافسة — named competitors & shares in the target market."""

    PAID = False
    SOURCE = "UN Comtrade"
    PREF_KEY = "competition"

    def __init__(self, top_n: int = 5) -> None:
        super().__init__("CompetitionAgent")
        self.top_n = top_n

    def _effective_top_n(self, task: dict) -> int:
        """عدد الصفوف من توجيه المستخدم — presentation-only top-N (1..10).

        «أعلى 8» / "top 3" في توجيه اللوحة يغيّر **عدد الصفوف المعروضة**
        فقط — القيم المجلوبة نفسها لا تُمسّ (الثابت التأسيسي).
        """
        import re
        m = re.search(r"(?:top|أعلى|اعلى)\s*(\d{1,2})",
                      str(task.get("instruction") or ""), re.IGNORECASE)
        if not m:
            return self.top_n
        return min(max(int(m.group(1)), 1), 10)

    @staticmethod
    def _competitors_from_store(hs: str, iso3: str, year: int):
        """شركاء من المخزن — stored partner rows as competitor DataPoints,
        or None on miss/unavailable (المخزن تحسين لا شرط — safe fallthrough).

        القيم تحمل إسنادها الأصلي: مصدر «مخزن الحقائق» وتاريخ الجلب الأصلي
        («من المخزن») — قراءة مخزّنة لا تُعرض كجلب حي.
        """
        if not iso3:
            return None
        try:
            import silk_store
            from silk_data_layer import ISO3_TO_M49
            from silk_data_layer_v2 import _competitor_dp
            got = silk_store.market_imports_from_store(hs, iso3, int(year))
            valued = [p for p in got["partners"]
                      if p.get("value_usd") is not None]
            grand = sum(p["value_usd"] for p in valued)
            if not valued or not grand:
                return None
            comps = []
            for p in valued:
                day = (p.get("retrieved_at") or "")[:10]
                stale = silk_store.freshness(p.get("retrieved_at"),
                                             "trade") != "fresh"
                comps.append(_competitor_dp(
                    ISO3_TO_M49.get(p["iso3"], p["iso3"]), p["value_usd"],
                    grand, hs_code=hs, market_label=iso3, year=int(year),
                    source="UN Comtrade (مخزن الحقائق)",
                    note_suffix=(" — من المخزن"
                                 + (f"، جُلبت أصلاً {day}" if day else "")
                                 + (" — أقدم من نافذة الحداثة" if stale else "")),
                    retrieved_at=p.get("retrieved_at")))
                if stale:
                    comps[-1].status = "stale"
            import silk_context
            silk_context.count_data("store_hits")
            return comps
        except Exception as e:  # noqa: BLE001 — المخزن تحسين لا شرط
            log.debug("competitor store read unavailable (%s %s %s): %s",
                      hs, iso3, year, e)
            return None

    def _execute(self, task: dict) -> AgentReport:
        """من يورّد للسوق وبأي حصة — ranked suppliers of the HS code to the market.

        المخزن أولاً: صفوف الشركاء المخزّنة تُخدم بصفر نداء خارجي وبإسنادها
        الأصلي («من المخزن» + تاريخ الجلب)؛ الغياب = المسار الحي القائم
        (market_competitors) كما كان. Store-first; live path unchanged on miss.
        """
        hs, market, year = task["hs_code"], task["market_m49"], task["year"]
        iso3 = task.get("iso3") or ""
        comps = self._competitors_from_store(hs, iso3, year)
        if comps is None:  # المخزن بارد/غير متاح — المسار الحي القائم كما كان
            comps = market_competitors(hs, market, year)
        if not comps:
            return AgentReport(
                self.name,
                [DataPoint(None, "UN Comtrade", 0.0,
                           f"HS{hs} competitors in market {market} {year}: no data / fetch failed")],
                True, "لا توجد بيانات منافسين — no competitor data available")
        findings = comps[: self._effective_top_n(task)]
        return AgentReport(self.name, findings, False,
                           f"top {len(findings)} supplier(s) by import value")


class ResearchManager:
    """مدير البحث — holds the agents and distributes a task to each."""

    def __init__(self, agents: list[Agent] | None = None) -> None:
        self.agents = agents or [TradeFlowAgent(), EconomicAgent(), CompetitionAgent()]

    def distribute(self, task: dict) -> list[AgentReport]:
        """وزّع المهمة — run every agent, collecting their reports (never crashes).

        يمرّر لكل وكيل أمرَه المحفوظ من لوحة «إعدادات الوكلاء» وقت التشغيل
        (BaseAgent.run يحسمه أيضاً من السياق — التمرير الصريح عقد المدير).
        """
        import silk_context
        reports: list[AgentReport] = []
        for agent in self.agents:
            key = getattr(agent, "PREF_KEY", "") or ""
            cmd = silk_context.agent_command(key) if key else ""
            try:
                reports.append(agent.run(task, instruction=cmd))
            except Exception as e:  # noqa: BLE001 — an agent must not crash the run
                log.warning("agent %s raised: %s", agent.name, e)
                reports.append(AgentReport(agent.name, [], True, f"agent error: {e}"))
        return reports


class JuryCommittee:
    """لجنة التحكيم — aggregates reports into a PRELIMINARY entry verdict."""

    @staticmethod
    def evaluate(reports: list[AgentReport]) -> dict:
        """قرار أولي بدخول السوق — explainable, confidence-weighted, flags thin data.

        Returns a dict with verdict, confidence, data_gaps (failed agents),
        and the contributing real findings. Never guesses past missing data.
        """
        contributing = [f for r in reports for f in r.findings if f.value is not None]
        data_gaps = [r.agent_name for r in reports if r.failed]
        n_ok = len(reports) - len(data_gaps)
        # ثقة كلية = متوسط ثقة الأدلة الحقيقية مرجّحًا بتغطية الوكلاء.
        avg_conf = sum(f.confidence for f in contributing) / len(contributing) if contributing else 0.0
        coverage = n_ok / len(reports) if reports else 0.0
        confidence = round(avg_conf * coverage, 2)

        if not contributing:
            verdict = "NO-GO (insufficient data) — قرار مؤجّل لانعدام البيانات"
        elif data_gaps:
            verdict = "PRELIMINARY / INCONCLUSIVE — مبدئي وناقص البيانات"
        else:
            verdict = "PRELIMINARY GO — مبدئي إيجابي"

        return {
            "verdict": verdict,
            "preliminary": True,
            "confidence": confidence,
            "agents_with_data": n_ok,
            "agents_total": len(reports),
            "data_gaps": data_gaps,
            "contributing_findings": contributing,
            # PR B §B8 (بلاغ تحليل ٧/قطر): النصفُ الإنجليزيّ داخليٌّ لا يصل
            # العميل — كان يُسرَّب إلى §5 حين يُعيد الكاتبُ صياغته بشكلٍ يفلت
            # من مُنظِّف `_PRELIM_EN_NOTE_RE`. حُذف من المصدر (عربيّ فقط)؛
            # المُنظِّف يبقى حارسَ انحدارٍ للمدوّنات المخزَّنة القديمة.
            "note": "تنبيه: قرار مبدئي والنواقص معلّمة لا مُخمّنة.",
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Silk multi-agent run — degrades gracefully offline (no fabricated data)")
    # مهمة واحدة: قطن (HS 5201) إلى الصين 2022 — one product/market.
    task = {"hs_code": "5201", "market_m49": 156, "iso3": "CHN", "year": 2022}
    manager = ResearchManager()
    reports = manager.distribute(task)
    for r in reports:
        flag = "FAILED" if r.failed else "ok"
        print(f"  [{flag}] {r.agent_name}: {r.summary}")
    decision = JuryCommittee.evaluate(reports)
    print(f"  JURY verdict: {decision['verdict']}")
    print(f"        confidence={decision['confidence']} "
          f"coverage={decision['agents_with_data']}/{decision['agents_total']} "
          f"gaps={decision['data_gaps']}")
