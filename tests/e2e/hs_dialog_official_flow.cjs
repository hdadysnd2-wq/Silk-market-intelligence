// رُتبة ٣ — حوارُ المرشّحين في متصفّحٍ حقيقي، على ترويستين من **فصلين
// مختلفين** وببُعدَي قياسٍ مختلفين (لا على 0401 وحدها).
//
// الحادثة (بلاغ المُشرِف): الحوارُ عرض حدوداً تناقض المرجع الرسميّ، ورمزاً لا
// وجود له، ومجموعةً ناقصة. الحوارُ هو المسارُ الافتراضيّ على أغلب الترويسات —
// وتأكيدُه يجب أن يقع على ما يُصيَّر في الـDOM فعلاً، لا على حمولة الـAPI.
//
// المسارُ هنا **مسارُ المستخدم الحقيقي** لا نداءُ مُصيِّرٍ من الكونسول: اضبط
// المنتج (خطّاف الاختبار) ← اختر سوقاً ← «بحث عميق» ← `ensureHs` يطلب
// `/classify_hs` من الخادم الحقيقي ← `showHsCandidates` يُصيِّر الحوار. ثم
// يُقرأ الـDOM ويُؤكَّد:
//   • كلُّ بندٍ معروض موجودٌ في المرجع الرسميّ،
//   • أشقّاءُ المحور الرقميّ **كاملون** في الـDOM (لا اقتطاع),
//   • نصُّ الحدّ المعروض يحمل أرقامَ النطاق الرسميّ،
//   • لا اسمَ منتجٍ/علامةٍ/بلدٍ في نصّ الحوار.

const { chromium } = require("playwright");

const BASE = process.env.BASE_URL;
const MARKET = process.env.PRERUN_MARKET_ISO3 || "ARE";
if (!BASE) { console.error("MISSING BASE_URL env"); process.exit(2); }

// الحالتان ومجموعةُ المرجع الرسميّ تُمرَّران من بايثون كي تبقيا **مشتقّتين من
// `data/hs_reference.csv`** لا مكتوبتين هنا صلباً.
const CASES = JSON.parse(process.env.DIALOG_CASES || "[]");
const OFFICIAL = new Set(JSON.parse(process.env.OFFICIAL_CODES || "[]"));
if (!CASES.length) { console.error("MISSING DIALOG_CASES env"); process.exit(2); }
if (!OFFICIAL.size) { console.error("MISSING OFFICIAL_CODES env"); process.exit(2); }

const steps = [];
function ok(name, detail) { steps.push({ name, ok: true, detail: detail || "" }); }
function fail(name, detail) {
  steps.push({ name, ok: false, detail: detail || "" });
  throw new Error(`STEP FAILED: ${name} — ${detail || ""}`);
}

// عزلُ الجولات — نفس درسِ `hs_tier_family_flow.cjs`: أعِد تحميل الصفحة بين
// الحالات كي لا يتسرّب طلبٌ معلَّقٌ ولا لافتةٌ متأخّرة إلى الجولة التالية.
async function resetPage(page) {
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector("#marketBox .mrow", { timeout: 20000 });
}

async function runCase(page, c) {
  const tag = `${c.heading}/${c.dimension}`;
  await resetPage(page);
  await page.evaluate((name) => window.__silkTestSetProduct(name), c.product);

  const mrow = page.locator(`#marketBox .mrow[data-iso3="${MARKET}"]`);
  const anyMrow = (await mrow.count()) ? mrow.first()
    : page.locator("#marketBox .mrow").first();
  if (!(await anyMrow.evaluate((el) => el.classList.contains("sel")))) {
    await anyMrow.click();
  }
  ok(`${tag}:setup`, c.product);

  // المسارُ الحقيقي — لا نداءَ مباشرٍ للمُصيِّر.
  await page.locator("#researchBtn").click();
  await page.waitForSelector(".prov", { timeout: 20000 });
  const dialogText = await page.locator(".prov").first().innerText();
  if (!/تأكيد رمز HS/.test(dialogText))
    fail(`${tag}:dialog_shown`, `dialog missing heading: ${dialogText}`);
  ok(`${tag}:dialog_shown`);

  const shown = await page.evaluate(() =>
    Array.from(document.querySelectorAll(".hsCand")).map((b) => ({
      hs6: b.dataset.hs, text: b.innerText,
    })));
  if (!shown.length) fail(`${tag}:rendered`, "no candidate buttons in the DOM");
  ok(`${tag}:rendered`, `${shown.length} candidates`);

  // (١) كلُّ بندٍ معروضٍ موجودٌ في المرجع الرسميّ.
  const alien = shown.filter((s) => !OFFICIAL.has(s.hs6)).map((s) => s.hs6);
  if (alien.length)
    fail(`${tag}:only_official`, `codes absent from the reference: ${alien}`);
  ok(`${tag}:only_official`, `${shown.length} codes all in hs_reference.csv`);

  // (٢) أشقّاءُ المحور كاملون في الـDOM — لا اقتطاعَ يُخفي البندَ الصحيح.
  const domCodes = new Set(shown.map((s) => s.hs6));
  const missing = c.siblings.filter((s) => !domCodes.has(s));
  if (missing.length)
    fail(`${tag}:siblings_complete`, `missing siblings in the DOM: ${missing}`);
  ok(`${tag}:siblings_complete`, `${c.siblings.length} siblings rendered`);

  // (٣) نصُّ الحدّ المعروض يحمل أرقامَ النطاق الرسميّ.
  let edgeChecks = 0;
  for (const code of Object.keys(c.edges)) {
    const row = shown.find((s) => s.hs6 === code);
    if (!row) fail(`${tag}:band_text`, `${code} expected in the DOM but absent`);
    for (const e of c.edges[code]) {
      if (!row.text.includes(e))
        fail(`${tag}:band_text`, `${code}: edge ${e} missing from «${row.text}»`);
      edgeChecks += 1;
    }
  }
  ok(`${tag}:band_text`, `${edgeChecks} official edge(s) present in the DOM`);

  // (٤) لا اسمَ منتجٍ/علامةٍ/بلدٍ في نصّ الحوار.
  for (const token of c.banned_tokens)
    if (dialogText.includes(token))
      fail(`${tag}:no_product_echo`, `token «${token}» echoed in the dialog`);
  ok(`${tag}:no_product_echo`, `${c.banned_tokens.length} token(s) absent`);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push(String(e)));
  try {
    for (const c of CASES) await runCase(page, c);
    if (consoleErrors.length) fail("no_page_errors", consoleErrors.join(" | "));
    ok("no_page_errors");
    console.log(JSON.stringify({ result: "PASS", steps }, null, 2));
    console.log("HSDIALOG PASS");
  } catch (e) {
    console.log(JSON.stringify({ result: "FAIL", steps }, null, 2));
    console.error(String(e && e.stack ? e.stack : e));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
