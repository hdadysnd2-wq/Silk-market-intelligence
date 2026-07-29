"""بلاغ إنتاجي (Nadec/اليمن #7، الجولة ٢): بعد فكس #180 (run_all_missions
يعيد البعثات الفاشلة) بقي resume:7 بلا أثر — updated_at ثابت، صفر نداء،
التكلفة كما هي. السبب الجذري ليس نوع نقطة التفتيش (تفكيكها يعيد AgentReport
حيّاً لا dict — يُثبَت أدناه) بل **قِصَر مسار `status=="completed"`** في
مُعالِج /research (api.py): التشغيلة تُوسَم "completed" حتى لو فشلت بعثة،
فكان المسار يُعيد النتيجة المخزَّنة ولا يبلغ run_all_missions إطلاقاً.

القفل الأساسي (الذي غاب في #180): بُنِيت نقطة التفتيش **كما يفكّكها الإنتاج**
(save_mission_checkpoint → load_mission_checkpoints عبر SQLite حقيقي)، لا
بكائن مُنشأ يدوياً — فيُكشَف أي انحراف بين ما يظنّه الحارس وما يصله فعلاً.

Run: python3 -m pytest tests/test_resume_completed_run_with_failed_mission.py -q
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import block_network  # noqa: E402

from silk_agents import AgentReport  # noqa: E402


def _failed_demand_trends() -> AgentReport:
    return AgentReport(
        "LLMMissionAgent:demand_trends", [], True,
        "demand_trends: خطأ استجابة الخادم (HTTP 429): تجاوز حدّ المعدّل")


# ── ١) القفل الأساسي: التفكيك الإنتاجي يعيد AgentReport بعلم failed حيّ ─────

def test_failed_checkpoint_round_trips_as_agentreport_not_dict():
    """save→load عبر SQLite حقيقي (نفس مسار الإنتاج) يعيد AgentReport حيّاً
    (لا dict)، وعلم failed=True ينجو من الدورة كاملةً — فرضية «يفكَّك dict
    فيبتلع getattr الفشل صامتاً» مدحوضة على هذا المسار."""
    import silk_storage as S
    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    S.save_mission_checkpoint(1, "demand_trends", _failed_demand_trends(),
                              path=db, market_iso3="YEM")
    loaded = S.load_mission_checkpoints(1, path=db, market_iso3="YEM")
    r = loaded["demand_trends"]
    assert isinstance(r, AgentReport)            # ليس dict
    assert r.failed is True                       # الفشل نجا من الدورة
    # وحالة العمود المخزَّن نفسها "failed" (قناة الكشف الرخيصة للحارس)
    assert S.mission_status_map(1, path=db)["demand_trends"] == "failed"


def test_run_all_missions_reruns_the_real_deserialized_failed_checkpoint(
        monkeypatch):
    """التكامل الحقيقي: نقطة تفتيش فاشلة **مُفكَّكة من التخزين** (لا مُنشأة
    يدوياً) تُعاد على الاستئناف — القفل الذي كان سيلتقط لو أن التفكيك أعاد
    dict فتعطّل الحارس."""
    import silk_storage as S
    import silk_missions as M
    from silk_market_resolver import resolve_market
    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    # خزِّن ١١ بعثة ناجحة + demand_trends فاشلة (كلها عبر مسار الإنتاج)
    for k in M.MISSION_ORDER:
        rep = (_failed_demand_trends() if k == "demand_trends"
               else AgentReport(f"LLMMissionAgent:{k}", [], False, "ok"))
        S.save_mission_checkpoint(7, k, rep, path=db, market_iso3="YEM")
    resume_reports = S.load_mission_checkpoints(7, path=db, market_iso3="YEM")

    ran: list[str] = []

    class _Stub:
        def __init__(self, spec):
            self._key = spec["key"]

        def run(self, task):
            ran.append(self._key)
            return AgentReport(f"LLMMissionAgent:{self._key}", [], False,
                               "أُعيد تشغيلها")

    monkeypatch.setattr(M, "LLMMissionAgent", _Stub)
    ref, _ = resolve_market("Yemen")
    with block_network():
        reports = M.run_all_missions(ref, product="حليب", hs_code="040120",
                                     resume_reports=resume_reports)
    assert ran == ["demand_trends"]              # الفاشلة وحدها أُعيدت
    assert reports["demand_trends"].failed is False


# ── ٢) القفل الجذري: مسار «completed» لا يعيد التسليم الصامت مع فشل بعثة ────

def _seed_completed_run_with_failed_mission(db: str) -> int:
    """تشغيلة موسومة completed تحمل بعثة فاشلة — الحالة الإنتاجية لتحليل ٧."""
    import silk_storage as S
    aid = S.create_research_run(
        "حليب", "YEM", "040120",
        {"product": "حليب", "market": "Yemen", "market_iso3": "YEM",
         "hs_code": "040120"},
        path=db, market_name="Yemen")
    import silk_missions as M
    for k in M.MISSION_ORDER:
        rep = (_failed_demand_trends() if k == "demand_trends"
               else AgentReport(f"LLMMissionAgent:{k}", [], False, "ok"))
        S.save_mission_checkpoint(aid, k, rep, path=db, market_iso3="YEM")
    # نتيجة مخزَّنة كي يجد مسارُ «أعِدها كما هي» شيئاً لو نُفِّذ (فالإخفاق
    # يعني «أُعيد التسليم» لا «لا نتيجة»).
    S.update_research_status(aid, "completed", path=db)
    return aid


def test_completed_run_with_failed_mission_is_not_pure_replayed(monkeypatch):
    """POST /research {resume} على تشغيلة completed بها بعثة فاشلة يجب أن
    يتخطّى «أعِدها كما هي» ويبلغ `deep_research` (مسار إعادة التشغيل) — لا
    تسليم صامت (بلاغ #7). نعزل قرار البوّابة عند أنظف حدّ: `deep_research`
    يُستدعى مباشرةً بعد الحارس، فبلوغه = لم يُقصَر المسار (بلا تمرير كامل
    الذيل الهشّ)؛ يُمرَّر `resume_reports` المحمَّلة فعلاً كي نتحقّق أنها
    تحمل demand_trends الفاشلة."""
    import pytest
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import silk_missions

    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    seen: dict = {}

    def _recording_deep_research(*a, **k):
        seen["called"] = True
        seen["resume_reports"] = k.get("resume_reports")
        raise RuntimeError("sentinel — أوقف الذيل بعد إثبات بلوغ إعادة التشغيل")

    with patch("silk_storage._db_path", return_value=db), \
         patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test",
                                 "SILK_API_KEY": "secret"}), \
         patch("silk_missions.deep_research",
               side_effect=_recording_deep_research):
        aid = _seed_completed_run_with_failed_mission(db)
        import api
        client = TestClient(api.app)
        client.post("/research", headers={"X-API-Key": "secret"},
                    json={"resume": aid})
        # الحارس الجذري: المسار بلغ `deep_research` (لم يعد النتيجة المخزَّنة
        # بصمت) — والبعثة الفاشلة حاضرة في نقاط التفتيش المحمَّلة للاستئناف.
        assert seen.get("called") is True
        rr = seen.get("resume_reports") or {}
        assert silk_missions._report_is_failed(rr.get("demand_trends"))


def test_completed_run_all_ok_still_pure_replays(monkeypatch):
    """تحفّظ idempotency: تشغيلة completed بلا أي بعثة فاشلة تبقى إعادة
    تسليم صرفة — لا إعادة تشغيل، لا حرق اعتمادات على نقرة استئناف عابرة."""
    import pytest
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import silk_storage as S
    import silk_missions as M

    db = os.path.join(tempfile.mkdtemp(), "silk.db")
    seen: dict = {}

    def _recording_deep_research(*a, **k):
        seen["called"] = True
        raise RuntimeError("يجب ألّا يُبلَغ — إعادة تسليم صرفة متوقَّعة")

    with patch("silk_storage._db_path", return_value=db), \
         patch.dict(os.environ, {"SILK_API_KEY": "secret"}), \
         patch("silk_missions.deep_research",
               side_effect=_recording_deep_research):
        aid = S.create_research_run(
            "تمر", "NLD", "080410",
            {"product": "تمر", "market": "Netherlands", "market_iso3": "NLD",
             "hs_code": "080410"}, path=db, market_name="Netherlands")
        for k in M.MISSION_ORDER:
            S.save_mission_checkpoint(
                aid, k, AgentReport(f"m:{k}", [], False, "ok"),
                path=db, market_iso3="NLD")
        S.save_analysis({"product": "تمر", "markets": [],
                         "deep_research": {"missions": {}}},
                        analysis_id=aid, path=db)
        S.update_research_status(aid, "completed", path=db)
        import api
        client = TestClient(api.app)
        r = client.post("/research", headers={"X-API-Key": "secret"},
                        json={"resume": aid})
        assert r.status_code == 200
        # إعادة تسليم صرفة: `deep_research` لم يُبلَغ إطلاقاً (لا إعادة تشغيل).
        assert seen.get("called") is None


# ── ٣) الحارس الدفاعي: علم الفشل يُقرأ من dict أيضاً (لا getattr أعمى) ──────

def test_report_failed_helper_handles_dict_and_object():
    """`_report_is_failed` يقرأ الفشل من dict مُفكَّك **و**من AgentReport —
    نقطة قلق المالك: getattr(dict, "failed", False) يعيد False صامتاً."""
    from silk_missions import _report_is_failed
    assert _report_is_failed({"failed": True}) is True
    assert _report_is_failed({"failed": False}) is False
    assert _report_is_failed(AgentReport("x", [], True, "s")) is True
    assert _report_is_failed(AgentReport("x", [], False, "s")) is False
    assert _report_is_failed({}) is False          # لا مفتاح = غير فاشل
