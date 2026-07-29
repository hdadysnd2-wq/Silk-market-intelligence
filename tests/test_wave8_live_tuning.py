"""اختبارات الموجة ٨ (V5) — أول جولة تنقيح ببيانات حية (تشغيلة هولندا/تمور).

يغطي: P0-1 (سياج JSON يُفقِد ردوداً صالحة كاملة)، P0-2 (لا رد نهائي بعد
استنفاد الميزانية — جولة إنهاء قسرية قبل إعلان الفجوة)، P0-3 (بنية تقرير
/research: التقرير السردي أولاً لا الهيكل الكلاسيكي غير المُغذّى، حكم واحد
لا تناقض، صفر JSON خام مُسرَّب)، P1-5 (هولندا في مرجع الديموغرافيا).
Run:  python3 -m pytest tests/test_wave8_live_tuning.py -q
"""
import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import docx_all_text  # noqa: E402


# ── P0-1: سياج JSON يُفقِد ردوداً صالحة كاملة ─────────────────────────────

def test_fenced_json_response_parses_and_keeps_grounded_findings():
    from silk_data_layer import DataPoint
    from silk_llm_runtime import _parse_output

    registry = {"dp1": DataPoint(9.96, "Albert Heijn", 0.6, "سعر رصد", "2026")}
    # نفس شكل رد التتبّع الحي: سياج ```json ... ``` + تعليق ختامي بعد
    # السياج يحوي قوساً معقوفاً — هذا التعليق كان يُفسِد rfind('}') القديم
    # عبر النص كله فيُسقط الرد بأكمله رغم أن المحتوى المسيَّج صالح تماماً.
    text = (
        "بناءً على البحث، إليك النتائج:\n\n"
        "```json\n"
        '{"findings": [{"claim": "سعر التمور في ألبرت هاين 9.96€/كغم",'
        ' "datapoint_ids": ["dp1"], "confidence": 0.6, "category": "price"}],'
        ' "gaps": [], "summary": "رُصد سعر تجزئة واحد"}\n'
        "```\n\n"
        "ملاحظة ختامية: هذا يعكس شريحة السوق {الفاخرة} تقريباً.")
    result = _parse_output(text, registry)
    assert len(result["findings"]) == 1
    assert "9.96" in result["findings"][0]["claim"]
    assert result["dropped"] == []
    assert result["summary"] == "رُصد سعر تجزئة واحد"


def test_unfenced_json_still_parses_backward_compatible():
    from silk_data_layer import DataPoint
    from silk_llm_runtime import _parse_output

    registry = {"dp1": DataPoint(5.0, "x", 0.5, "n", "2026")}
    text = '{"findings": [{"claim": "بند", "datapoint_ids": ["dp1"], ' \
           '"confidence": 0.5}], "gaps": [], "summary": "s"}'
    result = _parse_output(text, registry)
    assert len(result["findings"]) == 1


def test_multiple_fences_falls_through_to_the_one_that_parses():
    from silk_data_layer import DataPoint
    from silk_llm_runtime import _parse_output

    registry = {"dp1": DataPoint(1, "x", 0.5, "n", "2026")}
    text = (
        "مثال الصيغة المطلوبة:\n```json\n{not valid json here\n```\n\n"
        "الرد الفعلي:\n```json\n"
        '{"findings": [{"claim": "بند حقيقي", "datapoint_ids": ["dp1"], '
        '"confidence": 0.5}], "gaps": [], "summary": "ok"}\n```')
    result = _parse_output(text, registry)
    assert len(result["findings"]) == 1
    assert result["findings"][0]["claim"] == "بند حقيقي"


def test_genuinely_broken_json_still_declares_gap_not_crash():
    from silk_llm_runtime import _parse_output
    result = _parse_output("نص عربي عادي بلا أي JSON إطلاقاً", {})
    assert result["findings"] == []
    assert "غير قابل للتفسير" in result["gaps"][0]


def test_run_llm_agent_keeps_findings_from_a_fenced_response():
    import silk_llm_runtime as rt
    from silk_market_resolver import resolve_market

    def fake_call_tools(system, messages, tools=None, max_tokens=1600,
                        model=None, timeout=None):
        if tools:
            return {"stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": "t1", "name": "web_search",
                 "input": {"query": "تمور هولندا سعر"}}]}
        return {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": (
                "```json\n" + json.dumps({
                    "findings": [{"claim": "سعر 9.96 يورو/كغم",
                                 "datapoint_ids": ["dp1"], "confidence": 0.6}],
                    "gaps": [], "summary": "ok"}, ensure_ascii=False)
                + "\n```\nتعليق ختامي {إضافي}.")}]}

    mission = {"key": "pricing_scout", "name": "استكشاف الأسعار",
              "instructions": "test", "allowed_tools": ["web_search"]}
    ref, _ = resolve_market("Netherlands")
    with patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_llm_runtime._execute_tool", return_value=[
             __import__("silk_data_layer").DataPoint(
                 9.96, "web", 0.6, "n", "2026")]):
        rep = rt.run_llm_agent(mission, ref, product="تمور",
                               budget={"tool_calls": 2})
    assert not rep.failed
    assert len(rep.findings) == 1
    assert "9.96" in rep.findings[0].value


# ── P0-2: لا رد نهائي بعد استنفاد الميزانية — جولة إنهاء قسرية ────────────

def test_forced_finalization_turn_recovers_findings_after_budget_exhaustion():
    import silk_llm_runtime as rt
    from silk_market_resolver import resolve_market

    calls = {"n": 0}

    def fake_call_tools(system, messages, tools=None, max_tokens=1600,
                        model=None, timeout=None):
        calls["n"] += 1
        if tools:
            # الوكيل يواصل طلب أدوات ولا يتوقف من تلقاء نفسه (محاكاة
            # consumer_culture/customs_requirements: استنفاد بلا رد نهائي).
            return {"stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": f"t{calls['n']}",
                 "name": "web_search", "input": {"query": "q"}}]}
        # الجولة القسرية الأخيرة (offer_tools=None) — يجيب أخيراً.
        return {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": json.dumps(
                {"findings": [{"claim": "بند من الجولة القسرية",
                              "datapoint_ids": ["dp1"], "confidence": 0.5}],
                 "gaps": [], "summary": "أُنهي قسرياً"})}]}

    mission = {"key": "consumer_culture", "name": "ثقافة المستهلك",
              "instructions": "test", "allowed_tools": ["web_search"]}
    ref, _ = resolve_market("Netherlands")
    with patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_llm_runtime._execute_tool", return_value=[
             __import__("silk_data_layer").DataPoint(1, "web", 0.5, "n", "2026")]):
        rep = rt.run_llm_agent(mission, ref, product="تمور",
                               budget={"tool_calls": 2})
    # الميزانية استُنفدت بعد جولتي أدوات، ثم جولة إنهاء قسرية واحدة أنقذت
    # النتائج بدل إعلان "لا رد نهائي من كلود" فوراً.
    assert not rep.failed
    assert len(rep.findings) == 1
    assert "الجولة القسرية" in rep.findings[0].value


def test_forced_finalization_turn_declares_gap_if_it_also_fails():
    import silk_llm_runtime as rt
    from silk_market_resolver import resolve_market

    def fake_call_tools(system, messages, tools=None, max_tokens=1600,
                        model=None, timeout=None):
        if tools:
            return {"stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": "t1", "name": "web_search",
                 "input": {"query": "q"}}]}
        # حتى الجولة القسرية بلا رد نهائي مفيد — فجوة معلنة، لا اختلاق.
        return {"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "لم أتمكن من التوصل لإجابة نهائية."}]}

    mission = {"key": "customs_requirements", "name": "الاشتراطات",
              "instructions": "test", "allowed_tools": ["web_search"]}
    ref, _ = resolve_market("Netherlands")
    with patch("silk_llm_runtime._call_tools", side_effect=fake_call_tools), \
         patch("silk_llm_runtime._execute_tool", return_value=[
             __import__("silk_data_layer").DataPoint(1, "web", 0.5, "n", "2026")]):
        rep = rt.run_llm_agent(mission, ref, product="تمور",
                               budget={"tool_calls": 1})
    assert rep.failed
    assert "فجوات" in rep.summary or "لا نتائج" in rep.summary


# ── P0-3: بنية تقرير /research مقلوبة + حكمان متناقضان ────────────────────

def _netherlands_research_result(mission_failed=False):
    from silk_agents import AgentReport
    from silk_data_layer import DataPoint
    from silk_market_resolver import resolve_market

    ref, _ = resolve_market("Netherlands")
    analyst_report = AgentReport("LLMAgent:market_analyst",
                                 [DataPoint("طلب استدلالي", "x", 0.6, "n")],
                                 False, "تحليل")
    pricing_report = AgentReport(
        "LLMAgent:pricing_scout",
        [] if mission_failed else
        [DataPoint("سعر ألبرت هاين 9.96€/كغم", "web", 0.6, "n")],
        mission_failed, "فشلت" if mission_failed else "ok")
    return {
        "product": "تمور", "hs_code": "080410", "year": None,
        "market": {"iso3": ref.iso3, "m49": ref.m49, "iso2": ref.iso2,
                  "name_en": ref.name_en, "name_ar": ref.name_ar},
        "markets": [],
        "deep_research": {
            "missions": {"pricing_scout": pricing_report},
            "analyst": {"report": analyst_report,
                       "by_category": {"demand": analyst_report.findings,
                                      "entry_cost": [], "price_competitiveness": [],
                                      "entry_door": [], "swot": []},
                       "missing_categories": []},
            "verdict": {"verdict": "PRELIMINARY GO",
                       "ai": {"verdict": "WATCH — مراقبة قبل الدخول",
                             "confidence": 0.55,
                             "reasoning": "نمو الاستيراد قائم لكن الهامش ضيّق."}},
            "report": {"report": (
                "## 1. الخلاصة التنفيذية\n"
                "السوق يُظهر طلباً متنامياً على التمور مع منافسة سعرية "
                "حادة من الموزّعين الكبار (المصدر: Albert Heijn).\n"
                "## 13. الحكم والتوصية\n"
                "الحكم WATCH — مراقبة قبل الدخول مبني على هامش ضيّق "
                "وتنافسية سعرية عالية."),
                      "review_cycles": 1, "unresolved_notes": []},
        },
    }


def test_research_docx_leads_with_exec_summary_and_synthesis_verdict(
        monkeypatch):
    import pytest
    pytest.importorskip("docx")
    from docx import Document
    from silk_render import build_view
    from silk_reports import render_docx
    monkeypatch.setenv("SILK_HERMETIC", "1")
    view = build_view(_netherlands_research_result())
    path = render_docx(view, os.path.join(tempfile.mkdtemp(), "nld.docx"))
    doc = Document(path)
    heads = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")
            and p.style.name != "Heading 0" and p.text.strip()]
    toc_idx = heads.index("المحتويات")
    # أول عنوان بعد المحتويات هو "الخلاصة التنفيذية" — لا هيكل كلاسيكي
    # غير مُغذّى يسبقه (بلاغ حي: كان القسم ١-١٤ الفارغ يسبق كل شيء).
    assert heads[toc_idx + 1] == "١. الخلاصة التنفيذية"
    text = docx_all_text(path)
    exec_idx = text.find("١. الخلاصة التنفيذية")
    # PR A §A2: الحكم المعتمد «توصية أولية بالدخول» (أطول من «التوصية بالدخول»
    # السابقة) دفع سطر القراءة الاستشارية «مراقبة السوق» أبعد — نافذةٌ أوسع.
    exec_section = text[exec_idx:exec_idx + 600]
    # الحكم يصل مُعرَّباً بالكامل — لا رمز آلة WATCH خام على وجه العميل
    # (سدّ تسريب: نفس تصنيف الشارة عبر _VERDICT_LABELS_AR/_verdict_tone).
    assert "مراقبة السوق" in exec_section
    assert "WATCH" not in exec_section


def test_research_docx_has_zero_placeholder_strings_and_zero_code_fences(
        monkeypatch):
    import pytest
    pytest.importorskip("docx")
    from silk_render import build_view
    from silk_reports import render_docx
    monkeypatch.setenv("SILK_HERMETIC", "1")
    view = build_view(_netherlands_research_result())
    path = render_docx(view, os.path.join(tempfile.mkdtemp(), "nld2.docx"))
    text = docx_all_text(path)
    assert "with_research" not in text
    assert "```" not in text
    assert "0 أسواق" not in text
    assert "تعذّر إصدار توصية" not in text
    assert "التغطية 0.0%" not in text


def test_research_docx_verdict_is_the_same_everywhere_no_contradiction(
        monkeypatch):
    import pytest
    pytest.importorskip("docx")
    from silk_render import build_view
    from silk_reports import render_docx
    monkeypatch.setenv("SILK_HERMETIC", "1")
    view = build_view(_netherlands_research_result())
    path = render_docx(view, os.path.join(tempfile.mkdtemp(), "nld3.docx"))
    text = docx_all_text(path)
    # الحكم يظهر في الخلاصة وفي قسم البحث العميق — نفس النص المُعرَّب، لا
    # بديل "تعذّر الحكم"/"غير محسوم" ناتج عن محرك §8 غير مُغذّى، ولا رمز
    # آلة WATCH خام (سدّ تسريب).
    assert text.count("مراقبة السوق") >= 2
    assert "WATCH" not in text
    assert "تعذّر الحكم" not in text
    assert "غير محسوم" not in text


def test_clean_report_text_replaces_raw_json_blob_not_prose():
    from silk_reports import _clean_report_text
    assert _clean_report_text('{"findings": [1,2,3]}') == (
        "بند تقني غير قابل للعرض المباشر — التفاصيل الكاملة في أثر التتبّع.")
    assert _clean_report_text("```json\n{...}\n```") == (
        "بند تقني غير قابل للعرض المباشر — التفاصيل الكاملة في أثر التتبّع.")
    assert _clean_report_text("نص عربي طبيعي بلا أي رمز JSON") == \
        "نص عربي طبيعي بلا أي رمز JSON"


def test_research_docx_omits_classic_sections_entirely_when_missions_failed(
        monkeypatch):
    # حتى حين تفشل كل البعثات، لا تظهر الأقسام الكلاسيكية الفارغة (١-١٤) —
    # فقط الخلاصة (من حكم التوليف المرحلة ١) + قسم البحث العميق + حدوده.
    import pytest
    pytest.importorskip("docx")
    from silk_render import build_view
    from silk_reports import render_docx
    monkeypatch.setenv("SILK_HERMETIC", "1")
    view = build_view(_netherlands_research_result(mission_failed=True))
    path = render_docx(view, os.path.join(tempfile.mkdtemp(), "nld4.docx"))
    text = docx_all_text(path)
    assert "منهجية البحث" not in text
    assert "تحليل التجارة (استيراد/تصدير)" not in text
    assert "حزمة وكلاء البحث غير مفعّلة" not in text


# ── P1-4: تشخيص فشل مصادر Railway (WGI/GDELT) ─────────────────────────────

def test_world_bank_falls_back_to_latest_year_when_requested_year_missing():
    # الثغرة الفعلية المؤكَّدة (مراجعة كود، بلا شبكة حية هنا): WGI/LPI
    # تُنشَر بفارق سنة أو أكثر — طلب سنة محددة غير منشورة بعد كان يعيد
    # None صامتاً رغم توفر بيانات فعلية لسنوات أقرب.
    import silk_data_layer as dl
    from silk_data_layer import DataPoint

    exact = DataPoint(None, "World Bank", 0.0, "PV.EST: no value returned for NLD", "2026")
    latest = DataPoint(0.9, "World Bank", 0.95, "PV.EST year=2022", "2026")

    calls = []

    def fake(iso3, indicator, year):
        calls.append(year)
        return exact if year == 2025 else latest

    with patch.object(dl, "_world_bank_for_year", side_effect=fake):
        dp = dl.world_bank("NLD", "PV.EST", 2025)
    assert dp.value == 0.9
    assert calls == [2025, None]  # محاولة السنة المطلوبة أولاً، ثم التراجُع
    assert "2025" in dp.note and "لم تُنشر" in dp.note
    assert "year=2022" in dp.note  # الملاحظة الأصلية للسنة الفعلية محفوظة


def test_world_bank_no_fallback_call_when_exact_year_succeeds():
    import silk_data_layer as dl
    from silk_data_layer import DataPoint

    ok = DataPoint(1.2, "World Bank", 0.95, "RL.EST year=2023", "2026")
    calls = []

    def fake(iso3, indicator, year):
        calls.append(year)
        return ok

    with patch.object(dl, "_world_bank_for_year", side_effect=fake):
        dp = dl.world_bank("NLD", "RL.EST", 2023)
    assert dp.value == 1.2
    assert calls == [2023]  # لا محاولة تراجُع زائدة حين تنجح السنة المطلوبة


def test_world_bank_no_year_requested_makes_a_single_call():
    import silk_data_layer as dl
    from silk_data_layer import DataPoint

    calls = []

    def fake(iso3, indicator, year):
        calls.append(year)
        return DataPoint(5.0, "World Bank", 0.95, "n", "2026")

    with patch.object(dl, "_world_bank_for_year", side_effect=fake):
        dl.world_bank("NLD", "SP.POP.TOTL", None)
    assert calls == [None]


def test_world_bank_both_exact_and_fallback_empty_stays_a_declared_gap():
    import silk_data_layer as dl
    from silk_data_layer import DataPoint

    empty = DataPoint(None, "World Bank", 0.0, "no value returned for XYZ", "2026")
    with patch.object(dl, "_world_bank_for_year", side_effect=lambda *a: empty):
        dp = dl.world_bank("XYZ", "PV.EST", 2025)
    assert dp.value is None
    assert dp.confidence == 0.0


def test_gdelt_sends_a_browser_like_user_agent():
    import silk_gdelt_agent as gd

    captured = {}

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def raise_for_status(self):
            pass
        def json(self):
            return {"articles": [{"title": "خبر", "url": "u", "seendate": "d",
                                  "domain": "x.com"}]}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["headers"] = headers
        return _Resp()

    with patch("requests.get", side_effect=fake_get):
        out = gd.gdelt_news("تمور", "Netherlands")
    assert captured["headers"].get("User-Agent")
    assert "python-requests" not in captured["headers"]["User-Agent"]
    assert out[0].value["title"] == "خبر"


def test_gdelt_non_json_body_gets_a_distinct_diagnosable_note():
    import silk_gdelt_agent as gd

    class _Resp:
        status_code = 200
        headers = {"content-type": "text/html"}
        def raise_for_status(self):
            pass
        def json(self):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    with patch("requests.get", return_value=_Resp()):
        out = gd.gdelt_news("تمور", "Netherlands")
    assert out[0].value is None
    assert "non-JSON" in out[0].note
    assert "text/html" in out[0].note


# ── P1-5: مرجع L1 يبدأ بسطر تعليق '#' كان يفسد كل الصفوف ─────────────────

def test_load_csv_skips_leading_comment_lines(tmp_path):
    from silk_llm_runtime import _load_csv
    p = tmp_path / "ref.csv"
    p.write_text(
        "# مرجع تجريبي — توثيق مصدر\n"
        "# سطر تعليق ثانٍ\n"
        "iso3,value\nNLD,7\nSAU,1\n", encoding="utf-8")
    rows = _load_csv(str(p))
    assert len(rows) == 2
    assert rows[0]["iso3"] == "NLD"
    assert rows[0]["value"] == "7"


def test_load_csv_works_with_no_comment_lines_too(tmp_path):
    from silk_llm_runtime import _load_csv
    p = tmp_path / "ref2.csv"
    p.write_text("iso3,value\nNLD,7\n", encoding="utf-8")
    rows = _load_csv(str(p))
    assert len(rows) == 1
    assert rows[0]["iso3"] == "NLD"


def test_netherlands_demographics_lookup_returns_the_real_row():
    # إعادة إنتاج حية للثغرة المُبلَّغة بالضبط: هولندا موجودة فعلاً في
    # demographics_l1.csv (نسبة مسلمين ٧٪، Pew Research) — كانت تُفقَد
    # بالكامل بسبب سطري التعليق في مقدّمة الملف، لا لغياب البيانات.
    from silk_llm_runtime import _tool_lookup_reference
    from silk_market_resolver import resolve_market

    ref, _ = resolve_market("Netherlands")
    ctx = {"market": ref, "product": "تمور", "hs_code": "080410",
          "extra_findings": [], "extra_context": ""}
    dps = _tool_lookup_reference({"table": "demographics"}, ctx)
    assert len(dps) == 1
    assert dps[0].value is not None
    assert dps[0].value["iso3"] == "NLD"
    assert dps[0].value["muslim_pct"] == "7"
    assert "Pew" in dps[0].value["muslim_pct_source"]


def test_agreements_lookup_also_recovers_after_comment_line_fix():
    # agreements_l1.csv يبدأ بسطر تعليق واحد — نفس فئة الثغرة.
    from silk_llm_runtime import _tool_lookup_reference
    from silk_market_resolver import resolve_market

    ref, _ = resolve_market("Netherlands")
    ctx = {"market": ref, "product": "تمور", "hs_code": "080410",
          "extra_findings": [], "extra_context": ""}
    dps = _tool_lookup_reference({"table": "agreements"}, ctx)
    assert dps and dps[0].value is not None
    assert dps[0].value["iso3"] == "NLD"
