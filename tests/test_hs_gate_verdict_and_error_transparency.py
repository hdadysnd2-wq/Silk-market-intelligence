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
    # وتُمرَّر للبوّابة قبل أيّ حجزٍ أو دولار (الغلافُ `preflight_resolve`
    # يُمرّرها إلى `preflight_block` نفسها — اللائحة ٦٥).
    assert "hs_confidence=hs_confidence" in src


# ── ١ب) ماسحُ الرموز المخزَّنة (tools/audit_stored_hs.py) ────────────────────
# رمزٌ خاطئ لا يموت مع تشغيلته: يبقى في `analyses`، يُعرَض في شريط «بحوثي
# السابقة» بمظهرٍ رسميّ، ويُعاد تشغيلُه عبر `resume` (الذي يتخطّى بوّابة HS
# كلياً) أو تُعاد كتابةُ تقريره من `found["hs_code"]`.

def _seeded_db(tmp_path, rows):
    """قاعدةُ تحليلاتٍ مبذورة — نفس أعمدة `silk_storage.init_db` المستعمَلة."""
    import sqlite3
    p = str(tmp_path / "silk.db")
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE analyses (id INTEGER PRIMARY KEY, product TEXT, "
                 "hs_code TEXT, created_at TEXT, status TEXT, kind TEXT)")
    for i, (prod, hs) in enumerate(rows, 1):
        conn.execute("INSERT INTO analyses (id,product,hs_code,created_at,"
                     "status,kind) VALUES (?,?,?,?,?,?)",
                     (i, prod, hs, "2026-07-20", "completed", "research"))
    conn.commit()
    conn.close()
    return p


def _audit(path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "audit_stored_hs",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools", "audit_stored_hs.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.audit_analyses(path)


def test_scanner_flags_a_stored_code_the_resolver_now_disagrees_with(tmp_path):
    """الحالةُ المُبلَّغة: 040110 مخزَّن بينما المُحلِّل يعيد 040120."""
    out = _audit(_seeded_db(tmp_path, [("حليب نادك", "040110")]))
    assert len(out) == 1
    assert out[0]["stored_hs"] == "040110"
    assert out[0]["resolver_today"] == "040120"
    assert out[0]["stored_confirmed"] is False


def test_scanner_flags_same_code_but_below_the_new_threshold(tmp_path):
    """الحالةُ الأخبث: لا خلافَ في الرمز، لكنّ ثقتَه دون العتبة اليوم."""
    out = _audit(_seeded_db(tmp_path, [("نفط خام", "520100")]))
    assert len(out) == 1
    assert out[0]["resolver_today"] == "520100"      # نفسُ الرمز — لا «خلاف»
    assert out[0]["below_new_threshold"] is True


def test_scanner_is_quiet_on_a_healthy_row(tmp_path):
    """رمزٌ سليمٌ لا يُبلَّغ عنه — لا ضجيجَ يُغرِق الإشارة."""
    assert _audit(_seeded_db(tmp_path, [("تمور", "080410")])) == []


def test_scanner_makes_no_network_call(tmp_path):
    """قراءةٌ فقط: صفر شبكة، صفر نداء كلود، صفر تكلفة."""
    import socket
    real = socket.socket

    def _no_net(*a, **k):
        raise OSError("network disabled for offline test")

    db = _seeded_db(tmp_path, [("حليب نادك", "040110"), ("تمور", "080410")])
    socket.socket = _no_net
    try:
        out = _audit(db)
    finally:
        socket.socket = real
    assert len(out) == 1


def test_scanner_handles_a_missing_database_quietly(tmp_path):
    """قاعدةٌ غائبة = لا صفوف، لا استثناء (تشغيلٌ أوّلٌ على قرصٍ نظيف)."""
    assert _audit(str(tmp_path / "nope.db")) == []


# ── ١ج) إغلاقُ المسارين غير المحروسين + جذرُ البذرة ─────────────────────────

def test_deepen_now_has_an_hs_gate_and_a_confirm_escape_hatch():
    """`/deepen` (المسار المدفوع) كان بلا بوّابة HS إطلاقاً — وبلا `hs_confirmed`.

    تصحيحٌ مسجَّل: الحقلُ لم يكن «ميتاً» في `DeepenRequest` — لم يكن موجوداً
    أصلاً (وُجد في `AnalyzeRequest`/`ResearchRequest` فقط). أُضيف مع البوّابة."""
    import ast
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api.py"), encoding="utf-8").read()
    cls = next(n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.ClassDef) and n.name == "DeepenRequest")
    fields = [t.target.id for t in cls.body if isinstance(t, ast.AnnAssign)]
    assert "hs_confirmed" in fields, "لا مَخرجَ تأكيدٍ صريح على المسار المدفوع"
    # والبوّابة نفسها مُستدعاة داخل معالج /deepen.
    deepen = src.split("def deepen(req: DeepenRequest")[1].split("\n    @app.")[0]
    assert "preflight_block" in deepen or "_preflight_deepen" in deepen
    assert "req.hs_confirmed" in deepen


def test_resume_and_regen_revalidate_and_tag_without_blocking():
    """`resume`/إعادةُ التوليد: تمرّ وتُوسَم — لا تُحجَب (العملُ المدفوع محفوظ)."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api.py"), encoding="utf-8").read()
    assert src.count("revalidate") >= 2          # الأنبوب + إعادة التوليد
    assert '"hs_revalidation"' in src
    # وسمٌ لا حجب: لا HTTPException في مسار إعادة التحقّق.
    assert "hs_revalidation" not in src.split("raise HTTPException")[0][-400:]


def test_revalidate_flags_a_stale_stored_code_but_returns_none_when_healthy():
    """عقدُ الوسم: dict للرمز الذي لم يعد يوافق حُكمَ اليوم، None للسليم."""
    stale = HC.revalidate("حليب نادك", "040110")
    assert stale and stale["resolver_today"] == "040120"
    assert stale["agrees_with_resolver_today"] is False
    assert HC.revalidate("تمور", "080410") is None


def test_revalidation_message_reaches_the_report_limits():
    """الوسمُ يظهر في «حدود هذا التقرير» — لا يُخزَّن صامتاً."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "silk_render.py"), encoding="utf-8").read()
    assert 'result.get("hs_revalidation")' in src
    assert 'limits.insert(0, _reval["message"])' in src


def test_seed_coverage_measures_the_root_cause_and_locks_it():
    """٩٧٪ من البذرة بلا حارسٍ دلاليّ عربي — الجذر، لا العَرَض.

    صفٌّ بلا `name_ar` وبلا `keywords` لا يُبلَغ من اسمٍ عربي إطلاقاً، فيصير
    **رمزَ إمدادٍ فقط**: لا يظهر إلا حين يُمرَّر صراحةً — وهناك عاشت حادثةُ
    040110. القفلُ يمنع نموَّ العدد بلا قرارٍ صريح."""
    cov = HC.seed_coverage()
    assert cov["total"] > 5000
    assert cov["arabic_guarded"] == cov["total"] - cov["supply_only"]
    # الحالةُ المرصودة وقت الكتابة: 159 مُحصَّناً من 5627 (5468 إمدادٌ فقط).
    # تحسينُ التغطية مرحَّبٌ به (يُخفِّض supply_only)؛ تدهورُها يكسر القفل.
    assert cov["supply_only"] <= 5468, (
        f"عددُ رموز الإمداد-فقط ارتفع إلى {cov['supply_only']} — كلُّ صفٍّ "
        "جديدٍ بلا عربية يوسّع السطحَ الذي دخل منه 040110")
    assert cov["arabic_guarded"] >= 159


def test_040110_family_is_supply_only_by_construction():
    """بنودُ الترويسة 0401 عدا 040120 بلا عربية — لا يبلغها المُحلِّل العربي."""
    from silk_hs_resolver import load_hs_codes
    rows = {r["hs_code"]: r for r in load_hs_codes()
            if str(r.get("hs_code", "")).startswith("0401")}
    assert (rows["040120"].get("name_ar") or "").strip()
    for code in ("040110", "040140", "040150"):
        assert not (rows[code].get("name_ar") or "").strip(), code
        assert not (rows[code].get("keywords") or "").strip(), code


def test_negation_aware_guard_kills_the_false_endorsement():
    """العيبُ المُبلَّغ مُغلَق: «full fat milk» لم يعد يُصادَق ضد 040110.

    وصفُ 040110 «…fat content…not exceeding 1%» (منزوعُ الدسم) يحوي «fat»
    و«milk»، فكان التداخلُ 0.67 يُصادِق الرمزَ المعاكس تماماً. التداخلُ يَعُدّ
    الكلماتِ ولا يقرأ النفي — فوعيُ النفي يتقدّم عليه ويفرض الرفض."""
    for name in ("full fat milk", "whole milk", "حليب كامل الدسم"):
        r = HC.confirm_hs(name, "040110")
        assert r["confirmed"] is False, name
        assert r.get("negation_conflict") is True, name
    # التداخلُ الخام يبقى مرصوداً (لم يُزوَّر) — الرفضُ يعلو عليه لا يمحوه.
    assert HC.confirm_hs("full fat milk", "040110")["overlap"] >= 0.5


def test_negation_awareness_does_not_over_block_healthy_pairs():
    """لا انحدارَ على المسار السليم — أزواجٌ صحيحةٌ تبقى مؤكَّدة."""
    for name, code in (("تمور", "080410"), ("عسل سدر", "040900"),
                       ("حليب نادك", "040120"),
                       ("زبدة الفول السوداني", "200811")):
        r = HC.confirm_hs(name, code)
        assert r["confirmed"] is True, (name, code)
        assert not r.get("negation_conflict"), (name, code)


def test_product_term_inside_an_excluded_span_is_rejected():
    """«other than frozen» + منتجٌ مجمّد = تأكيدٌ لِما يستثنيه الرمز."""
    blocked = HC.confirm_against_description(
        "frozen beef", "999999", "Meat of bovine animals, other than frozen")
    assert blocked["confirmed"] is False
    assert blocked.get("negation_conflict") is True
    # والمنتجُ الذي **يقع داخل** نطاق الرمز لا يُرفَض بهذه القاعدة.
    ok = HC.confirm_against_description(
        "fresh beef", "999999", "Meat of bovine animals, other than frozen")
    assert not ok.get("negation_conflict")


def test_exclusion_detector_distinguishes_bounded_from_plain_descriptions():
    """كاشفُ النفي/العتبة يميّز الوصفَ المحدود من الوصف العادي."""
    assert HC.describes_by_exclusion("fat content not exceeding 1%") is True
    assert HC.describes_by_exclusion("Dates, fresh or dried") is False


def test_false_endorsement_surface_is_measured_and_locked():
    """حجمُ السطح المعرَّض مقيسٌ لا موصوف — ٥٠٪ من الجدول.

    صفٌّ يُعرَّف بنفيٍ أو عتبةٍ رقمية لا يستطيع التداخلُ الحكمَ عليه بنيوياً؛
    وحين يكون **إمداداً فقط** (بلا عربية) فهو بالضبط سطحُ المصادقة الكاذبة."""
    import csv
    rows = list(csv.DictReader(open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "hs_codes.csv"), encoding="utf-8")))
    bounded = [r for r in rows
               if HC.describes_by_exclusion(f"{r.get('name_en') or ''} "
                                            f"{r.get('name_ar') or ''}")]
    assert len(rows) > 5000
    # الحالةُ المرصودة وقت الكتابة: 2,828 من 5,627 (50.3%). القفلُ يرصد
    # النموّ المفاجئ؛ الانخفاض (تحسينُ الأوصاف) مرحَّبٌ به.
    assert len(bounded) <= 2900, (
        f"سطحُ الأوصاف المحدودة بنفي/عتبة ارتفع إلى {len(bounded)}")
    assert len(bounded) >= 2000


def test_research_ambiguity_gate_is_deterministic_and_valved():
    """بوّابةُ الالتباس على `/research`: بلا كلود، وبصمّامٍ صريح."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "api.py"), encoding="utf-8").read()
    assert "SILK_RESEARCH_AMBIGUITY_GATE" in src
    assert "_research_ambiguity_gate_enabled()" in src
    # لا نداءَ كلود في هذه البوّابة إطلاقاً — التصنيفُ الحتميّ وحده.
    assert 'classify_general(product or "", allow_claude=False)' in src, (
        "البوّابة يجب ألّا تصرف نداء كلود")
    # وحسمُ السمة الرقمية الذي يسبق الحوار (اللائحة ٦٥) حتميٌّ كذلك: بطاقةُ
    # العبوة بلا نداء، والويبُ استعلامُ بحثٍ واحد لا نداءَ نموذج.
    block = src.split('"error": "hs_ambiguous"')[0][-2600:]
    assert "resolve_or_probe(" in block, (
        "بوّابةُ الالتباس يجب أن تقيس العتبة قبل أن تسأل عنها")
    # مقصورةٌ على /research: لا ذكرَ لها في معالجَي /analyze و/deepen.
    assert src.count("_research_ambiguity_gate_enabled()") == 2   # التعريف + نداء


def test_ambiguous_product_is_caught_deterministically():
    """المُصنِّف يرفض الحسمَ التلقائي عند تقارب المرشّحين — بلا شبكة."""
    import socket
    real = socket.socket

    def _no_net(*a, **k):
        raise OSError("network disabled for offline test")

    socket.socket = _no_net
    try:
        import silk_hs_classifier as K
        assert K.classify_general("حليب", allow_claude=False)["tier"] != "auto"
        assert K.classify_general("تمور", allow_claude=False)["tier"] == "auto"
    finally:
        socket.socket = real


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
