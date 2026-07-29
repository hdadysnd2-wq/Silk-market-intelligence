"""بلاغ إنتاجي (Nadec/اليمن #7): `resume` كان يعامل بعثة ذات نقطة تفتيش
**فاشلة** كأنها منجزة فيتخطّاها — `to_run = [k ... if k not in reports]`
(silk_missions.py) لا ينظر إلى علم `failed` إطلاقاً، فتشغيلة resume:7 أعادت
صفر نداء وبقيت demand_trends فاشلة كما هي. القفل (test-first):

الاستئناف يعيد تلقائياً كل بعثة نقطتها فاشلة (بما فيها المنتهية مهلتها —
`_timed_out_report` يعلّم failed=True)، ويتخطّى الناجحة كما كان — لا إعادة
دفع لنداءات نجحت فعلاً.

Run: python3 -m pytest tests/test_resume_retries_failed_missions.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import block_network  # noqa: E402

from silk_agents import AgentReport  # noqa: E402


def _ref(name="Netherlands"):
    from silk_market_resolver import resolve_market
    ref, _ = resolve_market(name)
    return ref


def _stubbed(monkeypatch):
    """بديل LLMMissionAgent يسجّل ما أُعيد تشغيله — صفر كلود/شبكة."""
    import silk_missions as M
    ran: list[str] = []

    class _Stub:
        def __init__(self, spec):
            self._key = spec["key"]

        def run(self, task):
            ran.append(self._key)
            return AgentReport(f"LLMMissionAgent:{self._key}", [], False,
                               "أُعيد تشغيلها بعد فشل نقطة التفتيش")

    monkeypatch.setattr(M, "LLMMissionAgent", _Stub)
    return ran


def _resume_all_ok():
    import silk_missions as M
    return {k: AgentReport(f"m:{k}", [], False, "ناجحة")
            for k in M.MISSION_ORDER}


def test_resume_reruns_failed_checkpointed_mission(monkeypatch):
    import silk_missions as M
    ran = _stubbed(monkeypatch)
    resume = _resume_all_ok()
    resume["demand_trends"] = AgentReport(
        "m:demand_trends", [], True,
        "خطأ استجابة الخادم (HTTP 429): تجاوز حدّ المعدّل")
    with block_network():
        reports = M.run_all_missions(_ref(), product="حليب",
                                     hs_code="040120", resume_reports=resume)
    assert ran == ["demand_trends"]          # الفاشلة وحدها أُعيدت
    assert reports["demand_trends"].failed is False
    assert "أُعيد" in reports["demand_trends"].summary


def test_resume_skips_all_when_none_failed(monkeypatch):
    """لا إعادة دفع لبعثات نجحت — الاستئناف الناجح كاملاً يبقى صفر نداء."""
    import silk_missions as M
    ran = _stubbed(monkeypatch)
    with block_network():
        reports = M.run_all_missions(_ref(), product="تمور",
                                     hs_code="080410",
                                     resume_reports=_resume_all_ok())
    assert ran == []
    assert set(M.MISSION_ORDER) <= set(reports)


def test_resume_reruns_failed_opportunity_gaps(monkeypatch):
    """بعثة الخاتمة (opportunity_gaps) لها مسار تشغيل منفصل — فشلها يُعاد
    أيضاً، لا المسار الموازي وحده."""
    import silk_missions as M
    ran = _stubbed(monkeypatch)
    resume = _resume_all_ok()
    resume["opportunity_gaps"] = AgentReport(
        "m:opportunity_gaps", [], True, "تجاوز المهلة الزمنية (90s)")
    with block_network():
        reports = M.run_all_missions(_ref(), product="تمور",
                                     hs_code="080410", resume_reports=resume)
    assert ran == ["opportunity_gaps"]
    assert reports["opportunity_gaps"].failed is False


def test_timed_out_checkpoint_is_marked_failed_so_resume_retries_it():
    """عائلة المهلة تدخل نفس الباب: `_timed_out_report` يعلّم failed=True —
    فنقطة تفتيشها تُعاد على الاستئناف بالقانون الجديد نفسه."""
    import silk_missions as M
    rep = M._timed_out_report("demand_trends")
    assert rep.failed is True
