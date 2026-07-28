// رُتبة ٣ — قفل مُعمَّم عبر عائلات منتجات حقيقية (الموجة ٤: بلاغ «UI-ONLY
// FIX»، اختبار القفل الصريح الذي طلبه المُشرِف): لكل زوج {منتج، توقّع}،
// يُضبَط المنتج عبر خطّاف الاختبار، يُنقَر «بحث عميق»، ثم:
//   expect=dialog → صندوق حوار «تأكيد رمز HS» يظهر، و«✓ صُنّف تلقائياً»
//                   لا يظهر على #pResolved في أيّ لحظة قبل اختيار مرشّح.
//   expect=auto   → «✓ صُنّف تلقائياً» يظهر مباشرةً على #pResolved، وصندوق
//                   حوار تصنيف HS لا يظهر إطلاقاً (عنوانه «تأكيد رمز HS»
//                   تحديدًا — يُميَّز عن نافذة استشارة بلد المنشأ المنفصلة
//                   التي قد تلي الحسم لبعض أزواج HS/سوق مبذورة وتستعمل نفس
//                   فئة .prov؛ إن ظهرت تُقفَل كي تتحرّر الحالة للجولة التالية).
//
// بلا مفتاح كلود (يُنزَع عمداً في كل e2e، LAW §2 — الدلو الثاني فقط):
// التصنيف هنا حتميٌّ (بذرة CSV) لا عامٌّ بمساعدة نموذج. لذا القائمة أدناه
// مبنيّةٌ على ما يُحسَم فعليًا بلا نموذج في هذه البيئة — وليست نسخةً حرفيّةً
// من دليل المالك الحيّ (الذي عمل بمفتاح كلود فعليّ): «زيت زيتون» تحديدًا
// حُسم تلقائيًا لدى المالك (مفتاح حيّ) لكنّه يعطي ٣ مرشّحين متقاربين بلا
// مفتاح هنا (هامشٌ<٠.١٥) => نتوقّع صندوق حوار، لا «✓» — وهذا سلوكٌ سليمٌ
// (فشل-آمن) لا تراجعًا: الشكّ يعني السؤال لا التخمين حين لا دليل حاسم.

const { chromium } = require("playwright");

const BASE = process.env.BASE_URL;
const MARKET = process.env.PRERUN_MARKET_ISO3 || "ARE";

if (!BASE) { console.error("MISSING BASE_URL env"); process.exit(2); }

// dialog: منتجاتٌ ضعيفة التمثيل أو غامضة في بذرتنا الحتمية بلا نموذج.
// auto:   منتجاتٌ تُحسَم بثقةٍ صارمة (تداخلٌ ≥٠.٨ + هامشٌ واضح) بلا نموذج.
// الترتيب مقصود: كل حالات dialog أولاً ثم حالات auto — شارةُ الحسم التلقائي
// تبقى معروضةً حتى ضبط منتجٍ جديدٍ يُصنَّف، فلو تلت حالةُ dialog حالةَ auto
// لظهرت شارةُ auto السابقة لحظةَ فحص «لا شارة خلف الصندوق» (فخّ ترتيبٍ لا فخّ
// منتج). «زبدة الفول السوداني» ضمن مجموعة auto: بعد تزويد البذرة بالكلمات
// المفتاحية العربية للرمز الصحيح 200811 (فول سوداني محضّر، طلب المالك
// 2026-07-23) صار يُحسَم تلقائياً للعائلة **الصحيحة** بلا مفتاح كلود (تداخلٌ
// تامٌّ + هامشٌ واضح فوق 040510 الألبان الذي نزل لمرشّحٍ ثانوي) — قفلٌ إيجابيٌّ
// للإصلاح: العائلة الصحيحة تُحسَم، لا الألبان الخاطئ.
const CASES = [
  { product: "مياه ورد", expect: "dialog" },
  { product: "عود معطر", expect: "dialog" },
  { product: "زيت زيتون", expect: "dialog" },
  { product: "زبدة الفول السوداني", expect: "auto" },
  { product: "تمر سكري", expect: "auto" },
  { product: "عسل سدر", expect: "auto" },
];

const steps = [];
function ok(name, detail) { steps.push({ name, ok: true, detail: detail || "" }); }
function fail(name, detail) {
  steps.push({ name, ok: false, detail: detail || "" });
  throw new Error(`STEP FAILED: ${name} — ${detail || ""}`);
}

// يلي الحسمَ (تلقائيًّا أو باختيار مرشّح) احتمالُ نافذة استشارة بلد منشأٍ
// منفصلة تمامًا (بوّابةٌ أخرى، بلا علاقة بهذا القفل) لبعض أزواج HS/سوق
// مبذورة. أغلقها إن ظهرت كي تتحرّر الحالة للجولة التالية، ولا تنتظرها إن لم تظهر.
async function settleAfterClassification(page) {
  try {
    await page.waitForSelector("#advOk", { timeout: 4000 });
    await page.locator("#advOk").click();
    await page.waitForFunction(
      () => !document.querySelector(".prov"), undefined, { timeout: 10000 });
  } catch (_) { /* لم تظهر استشارةٌ — طبيعيٌّ لأزواج HS/سوق غير مبذورة */ }
}

// عزلُ الجولات (سباقٌ سابقٌ لهذا الفرع، أُصلِح هنا): بعد تأكيد الرمز يُطلِق
// `cb` طلبَ `/research` يمرّ ببوّاباتٍ تضرب مصادرَ حيّة (كومتريد) قبل أن يُردّ
// بـ409 (لا مفتاح كلود في e2e عمداً) — وقد يتجاوز ذلك ٣٠ ثانية على عدّاءٍ
// بطيء. الجولةُ التالية كانت تنقر زرّاً ما يزال معطّلاً، ثم تصل لافتةُ الردّ
// السابق فتعترض النقر. وانتظارُ الزرّ لم يكن ينفع أصلاً: التوقيعُ
// `waitForFunction(fn, arg, options)` — فـ`{timeout}` المُمرَّر ثانياً كان
// يُقرأ **وسيطاً** لا خياراً، فبقيت المهلةُ الافتراضية (٣٠ث) دوماً (لذلك
// ظهرت «Timeout 30000ms» رغم طلبِ ١٥ث ثم ٦٠ث).
//
// الحلُّ الحتميّ: **أعِد تحميل الصفحة بين الجولات**. الطلبُ المعلَّق يُهجَر مع
// السياق، فلا حالةَ تتسرّب ولا لافتةَ تصل متأخّرة — وتسقط معه أيضاً حاجةُ
// ترتيبِ الحالات (dialog قبل auto) التي كانت تلتفّ حول بقاء الشارة. أبطأُ
// بثوانٍ، وحتميٌّ بدل مرهونٍ بسرعة الشبكة.
async function resetPage(page) {
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForSelector("#marketBox .mrow", { timeout: 20000 });
}

async function runCase(page, { product, expect }) {
  // حالةٌ نظيفةٌ تماماً لكل جولة — لا طلبَ معلَّقاً ولا شارةَ باقية.
  await resetPage(page);
  await page.evaluate((name) => window.__silkTestSetProduct(name), product);
  ok(`${product}:product_set`);

  // نقرة صفّ السوق تُبدِّل التحديد (toggle) — انقر فقط إن لم يكن محدَّداً
  // أصلاً، وإلا تُلغي جولةٌ لاحقة تحديد جولةٍ سابقة (فخّ e2e لا فخّ منتج).
  const mrow = page.locator(`#marketBox .mrow[data-iso3="${MARKET}"]`);
  const anyMrow = (await mrow.count()) ? mrow.first()
    : page.locator("#marketBox .mrow").first();
  if (!(await anyMrow.evaluate((el) => el.classList.contains("sel")))) {
    await anyMrow.click();
  }
  ok(`${product}:market_selected`);

  await page.locator("#researchBtn").click();

  if (expect === "dialog") {
    await page.waitForSelector(".prov", { timeout: 15000 });
    const dialogText = await page.locator(".prov").first().innerText();
    if (!/تأكيد رمز HS/.test(dialogText))
      fail(`${product}:candidates_modal`, `dialog missing heading: ${dialogText}`);
    if (/صُنّف تلقائياً/.test(dialogText))
      fail(`${product}:candidates_modal`,
        `dialog must never show the auto-classified checkmark: ${dialogText}`);
    // القفل الصريح الذي طلبه المُشرِف: الشارة لا تظهر إطلاقاً بينما الصندوق حاجب.
    const badgeText = await page.locator("#pResolved").innerText().catch(() => "");
    if (/صُنّف تلقائياً/.test(badgeText))
      fail(`${product}:no_auto_badge_behind_dialog`,
        `#pResolved shows the auto badge while a candidates dialog is open: ${badgeText}`);
    ok(`${product}:candidates_modal`, "blocking dialog shown, no auto-badge anywhere");
    // أغلِق الصندوق باختيار أوّل مرشّحٍ لتحرير الحالة للجولة التالية.
    const candCount = await page.locator(".hsCand").count();
    if (candCount < 1) fail(`${product}:candidates_present`, "no candidate buttons rendered");
    await page.locator(".hsCand").first().click();
    await page.waitForFunction(
      () => !document.querySelector(".prov"), undefined, { timeout: 15000 });
    ok(`${product}:candidate_selected`);
    await settleAfterClassification(page);
  } else {
    await page.waitForFunction(() => {
      const el = document.querySelector("#pResolved");
      return el && el.classList.contains("on") && /✓/.test(el.textContent || "");
    }, undefined, { timeout: 15000 });
    const badgeText = await page.locator("#pResolved").innerText();
    if (!/صُنّف تلقائياً/.test(badgeText))
      fail(`${product}:auto_classify`, `resolved badge missing the auto text: ${badgeText}`);
    ok(`${product}:auto_classify`, "honest ✓ badge shown, no blocking HS dialog");
    await settleAfterClassification(page);
  }
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("pageerror", (e) => consoleErrors.push(String(e)));

  try {
    await page.goto(BASE, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForSelector("#marketBox .mrow", { timeout: 15000 });
    ok("dashboard_render");

    for (const c of CASES) {
      await runCase(page, c);
    }

    if (consoleErrors.length)
      fail("no_page_errors", consoleErrors.join(" | "));
    ok("no_page_errors");

    console.log(JSON.stringify({ result: "PASS", steps }, null, 2));
    console.log("HSTIERFAMILY PASS");
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error(JSON.stringify({ result: "FAIL", steps }, null, 2));
  console.error(String(e && e.stack ? e.stack : e));
  process.exit(1);
});
