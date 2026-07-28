"""رُتبة ٣ — متصفّح حقيقي (rung 3 — the real-browser lane).

> **القاعدة الجديدة (تأكيد المالك آخِراً لا أوّلاً).** الرُتبة ٢ تُثبِت أن
> الخادم يخدم الـHTTP الصحيح؛ هذه الرُتبة تُثبِت أن **الواجهة الفعلية تعمل**:
> chromium (headless) ينقر الأزرار الحقيقية ضدّ خادم رُتبة ٢. التدفّق: افتح
> اللوحة ← انقر عنصر الشريط الجانبي ← يُعرَض التقرير ← تصدير Word (‏.docx غير
> فارغ) ← تصدير Markdown (محتوى حقيقي لا القالب الفارغ) ← صندوق التقدّم. هذا
> بالضبط كان سيلتقط **خطأَي التصدير** و**الشريط الجانبي الميت** قبل المالك.

يُقلِع الخادم الحقيقي (`tools/live_shape_server.LiveShapeServer`) ثم يشغّل
تدفّق Playwright (`tests/e2e/live_shape_flow.mjs`) عبر Node مع NODE_PATH يحلّ
حزمة playwright العمومية والمتصفّح من `/opt/pw-browsers`.

بيئة بلا Node/playwright: يُخطَّى بسبب صريح (أفضل جهد — نفس تساهل اختبارات
الواجهة عبر Node)، لكن في وظيفة `e2e-live-shape` كلاهما مثبّت فيعمل فعلياً.
يُتخطّى في `pytest tests/ -q` الافتراضية (SILK_RUN_E2E غير مضبوط).

Run locally:
    SILK_RUN_E2E=1 python3 -m pytest tests/test_rung3_playwright_e2e.py -q -s
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "tools"))

pytestmark = pytest.mark.e2e

_FLOW = os.path.join(_ROOT, "tests", "e2e", "live_shape_flow.cjs")
_PRERUN_FLOW = os.path.join(_ROOT, "tests", "e2e", "prerun_flow.cjs")
_READINESS_FLOW = os.path.join(_ROOT, "tests", "e2e", "readiness_flow.cjs")
_HS_CANDIDATES_FLOW = os.path.join(_ROOT, "tests", "e2e", "hs_candidates_flow.cjs")
_HS_TIER_FAMILY_FLOW = os.path.join(_ROOT, "tests", "e2e", "hs_tier_family_flow.cjs")
_HS_DIALOG_FLOW = os.path.join(_ROOT, "tests", "e2e",
                               "hs_dialog_official_flow.cjs")

# حالتا رُتبة ٣ لحوار المرشّحين — **فصلان مختلفان وبُعدا قياسٍ مختلفان** (شرط
# المُشرِف: لا على 0401 وحدها). المكتوب هنا صلباً هو **اسم المنتج والتوقّع**
# فقط؛ كلُّ ما يُؤكَّد في المتصفّح (الأشقّاء، حدود النطاق، مجموعة المرجع)
# يُشتَقّ لحظةَ التشغيل من `data/hs_reference.csv`.
_DIALOG_CASES = (
    {"product": "حليب طازج مبستر", "heading": "0401", "dimension": "fat",
     "anchor": "040110"},
    {"product": "شاي أخضر معبأ", "heading": "0902", "dimension": "weight",
     "anchor": "090210"},
)

# رموزٌ تُمنَع من نصّ الحوار: اسمُ المنتج المكتوب وعلامةٌ تجاريةٌ وبلدٌ — نثرُ
# الحوار وصفٌ رسميٌّ للبند، لا صدىً لِما كتبه التاجر (البند ٧٤).
_DIALOG_BANNED = ("نادك", "المراعي", "هولندا", "طازج", "معبأ")


def _dialog_cases_payload() -> tuple[str, str]:
    """يبني حمولةَ الحالات ومجموعةَ المرجع الرسميّ **من المرجع نفسه**."""
    import json

    import silk_hs_attributes as attrs
    import silk_hs_dialog as dialog
    import silk_hs_resolver as resolver

    cases = []
    for case in _DIALOG_CASES:
        anchor = case["anchor"]
        siblings = dialog.axis_siblings(anchor)
        assert len(siblings) >= 2, f"{anchor}: axis has no sibling to complete"
        edges: dict[str, list[str]] = {}
        for hs6 in siblings:
            band = attrs.band_of(hs6, resolver.official_description(hs6))
            assert band, f"{hs6}: no band parsed from the official description"
            unit = band.get("unit") or ""
            vals = [v for v in (band.get("lo"), band.get("hi")) if v is not None]
            # الحدُّ المتوقَّع مشتقٌّ من **المُحلِّل** (`band_of`) لا من المُصيِّر
            # المُختبَر (`range_ar`) — فالتأكيد ليس دائرياً.
            edges[hs6] = [
                f"{int(v) if float(v).is_integer() else v}{unit}" for v in vals]
        cases.append({
            "product": case["product"], "heading": case["heading"],
            "dimension": case["dimension"], "siblings": siblings,
            "edges": edges, "banned_tokens": list(_DIALOG_BANNED),
        })
    official = sorted(resolver.load_hs_reference())
    assert len(official) > 5000, f"reference looks truncated: {len(official)}"
    return json.dumps(cases, ensure_ascii=False), json.dumps(official)


def _node() -> str | None:
    return shutil.which("node")


# ── محرّكُ تحويل PDF: شرطُ بيئةٍ صريحٌ لا حُمرةٌ صامتة (PART 3) ───────────────
#
# التدفّقُ الكامل ينقر زرّ **PDF** (المُسلَّم النهائي، اللائحة ١٩) فيحتاج
# فلاتر Writer لقراءة .docx. صورةُ النشر (`Dockerfile`) ووظيفةُ
# `e2e-live-shape` تُثبّتان `libreoffice-writer`؛ بيئةُ تطويرٍ بلا الحزمة
# تُفشِل الخطوةَ بمهلةِ تنزيلٍ غامضة لا علاقة لها بالشيفرة — وهو بالضبط ما
# يجعل رُتبةً حمراءَ تُقرأ «ورقَ حائط» فتُتجاهَل. الحلُّ: **تخطٍّ مُسمّىً
# بسببه** بعد إثباتِ العطل على مستندٍ أدنى (لا على مستنداتنا)، فيبقى الفشلُ
# الحقيقيُّ أحمرَ ويبقى عطلُ البيئة معلَناً.
_MIN_DOCX_PROBE = "silk_rung3_pdf_probe"


def _docx_to_pdf_engine_works() -> bool:
    """هل تستطيع البيئةُ تحويل **أبسط** .docx إلى PDF؟ — مِجَسٌّ لا يمسّ
    شيفرتنا إطلاقاً: يفشل => العطلُ في الحزم لا في المستند."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return False
    import tempfile
    try:
        from docx import Document
    except Exception:  # noqa: BLE001
        return False
    d = tempfile.mkdtemp(prefix=_MIN_DOCX_PROBE)
    src = os.path.join(d, "probe.docx")
    doc = Document()
    doc.add_paragraph("probe")
    doc.save(src)
    profile = tempfile.mkdtemp(prefix=_MIN_DOCX_PROBE + "_lo")
    try:
        subprocess.run(
            [soffice, "--headless", "--norestore",
             f"-env:UserInstallation=file://{profile}",
             "--convert-to", "pdf", "--outdir", d, src],
            capture_output=True, timeout=120,
            env={**os.environ, "HOME": profile})
    except Exception:  # noqa: BLE001
        return False
    return os.path.exists(os.path.join(d, "probe.pdf"))


def _node_path() -> str | None:
    """جذر حزم npm العمومية — حيث تُثبَّت playwright في هذه البيئة/CI."""
    node = _node()
    if not node:
        return None
    try:
        out = subprocess.run(["npm", "root", "-g"], capture_output=True,
                             timeout=30)
        root = out.stdout.decode().strip()
        return root or None
    except Exception:  # noqa: BLE001
        return None


def _playwright_available(node_path: str | None) -> bool:
    node = _node()
    if not node or not node_path:
        return False
    env = dict(os.environ, NODE_PATH=node_path)
    r = subprocess.run(
        [node, "-e", "require('playwright');console.log('ok')"],
        capture_output=True, env=env, timeout=30)
    return r.returncode == 0


def test_rung3_full_browser_flow_word_and_md_export_and_sidebar():
    """التدفّق الكامل عبر متصفّح حقيقي — يجب أن يخرج سكربت Playwright بـ0 ويطبع
    RUNG3 PASS بعد اجتياز كل خطوة (لوحة/تقدّم/نقر جانبي/Word/Markdown)."""
    node = _node()
    if not node:
        pytest.skip("node غير متاح في هذه البيئة (أفضل جهد؛ وظيفة CI تثبّته)")
    node_path = _node_path()
    if not _playwright_available(node_path):
        pytest.skip("حزمة playwright غير محلولة عبر NODE_PATH "
                    "(أفضل جهد؛ وظيفة e2e-live-shape تثبّتها)")

    # PART 3: التدفّقُ ينقر زرّ PDF — بلا فلاتر Writer يفشل التنزيل لسببٍ
    # بيئيٍّ بحت. أثبِتْه على مستندٍ أدنى ثم تخطَّ **بسببٍ مُسمّى** بدل
    # ترك رُتبةٍ حمراءَ صامتة. وظيفةُ `e2e-live-shape` تُثبّت الحزمة فتعمل.
    if not _docx_to_pdf_engine_works():
        pytest.skip("PDF_ENGINE_MISSING: محرّكُ docx→PDF غير مكتمل في هذه "
                    "البيئة (فشل تحويلُ مستندٍ أدنى لا يمسّ شيفرتنا — حزمةُ "
                    "libreoffice-writer غائبة). الخطوةُ تعمل على وظيفة "
                    "e2e-live-shape وعلى صورة النشر (اللائحة ١٩).")

    from live_shape_server import LiveShapeServer
    with LiveShapeServer() as srv:
        env = dict(
            os.environ,
            NODE_PATH=node_path or "",
            BASE_URL=srv.base_url,
            COMPLETED_ID=str(srv.completed_id),
            RUNNING_ID=str(srv.running_id),
        )
        # المتصفّح يُورَّث من البيئة كما هي: هذه البيئة تصدّر
        # PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers مسبقاً؛ وعلى CI يضعه
        # `playwright install chromium` في مخبأه الافتراضي. لا نفرض مساراً
        # هنا كي لا نُخطئ الموضع في أيّ من البيئتين.
        r = subprocess.run([node, _FLOW], capture_output=True, env=env,
                           timeout=180)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 0, (
            f"Playwright flow failed (rc={r.returncode}).\n"
            f"STDOUT:\n{out}\nSTDERR:\n{err}")
        assert "RUNG3 PASS" in out, f"missing PASS marker.\nSTDOUT:\n{out}"


def test_rung3_prerun_modals_flow_classify_confirm_advisory_consent():
    """الشرط المُلزِم (Wave 1): متصفّح حقيقي ينقر نوافذ ما قبل التشغيل الجديدة —
    اختيار منتج/سوق ← «بحث عميق» ← نافذة تصنيف HS ← تأكيد ← نافذة استشارة بلد
    المنشأ ← موافقة صريحة ← إعادة إرسال بالموافقة. الأعلام مُفعَّلة في الخادم
    فقط (prerun_flags)، والاستشارة تُطلق من مخبأ تصدير مبذور (بلا شبكة/مفتاح/
    soffice). يخرج السكربت بـ0 ويطبع PRERUN PASS بعد كل خطوة."""
    node = _node()
    if not node:
        pytest.skip("node غير متاح في هذه البيئة (أفضل جهد؛ وظيفة CI تثبّته)")
    node_path = _node_path()
    if not _playwright_available(node_path):
        pytest.skip("حزمة playwright غير محلولة عبر NODE_PATH "
                    "(أفضل جهد؛ وظيفة e2e-live-shape تثبّتها)")

    from live_shape_server import LiveShapeServer
    with LiveShapeServer(prerun_flags=True) as srv:
        env = dict(
            os.environ,
            NODE_PATH=node_path or "",
            BASE_URL=srv.base_url,
            PRERUN_PRODUCT=srv.PRERUN_PRODUCT,
            PRERUN_HS=srv.PRERUN_HS,
            PRERUN_MARKET_ISO3=srv.PRERUN_MARKET_ISO3,
        )
        r = subprocess.run([node, _PRERUN_FLOW], capture_output=True, env=env,
                           timeout=180)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 0, (
            f"Prerun Playwright flow failed (rc={r.returncode}).\n"
            f"STDOUT:\n{out}\nSTDERR:\n{err}")
        assert "PRERUN PASS" in out, f"missing PASS marker.\nSTDOUT:\n{out}"


def test_rung3_readiness_panel_flow_checklist_before_confirm():
    """عائلة D (Wave 1.5): متصفّح حقيقي يرى لوحة «جاهزية الدراسة» — كلُّ تدهورٍ
    كسطر ✓/⚠/✗ قبل زرّ التأكيد — ثم «أكمل الدراسة» يرسل الموافقات الموحّدة.
    الخادم في وضع readiness_panel (SILK_PRERUN_ADVISORIES + مخبأ مبذور)."""
    node = _node()
    if not node:
        pytest.skip("node غير متاح (أفضل جهد؛ وظيفة CI تثبّته)")
    node_path = _node_path()
    if not _playwright_available(node_path):
        pytest.skip("حزمة playwright غير محلولة عبر NODE_PATH (أفضل جهد)")

    from live_shape_server import LiveShapeServer
    with LiveShapeServer(readiness_panel=True) as srv:
        env = dict(
            os.environ,
            NODE_PATH=node_path or "",
            BASE_URL=srv.base_url,
            PRERUN_PRODUCT=srv.PRERUN_PRODUCT,
            PRERUN_HS=srv.PRERUN_HS,
            PRERUN_MARKET_ISO3=srv.PRERUN_MARKET_ISO3,
        )
        r = subprocess.run([node, _READINESS_FLOW], capture_output=True,
                           env=env, timeout=180)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 0, (
            f"Readiness Playwright flow failed (rc={r.returncode}).\n"
            f"STDOUT:\n{out}\nSTDERR:\n{err}")
        assert "READINESS PASS" in out, f"missing PASS marker.\nSTDOUT:\n{out}"


def test_rung3_hs_candidates_dialog_blocks_on_flagged_product_and_never_auto_badges():
    """الموجة ٣ (المصنّف العام، systemic fix): متصفّح حقيقي ينقر صندوق حوار
    مرشّحي HS لمنتجٍ غير معتادٍ مُعلَّمٍ فعلاً («عود معطر»، عائلة حادثة
    الكويت LESSONS ٣٩) — صندوقٌ حاجبٌ بمرشّحين فعليّين، صفر «✓ صُنّف
    تلقائياً» على منتجٍ غير محسوم، واختيار مرشّحٍ يُتابع التدفّق فعلياً.

    (بطل الحادثة الأصلية «زبدة الفول السوداني» عاد يُحسَم تلقائياً للرمز
    الصحيح 200811 بعد إصلاح البذرة — طلب المالك 2026-07-23؛ قفلُه صار حالةَ
    auto في `hs_tier_family_flow.cjs`.)

    **بلا مفتاح كلود** (يُنزَع عمداً في كل e2e) — المرشّحون هنا حتميّون
    (بذرة CSV) لا عامّون بمساعدة نموذج؛ إثبات المسار العام الحيّ هو بوّابة
    المالك المدفوعة (LAW §2)، لا هذا الاختبار."""
    node = _node()
    if not node:
        pytest.skip("node غير متاح في هذه البيئة (أفضل جهد؛ وظيفة CI تثبّته)")
    node_path = _node_path()
    if not _playwright_available(node_path):
        pytest.skip("حزمة playwright غير محلولة عبر NODE_PATH "
                    "(أفضل جهد؛ وظيفة e2e-live-shape تثبّتها)")

    from live_shape_server import LiveShapeServer
    with LiveShapeServer(prerun_flags=True) as srv:
        env = dict(
            os.environ,
            NODE_PATH=node_path or "",
            BASE_URL=srv.base_url,
            PRERUN_MARKET_ISO3=srv.PRERUN_MARKET_ISO3,
        )
        r = subprocess.run([node, _HS_CANDIDATES_FLOW], capture_output=True,
                           env=env, timeout=180)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 0, (
            f"HS-candidates Playwright flow failed (rc={r.returncode}).\n"
            f"STDOUT:\n{out}\nSTDERR:\n{err}")
        assert "HSCAND PASS" in out, f"missing PASS marker.\nSTDOUT:\n{out}"


def test_rung3_hs_dialog_renders_only_official_codes_complete_siblings_and_bands():
    """البند ٧٢–٧٤ في متصفّحٍ حقيقي: حوارُ المرشّحين على **فصلين مختلفين**
    وببُعدَي قياسٍ مختلفين — «حليب طازج مبستر» (الفصل ٠٤، بُعد `fat`) و«شاي
    أخضر معبأ» (الفصل ٠٩، بُعد `weight`) — يُصيَّر عبر مسار المستخدم الحقيقي
    (خطّاف المنتج ← سوق ← «بحث عميق» ← `/classify_hs` ← `showHsCandidates`)،
    ثم يُقرأ الـDOM ويُؤكَّد أربعُ خصائصٍ مشتقّةٍ من `data/hs_reference.csv`:
    لا رمزَ خارج المرجع، أشقّاءُ المحور كاملون، حدودُ النطاق الرسميّة حاضرةٌ
    نصّاً، ولا صدىً لاسم منتجٍ/علامةٍ/بلدٍ في نثر الحوار.

    الحادثة التي يقفلها: الحوارُ كان يعرض نثرَ نموذجٍ (حدوداً تناقض المرجع)
    ورمزاً لا وجود له في المرجع ولا في البذرة، ومجموعةً مقتطعةً إلى ثلاثة —
    فيخرج التاجرُ برمزٍ جمركيٍّ **خاطئ بلا أيّ إشارة**."""
    node = _node()
    if not node:
        pytest.skip("node غير متاح في هذه البيئة (أفضل جهد؛ وظيفة CI تثبّته)")
    node_path = _node_path()
    if not _playwright_available(node_path):
        pytest.skip("حزمة playwright غير محلولة عبر NODE_PATH "
                    "(أفضل جهد؛ وظيفة e2e-live-shape تثبّتها)")

    cases_json, official_json = _dialog_cases_payload()
    from live_shape_server import LiveShapeServer
    with LiveShapeServer(prerun_flags=True) as srv:
        env = dict(
            os.environ,
            NODE_PATH=node_path or "",
            BASE_URL=srv.base_url,
            PRERUN_MARKET_ISO3=srv.PRERUN_MARKET_ISO3,
            DIALOG_CASES=cases_json,
            OFFICIAL_CODES=official_json,
        )
        r = subprocess.run([node, _HS_DIALOG_FLOW], capture_output=True,
                           env=env, timeout=240)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 0, (
            f"HS-dialog Playwright flow failed (rc={r.returncode}).\n"
            f"STDOUT:\n{out}\nSTDERR:\n{err}")
        assert "HSDIALOG PASS" in out, f"missing PASS marker.\nSTDOUT:\n{out}"


def test_rung3_ui_tier_consumption_locked_across_product_families():
    """قفلٌ صريحٌ طلبه المُشرِف («UI-ONLY FIX»، الموجة ٤): عبر ست عائلات
    منتجاتٍ حقيقية دفعةً واحدة — «مياه ورد»/«عود معطر»/«زيت زيتون» تُظهِر
    صندوق حوار المرشّحين ولا تُظهِر «✓ صُنّف تلقائياً» إطلاقاً؛ «زبدة الفول
    السوداني» (بعد إصلاح البذرة 2026-07-23 تُحسَم للرمز الصحيح 200811)
    و«تمر سكري»/«عسل سدر» تُظهِر الشارة مباشرةً بلا صندوق حوار. يغطّي نفس
    نقطة الاختناق (`ensureHs`) من مسار الاختيار عبر خطّاف
    الاختبار — الفارق عن `hs_candidates_flow.cjs` أنّ هذا يُثبِت **التعميم**
    (عائلات متعددة متتالية على نفس الجلسة) لا مثالاً واحداً.

    **بلا مفتاح كلود** (يُنزَع عمداً في كل e2e، LAW §2 — الدلو الثاني فقط):
    التصنيف حتميٌّ (بذرة CSV). «زيت زيتون» في دليل المالك الحيّ (بمفتاح
    فعليّ) يُحسَم تلقائيًا؛ هنا بلا مفتاح يعطي ٣ مرشّحين متقاربين فيسقط في
    صندوق الحوار عوضًا — سلوكٌ فشل-آمنٌ سليم، موثَّقٌ صراحةً داخل السكربت،
    لا انحرافًا عن الطلب."""
    node = _node()
    if not node:
        pytest.skip("node غير متاح في هذه البيئة (أفضل جهد؛ وظيفة CI تثبّته)")
    node_path = _node_path()
    if not _playwright_available(node_path):
        pytest.skip("حزمة playwright غير محلولة عبر NODE_PATH "
                    "(أفضل جهد؛ وظيفة e2e-live-shape تثبّتها)")

    from live_shape_server import LiveShapeServer
    with LiveShapeServer(prerun_flags=True) as srv:
        env = dict(
            os.environ,
            NODE_PATH=node_path or "",
            BASE_URL=srv.base_url,
            PRERUN_MARKET_ISO3=srv.PRERUN_MARKET_ISO3,
        )
        r = subprocess.run([node, _HS_TIER_FAMILY_FLOW], capture_output=True,
                           env=env, timeout=240)
        out = r.stdout.decode("utf-8", "replace")
        err = r.stderr.decode("utf-8", "replace")
        assert r.returncode == 0, (
            f"HS tier-family Playwright flow failed (rc={r.returncode}).\n"
            f"STDOUT:\n{out}\nSTDERR:\n{err}")
        assert "HSTIERFAMILY PASS" in out, f"missing PASS marker.\nSTDOUT:\n{out}"
