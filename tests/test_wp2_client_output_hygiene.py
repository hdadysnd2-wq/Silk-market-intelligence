"""WP-2 (برنامج إصلاح جودة التقارير) — لا مخرَج داخلي خام يصل العميل أبداً.

بلاغ التدقيق (تقريرا الكويت المُسلَّمان 2026-07-22): «بند تقني غير قابل
للعرض المباشر — التفاصيل في أثر التتبع» ظهر في قسم القرار؛ عشر نقاط
«إذن ماذا؟ يجب…» مبتورة بـ«...»؛ وقسم «المخاطر» خرج خاوياً بفقرة اعتذار.
الأقفال:

1. `_client_prose`: كتلة خام تُستخلَص أو تُسقَط — لا نصّ نائب في متن العميل.
2. سقالة «إذن ماذا»/"So what" ممنوعة: تعليمة المحلل أُعيدت صياغتها،
   المُنظِّف الحتمي ينزعها، والبوابة تُفشِل أي بقايا.
3. قسم عميل بلا سرد كاتب يمرّ فقط بنثر الصياغة التجارية المُحضَّر
   (`rephrase_client_sections`) — لا سرد نقاط `dp.value` خام.
4. «المخاطر» تقرأ من بعثة المخاطر (risk_news) + بنود SWOT — لا () فارغة.
5. مُوجز المحلل يقصّ عند حدّ جملة لا `[:320]` الحرفي.

Run: python3 -m pytest tests/test_wp2_client_output_hygiene.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── ١) لا نصّ نائب في متن العميل ────────────────────────────────────────────

def test_client_prose_never_returns_the_technical_placeholder():
    from silk_reports import _client_prose, _UNRENDERABLE_NOTE
    out = _client_prose('{"claim": "الطلب مرتفع في السوق"}')
    assert _UNRENDERABLE_NOTE not in out
    assert "أثر التتبع" not in out
    # الاستخلاص نجح — المضمون العربي وصل.
    assert "الطلب مرتفع" in out


def test_client_prose_drops_unextractable_json_instead_of_placeholder():
    from silk_reports import _client_prose
    assert _client_prose('{"x": 1, "y": [') == ""
    # سياج حول نثرٍ فعلي: يُنزَع السياج ويبقى المضمون (ليس نائباً).
    assert _client_prose("```\nنص مضمون فعلي\n```") == "نص مضمون فعلي"


def test_internal_trim_sentence_keeps_placeholder_for_internal_paths():
    """المسارات الداخلية (?internal=1) تُبقي النائب — العقد لم يُعمَّم عليها."""
    from silk_reports import _trim_sentence, _UNRENDERABLE_NOTE
    assert _UNRENDERABLE_NOTE in _trim_sentence('{"raw": 1}')


# ── ٢) سقالة «إذن ماذا» ─────────────────────────────────────────────────────

def test_analyst_instruction_bans_literal_so_what():
    src = open(os.path.join(_ROOT, "silk_market_analyst.py"),
               encoding="utf-8").read()
    assert "ممنوع منعاً باتاً" in src
    assert "«إذن ماذا»" in src
    # الصيغة القديمة الموجِبة للسقالة أُزيلت.
    assert "'إذن ماذا؟' للمصدّر السعودي" not in src


def test_sanitizer_strips_so_what_scaffold_keeping_the_impact_prose():
    from silk_render import _strip_internal_plumbing
    out = _strip_internal_plumbing(
        "إذن ماذا؟ يجب التركيز على قناة التجزئة الحديثة أولاً.")
    assert "إذن ماذا" not in out
    assert "يجب التركيز على قناة التجزئة الحديثة" in out
    out2 = _strip_internal_plumbing("الخلاصة: So what? ركّز على الحلال.")
    assert "So what" not in out2
    assert "ركّز على الحلال" in out2


def test_gate_fails_on_literal_so_what_in_client_text():
    import silk_quality_gate as G
    f = G._check_client_scaffold_leak("نقطة أولى. إذن ماذا؟ يجب كذا.")
    assert f and f[0]["check"] == "client_scaffold_leak"
    assert f[0]["repairable"] is False
    assert G._check_client_scaffold_leak("ماذا يعني هذا لقرارك: ابدأ.") == []


# ── ٣) البوابة تُفشِل النصوص النائبة والبتر ─────────────────────────────────

def test_gate_fails_on_placeholder_strings():
    import silk_quality_gate as G
    for ph in ("بند تقني غير قابل للعرض المباشر — التفاصيل في أثر التتبع.",
               "التحليل السردي التفصيلي لهذا القسم غير متاح ضمن هذا التقرير؛"):
        f = G._check_placeholder_leak(ph)
        assert f and f[0]["check"] == "placeholder_leak", ph
        assert f[0]["repairable"] is False


def test_trailing_ellipsis_is_fail_class_except_quotations():
    import silk_quality_gate as G
    f = G._check_trailing_ellipsis("فقرة تحليلية مبتورة تنتهي هكذا...")
    assert f and f[0]["repairable"] is False
    # كتلة اقتباس حرفي تُستثنى.
    assert G._check_trailing_ellipsis("> اقتباس من مصدر ينتهي...") == []
    # PR A: فئاتُ FAIL ثابتٌ واحد (`FAIL_TRIGGER_CHECKS`) بدل صفٍّ حرفيّ
    # داخل `run_quality_gate` — نفس النيّة، تحقّقٌ بنيويّ لا تحليلُ نصٍّ هشّ.
    for check in ("client_scaffold_leak", "placeholder_leak",
                  "trailing_ellipsis"):
        assert check in G.FAIL_TRIGGER_CHECKS, check


# ── ٤) قسم عميل بلا سرد = نثر مُحضَّر أو FAIL (يشمل «المخاطر») ──────────────

def _dr_without_risk_narrative(prose: "dict | None" = None) -> dict:
    dr = {
        "report": {"text": (
            "## 1. الخلاصة التنفيذية\nنص الخلاصة.\n\n"
            "## 3. نظرة عامة على السوق وحجمه\nحجم السوق 9 مليون دولار.\n\n"
            "## 6. المشهد التنافسي\nثلاثة مورّدين رئيسيون.\n\n"
            "## 10. التوصيات الاستراتيجية\nتعليل الحكم.\n\n"
            "### خارطة طريق الدخول (٩٠ يوماً)\nالخطوة الأولى.\n")},
        "analyst": {"by_category": {
            "swot": [{"value": "تهديد: تقلّب أسعار الشحن يضغط الهامش.",
                      "confidence": 0.7}]}},
        "missions": {"risk_news": {
            "failed": False, "summary": "مخاطر مرصودة",
            "findings": [{"value": "استقرار سياسي مرتفع وفق WGI.",
                          "confidence": 0.8}]}},
    }
    if prose:
        dr["client_fallback_prose"] = prose
    return dr


def test_gate_fails_risk_section_without_prepared_prose():
    import silk_quality_gate as G
    findings = G._check_client_section_would_be_placeholder(
        _dr_without_risk_narrative())
    assert any("المخاطر" in f["note"] for f in findings)


def test_gate_passes_section_with_prepared_commercial_prose():
    import silk_quality_gate as G
    findings = G._check_client_section_would_be_placeholder(
        _dr_without_risk_narrative(
            {"المخاطر": "المخاطر الكلية محدودة، وأبرز ما يقيّد الهامش "
                        "تقلّب أسعار الشحن."}))
    assert not any("المخاطر" in f["note"] for f in findings)


def test_risk_fallback_sources_read_risk_mission_and_swot_not_empty_tuple():
    from silk_reports import _client_fallback_sources
    items = _client_fallback_sources(_dr_without_risk_narrative(), "المخاطر")
    joined = " ".join(items)
    assert "تقلّب أسعار الشحن" in joined      # SWOT
    assert "استقرار سياسي" in joined           # بعثة المخاطر


def test_client_docx_renders_prepared_prose_never_raw_bullets(tmp_path):
    pytest.importorskip("docx")
    from docx import Document
    from silk_render import build_view
    import silk_reports as R
    from tools.canonical_kuwait_peanut_butter import kuwait_research_blob
    blob = kuwait_research_blob()
    # احذف سرد الكاتب لقسم المخاطر (٩) من المدوّنة — نحاكي فشل ذلك القسم —
    # وجهّز نثر الصياغة التجارية بدله.
    _txt = blob["deep_research"]["report"]["report"]
    _head, _sep, _tail = _txt.partition("## 9.")
    _rest = _tail.partition("## 10.")
    blob["deep_research"]["report"]["report"] = (
        _head + ("## 10." + _rest[2] if _rest[1] else ""))
    os.environ["SILK_HERMETIC"] = "1"
    view = build_view(blob)
    view["test_run"] = True
    dr = view["deep_research"]
    dr["client_fallback_prose"] = {
        "المخاطر": "أبرز المخاطر المرصودة تتصل بتذبذب التوريد الإقليمي."}
    out = str(tmp_path / "client.docx")
    R.render_client_docx(view, out)
    text = "\n".join(p.text for p in Document(out).paragraphs)
    assert "أبرز المخاطر المرصودة" in text
    assert "بند تقني غير قابل للعرض المباشر" not in text
    assert "إذن ماذا" not in text


# ── ٥) نداء الصياغة التجارية المصغّر ────────────────────────────────────────

def test_rephrase_client_sections_fills_heads_via_single_calls(monkeypatch):
    import silk_ai_judge as J
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls = []

    def fake_call(system, user, max_tokens=1600, model=None, timeout=None):
        calls.append(user)
        return "صياغة تجارية سليمة للقسم."

    monkeypatch.setattr(J, "_call", fake_call)
    out = J.rephrase_client_sections(_dr_without_risk_narrative())
    assert out.get("المخاطر") == "صياغة تجارية سليمة للقسم."
    assert calls and "إذن ماذا" in calls[0]   # التعليمة تحظر السقالة صراحة


def test_rephrase_failure_returns_nothing_so_gate_blocks(monkeypatch):
    import silk_ai_judge as J
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(J, "_call", lambda *a, **k: None)
    assert J.rephrase_client_sections(_dr_without_risk_narrative()) == {}
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert J.rephrase_client_sections(_dr_without_risk_narrative()) == {}


# ── ٦) مُوجز المحلل: قصّ عند حدّ جملة لا [:320] ─────────────────────────────

def test_comprehensive_digest_trims_at_sentence_boundary_not_charcount():
    from silk_market_analyst import _comprehensive_digest
    from silk_data_layer import DataPoint
    long_val = ("جملة أولى قصيرة. " * 10 +
                "جملة أخيرة طويلة جداً ستُقصّ قبل اكتمالها بكثير من الكلمات")
    digest = _comprehensive_digest(
        {"demand": [DataPoint(long_val, "UN Comtrade", 0.9, "n")]})
    assert "…" not in digest
    src = open(os.path.join(_ROOT, "silk_market_analyst.py"),
               encoding="utf-8").read()
    assert "str(dp.value)[:320]" not in src


def test_comprehensive_digest_drops_raw_json_values():
    from silk_market_analyst import _comprehensive_digest
    from silk_data_layer import DataPoint
    digest = _comprehensive_digest(
        {"demand": [DataPoint('{"broken": [', "UN Comtrade", 0.9, "n"),
                    DataPoint("الطلب موثّق ومتنامٍ.", "UN Comtrade", 0.9, "n")]})
    assert "بند تقني" not in digest
    assert "الطلب موثّق" in digest
