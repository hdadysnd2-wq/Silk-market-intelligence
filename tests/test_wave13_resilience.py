"""اختبارات صمود /research — حادثة نفاد الاعتمادات (wave 13).

بلاغ حادثة: تشغيلة /research حية فشلت منتصف الطريق وأحرقت اعتمادات دون
أي نتيجة قابلة للاستخدام. يقفل هذا الملف: (١) نقطة تفتيش/استئناف — عطل
منتصف التشغيلة لا يخسر البعثات المكتملة، والاستئناف لا يعيد نداء أيّ
بعثة مكتملة بالفعل (٢) تشغيل خلفي — async_run=true يعيد analysis_id
فوراً (202) وGET /research/{id}/status يعكس التقدّم حتى الاكتمال (٣)
نفاد الميزانية الكلية (SILK_RESEARCH_MAX_LLM_CALLS) ينتهي برد ٢٠٠ سليم
يسمّي السقف صراحة في deep_research.budget_status، لا خطأً صلباً.
كل شيء مموّه — لا شبكة، لا مفتاح كلود حقيقي.
Run:  python3 -m pytest tests/test_wave13_resilience.py -q
"""
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client():
    from fastapi.testclient import TestClient
    import api
    return TestClient(api.app)


def _fake_call_tools_factory(log_list=None):
    def fake_call_tools(system, messages, tools=None, max_tokens=1600,
                        model=None, timeout=None):
        if log_list is not None:
            log_list.append(system[:60])
        return {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": json.dumps(
                {"findings": [], "gaps": [], "summary": "ok"})}]}
    return fake_call_tools


def _fake_call(system, user, max_tokens=1600, model=None, timeout=None):
    return json.dumps({"verdict": "WATCH", "confidence": 0.5, "reasoning": "ok"})


# ── ١: نقطة تفتيش/استئناف — عطل منتصف الطريق لا يخسر البعثات المكتملة ───────

def test_mid_run_crash_then_resume_skips_completed_missions():
    import silk_market_analyst
    from silk_agents import AgentReport

    tool_calls: list[str] = []
    fake_call_tools = _fake_call_tools_factory(tool_calls)
    missions_ran: list[str] = []

    class _CompletedMission:
        """بعثة مكتملة فعلاً (failed=False) — فكس «الاستئناف يعيد الفاشلة»
        (بلاغ Nadec/اليمن #7) جعل علمَ الفشل فيصلَ التخطّي: المموّه القديم
        (صفر نتائج مستشهدة عبر _call_tools) كان يُخزَّن failed=True فتُعاد
        بعثاته شرعاً على الاستئناف — عكس مقصد هذا الاختبار (بعثات ناجحة
        لا يُعاد دفعها). الآن تُموَّه البعثة الناجحة صراحةً."""

        def __init__(self, spec):
            self._key = spec["key"]

        def run(self, task):
            missions_ran.append(self._key)
            return AgentReport(f"LLMMissionAgent:{self._key}", [], False, "ok")

    real_analyze_market = silk_market_analyst.analyze_market
    state = {"n": 0}

    def flaky_analyze_market(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            # محاكاة عطل حقيقي (لا سقوط عملية فعلي ممكن هيرمتياً) — استثناء
            # غير متوقع بعد اكتمال الاثنتي عشرة بعثة، قبل إنهاء التشغيلة.
            raise RuntimeError("simulated mid-run crash after missions")
        return real_analyze_market(*a, **k)

    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test",
                                 "SILK_API_KEY": "secret"}), \
         patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_synthesis._call", side_effect=_fake_call), \
         patch("silk_ai_judge._call", side_effect=_fake_call), \
         patch("silk_storage._db_path", return_value=db), \
         patch("silk_missions.LLMMissionAgent", _CompletedMission), \
         patch("silk_market_analyst.analyze_market",
              side_effect=flaky_analyze_market):
        client = _client()
        hdr = {"X-API-Key": "secret"}

        r1 = client.post("/research", headers=hdr, json={
            "product": "تمور", "market": "Nigeria", "hs_code": "080410",
            "persist": True})
        assert r1.status_code == 500
        detail = r1.json()["detail"]
        assert detail["error"] == "research_run_failed"
        analysis_id = detail["analysis_id"]
        assert analysis_id is not None
        # كل الاثنتي عشرة بعثة اكتملت وخُزِّنت قبل العطل المحاكى في المحلل.
        assert len(missions_ran) == 12

        missions_ran.clear()
        tool_calls.clear()
        r2 = client.post("/research", headers=hdr,
                         json={"resume": analysis_id})
        assert r2.status_code == 200
        data = r2.json()
        assert data["analysis_id"] == analysis_id
        assert len(data["deep_research"]["missions"]) == 12
        # الاستئناف لا يعيد نداء أي بعثة **مكتملة** — صفر بعثة تُعاد، ونداء
        # واحد فقط (المحلل، الذي لا يُخزَّن كنقطة تفتيش عمداً وفق التوجيه:
        # "أعد المحلل/الكاتب").
        assert missions_ran == []
        assert len(tool_calls) == 1


def test_resuming_an_already_completed_run_is_a_pure_replay_no_new_calls():
    """استئناف تشغيلة مكتملة أصلاً = إعادة تسليم بلا أي نداء جديد — أمان
    التكرار (idempotency)، لا حرق اعتمادات مضاعف عند نقر «استئناف» خطأً.

    تُموَّه البعثات ناجحةً صراحةً (failed=False): «مكتملة فعلاً» تعني نجاح
    كل بعثاتها. المموّه القديم (findings=[] عبر _call_tools) كان يخزّنها
    failed=True، فبعد فكس «resume يعيد الفاشلة» (بلاغ Nadec/اليمن #7) صارت
    تلك التشغيلة **تُعاد** بحقّ لا تُسلَّم صامتة — فلم تعد تختبر مقصدها.
    الآن بعثات ناجحة حقيقية => إعادة تسليم صرفة صحيحة الدلالة."""
    from silk_agents import AgentReport
    tool_calls: list[str] = []
    fake_call_tools = _fake_call_tools_factory(tool_calls)
    missions_ran: list[str] = []

    class _CompletedMission:
        def __init__(self, spec):
            self._key = spec["key"]

        def run(self, task):
            missions_ran.append(self._key)
            return AgentReport(f"LLMMissionAgent:{self._key}", [], False, "ok")

    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test",
                                 "SILK_API_KEY": "secret"}), \
         patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_missions.LLMMissionAgent", _CompletedMission), \
         patch("silk_synthesis._call", side_effect=_fake_call), \
         patch("silk_ai_judge._call", side_effect=_fake_call), \
         patch("silk_storage._db_path", return_value=db):
        client = _client()
        hdr = {"X-API-Key": "secret"}
        r1 = client.post("/research", headers=hdr, json={
            "product": "تمور", "market": "Nigeria", "hs_code": "080410",
            "persist": True})
        assert r1.status_code == 200
        analysis_id = r1.json()["analysis_id"]

        tool_calls.clear()
        missions_ran.clear()
        r2 = client.post("/research", headers=hdr,
                         json={"resume": analysis_id})
        assert r2.status_code == 200
        assert r2.json()["analysis_id"] == analysis_id
        assert tool_calls == []      # صفر نداء ذيل — إعادة تسليم صرفة
        assert missions_ran == []    # ولا بعثة أُعيدت (كلها ناجحة أصلاً)


def test_resume_of_unknown_id_returns_404_not_fabricated_result():
    with patch.dict(os.environ, {"SILK_API_KEY": "secret"}):
        r = _client().post("/research", headers={"X-API-Key": "secret"},
                           json={"resume": 999999})
    assert r.status_code == 404


def test_resume_of_non_research_analysis_is_rejected():
    """استئناف معرّف ينتمي لتحليل /analyze (لا /research) = 400 واضح، لا
    محاولة استئناف عشوائية على بيانات لا تخصّ هذا المسار."""
    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    with patch("silk_storage._db_path", return_value=db):
        import silk_storage
        aid = silk_storage.save_analysis({"product": "تمور", "markets": []})
        with patch.dict(os.environ, {"SILK_API_KEY": "secret"}):
            r = _client().post("/research", headers={"X-API-Key": "secret"},
                               json={"resume": aid})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "not_a_research_run"


# ── ٢: تشغيل خلفي — الطلب لا يساوي التشغيلة نفسها ────────────────────────────

def test_async_run_returns_immediately_then_status_reflects_progress():
    tool_calls: list[str] = []
    fake_call_tools = _fake_call_tools_factory(tool_calls)
    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test",
                                 "SILK_API_KEY": "secret"}), \
         patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_synthesis._call", side_effect=_fake_call), \
         patch("silk_ai_judge._call", side_effect=_fake_call), \
         patch("silk_storage._db_path", return_value=db):
        client = _client()
        hdr = {"X-API-Key": "secret"}

        r = client.post("/research", headers=hdr, json={
            "product": "تمور", "market": "Nigeria", "hs_code": "080410",
            "persist": True, "async_run": True})
        assert r.status_code == 202
        body = r.json()
        assert body["status"] == "running"
        analysis_id = body["analysis_id"]
        assert body["poll_url"] == f"/research/{analysis_id}/status"

        # يجب البقاء ضمن كتلة الأدوية المموَّهة أثناء الاستطلاع — الخيط
        # الخلفي قد لا يزال يعمل، وخروج `with patch` قبله يُسرّب نداءً حقيقياً.
        status = None
        for _ in range(200):
            sr = client.get(f"/research/{analysis_id}/status", headers=hdr)
            assert sr.status_code == 200
            status = sr.json()
            assert status["missions_total"] == 12
            if status["status"] != "running":
                break
            time.sleep(0.02)
        assert status["status"] == "completed"
        assert status["missions_completed"] == 12

        final = client.get(f"/analyses/{analysis_id}", headers=hdr)
        assert final.status_code == 200
        assert len(final.json()["deep_research"]["missions"]) == 12


def test_async_run_without_persist_is_rejected_not_silently_dropped():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test",
                                 "SILK_API_KEY": "secret"}):
        r = _client().post("/research", headers={"X-API-Key": "secret"}, json={
            "product": "تمور", "market": "Nigeria", "hs_code": "080410",
            "persist": False, "async_run": True})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "async_requires_persist"


def test_status_endpoint_404s_for_unknown_analysis():
    with patch.dict(os.environ, {"SILK_API_KEY": "secret"}):
        r = _client().get("/research/999999/status",
                          headers={"X-API-Key": "secret"})
    assert r.status_code == 404


# ── ٣: نفاد الميزانية الكلية — إنهاء رشيق يسمّي السقف صراحة ──────────────────

def test_llm_call_cap_exhaustion_finishes_gracefully_and_names_the_cap():
    tool_calls: list[str] = []
    fake_call_tools = _fake_call_tools_factory(tool_calls)
    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    # سقف أدنى بكثير من ١٢ بعثة + محلل — يُتجاوَز حتماً، بلا اعتماد على
    # توقيت التوازي (المجموع النهائي وحده هو ما يُقارَن بالسقف).
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test",
                                 "SILK_API_KEY": "secret",
                                 "SILK_RESEARCH_MAX_LLM_CALLS": "3"}), \
         patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_synthesis._call", side_effect=_fake_call), \
         patch("silk_ai_judge._call", side_effect=_fake_call), \
         patch("silk_storage._db_path", return_value=db):
        r = _client().post("/research", headers={"X-API-Key": "secret"}, json={
            "product": "تمور", "market": "Nigeria", "hs_code": "080410",
            "persist": True})
    # لا خطأ صلب رغم تجاوز السقف — تسليم ٢٠٠ برد كامل موسوم.
    assert r.status_code == 200
    data = r.json()
    assert len(data["deep_research"]["missions"]) == 12
    budget = data["deep_research"]["budget_status"]
    assert budget["exhausted"] is True
    assert any("SILK_RESEARCH_MAX_LLM_CALLS" in c for c in budget["caps_hit"])
    assert budget["llm_cap"] == 3
    assert budget["llm_calls"] >= 3


def test_budget_status_present_and_not_exhausted_under_normal_run():
    """تحت السقف الافتراضي، budget_status حاضر دوماً (شفافية) لكن غير مستنفَد —
    لا يُظهَر تحذير زائف لتشغيلة سليمة."""
    tool_calls: list[str] = []
    fake_call_tools = _fake_call_tools_factory(tool_calls)
    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test",
                                 "SILK_API_KEY": "secret"}), \
         patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_synthesis._call", side_effect=_fake_call), \
         patch("silk_ai_judge._call", side_effect=_fake_call), \
         patch("silk_storage._db_path", return_value=db):
        r = _client().post("/research", headers={"X-API-Key": "secret"}, json={
            "product": "تمور", "market": "Nigeria", "hs_code": "080410",
            "persist": True})
    assert r.status_code == 200
    budget = r.json()["deep_research"]["budget_status"]
    assert budget["exhausted"] is False
    assert budget["caps_hit"] == []


# ── وحدات مباشرة (silk_storage) — نقاط تفتيش فاسدة لا تكسر الاستئناف ────────

def test_corrupt_checkpoint_row_is_skipped_not_fatal():
    import silk_storage
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    silk_storage.init_db(db)
    aid = silk_storage.create_research_run(
        "تمور", "NGA", "080410", {"product": "تمور", "market": "Nigeria"}, db)
    with silk_storage._connect(db) as conn:
        conn.execute(
            "INSERT INTO research_missions "
            "(analysis_id, mission_key, status, report_json, completed_at) "
            "VALUES (?, 'trade_flow', 'completed', 'not-json{{', 'now')",
            (aid,))
    loaded = silk_storage.load_mission_checkpoints(aid, db)
    assert loaded == {}  # الصفّ الفاسد أُهمِل، لا استثناء


def test_mark_research_failed_preserves_completed_checkpoints():
    import silk_storage
    from silk_agents import AgentReport
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    aid = silk_storage.create_research_run(
        "تمور", "NGA", "080410", {"product": "تمور", "market": "Nigeria"}, db)
    silk_storage.save_mission_checkpoint(
        aid, "trade_flow", AgentReport("x", [], False, "ok"), db)
    silk_storage.mark_research_failed(aid, "boom", db)
    assert silk_storage.get_research_run(aid, db)["status"] == "failed"
    assert "trade_flow" in silk_storage.load_mission_checkpoints(aid, db)
