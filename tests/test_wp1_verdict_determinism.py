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


# ── ٢) معاملات المعاينة: قرار واعٍ بالنموذج ─────────────────────────────────
# الثلاثة (temperature/top_p/top_k) أُزيلت معاً من Claude 4.7 فصاعداً: إرسال
# أيٍّ منها يردّ 400 فيسقط النداء كلّه (تقرير بلا سرد، لا حكم غير حتمي). تبقى
# temperature=0 حيث يقبلها النموذج — تخفّض التشتّت، ولا تضمن تطابقاً.

class _FakeResp:
    def __init__(self, payload_store):
        self._store = payload_store

    def raise_for_status(self):
        return None

    def json(self):
        return {"content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn", "usage": {}}


def _capture(monkeypatch, model, *, mode="complete", extra=None):
    """نفّذ نداءً واحداً والتقط الحمولة المرسَلة فعلاً — أداة الاختبارات أدناه.

    `extra` يحقن مفاتيح في الحمولة قبل الإرسال (لاختبار المنقّي على مفاتيح
    لا يبنيها المزوّد اليوم — top_p/top_k — فيبقى الحارس صالحاً لو أضافها
    موضعٌ جديد لاحقاً)."""
    import requests
    from silk_llm_provider import AnthropicProvider
    captured = {}

    def fake_post(url, timeout=None, headers=None, json=None):
        captured.update(json or {})
        return _FakeResp(captured)

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicProvider()
    if extra:
        real_post = provider._post

        def post_with_extra(key, payload, timeout):
            payload.update(extra)
            return real_post(key, payload, timeout)

        monkeypatch.setattr(provider, "_post", post_with_extra)
    if mode == "vision":
        out = provider.complete_vision(
            "s", "t", "YWJj", "image/png", 100, model, 30.0)
    elif mode == "tools":
        provider.complete_tools("s", [], None, 100, model, 30.0)
        return captured
    else:
        out = provider.complete("s", "u", 100, model, 30.0)
    assert out == "ok"
    return captured


def test_sampling_params_present_for_a_still_supported_model(monkeypatch):
    """نموذج ما زال يقبلها (Haiku 4.5 بمعرّفه المؤرَّخ) — temperature=0 مُرسَلة،
    فالصفّ ٤٧ يبقى نافذاً حيث الرافعة موجودة أصلاً."""
    captured = _capture(monkeypatch, "claude-haiku-4-5-20251001")
    assert captured.get("temperature") == 0
    assert "top_p" not in captured    # لا يُرسَلان معاً (400 مستقلّ)
    assert "top_k" not in captured


def test_sampling_params_absent_for_the_repo_default_model(monkeypatch):
    """`claude-opus-4-8` — نموذج هذا الريبو الافتراضي (silk_ai_judge.py:20)
    ويرفض المعاملات: تُحذف كلّياً. هذا هو الاختبار الذي كان سيلتقط أن قائمةً
    تغطي الجيل الخامس وحده لا تصلح إنتاجياً."""
    captured = _capture(monkeypatch, "claude-opus-4-8")
    for key in ("temperature", "top_p", "top_k"):
        assert key not in captured, key
    # بقية الحمولة سليمة — الحذف لا يمسّ شيئاً آخر
    assert captured["model"] == "claude-opus-4-8"
    assert captured["max_tokens"] == 100


def test_key_is_omitted_never_neutralised(monkeypatch):
    """الحذف لا الضبط على قيمة محايدة — حضور المفتاح وحده يكفي للرفض بـ400،
    فقيمة 1.0 «المحايدة» ليست علاجاً."""
    captured = _capture(monkeypatch, "claude-opus-5")
    assert "temperature" not in captured.keys()


def test_every_rejecting_family_is_covered(monkeypatch):
    """كل بادئة في القائمة الافتراضية، ومعها لاحقة مؤرَّخة وصيغة كبيرة الأحرف."""
    from silk_llm_provider import _NO_SAMPLING_PARAMS_PREFIXES
    assert set(_NO_SAMPLING_PARAMS_PREFIXES) == {
        "claude-opus-4-7", "claude-opus-4-8", "claude-opus-5",
        "claude-sonnet-5", "claude-fable-5", "claude-mythos"}
    for prefix in _NO_SAMPLING_PARAMS_PREFIXES:
        for model in (prefix, f"{prefix}-20260401", prefix.upper()):
            assert "temperature" not in _capture(monkeypatch, model), model
    # بادئة العائلة تغطي الفرعين معاً (mythos-5 و mythos-preview)
    for model in ("claude-mythos-5", "claude-mythos-preview"):
        assert "temperature" not in _capture(monkeypatch, model), model


def test_still_supported_families_keep_the_param(monkeypatch):
    """ما دون 4.7 لم يتغيّر — لا حذف شامل يعيد اللاحتمية عليها."""
    for model in ("claude-haiku-4-5-20251001", "claude-sonnet-4-6",
                  "claude-opus-4-6", "claude-sonnet-4-5"):
        assert _capture(monkeypatch, model).get("temperature") == 0, model


def test_scrub_strips_top_p_and_top_k_on_a_rejecting_model(monkeypatch):
    """المنقّي في `_post` نقطة اختناق: أيّ موضع يحقن top_p/top_k لا يستطيع
    إعادة إنتاج الـ400 على نموذج رافض."""
    captured = _capture(monkeypatch, "claude-opus-4-8",
                        extra={"top_p": 0.9, "top_k": 40})
    for key in ("temperature", "top_p", "top_k"):
        assert key not in captured, key


def test_temperature_and_top_p_are_never_sent_together(monkeypatch):
    """على نموذج يقبلها: `top_p` يُنزَع عند تزاحمه مع `temperature` المثبَّتة
    (Anthropic توصي بضبط أحدهما لا كليهما؛ إرسالهما معاً 400 مستقلّ)."""
    captured = _capture(monkeypatch, "claude-haiku-4-5-20251001",
                        extra={"top_p": 0.9})
    assert captured.get("temperature") == 0
    assert "top_p" not in captured


def test_vision_call_follows_the_same_model_aware_decision(monkeypatch):
    """موضع الرؤية يتبع نفس الحكم — لا موضع ثانٍ يفوت الإصلاح."""
    assert _capture(monkeypatch, "claude-haiku-4-5-20251001",
                    mode="vision").get("temperature") == 0
    assert "temperature" not in _capture(
        monkeypatch, "claude-opus-4-8", mode="vision")


def test_tools_loop_is_scrubbed_too_but_never_pinned(monkeypatch):
    """حلقة الأدوات (البعثات) تبقى على افتراض المزوّد — لا تثبيت (مخرجاتها لا
    تحدّد الحكم المعروض) — لكنها تمرّ بالمنقّي كغيرها."""
    assert "temperature" not in _capture(monkeypatch, "claude-x", mode="tools")
    captured = _capture(monkeypatch, "claude-opus-4-8", mode="tools",
                        extra={"temperature": 0.5, "top_k": 40})
    assert "temperature" not in captured
    assert "top_k" not in captured


def test_no_sampling_params_list_is_overridable_by_env(monkeypatch):
    """`SILK_LLM_NO_SAMPLING_PARAMS` مخرج تشغيلي بلا نشر — يحلّ محل الافتراضية."""
    monkeypatch.setenv("SILK_LLM_NO_SAMPLING_PARAMS", "claude-zeta, claude-eta")
    assert "temperature" not in _capture(monkeypatch, "claude-zeta-1")
    assert "temperature" not in _capture(monkeypatch, "claude-eta")
    # ما ليس في القائمة الجديدة يستعيد المعامل — الاستبدال كامل لا إضافة
    assert _capture(monkeypatch, "claude-opus-4-8").get("temperature") == 0


def test_empty_env_override_means_no_model_is_exempt(monkeypatch):
    """قيمة فارغة صريحة = إعادة الإرسال للجميع (مخرج طوارئ إن أخطأت القائمة)."""
    monkeypatch.setenv("SILK_LLM_NO_SAMPLING_PARAMS", "")
    assert _capture(monkeypatch, "claude-opus-4-8").get("temperature") == 0


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
    # PR A: فئاتُ FAIL صارت ثابتاً واحداً على مستوى الوحدة
    # (`FAIL_TRIGGER_CHECKS`) بدل صفٍّ حرفيٍّ داخل `run_quality_gate` — نفس
    # النيّة، تحقّقٌ بنيويّ لا تحليلُ نصٍّ هشّ.
    assert "confidence_band_mismatch" in G.FAIL_TRIGGER_CHECKS


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
