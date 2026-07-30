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


def _without_comments(src: str) -> str:
    """الصفحة بلا شروح — ما يراه المستخدم فعلاً، لا ما يشرحه المطوّر.

    يلزم للفحوص التي تمنع **نصّاً معروضاً**: ذكرُ العبارة الممنوعة في تعليقٍ
    يشرح سببَ إزالتها كان يُحمِّر الحارسَ على شيفرةٍ سليمة، وحارسٌ يُنذِر كاذباً
    يُدرَّب المرءُ على تجاهله.
    """
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)      # شروح HTML
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)       # شروح CSS/JS الكتلية
    return re.sub(r"(?m)^\s*//.*$", " ", src)              # شروح JS السطرية


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


# ══════ الحارس الذي كان غائباً فمرّت صفحةٌ ميّتة تماماً ═══════════════════════
# **الحادثة:** كتبتُ سلسلةً تفتح بـ`"` وتُغلق بـ`'`، فالتقط المُحلِّلُ بقيّةَ
# النصّ داخل سلسلةٍ لا تنتهي ⇒ **خطأ صياغة يقتل كل السكربت**: لا زرّ يعمل، ولا
# حتى الدخول. ومع ذلك **مرّت كل حُرّاس هذا الملف** — لأنها تُطابِق نصوصاً، وكلُّ
# النصوص المطلوبة كانت حاضرة في ملفٍ لا يعمل. التقطه المتصفّح في رُتبة ٣ فقط.
# القاعدة: حارسُ نصٍّ لا يُثبِت أن الصفحة **تعمل**؛ يلزم فحصُ تحليلٍ فعليّ.
def _script_body(html: str) -> str:
    m = re.search(r"<script>(.*)</script>", html, re.S)
    assert m, "لا كتلة سكربت في الصفحة"
    return m.group(1)


def test_the_page_script_parses_with_a_real_js_engine(html):
    """تحليلٌ فعليّ بـnode — لا مطابقةُ نصّ.

    **لماذا مُحلِّلٌ حقيقيّ ولا شيء أقلّ:** كتبتُ أوّلاً ماسحَ سلاسل هرمتيّاً
    (يتعقّب الاقتباسات بلا أدوات خارجية) فأنذر **كاذباً** على شيفرةٍ سليمة:
    السلسلةُ الحرفية `/[&<>"']/g` في `esc()` تحمل اقتباسات داخل **تعبيرٍ نمطيّ**،
    ولا يُميّزها عن السلسلة إلا محلّلٌ كامل (تمييزُ القسمة من النمط يحتاج تحليلاً
    نحويّاً لا مسحاً). وحارسٌ يُنذِر كاذباً يُدرَّب المرءُ على تجاهله، فحُذِف.

    **الحدّ المُعلَن بصراحة:** يُتخطّى إن غاب node، فالحزمة الهرمتية وحدها **لا
    تُثبِت** أن الصفحة تُحلَّل في تلك البيئة. المُعوِّض أن عُقدة CI تحمل node،
    وأن رُتبة ٣ (متصفّح حقيقي) تُسقِط أيّ سكربت ميّت فوراً — وهي من التقطت
    الحادثة أصلاً.
    """
    import shutil
    import subprocess
    import tempfile
    node = shutil.which("node") or next(
        (p for p in ("/opt/node22/bin/node", "/usr/bin/node",
                     "/usr/local/bin/node") if pathlib.Path(p).exists()), None)
    if not node:
        pytest.skip("node غير متوفّر — الماسح الهرمتيّ يبقى هو الحارس")
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "page.js"
        f.write_text(_script_body(html), encoding="utf-8")
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, f"سكربت الصفحة لا يُحلَّل:\n{r.stderr[:1200]}"


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
    # التفكيك من `Promise.all` — `const [a, b] = await Promise.all([api("/x"), …])`
    # ثم `a.key`. بلا هذا الشكل كان المُساعِد يرجع `None` على شيفرةٍ **سليمة**،
    # وهي نقطةٌ عمياء أخطر من الفشل: مفتاحٌ خاطئ في نداءٍ مُفكَّك يمرّ بلا حارس.
    for m in re.finditer(r"\[([^\]]+)\]\s*=\s*await\s+Promise\.all\(\s*\[(.*?)\]\s*\)",
                         html, re.S):
        names = [n.strip() for n in m.group(1).split(",")]
        calls = re.findall(r'api\(\s*"([^"]+)"', m.group(2))
        for idx, call in enumerate(calls):
            if call.split("?")[0] != path.split("?")[0] or idx >= len(names):
                continue
            var = names[idx]
            after = html[m.end():m.end() + 900]
            m2 = re.search(rf'\b{re.escape(var)}\s*\.\s*(\w+)\b', after)
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

    # املأ صفّاً واحداً في كل مجموعة كي تُفحَص **حقول العنصر** لا القائمة الفارغة:
    # قائمةٌ فارغة تجعل فحص الحقول لا-عمليّاً فيبدو أخضر بلا أن يقيس شيئاً.
    cl.post("/platform/drafts", headers=hdr(tok),
            json={"subject_ar": "موضوع", "body_ar": "نصّ"})
    cl.post("/platform/prospects", headers=hdr(tok),
            json={"email": "keys@buyer.example", "first_name": "A",
                  "company": "C", "industry": "I"})
    cl.post("/platform/smtp-configs", headers=hdr(tok),
            json={"label": "main", "host": "smtp.example.com", "port": 587,
                  "from_email": "f@example.com", "is_active": True})
    cl.post("/platform/studies", headers=hdr(tok),
            json={"title_ar": "دراسة", "target_count": 1})

    # (المسار, مفتاح القائمة الجذري, الحقول التي تقرؤها الصفحة من كل عنصر)
    checks = [
        ("/wallet/ledger?limit=5", "entries",
         ("id", "operation_type", "amount", "balance_after", "created_at")),
        ("/studies", "studies",
         ("id", "title_en", "title_ar", "state", "target_count")),
        ("/users", "users",
         ("id", "email", "first_name", "last_name", "role", "is_active")),
        # المجموعات التي أضافتها موجة الواجهة العاملة — لكلٍّ منها جدولٌ في
        # الصفحة، فاسمُ مفتاحٍ خاطئ يُفرِّغه صمتاً كما فرّغ الدفترَ سابقاً.
        ("/drafts", "drafts",
         ("id", "subject_ar", "subject_en", "study_id", "version")),
        ("/prospects", "prospects",
         ("id", "email", "first_name", "last_name", "company")),
        ("/smtp-configs", "smtp_configs", ("id", "label", "host", "is_active")),
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


def test_admin_panel_reads_the_real_admin_response_keys(monkeypatch, html):
    """مفاتيح لوحة الأدمِن حقيقية أيضاً — `accounts` و`on` لا اسمٌ مُخترَع."""
    info = seed(monkeypatch)
    make_factory("gold", "ui-admin-keys@example.com")
    cl = client()
    tok = login(cl, info["admin"]["email"], info["admin"]["password"])
    accts = cl.get("/platform/admin/accounts", headers=hdr(tok)).json()
    assert _root_key_the_page_reads(html, "/admin/accounts") == "accounts"
    assert "accounts" in accts and accts["accounts"]
    for k in ("id", "name", "tier", "seats_used", "seats_limit", "is_active"):
        assert k in accts["accounts"][0], f"/admin/accounts: حقل غائب: {k}"
    kill = cl.get("/platform/admin/kill-switch", headers=hdr(tok)).json()
    assert "on" in kill, f"/admin/kill-switch: المتوقّع `on`، وُجد {list(kill)}"
    assert "kill.on" in html, "الصفحة لا تقرأ حالة مفتاح الإيقاف"


# ══════ الحارس الذي يحمل بلاغ المالك: «كيف أستعمل؟ لا يوجد أيّ خيار» ══════════
# الشاشة الأولى كانت قارئةً فقط: ٢٩ نقطةَ كتابة في الـAPI مقابل **زرَّي** دخول
# وخروج. فبدت مستنداً لا أداة. هذا الحارس يمنع الانحدار إلى تلك الحالة: كلُّ فعلٍ
# يقوم عليه المنتَج يجب أن يبقى **قابلاً للنقر** من الصفحة.
_REQUIRED_WRITES = [
    ('"/studies"', "إنشاء دراسة"),
    ('"/drafts"', "إنشاء نصّ رسالة"),
    ('"/prospects"', "إضافة عميل محتمل"),
    ('"/smtp-configs"', "إعداد بريد الإرسال"),
    ('"/users"', "إضافة مستخدم فرعي"),
    ('/launch', "إطلاق دراسة"),
    ('/report', "إصدار تقرير"),
    # الأرشفة والإنهاء تُبنيان كـ`"/studies/" + id + "/" + action`، فالفعلُ يمرّ
    # وسيطاً لا حرفياً في المسار — فيُفحَص الوسيط نفسه. (الفحص على `/archive`
    # كان يفشل على شيفرةٍ **سليمة**: حارسٌ يُنذِر كاذباً يُدرَّب المرءُ على تجاهله.)
    ('actBtn("أرشفة", s.id, "archive"', "أرشفة"),
    ('actBtn("إنهاء", s.id, "complete"', "إنهاء"),
    ('"/admin/fund"', "تمويل محفظة مصنع"),
    ('"/admin/kill-switch"', "مفتاح إيقاف الإرسال"),
    ('/tier', "تغيير الطبقة"),
]


@pytest.mark.parametrize("needle,label", _REQUIRED_WRITES,
                         ids=[l for _n, l in _REQUIRED_WRITES])
def test_the_page_still_exposes_this_action(html, needle, label):
    """كل فعلٍ من هذه القائمة له نداءٌ في الصفحة — وإلا عادت لوحةَ عرضٍ صامتة."""
    assert needle in html, (
        f"لا سبيل إلى «{label}» من الصفحة — الفعل موجود في الـAPI وغائب عن "
        "الواجهة، وهو بعينه بلاغ «لا يوجد أيّ خيار»")


def test_a_write_call_uses_a_writing_http_method(html):
    """الأفعال تُنادى بـPOST فعلاً — لا نداءُ قراءةٍ يبدو زرّ فعل.

    بلا هذا الفحص كان يكفي أن يُذكَر المسار نصّاً ليمرّ الحارسُ أعلاه، فيبدو
    الزرُّ موجوداً وهو لا يكتب شيئاً.
    """
    for needle in ('"/studies"', '"/drafts"', '"/prospects"', '"/smtp-configs"',
                   '"/users"', '"/admin/fund"', '"/admin/kill-switch"'):
        # نافذةٌ بعد النداء تكفي لظهور method: "POST" في نفس الاستدعاء.
        idx = 0
        found = False
        while True:
            idx = html.find("api(" + needle, idx + 1)
            if idx < 0:
                break
            if 'method: "POST"' in html[idx:idx + 260]:
                found = True
                break
        assert found, f"{needle}: لا نداء POST — الزرّ لا يكتب شيئاً"


def test_launch_sends_both_a_draft_and_prospects(html):
    """الإطلاق يُرسِل `draft_id` **و**`prospect_ids` — وإلا صفَّ صفر بريد.

    الخادم يصفّ البريد فقط داخل `if draft_id and prospect_ids`. فزرُّ إطلاقٍ
    يُرسِل أحدهما ينجح ظاهرياً، ويستهلك الحصّة، ويُخرِج **صفر** رسالة — نجاحٌ
    يفعل أقلّ مما يُظهِر. الصفحة تُلزِم الاثنين بنيوياً، وهذا يقفلها على ذلك.
    """
    m = re.search(r'api\("/studies/"[^;]*?/launch"[^;]*?body:\s*JSON\.stringify\(\{([^}]*)\}',
                  html, re.S)
    assert m, "لم يُوجد نداء الإطلاق بجسمٍ مُهيَّأ في الصفحة"
    payload = m.group(1)
    for key in ("draft_id", "prospect_ids"):
        assert key in payload, (
            f"جسم الإطلاق بلا `{key}` — سيصفّ صفر بريد بلا أيّ خطأ ظاهر")


def test_the_page_never_calls_a_missing_feature_unavailable(html):
    """«غير متاح» اختفت لصالح «في الباقة …» — بلاغُ مالك: تُقرأ «مكسور».

    الطبقة silver كانت تُظهِر أربعة صفوف «غير متاح» والمصفوفة صحيحة
    (`models.TIER_LIMITS`: الثلاثة في platinum فقط) — فالعيب عرضيٌّ: النصّ يجب
    أن يسمّي الباقة التي تفتح الميزة لا أن يبدو عطلاً.
    """
    # يُفحَص **ما يُعرَض** لا الشروح: الفحص الأوّل كان يُحمِّر على ذكرِ العبارة
    # داخل تعليقٍ يشرح سببَ إزالتها — إنذارٌ كاذب على شيفرةٍ سليمة.
    visible = _without_comments(html)
    assert "غير متاح" not in visible, (
        "«غير متاح» تُقرأ «مكسور» بدل «ليست في خطّتك» — سمِّ الباقة التي تفتحها")
    assert "في الباقة " in visible, "لا نصّ ترقية يسمّي الباقة"


# ══════ القائمة الجانبية · the sidebar (بلاغ مالك: «المفروض قائمة جانبية») ════
def test_every_sidebar_section_points_at_a_panel_that_exists(html):
    """كل مدخلٍ في `SECTIONS` يشير إلى لوحٍ موجود — لا مدخلَ يفتح فراغاً.

    القائمة تُبنى من جدولٍ واحد، فمِعرَّفٌ مكتوبٌ خطأً يُنتِج زرّاً يُبدِّل إلى
    **لا شيء** بلا أيّ خطأ في وحدة التحكّم: القسم لا يظهر والسابق يختفي. وهذا
    عيبٌ لا تلتقطه مراجعةُ عين.
    """
    m = re.search(r"const SECTIONS = \[(.*?)\n\];", html, re.S)
    assert m, "لم يُوجد جدول الأقسام `SECTIONS`"
    panels = re.findall(r'panel:\s*"(\w+)"', m.group(1))
    assert len(panels) >= 7, f"عدد الأقسام أقلّ من المتوقّع: {panels}"
    # شريط المقاييس سياقٌ دائم لا قسماً — فلا يجوز أن يعود إلى الجدول.
    assert "factoryStats" not in panels, (
        "شريط المقاييس عاد قسماً يُبدَّل؛ وهو سياقٌ دائم (والقسم كان يُفكِّك شبكته)")
    for pid in panels:
        assert f'id="{pid}"' in html, f"القسم يشير إلى لوحٍ غير موجود: {pid}"


def test_every_panel_in_the_page_is_reachable_from_the_sidebar(html):
    """والعكس: لوحٌ في الصفحة بلا مدخلٍ في القائمة = محتوىً لا سبيل إليه.

    هذا الاتجاه هو الذي يُنتِج بلاغ «لا يوجد أيّ خيار» من جديد: القسم موجود
    ومحمَّل ولا زرَّ يُظهِره.
    """
    m = re.search(r"const SECTIONS = \[(.*?)\n\];", html, re.S)
    listed = set(re.findall(r'panel:\s*"(\w+)"', m.group(1)))
    in_page = set(re.findall(r'<section class="panel sect" id="(\w+)"', html))
    orphans = in_page - listed
    assert not orphans, f"ألواحٌ لا يفتحها أيّ مدخل في القائمة: {sorted(orphans)}"


def test_the_sidebar_hides_admin_sections_from_a_factory(html):
    """أقسام الأدمِن لا تُبنى لمصنع — التصفية بالدور لا بإخفاءٍ بصريّ.

    زرٌّ مخفيٌّ بـCSS يبقى في الشجرة ويُنقَر برمجياً؛ التصفية عند البناء تمنع
    وجوده أصلاً.
    """
    assert "function visibleSections" in html, "لا تصفية للأقسام بالدور"
    assert 'x.role === "silk_admin"' in html


def test_the_page_is_usable_at_a_phone_width(html):
    """استجابةٌ عند ٣٧٥px: القائمة تنطوي، والبطاقات تتراصف، ولا تمريرَ أفقيّ.

    (معيارُ قبولٍ صريح في الأمر المُعدَّل §8.1(1). التحقّق البصريّ الفعليّ يجري
    في رُتبة ٣ بلقطة عند ٣٧٥px؛ هذا يقفل وجودَ القواعد نفسها.)
    """
    assert "@media(max-width:820px)" in html, "لا استعلامَ وسائط للجوّال"
    mobile = html[html.index("@media(max-width:820px)"):][:600]
    assert "translateX(100%)" in mobile, "القائمة لا تنطوي على الجوّال"
    assert "grid-template-columns:1fr" in mobile, "البطاقات لا تتراصف"
    # الجداول تُمرَّر داخل حاوٍ لا تُمرِّر الصفحة أفقياً.
    assert ".tbl{width:100%;overflow-x:auto}" in html


def test_the_admin_and_factory_toolbars_are_separated(html):
    """شريطُ الأدمِن لا يظهر لمصنع ولا العكس — نقاط الأدمِن ترفض 403 أصلاً.

    زرٌّ يظهر ثم يُرفَض 403 تجربةٌ سيّئة، والأسوأ أنه يُلبِس المستأجرَ حدودَ دوره.
    """
    assert 'id="adminBar"' in html and 'id="factoryBar"' in html
    assert 'ME.role === "silk_admin"' in html, "لا تفريقَ بالدور في الصفحة"


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
