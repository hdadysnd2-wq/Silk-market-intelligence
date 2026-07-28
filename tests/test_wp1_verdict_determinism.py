"""WP-1 (برنامج إصلاح جودة التقارير) — حتمية الحكم ومصدره الواحد.

بلاغ التدقيق (تقريرا الكويت/زبدة الفول السوداني المُسلَّمان 2026-07-22):
تشغيلتان بنفس المدخلات في نفس اليوم أنتجتا «مراقبة» ثم «دخول»؛ شارة الغلاف
قالت GO بينما سرد المتن قال WATCH؛ و«ثقة 68%» كانت تقريراً ذاتياً غير معاير
من النموذج. الأقفال هنا:

1. الحكم المعروض يُشتقّ من الحقل الحتمي حصراً (`authoritative_verdict`) —
   قراءة كلود (ai.verdict) استشارية داخلية لا توصية.
2. temperature مثبّتة صفراً على نداءات `complete` (توليف/كاتب/مراجع)،
   وحلقة الأدوات تبقى على افتراضها (لا تحدّد الحكم مباشرة).
3. سُلَّم معايرة الثقة الواحد في `silk_style_contract` يستهلكه العارض
   والحارس معاً، وعدم تطابق التسمية مع الرقم = FAIL لا تحذير.
4. ثلاث عمليات عرض متتالية لنفس المدوّنة القانونية = مخرجات متطابقة بايتاً.

Run: python3 -m pytest tests/test_wp1_verdict_determinism.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tools.canonical_kuwait_peanut_butter import kuwait_research_blob  # noqa: E402


# ── ١) المصدر الواحد للحكم: الحتمي أولاً ─────────────────────────────────────

def test_authoritative_verdict_prefers_deterministic_over_ai():
    from silk_narrative import authoritative_verdict
    raw, conf = authoritative_verdict(
        {"verdict": "WATCH", "confidence": 0.5,
         "ai": {"verdict": "GO", "confidence": 0.9}})
    assert raw == "WATCH"
    assert conf == 0.5


def test_authoritative_verdict_falls_back_to_ai_only_when_deterministic_absent():
    from silk_narrative import authoritative_verdict
    raw, conf = authoritative_verdict(
        {"ai": {"verdict": "CONDITIONAL-GO", "confidence": 0.6}})
    assert raw == "CONDITIONAL-GO"
    assert conf == 0.6
    assert authoritative_verdict(None) == ("", None)


def test_resolve_vtxt_ignores_conflicting_ai_verdict():
    """شارة الغلاف وصفّ الجدول وسطر «التوصية:» كلها تُشتقّ من `_resolve_vtxt`
    — التي يجب أن تتجاهل حكم كلود المخالف."""
    from silk_reports import _resolve_vtxt
    dr = {"verdict": {"verdict": "WATCH", "confidence": 0.5,
                      "ai": {"verdict": "GO", "confidence": 0.9}}}
    assert _resolve_vtxt(dr) == "WATCH"


def test_view_verdict_tone_derives_from_deterministic_field():
    from silk_render import build_view
    blob = kuwait_research_blob()
    blob["deep_research"]["verdict"] = {
        "verdict": "WATCH", "confidence": 0.5,
        "ai": {"verdict": "GO", "confidence": 0.9, "reasoning": "ادخل فوراً."}}
    os.environ["SILK_HERMETIC"] = "1"
    view = build_view(blob)
    dr = view["deep_research"]
    assert dr["verdict_tone"] == "watch"
    assert dr["verdict_label"] == "مراقبة السوق"


def test_writer_receives_the_deterministic_verdict_not_ai():
    """`_summarize_verdict` (المحقون في برومبت الكاتب قيداً صلباً) يحمل الحكم
    الحتمي لا قراءة كلود المخالفة — فلا يعيد الكاتب إصدار توصية موازية."""
    from silk_ai_judge import _summarize_verdict
    out = _summarize_verdict(
        {"verdict": "WATCH", "confidence": 0.5,
         "ai": {"verdict": "GO", "confidence": 0.9}})
    assert "مراقبة السوق" in out
    assert "50%" in out


def test_writer_prompt_carries_hard_verdict_constraint():
    """القيد الصلب في برومبت الكاتب: «الحكم المعتمد» + منع أي توصية مختلفة
    + منع اختراع نسبة/تسمية ثقة غير المرفقة."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "silk_ai_judge.py"), encoding="utf-8").read()
    assert "الحكم المعتمد" in src
    assert "يُمنَع إصدار أي توصية مختلفة" in src
    assert "لا \nتخترع نسبة ثقة" in src or "تخترع نسبة ثقة" in src


# ── ٢) temperature مثبّتة صفراً على نداءات complete ─────────────────────────

class _FakeResp:
    def __init__(self, payload_store):
        self._store = payload_store

    def raise_for_status(self):
        return None

    def json(self):
        return {"content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn", "usage": {}}


def _capture_complete(monkeypatch, model, *, vision=False):
    """نفّذ نداءً واحداً والتقط الحمولة المرسَلة فعلاً — أداة الاختبارات أدناه."""
    import requests
    from silk_llm_provider import AnthropicProvider
    captured = {}

    def fake_post(url, timeout=None, headers=None, json=None):
        captured.update(json or {})
        return _FakeResp(captured)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    if vision:
        out = AnthropicProvider().complete_vision(
            "s", "t", "YWJj", "image/png", 100, model, 30.0)
    else:
        out = AnthropicProvider().complete("s", "u", 100, model, 30.0)
    assert out == "ok"
    return captured


def test_complete_pins_temperature_zero(monkeypatch):
    captured = _capture_complete(monkeypatch, "claude-x")
    assert captured.get("temperature") == 0
    assert "top_p" not in captured   # لا top_p مع temperature (توصية Anthropic)


def test_complete_pins_temperature_zero_on_legacy_model(monkeypatch):
    """نموذج يقبل المعامل (جيل 4) — الحتمية تبقى مثبّتة، الصفّ ٤٧ سليم."""
    captured = _capture_complete(monkeypatch, "claude-opus-4-8")
    assert captured.get("temperature") == 0


def test_complete_omits_temperature_on_deprecating_model(monkeypatch):
    """نموذج أزال المعامل (جيل ٥) — يُهمَل تماماً، لا يُرسَل صفراً ولا غيره:
    إرساله يردّ 400 فيصير النداء كلّه فشلاً (تقرير بلا سرد)."""
    captured = _capture_complete(monkeypatch, "claude-opus-5")
    assert "temperature" not in captured
    # الحمولة الباقية سليمة — الحذف لا يمسّ شيئاً آخر
    assert captured["model"] == "claude-opus-5"
    assert captured["max_tokens"] == 100


def test_complete_omits_temperature_for_every_default_prefix(monkeypatch):
    """كل بادئة في القائمة الافتراضية، ومعها لاحقة مؤرَّخة (مطابقة ببادئة)."""
    from silk_llm_provider import _NO_TEMPERATURE_PREFIXES
    assert set(_NO_TEMPERATURE_PREFIXES) == {
        "claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-mythos-5"}
    for prefix in _NO_TEMPERATURE_PREFIXES:
        for model in (prefix, f"{prefix}-20260401", prefix.upper()):
            captured = _capture_complete(monkeypatch, model)
            assert "temperature" not in captured, model


def test_vision_call_follows_the_same_model_aware_decision(monkeypatch):
    """موضع الرؤية يتبع نفس الحكم — لا موضع ثانٍ يفوت الإصلاح."""
    assert _capture_complete(
        monkeypatch, "claude-opus-4-8", vision=True).get("temperature") == 0
    assert "temperature" not in _capture_complete(
        monkeypatch, "claude-opus-5", vision=True)


def test_no_temperature_list_is_overridable_by_env(monkeypatch):
    """`SILK_LLM_NO_TEMPERATURE` مخرج تشغيلي بلا نشر — يحلّ محل الافتراضية."""
    monkeypatch.setenv("SILK_LLM_NO_TEMPERATURE", "claude-opus-4-8, claude-zeta")
    assert "temperature" not in _capture_complete(monkeypatch, "claude-opus-4-8")
    assert "temperature" not in _capture_complete(monkeypatch, "claude-zeta-1")
    # ما ليس في القائمة الجديدة يستعيد المعامل — الاستبدال كامل لا إضافة
    assert _capture_complete(monkeypatch, "claude-opus-5").get("temperature") == 0


def test_empty_env_override_means_no_model_is_exempt(monkeypatch):
    """قيمة فارغة صريحة = إعادة الإرسال للجميع (مخرج طوارئ إن أخطأت القائمة)."""
    monkeypatch.setenv("SILK_LLM_NO_TEMPERATURE", "")
    assert _capture_complete(monkeypatch, "claude-opus-5").get("temperature") == 0


def test_complete_tools_keeps_default_temperature(monkeypatch):
    """حلقة الأدوات (البعثات) تبقى على افتراضها — مخرجاتها لا تحدّد الحكم
    المعروض مباشرة (الحكم من المحرّك الحتمي حصراً)."""
    import requests
    from silk_llm_provider import AnthropicProvider
    captured = {}

    def fake_post(url, timeout=None, headers=None, json=None):
        captured.update(json or {})
        return _FakeResp(captured)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    AnthropicProvider().complete_tools("s", [], None, 100, "claude-x", 30.0)
    assert "temperature" not in captured


# ── ٣) سُلَّم معايرة الثقة الواحد ────────────────────────────────────────────

def test_confidence_band_single_ladder_in_style_contract():
    from silk_style_contract import (CONFIDENCE_HIGH_MIN_PCT,
                                     CONFIDENCE_MEDIUM_MIN_PCT,
                                     confidence_band_label)
    assert (CONFIDENCE_HIGH_MIN_PCT, CONFIDENCE_MEDIUM_MIN_PCT) == (80, 60)
    assert confidence_band_label(80) == "عالية"
    assert confidence_band_label(79) == "متوسطة"
    assert confidence_band_label(60) == "متوسطة"
    assert confidence_band_label(59) == "منخفضة"


def test_confidence_phrase_consumes_the_ladder():
    from silk_narrative import confidence_phrase
    assert confidence_phrase(0.68) == "متوسطة (68%)"
    assert confidence_phrase(0.5) == "منخفضة (50%)"
    assert confidence_phrase(0.85) == "عالية (85%)"


def test_gate_flags_band_mismatch_as_fail_class():
    """تسمية «عالية (68%)» المخالفة للسُلَّم = بند غير قابل للإصلاح مصنَّف
    ضمن فئات FAIL في البوابة (كان تحذيراً فقط)."""
    import silk_quality_gate as G
    findings = G._check_confidence_band_label("الحكم بثقة عالية (68%).")
    assert findings and findings[0]["check"] == "confidence_band_mismatch"
    assert findings[0]["repairable"] is False
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "silk_quality_gate.py"),
        encoding="utf-8").read()
    fail_block = src.split('if any(f["check"] in (')[1].split(
        'for f in non_repairable')[0]
    assert '"confidence_band_mismatch"' in fail_block


# ── ٤) ثلاث عمليات عرض متتالية = مخرجات متطابقة بايتاً ──────────────────────

def test_three_consecutive_renders_are_byte_identical_and_single_verdict():
    from silk_render import build_view
    from silk_reports import render_markdown
    os.environ["SILK_HERMETIC"] = "1"
    outs = []
    for _ in range(3):
        view = build_view(kuwait_research_blob())
        outs.append(render_markdown(view).encode("utf-8"))
    assert outs[0] == outs[1] == outs[2]
    text = outs[0].decode("utf-8")
    # الشارة (صفّ «الحكم») وسطر «التوصية:» يحملان نفس التسمية القانونية.
    assert text.count("| الحكم | مراقبة السوق |") == 1
    assert "التوصية: **مراقبة السوق**" in text or "**مراقبة السوق**" in text
    assert "GO" not in text


def test_client_docx_drops_ai_reasoning_when_ai_disagrees(tmp_path):
    """تعليل كلود المخالف للحكم الحتمي لا يصل تقرير العميل — يبقى قراءة
    استشارية في التصدير الداخلي فقط."""
    pytest.importorskip("docx")
    from docx import Document
    from silk_render import build_view
    import silk_reports as R
    blob = kuwait_research_blob()
    blob["deep_research"]["verdict"] = {
        "verdict": "WATCH", "confidence": 0.5,
        "ai": {"verdict": "GO", "confidence": 0.9,
               "reasoning": "أدخل السوق فوراً بلا شروط."}}
    os.environ["SILK_HERMETIC"] = "1"
    view = build_view(blob)
    view["test_run"] = True
    out = str(tmp_path / "client.docx")
    R.render_client_docx(view, out)
    text = "\n".join(p.text for p in Document(out).paragraphs)
    assert "أدخل السوق فوراً بلا شروط" not in text
    assert "التوصية: مراقبة السوق" in text
