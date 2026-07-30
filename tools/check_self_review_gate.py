"""بوّابة §58 في CI — assert every PR body declares its self-review outcome.

**الفجوة التي تُغلقها:** القاعدة §58 في CLAUDE.md تُلزِم تشغيل `/code-review` على
الفرق العامل ومعالجة (أو تسجيل «خطر مقبول») لكل ملاحظة بخطورة high فأعلى، وأن
يذكر ملخّص كل PR أن المراجعة جرت وملاحظاتها. لكن لا شيء **آلي** يمنع دمج PR
نُسِيَت فيه — فكانت تعتمد على تذكّر شخص. هذا الفحص يجعلها بوّابة.

**ما يفرضه** (بحدّ أدنى لا يخترعه أحد بلا قصد): متن الـPR يجب أن يحتوي
1. قسم مراجعة ذاتية (عنوان يذكر «المراجعة الذاتية» أو self-review أو §58)، و
2. تصريحاً صريحاً عن الملاحظات عالية الخطورة — إمّا «لا عيوب high» أو عدداً
   مُصلَحاً/مسجَّلاً كخطر مقبول.

**لا يزعم** أنه يتحقّق من جودة المراجعة — يتحقّق من **وجود التصريح**، فيمنع
النسيان الصامت. Existence of the declaration, not its quality.

يعمل على أحداث pull_request فقط (يقرأ متن الـPR من حمولة الحدث)؛ وعلى push
يخرج بنجاح لأن لا متن هناك. Exit 0 = pass, 1 = missing declaration.
"""
from __future__ import annotations

import json
import os
import re
import sys

# عناوين/عبارات تُثبِت وجود قسم المراجعة الذاتية.
_SECTION_MARKERS = (
    "المراجعة الذاتية", "مراجعة ذاتية", "self-review", "self review",
    "§58", "code-review", "code review",
)

# الأرقام العربية-الهندية (٠-٩) مقبولة كالغربية: هذا ريبو عربيّ أولاً، وكتابة
# «٢ مُصلَحتان» هي الصيغة الطبيعية فيه. بوّابةٌ ترفض التصريح الصحيح لأنه مكتوب
# بلغة الريبو تدفع كاتبه إلى **حذفها** — وهو أسوأ ما قد يحدث لحارس، والبوّابة
# نفسها تحذّر منه. Arabic-Indic digits and dual/plural forms are first-class.
_DIGITS = r"[0-9٠-٩]+"
# صِيَغ «مُصلَحة» مفردةً ومثنّى وجمعاً، بالهمزة والشكل أو بدونهما.
_FIXED_AR = r"مُ?صلَ?ح(ة|تان|تين|ات|ان)"

# تصريح صريح عن الملاحظات العالية: إمّا نفيها أو معالجتها/تسجيلها.
_HIGH_DECLARATIONS = (
    r"لا\s+(عيوب|ملاحظات|ملاحظة)\s+high",
    # «لا ملاحظة high غير معالَجة» — نفيٌ بصيغة «لا شيء متبقٍّ» لا «لا شيء وُجد».
    r"لا\s+(ملاحظة|ملاحظات|عيوب)\s+high[^.\n]{0,40}(غير\s+معالَ?جة|بلا\s+معالجة)",
    r"no\s+high[- ](severity\s+)?(findings|defects|issues)",
    r"no\s+unaddressed\s+high",
    r"high[- ]severity[^.\n]{0,40}(fixed|resolved|addressed|none)",
    rf"{_DIGITS}\s*(ملاحظة|ملاحظات|finding|findings)[^.\n]{{0,60}}({_FIXED_AR}|fixed|addressed)",
    # الترتيب المعاكس: «٢ مُصلَحتان» / «مُصلَحتان: ٢» بلا كلمة «ملاحظة» بينهما.
    rf"{_DIGITS}\s*{_FIXED_AR}",
    rf"{_FIXED_AR}[^.\n]{{0,20}}{_DIGITS}",
    r"خطر\s+مقبول",                     # accepted-risk ledger entry
    r"accepted\s+risk",
)


def _pr_body() -> str | None:
    """متن الـPR من حمولة حدث GitHub — None when this isn't a PR event."""
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    pr = payload.get("pull_request")
    if not isinstance(pr, dict):
        return None
    return pr.get("body") or ""


def check(body: str) -> list[str]:
    """افحص المتن — returns a list of problems ([] means the gate passes)."""
    problems: list[str] = []
    low = body.lower()
    if not any(m.lower() in low for m in _SECTION_MARKERS):
        problems.append(
            "PR body has no self-review section — CLAUDE.md §58 requires running "
            "/code-review on the working diff and summarizing its findings. Add a "
            "section titled e.g. 'المراجعة الذاتية · self-review (§58)'.")
    if not any(re.search(p, body, re.IGNORECASE) for p in _HIGH_DECLARATIONS):
        problems.append(
            "PR body does not state the high-severity outcome — say explicitly "
            "'no high-severity findings', or how many were fixed, or record an "
            "'accepted risk' (خطر مقبول) in docs/DEEP_RESEARCH_DECISIONS.md.")
    return problems


def main() -> int:
    body = _pr_body()
    if body is None:
        print("check_self_review_gate: not a pull_request event — skipping.")
        return 0
    if not body.strip():
        print("FAIL: pull request body is empty; §58 requires a self-review summary.")
        return 1
    problems = check(body)
    if problems:
        print("FAIL: CLAUDE.md §58 self-review gate\n")
        for p in problems:
            print(f"  - {p}")
        print("\nThis gate checks that the declaration EXISTS, not that the review "
              "was good. Do not delete the gate to pass it.")
        return 1
    print("check_self_review_gate: §58 self-review declaration present. OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
