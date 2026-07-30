"""فحص دخان لصفحة لوحة المنصّة — رُتبتا ٢+٣ (خادم حقيقي + متصفّح حقيقي).

**لماذا يوجد:** الحزمة الهرمتية تُثبِت العقود ولا تُقلِع خادماً ولا متصفّحاً.
وستّ موجات من المنصّة شُحنت بلا أي دليل رُتبة ٢/٣ لأن **لا شيء كان له شاشة**؛
هذه الصفحة أوّل سطحٍ يُنقَر، فيلزمها دليلٌ من مقاسها.

يُقلِع `uvicorn api:app` على قاعدة منصّة معزولة مبذورة، ثم يقود chromium فعلياً:
دخول ← لوحة بأرقام حقيقية ← «إنهاء» يُرفَض ببريد معلّق ← «أرشفة» تُلغي المصفوف.

    python3 tools/platform_ui_smoke.py                 # يُقلِع خادمه ويُنهيه
    python3 tools/platform_ui_smoke.py --base http://127.0.0.1:8000   # خادم قائم

**لا يُشغَّل هيرمتياً** ولا في `pytest tests/` — يحتاج منفذاً وchromium. لقطات
الشاشة تُكتَب في `--shots` (الافتراضي مجلّد مؤقّت) لتُرفَق كدليل.

Rung 2+3 smoke: boots a real server on a seeded isolated DB and drives chromium.
NOT hermetic — never collected by pytest.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

_ROOT = pathlib.Path(__file__).resolve().parent.parent
# chromium المُثبَّت مسبقاً في هذه البيئة؛ `playwright install` ممنوع هنا.
_CHROMIUM_CANDIDATES = ("/opt/pw-browsers/chromium",
                        os.environ.get("SILK_CHROMIUM_PATH", ""))
PASSWORD = "SmokeOwner1234"
ADMIN_PASSWORD = "SmokeAdmin1234"


def _chromium() -> str | None:
    for c in _CHROMIUM_CANDIDATES:
        if c and pathlib.Path(c).exists():
            return c
    return None


def _seed(db_path: str) -> str:
    """ابذر قاعدة معزولة: حساب مصنع + دراستان (إحداهما ببريد مصفوف) + محفظة."""
    os.environ["SILK_PLATFORM_DB"] = db_path
    os.environ.setdefault("SILK_PLATFORM_SECRET", secrets.token_hex(32))
    os.environ["SILK_PLATFORM_BCRYPT_ROUNDS"] = "4"   # اختبار فقط
    os.environ["SILK_SEED_FACTORY_A_PASSWORD"] = PASSWORD
    # نصفُ الأدمِن يُقاد أيضاً (تمويل/طبقة/إيقاف)، فكلمته صريحة لا مولَّدة.
    os.environ["SILK_SEED_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    sys.path.insert(0, str(_ROOT))
    from silk_platform import db as pdb, seed as pseed, wallet
    from silk_platform.db import now_iso
    from silk_platform.models import Operation
    pdb.init_db(db_path, force=True)
    conn = pdb.connect(db_path)
    try:
        info = pseed.seed(conn)
        fa, fu = info["factory_a"]["account_id"], info["factory_a"]["user_id"]
        now = now_iso()
        for state, title in (("in_progress", "حملة تمور — هولندا"),
                             ("draft", "حملة عسل — بريطانيا")):
            sid = conn.execute(
                "INSERT INTO studies (owner_id, title_ar, state, target_count, "
                "created_by_user_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (fa, title, state, 12, fu, now, now)).lastrowid
            if state == "in_progress":       # بريدٌ معلّق ⇒ «إنهاء» يجب أن يُرفَض
                for i in range(4):
                    conn.execute(
                        "INSERT INTO email_queue (account_id, study_id, "
                        "prospect_email, subject, body, status, queued_at) "
                        "VALUES (?,?,?,'S','B','queued',?)",
                        (fa, sid, f"q{i}@example.com", now))
        conn.commit()
        wallet.ensure_wallet(conn, fa)
        wallet.post_entry(conn, account_id=fa, actor_user_id=fu,
                          operation=Operation.WALLET_FUNDED, amount=5000,
                          description="smoke funding")
        wallet.post_entry(conn, account_id=fa, actor_user_id=fu,
                          operation=Operation.EMAIL_SENT, amount=-15,
                          description="email sent")
        return info["factory_a"]["email"]
    finally:
        conn.close()


def _wait_up(base: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/health", timeout=3).read()
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise SystemExit("الخادم لم يُقلِع في الوقت المتاح")


def drive(base: str, email: str, shots: pathlib.Path) -> None:
    """قُد المتصفّح عبر التدفّق كاملاً — يرفع AssertionError عند أي انحراف."""
    from playwright.sync_api import sync_playwright
    exe = _chromium()
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 1600})
        # استثناءات JS الحقيقية فقط؛ حالاتُ HTTP المتوقّعة (401 مِجَسّ الجلسة،
        # 409 بوّابة الحالة، 404 favicon) ضجيجُ وحدةِ تحكّم لا خطأ صفحة.
        js_errors: list[str] = []
        pg.on("pageerror", lambda e: js_errors.append(str(e)))

        pg.goto(base + "/platform.html", wait_until="networkidle")
        assert pg.is_visible("#loginView"), "شاشة الدخول غير ظاهرة"
        pg.fill("#email", email)
        pg.fill("#pw", PASSWORD)
        pg.click("#loginBtn")
        pg.wait_for_selector("#appView:not(.hide)", timeout=20000)
        # انتظار بالمُحدِّدات لا بنصٍّ يُقيَّم — CSP تمنع `unsafe-eval`.
        pg.locator("#wBal:not(:text-is('—'))").wait_for(timeout=20000)
        print("١) دخل ورأى اللوحة:", pg.inner_text("#whoAmI"))
        print("   الرصيد:", pg.inner_text("#wBal"),
              "| الطبقة:", pg.inner_text("#whoTier"),
              "| الحصّة:", pg.inner_text("#eStudies"),
              "| المقاعد:", pg.inner_text("#sSeats"))
        # انتظارٌ لا عدٌّ فوريّ: `#wBal` يُملأ **قبل** `Promise.all` الذي يحمّل
        # الجداول، فعدُّ الصفوف لحظةَ ظهور الرصيد سباقٌ يفشل عشوائياً. (فشل فعلاً
        # هنا بعد أن صار التحميل يشمل ستّ مجموعات بدل ثلاث.)
        # القائمة الجانبية تُبدِّل فعلاً — لا مجرّد وجودِ أزرار. (بلاغ المالك:
        # «المفروض فيه قائمة جانبية بس النثر هذا».)
        def goto(label: str, panel: str) -> None:
            """انتقِل إلى قسمٍ ثم انتظِر ظهوره.

            مع القائمة الجانبية يظهر **قسمٌ واحد** فقط، فأيّ فحصٍ على صفوف قسمٍ
            آخر يجد عناصر «مخفيّة» — لا غائبة. فالتنقّل شرطُ صحّةٍ للفحص نفسه.
            """
            pg.locator("#sideNav button", has_text=label).first.click()
            pg.locator(f"#{panel}.on").wait_for(timeout=15000)

        nav = pg.locator("#sideNav button")
        # `buildNav()` يجري **بعد** `Promise.all` للتحميل، فالعدُّ لحظةَ ظهور
        # الرصيد سباقٌ يقرأ صفراً. انتظِر أوّل مدخل ثم عُدّ.
        nav.first.wait_for(timeout=20000)
        assert nav.count() >= 6, f"القائمة الجانبية ناقصة: {nav.count()} مدخلاً"
        goto("الدفتر", "ledgerPanel")
        pg.locator("#ledgerBody tr").first.wait_for(timeout=20000)
        assert not pg.is_visible("#studiesPanel"), \
            "قسمان ظاهران معاً — التبديل لا يعمل"
        goto("الدراسات", "studiesPanel")
        print("١ب) القائمة الجانبية تُبدِّل الأقسام فعلاً —",
              nav.count(), "مدخلاً ✔")
        # شريط المقاييس سياقٌ **دائم**: يبقى ظاهراً وشبكةً في أيّ قسم.
        disp = pg.evaluate(
            "getComputedStyle(document.getElementById('factoryStats')).display")
        assert disp == "grid", f"بطاقات المقاييس ليست شبكة: display={disp}"
        assert pg.is_visible("#factoryStats"), "شريط المقاييس اختفى مع تبديل القسم"
        pg.screenshot(path=str(shots / "01_dashboard.png"), full_page=True)

        # ٣٧٥px — معيار قبولٍ صريح: القائمة تنطوي ولا تمريرَ أفقيّ للصفحة.
        pg.set_viewport_size({"width": 375, "height": 820})
        pg.locator("#sideToggle").wait_for(state="visible", timeout=15000)
        doc_w = pg.evaluate("document.documentElement.scrollWidth")
        assert doc_w <= 375 + 1, f"تمريرٌ أفقيّ عند ٣٧٥px: عرض المستند {doc_w}"
        pg.click("#sideToggle")
        pg.locator("#sideBar.open").wait_for(timeout=15000)
        pg.screenshot(path=str(shots / "01b_mobile_375.png"), full_page=True)
        goto("الدراسات", "studiesPanel")
        print("١ج) عند ٣٧٥px: القائمة تنطوي وتُفتَح بالزرّ، ولا تمريرَ أفقيّ ✔")
        pg.set_viewport_size({"width": 1280, "height": 1600})

        goto("الدراسات", "studiesPanel")
        row = pg.locator("#studiesBody tr").filter(has_text="جارية").first
        sid = row.locator("td").first.inner_text().strip()
        row.locator("button", has_text="إنهاء").click()
        pg.wait_for_selector("#appMsg.on.err", timeout=20000)
        msg = pg.inner_text("#appMsg")
        assert "الطابور" in msg, f"رسالة الرفض ليست عربية/متوقّعة: {msg}"
        print(f"٢) رُفض «إنهاء» على #{sid} بسبب البريد المعلّق ✔")
        pg.screenshot(path=str(shots / "02_complete_refused.png"), full_page=True)

        pg.locator("#studiesBody tr").filter(has_text="جارية").first \
          .locator("button", has_text="أرشفة").click()
        pg.wait_for_selector("#appMsg.on.good", timeout=20000)
        msg = pg.inner_text("#appMsg")
        assert "4" in msg or "٤" in msg, f"لم يُبلَّغ عن إلغاء ٤ رسائل: {msg}"
        print("٣) الأرشفة ألغت البريد المصفوف:", msg)
        pg.locator("#studiesBody").get_by_text("مؤرشَفة").first.wait_for(timeout=20000)
        pg.screenshot(path=str(shots / "03_archived.png"), full_page=True)

        # ═════ التدفّق العامل — الغرض من موجة الواجهة ═══════════════════════
        # بلاغ المالك كان «كيف أستعمل؟ لا يوجد أيّ خيار». فالدليلُ المطلوب ليس
        # «الصفحة تُحمَّل» بل **إنجازُ عملٍ حقيقيّ بالنقر**: بريد إرسال ← عميل
        # محتمل ← نصّ رسالة ← دراسة ← إطلاق يصفّ بريداً فعلاً ← تقرير يُخصَم.
        # **خللٌ التقطه المتصفّح في مُسيِّري أنا:** الانتظار على `#appMsg.on`
        # يعود **فوراً** لأن رسالة الفعل السابق ما زالت `.on` — فكنتُ أقرأ رسالةً
        # بائتة، وأخطر من ذلك: كان فحصُ «لا err» قد يمرّ على نجاحٍ **سابق** بينما
        # الفعلُ الحاليّ فشل. فالانتظارُ الصحيح على **تغيّر النصّ** لا على ظهوره.
        # `expect` يستقصي عبر البروتوكول بلا تقييم نصٍّ في الصفحة (CSP تمنعه).
        from playwright.sync_api import expect

        def submit(dialog_title: str) -> str:
            before = pg.inner_text("#appMsg") if pg.is_visible("#appMsg") else ""
            dlg = pg.locator(".veil .dlg", has_text=dialog_title).last
            dlg.locator(".btn.go").click()
            box = pg.locator("#appMsg")
            expect(box).to_be_visible(timeout=25000)
            if before:
                expect(box).not_to_have_text(before, timeout=25000)
            cls = pg.get_attribute("#appMsg", "class") or ""
            text = pg.inner_text("#appMsg")
            assert "err" not in cls, f"«{dialog_title}» فشل: {text}"
            return text

        pg.click("#newSmtpBtn")
        pg.fill('.veil [name="host"]', "smtp.smoke.local")
        pg.fill('.veil [name="from_email"]', "sender@smoke.local")
        pg.fill('.veil [name="label"]', "بريد الدخان")
        submit("إعداد بريد الإرسال")
        pg.locator("#smtpState:text-is('جاهز')").wait_for(timeout=20000)
        print("٤) أُضيفت تهيئة SMTP بالنقر — الحالة:", pg.inner_text("#smtpState"))

        pg.click("#newProspectBtn")
        pg.fill('.veil [name="email"]', "buyer@smoke.example")
        pg.fill('.veil [name="first_name"]', "Jan")
        submit("عميل محتمل جديد")
        goto("العملاء المحتملون", "prospectsPanel")
        pg.locator("#prospectsBody tr").first.wait_for(timeout=20000)
        print("٥) أُضيف عميل محتمل بالنقر ✔")

        pg.click("#newStudyBtn")
        pg.fill('.veil [name="title_ar"]', "حملة الدخان العاملة")
        pg.fill('.veil [name="target_count"]', "1")
        submit("دراسة جديدة")
        goto("الدراسات", "studiesPanel")
        study_row = pg.locator("#studiesBody tr").filter(
            has_text="حملة الدخان العاملة").first
        study_row.wait_for(timeout=20000)
        new_sid = study_row.locator("td").first.inner_text().strip()
        print(f"٦) أُنشئت الدراسة #{new_sid} بالنقر ✔")

        pg.click("#newDraftBtn")
        pg.fill('.veil [name="subject_ar"]', "عرض تعاون تجاري")
        pg.fill('.veil [name="body_ar"]', "نصّ الرسالة التجريبية.")
        submit("نصّ رسالة جديد")
        goto("نصوص الرسائل", "draftsPanel")
        pg.locator("#draftsBody tr").first.wait_for(timeout=20000)
        print("٧) أُضيف نصّ رسالة بالنقر ✔")

        # الإطلاق: النافذة تُلزِم نصّاً وعملاء، والخادم يصفّ فقط بهما معاً.
        goto("الدراسات", "studiesPanel")
        pg.locator("#studiesBody tr").filter(has_text="حملة الدخان العاملة").first \
          .locator("button", has_text="إطلاق").click()
        msg = submit("إطلاق الدراسة")
        assert "صُفَّ 0 بريد" not in msg, f"الإطلاق صفَّ صفراً — العطل الصامت: {msg}"
        print("٨) الإطلاق صفَّ بريداً فعلاً:", msg)
        pg.screenshot(path=str(shots / "04_launched.png"), full_page=True)

        # مسارُ المال: يُخصَم $1.00 فعلاً. لا يجوز الاكتفاء بـ«ظهرت رسالة» —
        # كان هذا الفحص يقرأ رسالةَ الإطلاق البائتة فيمرّ ولو فشل التقرير.
        goto("الدراسات", "studiesPanel")
        before_bal = pg.inner_text("#wBal")
        before_msg = pg.inner_text("#appMsg") if pg.is_visible("#appMsg") else ""
        pg.locator("#studiesBody tr").filter(has_text="حملة الدخان العاملة").first \
          .locator("button", has_text="تقرير").click()
        box = pg.locator("#appMsg")
        expect(box).to_be_visible(timeout=25000)
        if before_msg:
            expect(box).not_to_have_text(before_msg, timeout=25000)
        msg = pg.inner_text("#appMsg")
        assert "err" not in (pg.get_attribute("#appMsg", "class") or ""), \
            f"تعذّر إصدار التقرير: {msg}"
        assert "تقرير" in msg, f"الرسالة ليست عن التقرير (بائتة؟): {msg}"
        # والخصم يجب أن يُرى في الرصيد نفسه لا في الرسالة وحدها.
        expect(pg.locator("#wBal")).not_to_have_text(before_bal, timeout=25000)
        print("٩) أُصدِر التقرير:", msg)
        print(f"   الرصيد قبل {before_bal} ← بعد {pg.inner_text('#wBal')}")
        pg.screenshot(path=str(shots / "05_report.png"), full_page=True)

        assert not js_errors, f"استثناءات JS: {js_errors}"
        print("١٠) استثناءات JS: لا شيء ✔")
        br.close()
    print("لقطات:", shots)


def drive_admin(base: str, email: str, password: str, shots: pathlib.Path) -> None:
    """لوحة الأدمِن بالنقر — تمويل محفظة مصنع + تغيير طبقة + مفتاح الإيقاف.

    نصفُ الأدمِن ليس ترفاً: محفظةُ المصنع تبدأ صفراً وأيّ إطلاق يرتدّ
    بـ`insufficient_balance`، والتمويل مسارُ أدمِن حصراً. فواجهةُ مصنعٍ بلا هذا
    النصف شاشةٌ كلُّ أزرارها تُرجِع رفضاً.
    """
    from playwright.sync_api import sync_playwright
    exe = _chromium()
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        pg = br.new_page(viewport={"width": 1280, "height": 1400})
        js_errors: list[str] = []
        pg.on("pageerror", lambda e: js_errors.append(str(e)))
        pg.goto(base + "/platform.html", wait_until="networkidle")
        pg.fill("#email", email)
        pg.fill("#pw", password)
        pg.click("#loginBtn")
        pg.wait_for_selector("#adminBar:not(.hide)", timeout=20000)
        pg.locator("#accountsBody tr").first.wait_for(timeout=20000)
        n = pg.locator("#accountsBody tr").count()
        print(f"أ) دخل الأدمِن ورأى {n} حساب مصنع ✔")

        from playwright.sync_api import expect
        box = pg.locator("#appMsg")

        def act(button_text: str, dialog_title: str | None = None) -> str:
            """انقر وانتظر **تغيّر** الرسالة — لا ظهورها (البائتة تُقرأ خطأً)."""
            before = pg.inner_text("#appMsg") if pg.is_visible("#appMsg") else ""
            if dialog_title:
                pg.locator(".veil .dlg", has_text=dialog_title).last \
                  .locator(".btn.go").click()
            else:
                pg.click(button_text)
            expect(box).to_be_visible(timeout=25000)
            if before:
                expect(box).not_to_have_text(before, timeout=25000)
            text = pg.inner_text("#appMsg")
            cls = pg.get_attribute("#appMsg", "class") or ""
            assert "err" not in cls, f"فشل: {text}"
            return text

        pg.locator("#accountsBody tr").first.locator("button", has_text="تمويل").click()
        pg.fill('.veil [name="usd"]', "25.00")
        funded = act("", "تمويل محفظة مصنع")
        assert "$25.00" in funded, f"لم يُبلَّغ بالمبلغ المُموَّل فعلاً: {funded}"
        print("ب) مُوِّلت محفظة مصنع بالنقر:", funded)
        pg.screenshot(path=str(shots / "06_admin_funded.png"), full_page=True)

        pg.locator("#accountsBody tr").first.locator("button", has_text="الطبقة").click()
        pg.select_option('.veil [name="tier"]', "gold")
        tiered = act("", "تغيير طبقة حساب")
        assert "gold" in tiered, f"لم تُبلَّغ الطبقة الجديدة: {tiered}"
        print("ج) تغيّرت الطبقة بالنقر:", tiered)

        killed = act("#killBtn")
        assert "أُوقف" in killed, f"مفتاح الإيقاف لم يعمل: {killed}"
        restored = act("#killBtn")                 # أعِده كي لا تُترك الخدمة موقوفة
        assert "أُعيد" in restored, f"لم يُعَد تشغيل الإرسال: {restored}"
        print("د) مفتاح الإيقاف عمل ثم أُعيد:", restored)
        pg.screenshot(path=str(shots / "07_admin_kill.png"), full_page=True)

        assert not js_errors, f"استثناءات JS في لوحة الأدمِن: {js_errors}"
        print("هـ) استثناءات JS: لا شيء ✔")
        br.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None,
                    help="خادم قائم (وإلا يُقلِع واحداً مؤقّتاً)")
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--shots", default=None)
    a = ap.parse_args()
    shots = pathlib.Path(a.shots) if a.shots else \
        pathlib.Path(tempfile.mkdtemp(prefix="silk-ui-smoke-"))

    if a.base:
        drive(a.base, os.environ.get("SILK_SMOKE_EMAIL", "owner@factory-a.local"),
              shots)
        return 0

    tmp = tempfile.mkdtemp(prefix="silk-ui-db-")
    email = _seed(os.path.join(tmp, "platform.db"))
    base = f"http://127.0.0.1:{a.port}"
    log = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api:app",
         "--host", "127.0.0.1", "--port", str(a.port)],
        cwd=str(_ROOT), stdout=log, stderr=subprocess.STDOUT, env=os.environ.copy())
    try:
        _wait_up(base)
        drive(base, email, shots)
        drive_admin(base, "admin@silk.local", ADMIN_PASSWORD, shots)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    print("UI SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
