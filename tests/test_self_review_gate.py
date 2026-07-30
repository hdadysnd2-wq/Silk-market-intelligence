"""اختبارات بوّابة §58 — the self-review gate's logic must itself be tested.

بوّابةٌ لم تُختبَر قد تكون خاملة (تمرّ دائماً) فتُعطي أماناً زائفاً — نفس عائلة
«الاختبار الأخضر الفارغ». هنا نُثبِت أنها ترفض ما يجب أن ترفضه وتقبل ما يجب.
A gate that always passes is worse than no gate; prove both directions.
"""
import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "check_self_review_gate",
    pathlib.Path(__file__).resolve().parent.parent / "tools" / "check_self_review_gate.py")
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def test_rejects_body_with_no_self_review_section():
    problems = gate.check("## What this is\n\nSome feature. Tests pass.")
    assert problems and any("no self-review section" in p for p in problems)


def test_rejects_self_review_section_without_high_severity_outcome():
    body = ("## المراجعة الذاتية · self-review (§58)\n\n"
            "شغّلت /code-review على الفرق العامل وقرأت الملاحظات.")
    problems = gate.check(body)
    assert problems and any("high-severity outcome" in p for p in problems)


def test_accepts_no_high_findings_declaration_arabic_and_english():
    for body in (
        "## المراجعة الذاتية (§58)\n\nمراجعة مستقلّة: **لا عيوب high**.",
        "## Self-review (§58)\n\nIndependent pass: no high-severity findings.",
    ):
        assert gate.check(body) == [], body


def test_accepts_counted_fixed_findings_declaration():
    body = ("## المراجعة الذاتية · self-review\n\n"
            "مراجعة موسَّعة: ٢٦ ملاحظة مُصلَحة وواحدة مؤجَّلة بوعي.\n"
            "27 findings reviewed, 26 fixed with regression locks.")
    assert gate.check(body) == []


def test_accepts_accepted_risk_ledger_declaration():
    body = ("## self-review (§58)\n\nOne high finding recorded as an "
            "**accepted risk** in docs/DEEP_RESEARCH_DECISIONS.md.")
    assert gate.check(body) == []


def test_empty_body_fails_both_checks():
    assert len(gate.check("")) == 2


def test_skips_cleanly_when_not_a_pull_request_event(monkeypatch):
    """على push (لا متن PR) تمرّ البوّابة بلا ضجيج — push events skip, exit 0."""
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert gate._pr_body() is None
    assert gate.main() == 0


def test_main_fails_on_a_noncompliant_pr_payload(tmp_path, monkeypatch):
    """تكامل: حمولة PR بلا تصريح ⇒ خروج ١ (يُسقِط الوظيفة في CI)."""
    payload = tmp_path / "event.json"
    payload.write_text('{"pull_request": {"body": "just a feature, no review"}}',
                       encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
    assert gate.main() == 1


def test_main_passes_on_a_compliant_pr_payload(tmp_path, monkeypatch):
    payload = tmp_path / "event.json"
    payload.write_text(
        '{"pull_request": {"body": "## self-review (\\u00a758)\\n\\n'
        'no high-severity findings; two MEDIUM fixed with locks."}}',
        encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(payload))
    assert gate.main() == 0
