"""أقفال ثلاثة إصلاحات (بلاغ المالك 2026-07-27) — hermetic, صفر شبكة.

١) **بوّابة ثقة التصنيف** (بلاغ «حليب نادك»): ثقةٌ غائبةٌ أو دون العتبة =
   لا ترتيبَ ولا إنفاق، بل مرشّحون يختار منهم المستخدم. الحادثة: جدول التقرير
   عرض «ثقة التصنيف —» ومضى الخطُّ على رمزٍ داخل الترويسة الصحيحة لكنه البند
   الخاطئ (040110 «دسم ≤1%» لمنتجٍ كامل الدسم). سببُ الشرطة نفسه: مسار
   `/research` كان يأخذ `dp.value` ويرمي `dp.confidence` فلا تصل النتيجةَ أبداً.

٢) **فصلُ الحكم عن السرد**: «PRELIMINARY / INCONCLUSIVE» حكمٌ حتميٌّ صادرٌ عن
   `JuryCommittee` كلّما فشلت بعثةٌ واحدة مع بقاء نتائج حقيقية — لم يكن له فرعٌ
   في `_verdict_tone` فينهار إلى «unknown» وتعرض الشارةُ «تعذّر إصدار توصية»
   بينما كتلةُ SWOT تحمل توصيةً كاملة. فشلُ نداءٍ لغويٍّ يُعلَّم في `narrative`
   وحده ولا يمسّ الحكم.

٣) **شفافية الأخطاء**: النمطُ القديم كان ينتهي بـ`.*` فيبتلع بقيةَ السطر —
   رمزَ الحالة والرسالةَ والقوسَ الخاتم («... — خطأ تقني مؤقت» بقوسٍ مفتوح)،
   ويطمس كلَّ أنماط الفشل في سلسلةٍ واحدة.

Run: python3 -m pytest tests/test_hs_gate_verdict_and_error_transparency.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import silk_hs_confirm as HC  # noqa: E402
import silk_narrative as N  # noqa: E402
import silk_render as R  # noqa: E402


# ── ١) بوّابة ثقة التصنيف ────────────────────────────────────────────────────

def test_absent_confidence_is_blocked_not_silently_analyzed():
    """ثقةٌ `None` («ثقة التصنيف —» حرفياً) = حجب، لا مضيّ صامت."""
    blocked = HC.confidence_block("حليب نادك", "040110", None)
    assert blocked is not None
    assert blocked["error"] == "hs_confidence_too_low"
    assert blocked["hs_confidence"] is None


def test_low_confidence_is_blocked_and_offers_candidates():
    """ثقةٌ دون العتبة تُحجَب، والردّ يحمل مرشّحين — لا رفضٌ عارٍ."""
    blocked = HC.confidence_block("منتج ما", "080410", 0.71)
    assert blocked is not None
    assert blocked["min_confidence"] == HC.min_confidence()
    assert isinstance(blocked["candidates"], list)


def test_confident_classification_passes_untouched():
    """ثقةٌ عند العتبة أو فوقها تمرّ — لا انحدار على المسار السليم."""
    assert HC.confidence_block("تمور", "080410", 1.0) is None
    assert HC.confidence_block("تمور", "080410", HC.min_confidence()) is None


def test_candidates_are_deterministic_and_need_no_network_or_key():
    """المرشّحون من البذرة الحتميّة — صفر نداء كلود، صفر شبكة، صفر تكلفة."""
    import socket
    real = socket.socket

    def _no_net(*a, **k):
        raise OSError("network disabled for offline test")

    socket.socket = _no_net
    try:
        cands = HC.deterministic_candidates("تمور")
    finally:
        socket.socket = real
    assert cands and all(c["hs6"] for c in cands)


def test_gate_is_fail_safe_on_by_default_and_env_disablable():
    """فشل-آمن: مفعّلة افتراضياً؛ تُطفأ صراحةً فقط."""
    assert HC.confidence_gate_enabled() is True
    old = os.environ.get("SILK_HS_CONFIDENCE_GATE")
    os.environ["SILK_HS_CONFIDENCE_GATE"] = "0"
    try:
        assert HC.confidence_gate_enabled() is False
        assert HC.confidence_block("س", "040110", None) is None
    finally:
        if old is None:
            os.environ.pop("SILK_HS_CONFIDENCE_GATE", None)
        else:
            os.environ["SILK_HS_CONFIDENCE_GATE"] = old


def test_confidence_valve_is_independent_of_the_semantic_valve():
    """إطفاءُ بوّابة التطابق الدلالي لا يُطفئ بوّابة الثقة (صمّامان منفصلان)."""
    old = os.environ.get("SILK_HS_CONFIRM_GATE")
    os.environ["SILK_HS_CONFIRM_GATE"] = "0"
    try:
        blocked = HC.preflight_block("حليب نادك", "040110", hs_confidence=None)
        assert blocked is not None
        assert blocked["error"] == "hs_confidence_too_low"
    finally:
        if old is None:
            os.environ.pop("SILK_HS_CONFIRM_GATE", None)
        else:
            os.environ["SILK_HS_CONFIRM_GATE"] = old


def test_legacy_caller_without_confidence_keeps_old_behavior():
    """مستدعٍ لا يمرّر ثقةً إطلاقاً لا تنطبق عليه البوّابة (`_UNSET` ≠ `None`)."""
    assert HC.preflight_block("تمور", "080410") is None


def test_explicit_user_confirmation_bypasses_the_confidence_gate():
    """تأكيدُ المستخدم الصريح يتقدّم — المالك آخِر مؤكِّد (LAW)."""
    assert HC.preflight_block("حليب نادك", "040110", True,
                              hs_confidence=None) is None


def test_engine_gates_before_ranking_not_after():
    """المحرّك يحجب **قبل** `rank_markets` — لا نداءَ سوقٍ واحد على رمزٍ مشكوك."""
    import ast
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "silk_engine.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "analyze")
    gate_line = rank_line = None
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == "confidence_block":
            gate_line = min(gate_line or node.lineno, node.lineno)
        if isinstance(node, ast.Call) and getattr(
                node.func, "id", "") == "rank_markets":
            rank_line = min(rank_line or node.lineno, node.lineno)
    assert gate_line and rank_line, (gate_line, rank_line)
    assert gate_line < rank_line, "بوّابة الثقة يجب أن تسبق الترتيب"


def test_research_path_carries_hs_confidence_into_the_result():
    """`/research` يحمل ثقةَ التصنيف إلى النتيجة — سببُ «ثقة التصنيف —»."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api.py"), encoding="utf-8").read()
    assert "hs_confidence = dp.confidence if dp.value is not None else None" in src
    assert '"hs_confidence": hs_confidence,' in src
    # وتُمرَّر للبوّابة قبل أيّ حجزٍ أو دولار.
    assert "hs_confidence=hs_confidence)" in src


# ── ٢) فصلُ الحكم (حتمي) عن السرد (نداءٌ لغويٌّ اختياري) ──────────────────────

def test_inconclusive_is_a_verdict_not_an_absence():
    """حكمُ اللجنة «INCONCLUSIVE» له نغمةٌ وتسميةٌ خاصّتان — لا «unknown»."""
    jury = "PRELIMINARY / INCONCLUSIVE — مبدئي وناقص البيانات"
    assert R._verdict_tone(jury) == "inconclusive"
    label = R._VERDICT_LABELS_AR["inconclusive"]
    assert label != R._VERDICT_LABELS_AR["unknown"]
    assert "تعذّر إصدار توصية" not in label
    assert N.verdict_ar(jury) == label


def test_unknown_stays_reserved_for_a_genuinely_absent_verdict():
    """«تعذّر إصدار توصية» تبقى لغيابِ الحكم الحتمي كلياً فقط."""
    assert R._verdict_tone("") == "unknown"
    assert R._VERDICT_LABELS_AR["unknown"] == "تعذّر إصدار توصية"


def test_every_jury_verdict_maps_to_a_real_label():
    """كلُّ مخرَجٍ ممكنٍ من `JuryCommittee.evaluate` يُصنَّف — لا فرعَ يسقط."""
    from silk_agents import AgentReport, JuryCommittee
    from silk_data_layer import DataPoint

    good = AgentReport("A", [DataPoint(1, "s", 0.9, "n", "2026-01-01")],
                       False, "ok")
    bad = AgentReport("B", [], True, "failed")
    for reports in ([good], [good, bad], [bad]):
        v = JuryCommittee.evaluate(reports)["verdict"]
        tone = R._verdict_tone(v)
        assert tone in R._VERDICT_LABELS_AR, (v, tone)
        # حكمٌ صدر فعلاً عن اللجنة لا يجوز أن يُعرَض «تعذّر إصدار توصية»
        # ما دامت هناك نتائج حقيقية مساهِمة.
        if any(not r.failed for r in reports):
            assert tone != "unknown", (v, tone)


def test_failed_narrative_is_marked_separately_from_the_verdict():
    """فشلُ الكاتب يُعلَّم في `narrative` ولا يخفض الشارة."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "silk_render.py"), encoding="utf-8").read()
    dr = src.split("def _deep_research_view(")[1]
    assert '"narrative": {' in dr
    assert '"available": bool(report_out.get("report")),' in dr
    # الشارة تُشتقّ من الحكم الحتمي وحده — لا من حالة الكاتب.
    assert '"verdict_label": _VERDICT_LABELS_AR[verdict_tone],' in dr


def test_web_badge_knows_the_new_tone():
    """الواجهة تعرف النغمة الجديدة — لا شارةٌ بلا صنف CSS."""
    web = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "web", "index.html"), encoding="utf-8").read()
    assert "inconclusive:" in web
    assert R._VERDICT_LABELS_AR["inconclusive"] in web


# ── ٣) شفافية الأخطاء ────────────────────────────────────────────────────────

def test_error_text_never_leaves_an_unclosed_paren():
    """العيبُ المُبلَّغ حرفياً: «(تعذّر الاتصال بالمصدر — خطأ تقني مؤقت»."""
    for raw in ["فشل نداء التحليل الآلي (ReadTimeout)",
                "فشل نداء كلود (HTTPError: HTTP 429 rate_limit)",
                "(HTTPSConnectionPool(host=x, port=443): read timeout)",
                "خطأ: HTTP 429)"]:
        out = N.humanize_technical_note(raw)
        assert out.count("(") == out.count(")"), (raw, out)


def test_arabic_list_markers_survive_the_sanitizer():
    """تعدادُ «1) 2) 3)» ترقيمٌ مشروع لا خلل — انحدارٌ التقطته العيّنة المُودَعة.

    أول صياغةٍ لموازنة الأقواس أسقطت كلَّ قوسٍ شارد، فحوّلت «1)» إلى «1» في
    `samples/research_report_latest.md`. القاعدة: يُسقَط المفتوحُ غير المُغلَق
    وحده (أثرُ استبدالٍ ابتلع خاتمته)، لا الخاتمُ الشارد."""
    for text in ["أول ثلاث خطوات: 1) تسجيل 2) عيّنة 3) دراسة",
                 "البنود: أ) الأول ب) الثاني",
                 "نص عادي (بين قوسين)"]:
        assert N.humanize_technical_note(text) == text


def test_failure_modes_are_distinguished_not_collapsed():
    """مهلةُ اتصالٍ ≠ مهلةُ ردٍّ ≠ تجاوزُ معدّل — أفعالٌ تشغيليةٌ مختلفة."""
    outs = {
        N.humanize_technical_note("فشل (ConnectTimeout)"),
        N.humanize_technical_note("فشل (ReadTimeout)"),
        N.humanize_technical_note("فشل (TooManyRequestsError)"),
    }
    assert len(outs) == 3, outs


def test_http_status_code_survives_translation():
    """رمزُ الحالة يبقى ظاهراً — كان يُمحى فيُخلِّف «خطأ استجابة الخادم: )»."""
    out = N.humanize_technical_note("Google Trends: HTTP 429")
    assert "429" in out
    assert not out.rstrip().endswith(":")
    assert N.humanize_technical_note("خطأ: HTTP 429)").count(")") <= 1


def test_non_error_http_status_is_left_alone():
    """2xx ليست خطأً — لا تُعرَّب «خطأ استجابة الخادم»."""
    out = N.humanize_technical_note("HTTP 200 بلا كتل نصية")
    assert "خطأ استجابة الخادم" not in out
    assert "200" in out


def test_trends_failure_reports_its_real_reason_not_a_wrong_one():
    """«(بلا شبكة)» كانت تُشخِّص خطأً تجاوزَ المعدّل — السببُ يُحفَظ."""
    out = N.humanize_technical_note(
        "pytrends unavailable / no network: HTTP 429")
    assert "429" in out
    # وبلا سببٍ فعليّ تبقى الصياغةُ القديمة (لا اختلاق سبب).
    assert "بلا شبكة" in N.humanize_technical_note(
        "pytrends unavailable / no network: ")


def test_no_raw_python_exception_class_reaches_the_client():
    """الثابتُ القديم محفوظ: لا اسمَ صنفِ استثناءٍ خام يمرّ للعميل."""
    for raw in ["ReadTimeout", "TooManyRequestsError", "ValueError: boom",
                "JSONDecodeError"]:
        out = N.humanize_technical_note(f"فشل ({raw})")
        assert raw not in out, (raw, out)


def test_provider_logs_the_real_status_code_and_body():
    """السجلّ يحمل رمزَ الحالة والجسم — تشخيصُ 429 مقابل 401 من السجلّات."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "silk_llm_provider.py"),
        encoding="utf-8").read()
    assert 'status=%s body=%s' in src
    assert 'detail.get("status_code"), detail.get("response_body")' in src
