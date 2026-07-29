"""اختبارات بلاغ حي ثانٍ (تمور/هولندا): ١٢/١٢ بعثة نجحت والتقاطعات
ممتلئة، لكن كاتب التقرير استمر يفشل بلا أي أثر تشخيصي. ثلاثة إصلاحات:

1. تتبّع مسار مهلة كاتب التقرير/المراجع (silk_ai_judge._traced_call) —
   يثبت أن المهلة الموسّعة تصل فعلياً كل نداء في مسار الكاتب، وأن
   استدعاء /research الكامل يسجّل أحداث تتبّع للمحلل والكاتب معاً (كانا
   يعملان بلا تتبّع إطلاقاً — trace_context البعثات يُغلَق قبلهما).
2. POST /analyses/{id}/report — إعادة توليد التقرير من نقاط تفتيش
   البعثات المحفوظة، نداء كاتب واحد بلا إعادة تشغيل أي بعثة.
3. تنظيف السباكة الداخلية (LLMAgent:*/وسوم dp) من الطبقة المعروضة
   للعميل — بلاغ منتج من المالك.

لا شبكة ولا مفتاح حقيقي مطلوبان (نفس تقليد المستودع الهيرمتي).
Run:  python3 -m pytest tests/test_wave_p2_writer_trace_regen_sanitize.py -q
"""
import contextlib
import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import block_network


@contextlib.contextmanager
def _env(**vals):
    old = {k: os.environ.get(k) for k in vals}
    try:
        for k, v in vals.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _ref():
    from silk_market_resolver import resolve_market
    ref, _ = resolve_market("Nigeria")
    return ref


def _mission_reports():
    from silk_agents import AgentReport
    from silk_data_layer import DataPoint
    return {"trade_flow": AgentReport(
        "LLMAgent:trade_flow",
        [DataPoint(950000.0, "UN Comtrade", 0.9, "استيراد 2023")], False, "ok")}


def _complete_draft():
    import silk_ai_judge as aj
    return "\n".join(f"## {i}. {s}\nنص." for i, s in
                     enumerate(aj._REPORT_SECTIONS, 1))


# ── ١: تتبّع مسار الكاتب/المراجع ──────────────────────────────────────────

def test_deep_report_records_trace_event_with_long_timeout(tmp_path):
    import silk_ai_judge as aj
    import silk_trace

    with _env(ANTHROPIC_API_KEY="test-key", SILK_TRACE_DIR=str(tmp_path)), \
         patch("silk_ai_judge._call", return_value=_complete_draft()):
        aj.deep_report({}, "خلاصة", {"verdict": "WATCH"}, "تمور", "هولندا",
                       trace_id="t1")

    events = silk_trace.read_trace("t1", dir_path=str(tmp_path))
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "report_call" and ev["stage"] == "draft"
    assert ev["timeout"] == aj._LONG_TIMEOUT
    assert ev["success"] is True
    assert "elapsed_ms" in ev


def test_deep_report_traces_revision_stage_separately(tmp_path):
    import silk_ai_judge as aj
    import silk_trace

    with _env(ANTHROPIC_API_KEY="test-key", SILK_TRACE_DIR=str(tmp_path)), \
         patch("silk_ai_judge._call", return_value=_complete_draft()):
        aj.deep_report({}, "خلاصة", {"verdict": "WATCH"}, "تمور", "هولندا",
                       review_notes=["أصلح X"], trace_id="t2")

    events = silk_trace.read_trace("t2", dir_path=str(tmp_path))
    assert events[0]["stage"] == "revision"


def test_review_report_traces_with_its_own_short_timeout(tmp_path):
    import silk_ai_judge as aj
    import silk_trace

    with _env(ANTHROPIC_API_KEY="test-key", SILK_TRACE_DIR=str(tmp_path)), \
         patch("silk_ai_judge._call",
               return_value='{"issues": [], "approved": true}'):
        aj.review_report(_complete_draft(), _mission_reports(), trace_id="t3")

    events = silk_trace.read_trace("t3", dir_path=str(tmp_path))
    assert events[0]["kind"] == "report_call"
    assert events[0]["stage"] == "review"
    assert events[0]["timeout"] == 30


def test_write_reviewed_report_traces_call_failure_with_long_timeout(tmp_path):
    """بلاغ حي — يثبت بالضبط ما طُلب: عند فشل الكاتب، السجل يحمل المهلة
    الموسّعة (300s) فعلاً لا الافتراضية (60s) — لا حاجة للتخمين بعد الآن."""
    import silk_ai_judge as aj
    import silk_trace

    with _env(ANTHROPIC_API_KEY="test-key", SILK_TRACE_DIR=str(tmp_path)), \
         patch("silk_ai_judge._call", return_value=None):
        out = aj.write_reviewed_report(
            {}, "خلاصة", {"verdict": "WATCH"}, "تمور", "هولندا",
            trace_id="t4")

    assert out["report"] is None
    events = silk_trace.read_trace("t4", dir_path=str(tmp_path))
    assert len(events) == 1  # فشل الكاتب => لا مراجعة تُحاوَل أصلاً
    assert events[0]["stage"] == "draft"
    assert events[0]["timeout"] == 300.0
    assert events[0]["success"] is False


def test_write_reviewed_report_traces_full_writer_reviewer_cycle(tmp_path):
    import silk_ai_judge as aj
    import silk_trace

    def fake_call(system, user, max_tokens=1600, model=None, timeout=None):
        if model == aj._FAST_MODEL:
            return '{"issues": [], "approved": true}'
        return _complete_draft()

    with _env(ANTHROPIC_API_KEY="test-key", SILK_TRACE_DIR=str(tmp_path)), \
         patch("silk_ai_judge._call", side_effect=fake_call):
        aj.write_reviewed_report({}, "خلاصة", {"verdict": "WATCH"},
                                 "تمور", "هولندا", trace_id="t5")

    events = silk_trace.read_trace("t5", dir_path=str(tmp_path))
    stages = [e["stage"] for e in events]
    assert stages == ["draft", "review"]
    assert events[0]["timeout"] == aj._LONG_TIMEOUT
    assert events[1]["timeout"] == 30


def test_no_trace_id_means_zero_tracing_cost(tmp_path):
    """نداء مكتبي مباشر (بلا trace_id) — لا كتابة تتبّع إطلاقاً، لا كسر،
    حتى لو كان SILK_TRACE_DIR مضبوطاً (لا ملف يُنشأ إطلاقاً)."""
    import silk_ai_judge as aj

    with _env(ANTHROPIC_API_KEY="test-key", SILK_TRACE_DIR=str(tmp_path)), \
         patch("silk_ai_judge._call", return_value=_complete_draft()):
        aj.deep_report({}, "خلاصة", {"verdict": "WATCH"}, "تمور", "هولندا")

    assert list(tmp_path.iterdir()) == []


def test_research_endpoint_traces_analyst_and_writer_calls(tmp_path):
    """تكامل كامل عبر /research — بلاغ حي: المحلل والكاتب كانا يعملان
    بلا أي أثر تتبّع (trace_context البعثات يُغلَق قبلهما في api.py).
    الآن: كلاهما يُسجَّل في نفس ملف تتبّع البعثات."""
    import pytest
    pytest.importorskip("fastapi"); pytest.importorskip("httpx")

    def fake_call_tools(system, messages, tools=None, max_tokens=1600,
                        model=None, timeout=None):
        return {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": json.dumps(
                {"findings": [], "gaps": [], "summary": "ok"})}]}

    def fake_call(system, user, max_tokens=1600, model=None, timeout=None):
        return json.dumps({"verdict": "WATCH", "confidence": 0.5,
                           "reasoning": "ok"})

    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    with _env(ANTHROPIC_API_KEY="test", SILK_API_KEY="secret",
             SILK_TRACE_DIR=str(tmp_path)), \
         patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_synthesis._call", side_effect=fake_call), \
         patch("silk_ai_judge._call", side_effect=fake_call), \
         patch("silk_storage._db_path", return_value=db):
        from fastapi.testclient import TestClient
        import api
        r = TestClient(api.app).post(
            "/research", headers={"X-API-Key": "secret"},
            json={"product": "تمور", "market": "Nigeria", "hs_code": "080410",
                 "persist": False})

    assert r.status_code == 200
    trace_id = r.json()["view"]["deep_research"]["trace_id"]
    import silk_trace
    events = silk_trace.read_trace(trace_id, dir_path=str(tmp_path))
    kinds = [e.get("kind") for e in events]  # حدث بوابة الجودة يحمل "event" لا "kind"
    assert "report_call" in kinds  # الكاتب/المراجع مسجَّلان الآن
    analyst_llm_events = [e for e in events
                          if e.get("kind") == "llm_call"
                          and e.get("mission") == "market_analyst"]
    assert analyst_llm_events  # المحلل الشامل مسجَّل أيضاً


# ── ٢: POST /analyses/{id}/report — إعادة توليد رخيصة ─────────────────────

def _stored_research_analysis():
    """شكل تحليل /research مخزَّن (بعد dataclasses.asdict) — dr.missions/
    analyst.report كلها dict عادية لا AgentReport حيّة."""
    return {
        "product": "تمور", "hs_code": "080410",
        "market": {"iso3": "NGA", "name_en": "Nigeria", "name_ar": "نيجيريا"},
        "deep_research": {
            "missions": {"trade_flow": {
                "agent_name": "LLMAgent:trade_flow", "failed": False,
                "summary": "ok", "findings": []}},
            "analyst": {"report": {"agent_name": "LLMAgent:market_analyst",
                                   "failed": False,
                                   "summary": "خمس تقاطعات مبنية على الأدلة"},
                       "by_category": {}, "missing_categories": []},
            "verdict": {"verdict": "WATCH", "confidence": 0.5},
            "report": {"report": None, "review_cycles": 0,
                      "unresolved_notes": [],
                      "failure_reason": "فشل نداء كلود (مهلة أو خطأ شبكة)"},
            "trace_id": "run-NGA-123",
        },
        "view": {},
    }


def _client(monkeypatch=None):
    import pytest
    pytest.importorskip("fastapi"); pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import importlib
    import api
    importlib.reload(api)
    return TestClient(api.create_app())


def test_regenerate_report_404_for_missing_analysis():
    with _env(SILK_API_KEY=None, SILK_RATE_LIMIT="0"):
        client = _client()
        with patch("silk_storage.get_analysis", return_value=None):
            r = client.post("/analyses/999/report")
    assert r.status_code == 404


def test_regenerate_report_400_for_non_research_analysis():
    with _env(SILK_API_KEY=None, SILK_RATE_LIMIT="0"):
        client = _client()
        with patch("silk_storage.get_analysis",
                   return_value={"product": "تمور", "markets": []}):
            r = client.post("/analyses/1/report")
    assert r.status_code == 400


def test_regenerate_report_409_when_no_checkpoints_stored():
    with _env(SILK_API_KEY="secret", SILK_RATE_LIMIT="0",
             ANTHROPIC_API_KEY="test"):
        client = _client()
        with patch("silk_storage.get_analysis",
                   return_value=_stored_research_analysis()), \
             patch("silk_storage.load_mission_checkpoints", return_value={}):
            r = client.post("/analyses/1/report",
                            headers={"X-API-Key": "secret"})
    assert r.status_code == 409


def test_regenerate_report_calls_writer_once_with_reconstructed_reports():
    from silk_agents import AgentReport
    from silk_data_layer import DataPoint

    live_reports = {"trade_flow": AgentReport(
        "LLMAgent:trade_flow",
        [DataPoint(950000.0, "UN Comtrade", 0.9, "n")], False, "ok")}
    captured = {}

    def fake_write_reviewed_report(mission_reports, analyst_summary, verdict,
                                   product, market_name, trace_id=None,
                                   hs_code=None, hs_confirmation=None,
                                   style=None):
        captured["mission_reports"] = mission_reports
        captured["analyst_summary"] = analyst_summary
        captured["verdict"] = verdict
        captured["product"] = product
        captured["market_name"] = market_name
        captured["trace_id"] = trace_id
        captured["hs_code"] = hs_code  # المقترح ٤: يُمرَّر لاشتقاق فئة المنتج
        return {"report": "## 1. الخلاصة التنفيذية\nنص.", "review_cycles": 1,
               "unresolved_notes": []}

    saved = {}

    def fake_save(result, path=None, analysis_id=None):
        saved["result"] = result
        saved["analysis_id"] = analysis_id
        return analysis_id

    with _env(SILK_API_KEY="secret", SILK_RATE_LIMIT="0",
             ANTHROPIC_API_KEY="test"):
        client = _client()
        with patch("silk_storage.get_analysis",
                   return_value=_stored_research_analysis()), \
             patch("silk_storage.load_mission_checkpoints",
                   return_value=live_reports), \
             patch("silk_ai_judge.write_reviewed_report",
                   side_effect=fake_write_reviewed_report), \
             patch("silk_storage.save_analysis", side_effect=fake_save):
            r = client.post("/analyses/42/report",
                            headers={"X-API-Key": "secret"})

    assert r.status_code == 200
    assert captured["mission_reports"] is live_reports  # بلا إعادة بناء زائفة
    assert captured["analyst_summary"] == "خمس تقاطعات مبنية على الأدلة"
    assert captured["verdict"] == {"verdict": "WATCH", "confidence": 0.5}
    assert captured["product"] == "تمور"
    assert captured["market_name"] == "Nigeria"
    assert captured["trace_id"] == "run-NGA-123"
    body = r.json()
    assert body["deep_research"]["report"]["report"].startswith("## 1.")
    assert "quality_gate" in body["view"]["deep_research"]
    assert saved["analysis_id"] == 42


def test_regenerate_report_blocked_gracefully_when_daily_cap_exhausted():
    from silk_agents import AgentReport

    with _env(SILK_API_KEY="secret", SILK_RATE_LIMIT="0",
             ANTHROPIC_API_KEY="test", SILK_PAID_DAILY_CAP="0",
             SILK_USAGE_DB=os.path.join(tempfile.mkdtemp(), "usage.db")):
        client = _client()
        with patch("silk_storage.get_analysis",
                   return_value=_stored_research_analysis()), \
             patch("silk_storage.load_mission_checkpoints",
                   return_value={"trade_flow": AgentReport("x", [], False, "")}), \
             patch("silk_ai_judge.write_reviewed_report") as wrr:
            r = client.post("/analyses/1/report",
                            headers={"X-API-Key": "secret"})

    assert r.status_code == 200
    assert r.json()["report"] is None
    wrr.assert_not_called()


# ── ٣: تنظيف السباكة الداخلية من الطبقة المعروضة ──────────────────────────

def test_strip_internal_plumbing_replaces_llmagent_with_arabic_mission_name():
    from silk_render import _strip_internal_plumbing

    out = _strip_internal_plumbing(
        "LLMAgent:tariffs_agreements: لا توجد بيانات WITS")
    assert "LLMAgent" not in out
    assert "tariffs_agreements" not in out
    assert "التعريفات الجمركية والاتفاقيات التجارية" in out


def test_strip_internal_plumbing_removes_dp_tags():
    from silk_render import _strip_internal_plumbing

    out = _strip_internal_plumbing("استناداً إلى [dp3] وdp12 معاً")
    assert "dp3" not in out
    assert "dp12" not in out


def test_strip_internal_plumbing_leaves_clean_text_untouched():
    from silk_render import _strip_internal_plumbing

    clean = "حجم الاستيراد 60 مليون دولار وفق UN Comtrade."
    assert _strip_internal_plumbing(clean) == clean


def test_strip_internal_plumbing_passthrough_on_empty():
    from silk_render import _strip_internal_plumbing

    assert _strip_internal_plumbing(None) is None
    assert _strip_internal_plumbing("") == ""


def test_deep_research_view_limits_use_mission_label_not_raw_name():
    from silk_render import build_view
    from silk_agents import AgentReport

    result = {
        "product": "تمور", "hs_code": "080410", "markets": [],
        "market": {"iso3": "NGA", "name_ar": "نيجيريا"},
        "deep_research": {
            "missions": {"tariffs_agreements": AgentReport(
                "LLMAgent:tariffs_agreements", [], True,
                "لا نتائج مبنية على استشهاد | فجوات: لا توجد بيانات WITS")},
            "analyst": {"report": AgentReport("A", [], True, ""),
                       "by_category": {}, "missing_categories": []},
            "verdict": {}, "report": {},
        },
    }
    view = build_view(result)
    limits_text = " ".join(view["deep_research"]["limits"])
    assert "LLMAgent" not in limits_text
    assert "tariffs_agreements" not in limits_text
    assert "التعريفات الجمركية والاتفاقيات التجارية" in limits_text


def test_deep_research_view_report_text_sanitized():
    from silk_render import build_view
    from silk_agents import AgentReport

    result = {
        "product": "تمور", "hs_code": "080410", "markets": [],
        "market": {"iso3": "NGA", "name_ar": "نيجيريا"},
        "deep_research": {
            "missions": {}, "analyst": {"report": AgentReport("A", [], True, ""),
                                        "by_category": {},
                                        "missing_categories": []},
            "verdict": {},
            "report": {"report": "## 1. الخلاصة التنفيذية\n"
                                 "وفق LLMAgent:tariffs_agreements لا يوجد "
                                 "رصد [dp7] للتعريفة.",
                      "review_cycles": 1, "unresolved_notes": []},
        },
    }
    view = build_view(result)
    text = view["deep_research"]["report"]["text"]
    assert "LLMAgent" not in text
    assert "dp7" not in text
    assert "التعريفات الجمركية والاتفاقيات التجارية" in text


def test_quality_gate_flags_residual_internal_plumbing_leak_as_repairable():
    import silk_quality_gate as qg

    view = {"deep_research": {
        "missions": {}, "analyst": {"by_category": {}},
        "report": {"text": "## 1. الخلاصة التنفيذية\n"
                           "نص عادي ثم LLMAgent:pricing_scout تسرّب هنا."},
    }}
    out = qg.run_quality_gate(view)
    checks = {f["check"]: f for f in out["findings"]}
    assert "internal_plumbing_leak" in checks
    assert checks["internal_plumbing_leak"]["repairable"] is True


def test_quality_gate_silent_when_report_text_already_clean():
    import silk_quality_gate as qg

    view = {"deep_research": {
        "missions": {}, "analyst": {"by_category": {}},
        "report": {"text": "## 1. الخلاصة التنفيذية\n"
                           "نص نظيف بلا أي سباكة داخلية."},
    }}
    out = qg.run_quality_gate(view)
    checks = {f["check"] for f in out["findings"]}
    assert "internal_plumbing_leak" not in checks
