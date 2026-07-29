"""محرك قرار دخول السوق — Silk market-entry decision engine (Stage 4, §8).

يستهلك حصراً حزمة وكلاء البحث المتحقَّق منها (silk_research §4b) عبر
`bundle["pillar_inputs"]` — لا نداء خارجياً واحداً هنا؛ محرك حسابي نقي قابل
للاختبار، كل عمود يطبع أساسه (المعادلة + المدخلات المستخدمة).

Score = w1·جاذبية السوق + w2·(1 − شدة المنافسة) + w3·الملاءمة التنظيمية
        + w4·هامش الربحية + w5·أمان السوق (المخاطر) — كل عمود ∈ [0,1].

توجيه المالك: تُرقّى المخاطر من بوابةٍ فقط إلى **عمودٍ خامسٍ موزون** — الاستقرار
السياسي/جودة التنظيم/الأداء اللوجستي/تقلّب الصرف ترفع أو تخفض الدرجة تناسبيًّا،
مع إبقاء بوابة الخطر الحرج (PV<−1.5) تقلب القرار NO-GO فوق العمود.

خيارا الأوزان (بوابة GATE 3 — قرار المالك يحدد الافتراضي النهائي):
  A (خطة §8):        سوق 0.25 · منافسة 0.20 · تنظيمي 0.15 · ربحية 0.25 · مخاطر 0.15
  B (تنظيمي مثقّل):   سوق 0.20 · منافسة 0.15 · تنظيمي 0.30 · ربحية 0.20 · مخاطر 0.15
كلا المجموعين يُحسبان دائماً ويظهران في المخرجات؛ SILK_DECISION_WEIGHTS يختار
المعتمد (الافتراضي A إلى حين قرار البوابة).

القواعد (§8): GO عند score ≥ 0.65 وثقة ≥ 0.6؛ NO-GO تحت 0.45 أو عند بوابة خطر
حرجة؛ وإلا CONDITIONAL-GO وشروطه = الأعمدة الضعيفة/الغائبة. عمود غائب لا
يُخمَّن: الأوزان تُعاد تسويتها والفجوة تصير شرطاً — لا اختلاق (المبدأ التأسيسي).
"""
from __future__ import annotations

import logging
import math
import os

log = logging.getLogger(__name__)

SCHEMA = "silk.decision/v1"

WEIGHT_OPTIONS: dict[str, dict[str, float]] = {
    "A": {"market": 0.25, "competition": 0.20, "regulatory": 0.15,
          "profit": 0.25, "risk": 0.15},
    "B": {"market": 0.20, "competition": 0.15, "regulatory": 0.30,
          "profit": 0.20, "risk": 0.15},
}
_N_PILLARS = len(next(iter(WEIGHT_OPTIONS.values())))   # عدد الأعمدة (٥ الآن)

# اسم عرض عربي قصير لكل خيار أوزان (قرار المالك، الطبقة ٨) — يحل محل رمز
# المفتاح الخام A/B على وجه التقرير؛ المفتاح A/B نفسه يبقى دون تغيير في
# WEIGHT_OPTIONS/SILK_DECISION_WEIGHTS (سطح API مستقر، لا تغيير بنيوي).
_WEIGHT_LABEL_AR: dict[str, str] = {"A": "الأوزان القياسية",
                                    "B": "الأوزان التنظيمية المُثقّلة"}

_GO, _NOGO = 0.65, 0.45          # عتبات §8
_MIN_CONF_GO = 0.60

_AR = {"market": "جاذبية السوق", "competition": "شدة المنافسة",
       "regulatory": "الملاءمة التنظيمية", "profit": "هامش الربحية",
       "risk": "أمان السوق (المخاطر)"}


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _conf_phrase(c: object) -> str:
    """ثقة بصيغة بشرية على وجه التقرير — silk_narrative.confidence_phrase
    (استيراد كسول: الوحدة تبقى مستوردة بلا تبعيات عرض عند فشل غير متوقع)."""
    try:
        from silk_narrative import confidence_phrase
        return confidence_phrase(c)
    except Exception:  # noqa: BLE001 — صياغة تجميلية لا شرط حساب
        return str(c)


def _mean_available(parts: dict[str, float | None]) -> tuple[float | None, list]:
    """متوسط المكوّنات المتاحة + قائمة الغائبة — never a guessed component."""
    have = {k: v for k, v in parts.items() if v is not None}
    missing = [k for k, v in parts.items() if v is None]
    return (round(sum(have.values()) / len(have), 3) if have else None, missing)


# ── الأعمدة الأربعة · the four pillars (كلٌّ يطبع أساسه) ─────────────────────

def _pillar_market(pi: dict) -> dict:
    """جاذبية السوق — حجم (لوغاريتمي) + نمو + دخل + زخم الحصة السعودية.

    مؤشر النشاط المثلَّث (§7 المرحلة ٢) يُستهلَك **فقط** عند غياب tam_log —
    بديل جزئي 0..1 غير دولاري عند فجوة TAM الرسمية، لا يُضاف فوق tam_log
    (لا ازدواج قياس السوق مرتين بمصدرين مختلفي الطبيعة).
    """
    tam, cagr = pi.get("tam_usd"), pi.get("import_cagr_pct")
    gdp, sau = pi.get("gdp_per_capita_usd"), pi.get("saudi_share_pct")
    tam_log = _clip(math.log10(tam) / 9) if tam and tam > 0 else None
    activity_idx = pi.get("market_activity_index")
    parts = {
        "tam_log": tam_log if tam_log is not None else (
            _clip(activity_idx) if activity_idx is not None else None),
        "cagr": _clip((cagr + 10) / 40) if cagr is not None else None,
        "income": _clip(gdp / 50_000) if gdp else None,
        "saudi_momentum": _clip(sau / 20) if sau is not None else None,
    }
    v, missing = _mean_available(parts)
    basis = ("متوسط المتاح من: log10(TAM)/9 (سقف 10^9$)، "
            "(CAGR+10)/40 (−10%→0، +30%→1)، دخل الفرد/50k$، "
            "الحصة السعودية/20%")
    if tam_log is None and activity_idx is not None:
        basis += ("؛ TAM الرسمي غائب — استُبدل بمؤشر النشاط المثلَّث "
                  "(Maps/Trends، مُقدَّر بثقة مسقوفة 0.5)")
    return {"value": v, "components": parts, "missing": missing, "basis": basis}


def _pillar_competition(pi: dict) -> dict:
    """شدة المنافسة — HHI + حصة المورّد الأكبر + كثافة الشركات المرشّحة."""
    hhi, top = pi.get("hhi"), pi.get("top_supplier_share_pct")
    n = pi.get("named_company_count")
    parts = {
        "hhi": _clip(hhi / 0.5) if hhi is not None else None,
        "top_share": _clip(top / 100) if top is not None else None,
        "named_density": _clip(n / 10) if n is not None else None,
    }
    v, missing = _mean_available(parts)
    return {"value": v, "components": parts, "missing": missing,
            # PR B §B10 (بلاغ تحليل ٧): «حصة الأكبر/100» كان يُقرأ رقماً مستقلاً
            # («المؤشرات المساهمة: 100») حين يخلط الكاتب المقامَ المعياريّ بعدّاد
            # المؤشرات. الصياغة الآن «مقسومةً على» صراحةً فلا يُلتقَط 100/0.5/10
            # كقيمةِ بيانات.
            "basis": "متوسط المتاح من: HHI مقسوماً على 0.5، وحصة المورّد "
                     "الأكبر مقسومةً على 100، وعدد المرشّحين بالاسم مقسوماً "
                     "على 10 — الدرجة المستهلكة في المجموع هي (1 − الشدة)"}


def _pillar_regulatory(pi: dict) -> dict:
    """الملاءمة التنظيمية — تعريفة منخفضة + وضوح الاشتراطات؛ بوابة الأهلية تُخفّض."""
    tariff, req = pi.get("tariff_applied_pct"), pi.get("entry_requirements_count")
    gate = pi.get("eligibility_gate")
    parts = {
        "tariff": _clip(1 - tariff / 30) if tariff is not None else None,
        "legibility": _clip(req / 8) if req is not None else None,
    }
    v, missing = _mean_available(parts)
    gated = bool(gate) and v is not None
    if gated:  # بوابة أهلية أمامية (EU 2017/625): سقف 0.3 حتى تُعبَر — قاعدة معلنة
        v = round(min(v, 0.3), 3)
    return {"value": v, "components": parts, "missing": missing,
            "eligibility_gate": bool(gate) if gate is not None else None,
            "basis": "متوسط المتاح من: (1 − تعريفة/30%)، وضوح القائمة (بنود/8)"
                     + ("؛ بوابة أهلية مفتوحة ⇒ سقف 0.3 حتى اعتماد المنشأة"
                        if gated else "")}


def _pillar_profit(pi: dict) -> dict:
    """هامش الربحية — هامش عند الحدود + موقع سعر الصادر السعودي مقابل السوق."""
    margin = pi.get("margin_at_border_pct")
    uv, sau_uv = pi.get("border_unit_value_usd_kg"), \
        pi.get("saudi_border_unit_value_usd_kg")
    ratio = (sau_uv / uv) if (uv and sau_uv) else None
    parts = {
        "margin": _clip(margin / 40) if margin is not None else None,
        # نسبة سعرك الحدودي لمتوسط السوق: 0.5→1.0 (منافس جداً)، 1.5→0.
        "price_position": _clip(1.5 - ratio) if ratio is not None else None,
    }
    v, missing = _mean_available(parts)
    return {"value": v, "components": parts, "missing": missing,
            "basis": "متوسط المتاح من: الهامش/40%، الموقع السعري "
                     "(1.5 − سعرك/متوسط السوق)"}


def _pillar_risk(pi: dict) -> dict:
    """أمان السوق — استقرار سياسي + جودة تنظيم + أداء لوجستي − تقلّب صرف.

    توجيه المالك: المخاطر عمودٌ موزون لا بوابةٌ فقط. أعلى = أأمن، ويُستهلَك مباشرةً
    في المجموع (لا معكوساً كالمنافسة). بوابة الخطر الحرج (PV<−1.5 في decide) تبقى
    منفصلةً وتقلب القرار NO-GO فوق هذا العمود — بوابةٌ وعمودٌ معًا لا أحدهما.
    مقاييس WGI في [−2.5, +2.5] ⇒ (x+2.5)/5؛ LPI في [1,5] ⇒ (LPI−1)/4.
    """
    pv, rq = pi.get("political_stability_wgi"), pi.get("regulatory_quality_wgi")
    lpi, fx = pi.get("logistics_lpi"), pi.get("fx_volatility_pct")
    parts = {
        "political_stability": _clip((pv + 2.5) / 5) if pv is not None else None,
        "regulatory_quality": _clip((rq + 2.5) / 5) if rq is not None else None,
        "logistics": _clip((lpi - 1) / 4) if lpi is not None else None,
        "fx_stability": _clip(1 - fx / 20) if fx is not None else None,
    }
    v, missing = _mean_available(parts)
    return {"value": v, "components": parts, "missing": missing,
            "basis": "متوسط المتاح من: (استقرار سياسي WGI+2.5)/5، (جودة تنظيم "
                     "WGI+2.5)/5، (LPI−1)/4، (1 − تقلّب الصرف/20%) — أعلى=أأمن"}


# ── سجل المخاطر · rule-derived risk register (كل بند بدليله) ─────────────────

def _risk_register(pi_risk: dict, coverage: float) -> list[dict]:
    R: list[dict] = []
    hhi = pi_risk.get("supplier_concentration_hhi")
    if hhi is not None and hhi > 0.25:
        R.append({"risk": "تركّز مصادر التوريد", "severity": "متوسطة",
                  "evidence": f"HHI={hhi} > 0.25 (كومتريد)"})
    fx = pi_risk.get("fx_volatility_pct")
    if fx is not None and fx > 5:
        R.append({"risk": "تقلب العملة", "severity": "متوسطة",
                  "evidence": f"معامل اختلاف الصرف {fx}% > 5% (World Bank)"})
    pv = pi_risk.get("political_stability_wgi")
    if pv is not None and pv < -0.5:
        R.append({"risk": "استقرار سياسي منخفض", "severity":
                  "عالية" if pv < -1.5 else "متوسطة",
                  "evidence": f"WGI PV.EST={pv} (World Bank)"})
    if coverage < 0.6:
        R.append({"risk": "تغطية بيانات منخفضة", "severity": "متوسطة",
                  "evidence": f"تغطية الوكلاء {round(100 * coverage)}% < 60% — "
                              "القرار مشروط باكتمالها"})
    return R


# ── القرار · decide() ────────────────────────────────────────────────────────

def decide(bundle: dict, weights_option: str | None = None) -> dict:
    """قرار موزون من حزمة البحث — deterministic, explainable, never fabricates.

    bundle: مخرجات ResearchOrchestrator.run_market (§4b). يعيد قاموس قرار كامل:
    verdict/score/confidence + الأعمدة بأساسها + كلا خياري الأوزان + الشروط
    والمخاطر والخطوات الأولى. عمود غائب => إعادة تسوية + شرط، لا تخمين.
    """
    opt = (weights_option or os.environ.get("SILK_DECISION_WEIGHTS", "A")
           ).strip().upper()
    if opt not in WEIGHT_OPTIONS:
        opt = "A"
    pi = bundle.get("pillar_inputs") or {}
    coverage = float(bundle.get("coverage") or 0.0)

    pillars = {"market": _pillar_market(pi.get("market_attractiveness") or {}),
               "competition": _pillar_competition(
                   pi.get("competition_intensity") or {}),
               "regulatory": _pillar_regulatory(pi.get("regulatory_fit") or {}),
               "profit": _pillar_profit(pi.get("profitability") or {}),
               "risk": _pillar_risk(pi.get("risk") or {})}

    def _score(weights: dict[str, float]) -> tuple[float | None, list[str]]:
        """المجموع الموزون على الأعمدة المتاحة — إعادة تسوية معلنة للأوزان."""
        contrib, wsum, missing = 0.0, 0.0, []
        for name, w in weights.items():
            v = pillars[name]["value"]
            if v is None:
                missing.append(name)
                continue
            if name == "competition":
                v = 1.0 - v  # الدرجة = عكس الشدة (§8)
            contrib += w * v
            wsum += w
        if wsum == 0:
            return None, missing
        return round(contrib / wsum, 3), missing

    scores = {name: _score(w)[0] for name, w in WEIGHT_OPTIONS.items()}
    score, missing_pillars = _score(WEIGHT_OPTIONS[opt])

    # الثقة = تغطية الوكلاء × نسبة الأعمدة المحسوبة (معلنة الأساس، لا رقم حدسي).
    pillar_frac = (_N_PILLARS - len(missing_pillars)) / _N_PILLARS
    confidence = round(coverage * pillar_frac, 2)

    risk_pi = pi.get("risk") or {}
    critical = bool(risk_pi.get("critical_risk"))
    risks = _risk_register(risk_pi, coverage)

    conditions: list[str] = []
    for name in missing_pillars:
        conditions.append(f"عمود {_AR[name]} غائب ({', '.join(pillars[name]['missing'])}) "
                          "— أكمل مصادره قبل قرار نهائي")
    for name, p in pillars.items():
        v = p["value"]
        if v is None:
            continue
        eff = 1.0 - v if name == "competition" else v
        if eff < 0.5:
            # سدّ تسريب (الطبقة ٨): كسر عشري خام على وجه التقرير ("ضعيف
            # (0.37)") — نسبة مئوية بشرية بدله، شقيقة إصلاح سطر «لماذا»
            # أعلاه لنفس السبب (لا رقم آلي خام يصل العميل).
            conditions.append(f"عمود {_AR[name]} ضعيف ({round(eff * 100)}%) "
                              f"— {p['basis']}")
    if pillars["regulatory"].get("eligibility_gate"):
        conditions.insert(0, "بوابة أهلية أمامية مفتوحة (منشأة معتمدة EU 2017/625) "
                             "— لا تقدّم قبل عبورها")

    if score is None:
        verdict = "NO-GO"
        why = "لا عمود واحداً قابلاً للحساب — بيانات غير كافية للقرار"
    elif critical:
        verdict = "NO-GO"
        why = ("بوابة خطر حرجة: مؤشر الاستقرار السياسي دون العتبة الحرجة "
               "(قاعدة الحكم المعلنة)")
    elif score >= _GO and confidence >= _MIN_CONF_GO and not conditions:
        verdict = "GO"
        why = (f"الدرجة الموزونة {score} بلغت عتبة المضي ({_GO}) والثقة "
               f"{_conf_phrase(confidence)} فوق الحد الأدنى "
               f"({round(_MIN_CONF_GO * 100)}%)")
    elif score < _NOGO:
        verdict = "NO-GO"
        why = f"الدرجة الموزونة {score} دون عتبة الرفض ({_NOGO})"
    else:
        verdict = "CONDITIONAL-GO"
        # أسباب فعلية فقط (إصلاح P0-1): القالب القديم "X أو Y أو Z" كان يطبع
        # الأسباب الثلاثة دوماً — فظهر «الثقة 0.91 دون 0.6» وهي ليست دونها،
        # وقرأه المالك تناقضاً في أرقام الثقة بين المشتقات. الآن تُسرد
        # الأسباب المتحقّقة حصراً.
        # سطر «لماذا» يظهر حرفياً على وجه التقرير (docx/markdown) — عربية
        # بشرية بلا رطانة كود: "score 0.64" الإنجليزية الخام كانت تصل
        # العميل، والثقة العشرية الخامة تصاغ عبر confidence_phrase
        # (نفس قاعدة إصلاح المرحلة ٥: لا كسر عشري خام على وجه التقرير).
        reasons = []
        if score < _GO:
            reasons.append(f"الدرجة الموزونة {score} في النطاق الشرطي")
        if confidence < _MIN_CONF_GO:
            reasons.append(f"الثقة {_conf_phrase(confidence)} دون الحد "
                           f"الأدنى ({round(_MIN_CONF_GO * 100)}%)")
        if conditions:
            reasons.append(f"شروط مفتوحة ({len(conditions)})")
        why = " و".join(reasons)

    first_steps = _first_steps(verdict, pillars, conditions, risks)
    return {
        "schema": SCHEMA, "verdict": verdict, "score": score,
        "confidence": confidence,
        # سدّ تسريب (الطبقة ٨): كسر تغطية عشري خام ("التغطية 0.65 × ...")
        # — نسبة مئوية بشرية بدله (شقيقة إصلاح سطر «لماذا» في المرحلة ٥).
        "confidence_basis": f"التغطية {round(coverage * 100)}% × الأعمدة "
                            f"المحسوبة {_N_PILLARS - len(missing_pillars)}"
                            f"/{_N_PILLARS}",
        "weights_option": opt, "weights": WEIGHT_OPTIONS[opt],
        "scores_by_option": scores,
        # سدّ تسريب (الطبقة ٨، قرار المالك): "بوابة GATE 3" مصطلح مسار عمل
        # داخلي — والحرف الخام A/B رمز مفتاح داخلي (يبقى weights_option/
        # SILK_DECISION_WEIGHTS كما هما لسطح الـAPI؛ العرض فقط يتغيّر).
        "weights_label": _WEIGHT_LABEL_AR.get(opt, opt),
        "weights_note": (f"تُحسب الدرجة بمجموعتي أوزان معاً للمقارنة؛ "
                         f"المعتمد لهذا القرار: {_WEIGHT_LABEL_AR.get(opt, opt)}"),
        "pillars": pillars, "missing_pillars": missing_pillars,
        "critical_risk": critical, "risks": risks, "conditions": conditions,
        "first_steps": first_steps, "why": why,
        "note": "قرار حتمي قابل للتفسير من الحزمة البحثية المتحقَّق منها — "
                "الأعمدة الغائبة شروط معلنة، لا تخمين",
    }


def _first_steps(verdict: str, pillars: dict, conditions: list[str],
                 risks: list[dict]) -> list[str]:
    """خطوات أولى قاعدية — مشتقة من أضعف الأعمدة والمخاطر، لا نصائح عامة."""
    steps: list[str] = []
    if verdict == "NO-GO":
        return ["عالج سبب NO-GO المذكور أولاً ثم أعد التحليل — لا خطوات دخول "
                "قبل ذلك"]
    if pillars["regulatory"].get("eligibility_gate"):
        steps.append("ابدأ بمسار اعتماد المنشأة (القائمة الأوروبية EU 2017/625) "
                     "— كل ما بعده محجوب عليه")
    # سدّ تسريب (الطبقة ٩): كانت الخطوات تشير لاسم وكيل داخلي خام إنجليزي
    # بين قوسين ("وكيل regulatory"/"وكيل supplier") — لا قيمة للقارئ في
    # معرفة أي وكيل داخلي غذّى الخطوة؛ عربية صرفة بلا إسناد داخلي الآن.
    if pillars["regulatory"]["value"] is not None and \
            pillars["regulatory"]["value"] < 0.5:
        steps.append("أغلق بنود قائمة الاشتراطات بنداً بنداً بمرجعها الرسمي")
    comp = pillars["competition"]["value"]
    if comp is not None and comp > 0.5:
        steps.append("سوق مركّز: ادخل عبر موزّع قائم من مرشّحي التوريد "
                     "المرصودين بدل البناء المباشر")
    prof = pillars["profit"]["value"]
    if prof is None:
        steps.append("أكمل بطاقة المنتج (تكلفة/كجم وطاقة شهرية) ليُحسب الهامش "
                     "وSOM قبل الالتزام")
    for r in risks:
        if r["risk"] == "تقلب العملة":
            steps.append("سعّر بعقود قصيرة أو تحوّط عملة — " + r["evidence"])
    if not steps:
        steps.append("تحقّق من مرشّحي التوزيع والتوريد بالاسم — المرشّحون "
                     "غير موثَّقين حتى تأكيدهم")
    return steps[:5]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    demo = {"coverage": 0.8, "pillar_inputs": {
        "market_attractiveness": {"tam_usd": 2.9e8, "import_cagr_pct": 6.0,
                                  "gdp_per_capita_usd": 48_000,
                                  "saudi_share_pct": 2.0},
        "competition_intensity": {"hhi": 0.12, "top_supplier_share_pct": 21.0,
                                  "named_company_count": 7},
        "regulatory_fit": {"tariff_applied_pct": 0.0,
                           "entry_requirements_count": 9,
                           "eligibility_gate": True},
        "profitability": {"border_unit_value_usd_kg": 3.4,
                          "saudi_border_unit_value_usd_kg": 3.1,
                          "margin_at_border_pct": 18.0},
        "risk": {"political_stability_wgi": 0.8, "fx_volatility_pct": 0.9,
                 "supplier_concentration_hhi": 0.12, "critical_risk": False}}}
    d = decide(demo)
    print(f"verdict={d['verdict']} score={d['score']} (A/B: "
          f"{d['scores_by_option']}) conf={d['confidence']}")
    for c in d["conditions"]:
        print("  شرط:", c)
    for s in d["first_steps"]:
        print("  خطوة:", s)
