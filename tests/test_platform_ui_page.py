"""حُرّاس صفحة لوحة المنصّة — `web/platform.html`.

**العائلة التي تُغلقها:** الصفحة تقرأ حقولاً من ردود `/platform/*`. اسمُ حقلٍ
خاطئ **لا يرفع خطأً** — يعرض قسماً فارغاً صمتاً. وقع هذا فعلاً أثناء البناء:
كُتِب `.ledger` والنقطة تُرجع `entries`، فكان الدفتر يظهر فارغاً دائماً بلا أي
إشارة. مراجعةُ عينٍ لا تلتقط هذا؛ هذه الاختبارات تلتقطه.

لذلك الحارس الأهمّ هنا **يقارن مفاتيح الردّ الحقيقية** بما تقرؤه الصفحة، لا
يتفحّص النصّ فقط. A wrong field name renders an empty section silently — these
tests compare the page's reads against real response keys.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from tests.platform_helpers import client, hdr, login, make_factory, seed

_PAGE = pathlib.Path(__file__).resolve().parent.parent / "web" / "platform.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert _PAGE.exists(), "web/platform.html مفقودة — صفحة اللوحة"
    return _PAGE.read_text(encoding="utf-8")


# ═════════════════ الاكتفاء الذاتي · self-contained (CSP-safe) ════════════════
def test_page_has_no_external_references(html):
    """لا CDN ولا خطّ خارجي — الخدمة تُقدّم CSP صارمة، والخارجي يُحجَب حيّاً.

    الخطوط مستضافة ذاتياً في `web/fonts/` (نفس اتفاق `index.html`).
    """
    external = re.findall(r'(?:src|href)\s*=\s*["\'](https?://[^"\']+)', html)
    assert not external, f"مراجع خارجية ستُحجَب بـCSP: {external}"


def test_page_uses_no_eval_so_it_survives_the_csp(html):
    """لا `eval`/`new Function` — سياسة `script-src` تمنعهما (وقد أثبتَه المتصفّح).

    اكتُشف عملياً: انتظارُ Playwright بنصٍّ يُقيَّم كان يُرفَض بـ`unsafe-eval`،
    فالسياسة فعّالة حقاً — والصفحة يجب أن تبقى نظيفة منهما.
    """
    for bad in ("eval(", "new Function(", "setTimeout(\"", "setInterval(\""):
        assert bad not in html, f"استعمالٌ يمنعه CSP: {bad}"


def test_page_is_rtl_arabic_first(html):
    assert 'dir="rtl"' in html and 'lang="ar"' in html


# ═══════════ كل مسار تطلبه الصفحة موجود فعلاً · every fetched path exists ════
def _paths_fetched(html: str) -> set[str]:
    """مسارات `/platform/...` التي تطلبها الصفحة — الحرفيّة منها.

    تُطبَّع القوالب (`"/studies/" + id + "/" + action`) إلى شكلٍ قابل للمقارنة.
    """
    out = set()
    for m in re.finditer(r'api\(\s*"([^"]+)"', html):
        out.add(m.group(1).split("?")[0])
    # النداءات المركّبة: "/studies/" + id + "/" + action
    for m in re.finditer(r'api\(\s*"(/[a-z-]+/)"\s*\+', html):
        out.add(m.group(1))
    return out


def test_every_path_the_page_calls_is_a_registered_route(html):
    """مسارٌ تطلبه الصفحة ولا وجود له = قسمٌ ميت — يُلتقَط هنا لا في الإنتاج."""
    import silk_platform.api as papi
    registered = set(re.findall(r'@app\.\w+\(_PREFIX \+ "([^"]+)"',
                                pathlib.Path(papi.__file__).read_text(encoding="utf-8")))
    # الأشكال المُعامَلة: حوِّل `/studies/{study_id}/archive` إلى بادئة قابلة للمطابقة.
    prefixes = {re.sub(r"\{[^}]+\}.*$", "", r) for r in registered}
    missing = []
    for p in _paths_fetched(html):
        if p in registered:
            continue
        if any(p == pre or p.rstrip("/") == pre.rstrip("/") for pre in prefixes):
            continue
        missing.append(p)
    assert not missing, (
        f"الصفحة تطلب مسارات غير مُسجَّلة: {missing}\nالمُسجَّل: {sorted(registered)}")


# ══════ الحارس الأهمّ: مفاتيح الردّ الحقيقية مقابل ما تقرؤه الصفحة ═══════════
def _root_key_the_page_reads(html: str, path: str) -> str | None:
    """المفتاح الجذري الذي تقرؤه الصفحة من ردّ هذا المسار — أو None.

    شكلان في الصفحة: قراءةٌ مباشرة `(await api("/x")).key`، أو عبر متغيّر
    (`out = await api("/users")` ثم `out.users`). نتعامل مع الاثنين كي لا يمرّ
    خللُ اسمٍ في أيٍّ منهما.
    """
    # طابِق على المسار الأساس بلا سلسلة الاستعلام: الاختبار قد يستعمل `?limit=5`
    # والصفحة `?limit=12` — والمقارنة الحرفية كانت تُرجع None فتُشخِّص خللاً
    # وهميّاً (وقع فعلاً في أوّل تشغيل لهذا الحارس).
    esc = re.escape(path.split("?")[0])
    m = re.search(rf'api\(\s*"{esc}(?:\?[^"]*)?"[^)]*\)\s*\)\s*\.\s*(\w+)', html)
    if m:
        return m.group(1)
    # عبر متغيّر: احصر النافذة على ما بعد النداء ثم خُذ أوّل `<var>.<key>`.
    m = re.search(rf'(\w+)\s*=\s*await\s+api\(\s*"{esc}(?:\?[^"]*)?"', html)
    if m:
        var = m.group(1)
        after = html[m.end():m.end() + 900]
        m2 = re.search(rf'\b{re.escape(var)}\s*\.\s*(\w+)\s*\|\|', after)
        if m2:
            return m2.group(1)
    return None


def test_page_reads_the_real_response_keys(monkeypatch, html):
    """كل حقلٍ تقرؤه الصفحة موجود في الردّ الفعلي — يقفل خلل `.ledger`/`entries`.

    اسمُ حقلٍ خاطئ يعرض قسماً فارغاً **بلا خطأ**، فلا اختبارُ نصٍّ يكفي ولا
    مراجعةُ عين. هنا نضرب النقاط فعلاً ونقارن.
    """
    seed(monkeypatch)
    f = make_factory("silver", "ui-keys@example.com", fund_cents=500)
    cl = client()
    tok = login(cl, f["email"], f["password"])

    # (المسار, مفتاح القائمة الجذري, الحقول التي تقرؤها الصفحة من كل عنصر)
    checks = [
        ("/wallet/ledger?limit=5", "entries",
         ("id", "operation_type", "amount", "balance_after", "created_at")),
        ("/studies", "studies",
         ("id", "title_en", "title_ar", "state", "target_count")),
        ("/users", "users",
         ("id", "email", "first_name", "last_name", "role", "is_active")),
    ]
    for path, root, item_fields in checks:
        body = cl.get(f"/platform{path}", headers=hdr(tok)).json()
        # (أ) الردّ يحمل المفتاح المتوقّع.
        assert root in body, (
            f"{path}: المتوقّع مفتاح `{root}` والردّ يحمل {list(body)}")
        assert isinstance(body[root], list)
        # (ب) **والصفحة تقرأ هذا المفتاح بعينه** — هذا هو الحارس الفعلي.
        #     الفحص (أ) وحده يختبر الـAPI لا الصفحة، فكان يمرّ ولو كتبت الصفحة
        #     `.ledger` بدل `.entries` (وهو الخلل الذي حدث فعلاً).
        page_key = _root_key_the_page_reads(html, path)
        assert page_key == root, (
            f"{path}: الصفحة تقرأ `{page_key}` والردّ يحمل `{root}` — "
            "قسمٌ سيظهر فارغاً صمتاً بلا أي خطأ")
        if body[root]:
            missing = [k for k in item_fields if k not in body[root][0]]
            assert not missing, f"{path}: حقول تقرؤها الصفحة وغائبة: {missing}"

    # حقول المحفظة والاستحقاقات المسطّحة.
    w = cl.get("/platform/wallet", headers=hdr(tok)).json()
    for k in ("balance", "lifetime_funded", "lifetime_spent", "delinquent"):
        assert k in w, f"/wallet: حقل تقرؤه الصفحة وغائب: {k}"
    ent = cl.get("/platform/entitlements", headers=hdr(tok)).json()
    for k in ("tier", "studies_limit", "studies_used", "studies_period",
              "seats_limit", "seats_used", "dashboard", "funnel", "export",
              "api_access", "white_label"):
        assert k in ent, f"/entitlements: حقل تقرؤه الصفحة وغائب: {k}"
    me = cl.get("/platform/me", headers=hdr(tok)).json()
    for k in ("email", "role"):
        assert k in me, f"/me: حقل تقرؤه الصفحة وغائب: {k}"


def test_page_renders_every_state_label_the_api_can_return(html):
    """كل حالة دراسة في مخطّط القاعدة لها ترجمة في الصفحة — لا حالة تظهر خاماً."""
    migration = (pathlib.Path(__file__).resolve().parent.parent /
                 "migrations" / "platform" / "001_platform_core.sql"
                 ).read_text(encoding="utf-8")
    m = re.search(r"state\s+TEXT NOT NULL DEFAULT 'draft'\s*CHECK \(state IN \(([^)]+)\)",
                  migration)
    assert m, "لم يُقرأ قيد حالات الدراسة من الترحيل"
    states = [s.strip().strip("'") for s in m.group(1).split(",")]
    for st in states:
        assert st in html, f"حالة `{st}` بلا ترجمة/تعامل في الصفحة"


def test_error_codes_the_gates_raise_have_arabic_messages(html):
    """أكواد بوّابات الحالة والمال لها رسائل عربية — المنتَج عربيّ أولاً.

    الترجمة على **الكود** لا على نصّ الخادم، فتبقى صحيحة لو أُعيدت صياغة النصّ.
    """
    for code in ("pending_emails", "in_flight_emails", "already_archived",
                 "invalid_transition", "insufficient_funds", "delinquent",
                 "tier_gate"):
        # حدُّ الكلمة ضروري: بلا `(?<![\w])` كان `XX_pending_emails:` يُشبِع
        # فحصَ `pending_emails:` (أُثبِت عملياً) — أي حارسٌ يُخدَع بإعادة تسمية.
        assert re.search(rf"(?<![\w]){code}\s*:", html), (
            f"كود بلا رسالة عربية: {code}")
