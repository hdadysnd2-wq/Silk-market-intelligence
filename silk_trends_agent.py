"""وكيل اتجاهات جوجل لسِلك — Silk Google Trends research agent (README step 4).

Pulls real public interest-over-time from Google Trends via pytrends (an OPTIONAL
dep, imported lazily). If pytrends is missing OR the network fails, returns a
provenance-tagged None — it never fabricates interest values (founding principle).

Optional dependency: pytrends  (pip install pytrends). Not imported at module top.
"""
from __future__ import annotations

import calendar
import logging
import os
import time

from silk_data_layer import DataPoint, _today
from silk_agents import BaseAgent, AgentReport

log = logging.getLogger(__name__)

# قابلة للحقن في الاختبارات الهرمتيكية — لا نوم حقيقياً تحت pytest.
_sleep = time.sleep


def _trends_max_attempts() -> int:
    """عدد المحاولات الكلي = 1 + SILK_TRENDS_RETRIES (الافتراضي ٢ إعادتان)."""
    try:
        n = int(os.environ.get("SILK_TRENDS_RETRIES", "2"))
    except (TypeError, ValueError):
        n = 2
    return max(0, n) + 1


def _trends_backoff_base_s() -> float:
    """قاعدة التراجع الأُسّي بالثواني (SILK_TRENDS_BACKOFF_S، الافتراضي ٢)."""
    try:
        v = float(os.environ.get("SILK_TRENDS_BACKOFF_S", "2"))
    except (TypeError, ValueError):
        v = 2.0
    return v if v > 0 else 2.0


def _is_rate_limited(e: Exception) -> bool:
    """429 بأشكاله: صنف TooManyRequests، أو رمز 429 في الردّ/الرسالة."""
    if "TooManyRequests" in type(e).__name__:
        return True
    code = getattr(getattr(e, "response", None), "status_code", None)
    return code == 429 or "429" in str(e)


def _retry_429(fetch, label: str):
    """أعد جلبة pytrends عند 429 **فقط** بتراجع أُسّي — بلاغ إنتاجي
    (Nadec/اليمن #7): أربع تشغيلات متتالية فشلت بعثتها على 429 لأن النداء
    الواحد كان يسقط فوراً إلى فجوة. أي استثناء غير 429 يُعاد رفعه فوراً
    (فجوة معلنة كما كانت — لا إعادة عمياء تحرق حصّة pytrends المحدودة)."""
    attempts = _trends_max_attempts()
    delay = _trends_backoff_base_s()
    for i in range(attempts):
        try:
            return fetch()
        except Exception as e:  # noqa: BLE001 — غير 429 يُعاد رفعه كما هو
            if not _is_rate_limited(e) or i >= attempts - 1:
                raise
            log.warning("Google Trends 429 (%s) — retry %d/%d in %.0fs",
                        label, i + 1, attempts - 1, delay)
            _sleep(delay)
            delay *= 2


def trends_interest(
    keyword: str,
    geo: str | None = None,
    timeframe: str = "today 12-m",
) -> DataPoint:
    """اهتمام جوجل تريندز — mean search interest 0-100 for a keyword.

    Standalone helper. Returns DataPoint(value=mean interest) on success, or
    DataPoint(value=None, confidence=0.0) if pytrends missing / network fails.
    The raw monthly series is stashed in DataPoint.note for the seasonality step.
    """
    try:
        from pytrends.request import TrendReq  # lazy: optional dep
    except ImportError:
        log.warning("pytrends not installed — trends interest unavailable")
        return DataPoint(None, "Google Trends", 0.0,
                         "pytrends unavailable / no network", _today())
    try:
        kw = (keyword or "").strip()
        if not kw:
            return DataPoint(None, "Google Trends", 0.0,
                             "empty keyword — no query", _today())
        def _fetch():
            py = TrendReq(timeout=(10, 30))
            py.build_payload([kw], timeframe=timeframe, geo=(geo or ""))
            return py.interest_over_time()
        df = _retry_429(_fetch, f"interest '{kw}'")
        if df is None or df.empty or kw not in df.columns:
            return DataPoint(None, "Google Trends", 0.0,
                             f"no interest data for '{kw}' (geo={geo or 'WW'})", _today())
        series = df[kw]
        mean = round(float(series.mean()), 1)
        return DataPoint(mean, "Google Trends", 0.7,
                         f"mean interest 0-100 for '{kw}' geo={geo or 'WW'} "
                         f"tf='{timeframe}' n={len(series)}", _today())
    except Exception as e:  # noqa: BLE001 — never raise to caller
        log.warning("Google Trends fetch failed ('%s', geo=%s): %s", keyword, geo, e)
        try:  # عائلة C (Wave 1.5): إعلان الفشل للمشغّل.
            import silk_ops_log
            silk_ops_log.record_service_failure(
                "trends", f"pytrends fetch failed ('{keyword}', geo={geo}): {e}")
        except Exception:  # noqa: BLE001
            pass
        return DataPoint(None, "Google Trends", 0.0,
                         f"pytrends unavailable / no network: {e}", _today())


def _snapshot_geo(geo: str | None) -> str:
    """مفتاح جغرافيا اللقطة — geo السوق (ISO2) بحروفٍ كبيرة، أو ``WW`` للعالم."""
    return (geo or "WW").strip().upper() or "WW"


def _snapshot_indicator(keyword: str) -> str:
    """مفتاح مؤشر اللقطة داخل جدول `indicators` — منفصلٌ ببادئة ``trends:`` كي
    لا يخلط مؤشرات البنك الدولي الحقيقية (لا مصدرَ رقمٍ ثانٍ، مجرّد تخزين لقطة)."""
    return f"trends:{(keyword or '').strip().lower()}"


def trends_interest_resilient(keyword: str, geo: str | None = None,
                              timeframe: str = "today 12-m") -> DataPoint:
    """سلسلة صمود الاتجاهات (WS11.1) — pytrends → لقطة مخزَّنة سابقة → فجوة معلنة.

    pytrends هشّةُ الحصّة (429 مرصود حياً)؛ فشلها لم يعد يسقط مباشرةً إلى فجوة:
    نجاحُ نداءٍ حيّ يُخزِّن لقطةً في `silk_store`، وفشلُ التحديث الحيّ يخدم **آخر
    لقطةٍ مخزَّنة** موسومةً «من المخزن» بتاريخ رصدها الأصلي وثقةٍ مخفوضة
    (`status="stale"`) — **لا تُقدَّم كحيّة، ولا يُختلَق رقمٌ طازج** (المبدأ
    التأسيسي: لقطةٌ موثَّقةٌ قديمة خيرٌ من رقمٍ حيّ ملفَّق، والفجوة خيرٌ منهما إن
    لم توجد لقطة). تعذّر المخزن أو غيابُ لقطة => الفجوة المعلنة كما هي.

    Chain: live pytrends (VERIFIED) → last stored snapshot (SECONDARY, stale,
    carrying its observed date) → declared gap. A price/interest is never
    estimated; a stale documented value beats a fabricated fresh one.
    """
    live = trends_interest(keyword, geo, timeframe)
    if live.value is not None:
        try:  # أفضل جهد: خزّن اللقطة كي يخدمها فشلٌ لاحق (لا يُسقِط عند التعذّر).
            import datetime
            import silk_store
            year = datetime.datetime.now(datetime.timezone.utc).year
            silk_store.upsert_indicator(
                _snapshot_geo(geo), _snapshot_indicator(keyword), year,
                live.value, "Google Trends", float(live.confidence), live.note)
        except Exception as e:  # noqa: BLE001 — التخزين تحسينٌ لا شرط
            log.warning("trends snapshot store failed ('%s'): %r", keyword, e)
        return live

    # التحديث الحيّ فشل — اخدم آخر لقطةٍ مخزَّنة إن وُجدت (موسومةً، غير حيّة).
    try:
        import silk_store
        row = silk_store.get_indicator(_snapshot_geo(geo),
                                       _snapshot_indicator(keyword))
    except Exception as e:  # noqa: BLE001
        log.warning("trends snapshot read failed ('%s'): %r", keyword, e)
        row = None
    if row is not None and row["value"] is not None:
        observed = row["retrieved_at"] or "؟"
        capped_conf = min(0.5, float(row["confidence"] or 0.5))
        # WS4/WS11.1 (حارس المالك ٢): أظهِر تاريخ اللقطة + الثقة المسقوفة في
        # تتبّع التشغيلة (بديل الـmanifest اليوم) — no-op صامت خارج سياق تتبّع.
        # التصيير المؤرَّخ في «المنهجية» (لا شارةَ متن) يأتي في PR واجهة WS9/WS10.
        try:
            import silk_trace
            silk_trace.record_event(
                event="trends_snapshot_served", keyword=(keyword or "").strip(),
                geo=_snapshot_geo(geo), observed_at=observed,
                confidence=capped_conf, status="stale")
        except Exception:  # noqa: BLE001 — تشخيص لا شرط تنفيذ
            pass
        return DataPoint(
            row["value"], "Google Trends", capped_conf,
            f"لقطة مخزَّنة (اهتمام بحث سابق) — تعذّر التحديث الحيّ؛ رُصدت في "
            f"{observed} (من المخزن، لا قيمة حيّة)", observed, status="stale")
    # لا لقطة — تبقى الفجوة المعلنة (value=None) كما هي، لا اختلاق.
    return live


def trends_series(keyword: str, geo: str | None = None,
                  timeframe: str = "today 12-m") -> dict:
    """سلسلة اهتمام + نمو من نداء pytrends واحد — Stage 3 المرحلة ٢ (§7).

    نداء واحد فقط (حارس ميزانية: لا يُضاعف استهلاك pytrends المحدود أصلاً —
    لاحظنا 429 حياً)؛ يُستخرَج منه المتوسط (كما `trends_interest`) **و**نسبة
    نمو الاتجاه (الربع الأول مقابل الربع الأخير من السلسلة نفسها، بنفس منطق
    `silk_trend.growth_pct` لكن على إشارة بحث لا تجارة). أقل من 4 نقاط
    مرصودة => `growth_pct=None` بصدق، لا تخمين. فشل/غياب pytrends =>
    القيم كلها None بملاحظة السبب — لا اختلاق.
    """
    try:
        from pytrends.request import TrendReq  # lazy: optional dep
    except ImportError:
        return {"mean": None, "growth_pct": None, "n": 0, "confidence": 0.0,
                "note": "pytrends unavailable / no network"}
    kw = (keyword or "").strip()
    if not kw:
        return {"mean": None, "growth_pct": None, "n": 0, "confidence": 0.0,
                "note": "empty keyword — no query"}
    try:
        def _fetch():
            py = TrendReq(timeout=(10, 30))
            py.build_payload([kw], timeframe=timeframe, geo=(geo or ""))
            return py.interest_over_time()
        df = _retry_429(_fetch, f"series '{kw}'")
        if df is None or df.empty or kw not in df.columns:
            return {"mean": None, "growth_pct": None, "n": 0, "confidence": 0.0,
                    "note": f"no interest data for '{kw}' (geo={geo or 'WW'})"}
        series = df[kw]
        n = len(series)
        mean = round(float(series.mean()), 1)
        growth = None
        if n >= 4:
            q = max(1, n // 4)
            first_q, last_q = float(series.iloc[:q].mean()), float(series.iloc[-q:].mean())
            if first_q > 0:
                growth = round(100.0 * (last_q - first_q) / first_q, 1)
        return {"mean": mean, "growth_pct": growth, "n": n, "confidence": 0.7,
                "note": f"متوسط اهتمام 0-100 لـ'{kw}' geo={geo or 'WW'} "
                        f"tf='{timeframe}' n={n}"
                        + (f"؛ نمو الربع الأول↔الأخير {growth}%" if growth is not None
                           else "؛ سلسلة أقصر من 4 نقاط — لا نمو محسوب")}
    except Exception as e:  # noqa: BLE001 — never raise to caller
        log.warning("Google Trends series fetch failed ('%s', geo=%s): %s",
                    keyword, geo, e)
        return {"mean": None, "growth_pct": None, "n": 0, "confidence": 0.0,
                "note": f"pytrends unavailable / no network: {e}"}


def _coerce_trend_value(v: object) -> object:
    """قيمة صف تريندز كما هي — رقم يبقى رقماً، و'Breakout' الصاعدة تبقى نصاً
    (لا تحويلها لرقم مختلَق). None يبقى None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    return s or None


def trends_context(keyword: str, geo: str | None = None,
                   timeframe: str = "today 12-m") -> dict:
    """سياق طلب أغنى من بناء حمولة pytrends واحد — R3 (ثقافة المستهلك الأعمق):
    الاستعلامات المرتبطة (الشائعة والصاعدة)، المواضيع الصاعدة، والتوزيع
    الإقليمي للاهتمام.

    كلها من `build_payload` واحد ثم ثلاث قراءات (related_queries/
    related_topics/interest_by_region) — كل قسم مُغلَّف باستثنائه فيتدهور
    **مستقلاً** إلى قائمة فارغة بملاحظة السبب عند غيابه؛ لا اختلاق قط
    (المبدأ المؤسِّس). فشل/غياب pytrends => كل الأقسام فارغة بملاحظة السبب.
    يعيد {"related_top", "related_rising", "topics_rising", "regions",
          "confidence", "note"} — كل بند {"label", "value"}.
    """
    empty = {"related_top": [], "related_rising": [], "topics_rising": [],
             "regions": [], "confidence": 0.0, "note": ""}
    try:
        from pytrends.request import TrendReq  # lazy: optional dep
    except ImportError:
        return {**empty, "note": "pytrends unavailable / no network"}
    kw = (keyword or "").strip()
    if not kw:
        return {**empty, "note": "empty keyword — no query"}
    try:
        def _payload():
            _py = TrendReq(timeout=(10, 30))
            _py.build_payload([kw], timeframe=timeframe, geo=(geo or ""))
            return _py
        py = _retry_429(_payload, f"context '{kw}'")
    except Exception as e:  # noqa: BLE001 — never raise to caller
        log.warning("Trends context payload failed ('%s', geo=%s): %s",
                    keyword, geo, e)
        return {**empty, "note": f"pytrends unavailable / no network: {e}"}

    def _rows(df, label_col: str, n: int = 6) -> list[dict]:
        rows: list[dict] = []
        try:
            if df is None or df.empty:
                return rows
            for _, r in df.head(n).iterrows():
                label = r.get(label_col)
                if label is None:
                    continue
                rows.append({"label": str(label),
                             "value": _coerce_trend_value(r.get("value"))})
        except Exception:  # noqa: BLE001 — قسم واحد يفشل لا يُسقِط البقية
            return []
        return rows

    out = {**empty, "confidence": 0.6}
    found = False
    try:
        rq = (py.related_queries() or {}).get(kw) or {}
        out["related_top"] = _rows(rq.get("top"), "query")
        out["related_rising"] = _rows(rq.get("rising"), "query")
        found = found or bool(out["related_top"] or out["related_rising"])
    except Exception as e:  # noqa: BLE001
        log.warning("related_queries failed ('%s'): %s", kw, e)
    try:
        rt = (py.related_topics() or {}).get(kw) or {}
        out["topics_rising"] = _rows(rt.get("rising"), "topic_title")
        found = found or bool(out["topics_rising"])
    except Exception as e:  # noqa: BLE001
        log.warning("related_topics failed ('%s'): %s", kw, e)
    try:
        reg = py.interest_by_region(resolution="REGION")
        if reg is not None and not reg.empty and kw in reg.columns:
            top = reg.sort_values(kw, ascending=False).head(6)
            out["regions"] = [{"label": str(idx),
                               "value": _coerce_trend_value(row[kw])}
                              for idx, row in top.iterrows()
                              if row[kw]]
            found = found or bool(out["regions"])
    except Exception as e:  # noqa: BLE001
        log.warning("interest_by_region failed ('%s'): %s", kw, e)

    if not found:
        return {**empty, "note":
                f"لا سياق اتجاهات مرتبط لـ'{kw}' (geo={geo or 'WW'})"}
    out["note"] = (f"سياق اتجاهات لـ'{kw}' geo={geo or 'WW'}: "
                   f"{len(out['related_top'])} استعلام شائع، "
                   f"{len(out['related_rising'])} صاعد، "
                   f"{len(out['topics_rising'])} موضوع صاعد، "
                   f"{len(out['regions'])} إقليم")
    return out


def _seasonality(keyword: str, geo: str | None, timeframe: str) -> DataPoint:
    """موسمية بسيطة — peak month of search interest over the timeframe."""
    try:
        from pytrends.request import TrendReq  # lazy: optional dep
    except ImportError:
        return DataPoint(None, "Google Trends", 0.0,
                         "pytrends unavailable / no network", _today())
    try:
        kw = (keyword or "").strip()

        def _fetch():
            py = TrendReq(timeout=(10, 30))
            py.build_payload([kw], timeframe=timeframe, geo=(geo or ""))
            return py.interest_over_time()
        df = _retry_429(_fetch, f"seasonality '{kw}'")
        if df is None or df.empty or kw not in df.columns:
            return DataPoint(None, "Google Trends", 0.0,
                             f"no series for seasonality of '{kw}'", _today())
        monthly = df[kw].groupby(df.index.month).mean()
        peak_month = int(monthly.idxmax())
        return DataPoint(peak_month, "Google Trends", 0.6,
                         f"peak interest month = {calendar.month_name[peak_month]} "
                         f"for '{kw}' geo={geo or 'WW'}", _today())
    except Exception as e:  # noqa: BLE001
        log.warning("Google Trends seasonality failed ('%s'): %s", keyword, e)
        return DataPoint(None, "Google Trends", 0.0,
                         f"pytrends unavailable / no network: {e}", _today())


def _weak_threshold() -> float:
    """عتبة «الطلب شبه المعدوم» على الصفة الدقيقة — config-driven."""
    try:
        v = float(os.environ.get("SILK_TRENDS_WEAK_THRESHOLD", "5"))
        return v if v > 0 else 5.0
    except (TypeError, ValueError):
        return 5.0


def _broaden_max_candidates() -> int:
    """سقف عدد المصطلحات الأعمّ المُستعلَمة — يحرس ميزانية pytrends المحدودة
    (429 مرصود حياً، مراجعة الشيفرة #2). config: SILK_TRENDS_BROADEN_MAX (٢)."""
    try:
        n = int(os.environ.get("SILK_TRENDS_BROADEN_MAX", "2"))
        return n if n > 0 else 2
    except (TypeError, ValueError):
        return 2


def broaden_if_weak(keyword: str, geo: str | None, timeframe: str,
                    interest: DataPoint) -> DataPoint | None:
    """Wave 2.3 — حين تُسجِّل الصفةُ الدقيقة طلباً شبه معدوم (≤ العتبة) بينما
    الفئة الأعمّ قوية، استعلِم عائلة المصطلح الأعمّ **آلياً** وأعِد كليهما،
    مؤطَّراً «الطلب على الفئة موجود؛ الصفة الدقيقة غير مبحوثة» — لا استنتاج
    «لا طلب» من استعلامٍ مفرطٍ في التخصيص (تدقيق زبدة الفول السوداني/اليمن:
    «زبدة الفول السوداني»=0.4 بينما «فوائد زبدة الفول السوداني»=100).

    المصطلح الأعمّ **مبنيّ على البيانات** (أقوى استعلام مرتبط من Trends نفسها،
    لا قائمة مكتوبة صلباً). لا إشارة أعمّ أقوى => None (لا اختلاق طلب)."""
    if interest.value is None or float(interest.value) > _weak_threshold():
        return None
    ctx = trends_context(keyword, geo, timeframe)
    # سقفٌ على عدد المرشّحين المُستعلَمين — لا حلقة غير محدودة تُغرِق pytrends
    # المحدود (429 مرصود حياً، مراجعة الشيفرة #2).
    cands = ((ctx.get("related_top") or [])
             + (ctx.get("related_rising") or []))[:_broaden_max_candidates()]
    for cand in cands:
        broader = (cand.get("label") or "").strip()
        if not broader or broader == keyword:
            continue
        bi = trends_interest(broader, geo, timeframe)
        if bi.value is not None and float(bi.value) > float(interest.value):
            return DataPoint(
                bi.value, "Google Trends", 0.6,
                f"مصطلح أعمّ '{broader}' متوسط اهتمامه {bi.value}/100 — "
                f"الطلب على الفئة موجود؛ الصفة الدقيقة '{keyword}' غير مبحوثة",
                _today())
    return None


# رسالة إغلاق فجوة الموسمية (Wave 2.2) — خطوة عملية مقترحة في «ما لم يكتمل».
SEASONALITY_GAP_CLOSURE = ("الموسمية (رمضان/الأعياد) غير مرصودة من مؤشرات "
                           "البحث — إغلاقها يتطلّب بحثاً ميدانياً أوّلياً "
                           "(مقابلات موزّعين أو استبيان طلب موسمي).")


class TrendsAgent(BaseAgent):
    """وكيل الاتجاهات — Google Trends demand signal for a product keyword."""

    PAID = False
    PREF_KEY = "trends"

    def __init__(self) -> None:
        super().__init__("TrendsAgent")

    def _execute(self, task: dict) -> AgentReport:
        """إشارة الطلب من بحث جوجل — mean interest + seasonality, real data only.

        task keys: keyword(str), geo(ISO2 like 'AE'/'SA', optional),
                   timeframe(default 'today 12-m').
        """
        keyword = task.get("keyword", "")
        geo = task.get("geo")
        timeframe = task.get("timeframe", "today 12-m")

        # WS11.1: سلسلة صمود — pytrends → لقطة مخزَّنة → فجوة (لا اختلاق).
        interest = trends_interest_resilient(keyword, geo, timeframe)
        if interest.value is None:
            return AgentReport(self.name, [interest], True,
                               "لا توجد بيانات اتجاهات — no Google Trends data available")
        findings = [interest]
        # Wave 2.3: توسيع المصطلح آلياً حين تكون الصفة الدقيقة شبه معدومة.
        broadened = broaden_if_weak(keyword, geo, timeframe, interest)
        summary = f"interest={interest.value}/100 for '{keyword}' (geo={geo or 'WW'})"
        if broadened is not None:
            findings.append(broadened)
            summary += ("؛ الطلب على الفئة الأعمّ موجود — الصفة الدقيقة "
                        "غير مبحوثة")
        season = _seasonality(keyword, geo, timeframe)
        if season.value is not None:
            findings.append(season)
        else:
            # Wave 2.2: فجوة الموسمية تُعلَن مرة واحدة مع خطوة الإغلاق (لا
            # تُسقَط صامتةً) — DataPoint فجوة معلنة يقرؤها السرد/الحدود.
            findings.append(DataPoint(None, "Google Trends", 0.0,
                                      SEASONALITY_GAP_CLOSURE, _today(),
                                      status="fetch_failed"))
        return AgentReport(self.name, findings, False, summary)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Silk TrendsAgent — degrades gracefully offline / without pytrends (no fabricated data)")
    report = TrendsAgent().run({"keyword": "تمور", "geo": "AE"})
    flag = "FAILED" if report.failed else "ok"
    print(f"  [{flag}] {report.agent_name}: {report.summary}")
    for f in report.findings:
        print(f"    value={f.value} conf={f.confidence} note={f.note}")
