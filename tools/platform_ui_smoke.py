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
              "| الطبقة:", pg.inner_text("#eTier"),
              "| الحصّة:", pg.inner_text("#eStudies"),
              "| المقاعد:", pg.inner_text("#sSeats"))
        assert pg.locator("#ledgerBody tr").count() > 0, \
            "الدفتر فارغ — خلل مفتاح `entries` عاد"
        pg.screenshot(path=str(shots / "01_dashboard.png"), full_page=True)

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

        assert not js_errors, f"استثناءات JS: {js_errors}"
        print("٤) استثناءات JS: لا شيء ✔")
        br.close()
    print("لقطات:", shots)


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
