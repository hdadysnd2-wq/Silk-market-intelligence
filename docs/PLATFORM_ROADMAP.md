# خارطة منصّة سِلك — الحالة الفعلية لكل PR · per-PR actual status

> **غرض هذا الملف:** القسم ١٤ من المواصفة يرسم تقسيماً نظرياً (PR-1…PR-8)، لكن
> **PR-1 دُمِج وفيه شرائح من PR-2/3/5** لأن معايير القبول في القسم ١٣ (المعنونة
> «PR-1 ACCEPTANCE CRITERIA») تطلب اختبارات حصص ومحفظة ومفتاح قتل. فلو قرأ أحدٌ
> القسم ١٤ وحده لبنى ما هو مبنيّ سلفاً — أو أسوأ: **نسخةً ثانية مختلفة التفاصيل**.
> هذا الملف هو مصدر الحقيقة لِما نُفِّذ فعلاً مقابل ما بقي، ويُحدَّث مع كل PR.
>
> **Why this file exists:** PR-1 (merged as `7b9c718`, PR #186) necessarily
> absorbed slices of PR-2/3/5 because Section 13 — titled "PR-1 acceptance
> criteria" — asserts quota/wallet/kill-switch behavior. Reading §14 alone would
> lead to rebuilding what exists, or to a second divergent implementation.

---

## PR-A — حارسان بنيويان (خارج القسم ١٤، بتوجيه المالك) · structural guards

أُدرِج **قبل** PR-2 بقرار المالك، لأن كل PR قادم يبني على أساسٍ بلا هذين الحارسين:

| الحارس | الفجوة التي يُغلقها | الملف |
|---|---|---|
| **حارس العزل بـAST** | لا شيء آلي كان يمنع PR قادماً من كتابة `SELECT … FROM studies WHERE id = ?` بلا قيد مالك ⇒ قراءة عابرة للمستأجرين. الآن كل جملة SQL تمسّ جدولاً مُستأجَراً يجب أن تحمل قيد مالك أو تُدرَج **بسببٍ مكتوب ومقيَّد بالوحدة**. | `tests/test_platform_isolation_ast_guard.py` |
| **بوّابة §58 في CI** | القاعدة كانت تعتمد على تذكّر شخص. الآن متن كل PR يجب أن يصرّح بأن `/code-review` جرى وبنتيجة الملاحظات عالية الخطورة، وإلا **تفشل وظيفة CI**. | `tools/check_self_review_gate.py` + خطوة في `ci.yml` + `.github/pull_request_template.md` |

كلاهما مُختبَر في **الاتجاهين** (يقبل الصحيح ويرفض المخالف)، وحارس AST يحمل
اختباراً يزرع مخالفةً متعمَّدة ليُثبِت أنه ليس خاملاً — «الأخضر الفارغ» أخطر من
غياب الحارس. Both gates are tested in both directions.

**قيدٌ معلَن على بوّابة §58:** تتحقّق من **وجود** التصريح لا من جودة المراجعة.
لا تستطيع شيفرةٌ أن تحكم على عمق مراجعة؛ لكنها تُنهي النسيان الصامت.

---

## PR-1 — auth + tenancy · **مدموج · MERGED** (`7b9c718`, PR #186)

مُنفَّذ بالكامل: الحسابات/المستخدمون/الجلسات/الأدوار، عزل المستأجرين في طبقة
البيانات، سجلّ التدقيق، الترحيلان، البذر، ٧٤ اختباراً هرمتياً.

---

## PR-2 — tiers, quotas, monthly reset

### ✅ مُنفَّذ سلفاً في PR-1 — **لا يُعاد بناؤه** · ALREADY SHIPPED, do not rebuild

| بند المواصفة §2 | الموضع الفعلي | الدليل |
|---|---|---|
| حدود الطبقات الأربع (دراسات/شهر، مدى حياة Basic) | `models.TIER_LIMITS` — **مصدر واحد** يحمل كل حقول الطبقة (مقاعد، قمع، لوحة، API، علامة بيضاء، تصدير) | `test_platform_quota_wallet_killswitch.py` |
| «العدّاد يزيد **فقط** عند draft→in_progress» | `quota.reserve_launch` + مطالبة ذرّية في `api.launch_study` | `test_counter_increments_only_on_launch` |
| Basic: دراسة واحدة مدى الحياة لا تُصفَّر | `quota.reserve_launch` فرع Basic | `test_basic_lifetime_limit_enforced_and_survives_monthly_reset` |
| Silver محجوب عند الإطلاق الثالث | نفس الدالّة (زيادة محروسة `WHERE count < limit`) | `test_silver_blocked_at_third_monthly_launch` |
| **تصفير الحصص الشهري** (يتخطّى Basic) | `quota.monthly_reset` — محروس بمفتاح الفترة ⇒ **خامل التكرار** | `test_monthly_reset_is_idempotent_within_a_month` |
| تجاوز الحصّة: منع + دعوة ترقية + قيد تدقيق | `reserve_launch` يكتب `quota_exceeded`، والنقطة ترجع `403 {upgrade:true}` | `test_quota_denied_launch_reverts_study_to_draft` |
| أمان التزامن للحصّة | زيادة ذرّية + تصفير كسول ذرّي | `test_platform_concurrency.py` (١٢ إطلاقاً متزامناً ⇒ ٢ بالضبط) |

### ✅ نُفِّذ في PR-2 · shipped in PR-2 (`docs/PLATFORM_PR2.md`)

| بند | الموضع | الدليل |
|---|---|---|
| **سقف المستخدمين لكل طبقة** (1 / 3 / 10 / غير محدود) | `entitlements.require_seat` + `users.py` (٦ نقاط) | `test_platform_tiers_seats.py` — Basic يمنع الثاني، Silver يمنع الرابع، Platinum بلا سقف |
| **إدارة الطبقات من Silk Admin** | `entitlements.set_account_tier` + `POST /admin/accounts/{id}/tier` | مدقَّق؛ **تخفيضٌ يترك مقاعد زائدة يُرفَض 409** ويمرّ بعد تعطيل الزائد |
| **بوّابة طبقة واحدة قابلة لإعادة الاستخدام** | `entitlements.has_feature/require_feature/dashboard_level/funnel_max_studies/snapshot` | ميزة مجهولة ترفع `ValueError` لا ترجع `False` |
| **مجدول فعلي** للمهام (§12) | `scheduler.py` (خيط opt-in) + `GET /entitlements` | `due_jobs` نقيّة؛ خامل التكرار بمُعرِّف فتحة؛ **لحاق** بعد التوقّف |
| توسيع حارس العزل إلى `users` | `tests/test_platform_isolation_ast_guard.py` | صار `users` جدولاً مُستأجَراً + إغلاق ثقبَي «ذكر العمود» و«جزء f-string المزدوج» |

**تصحيحان اكتُشفا أثناء التنفيذ** (لا في المواصفة): عدّاد شهرٍ مضى كان سيُقرأ
«مستهلَك بالكامل» بينما الإطلاق ينجح (رقمان متعارضان) — أُصلح قراءةً خالصة في
`studies_used_effective`؛ ومهمّة شهرية كانت تُسقَط لشهرٍ كامل لو توقّفت الحاوية
يوم ١ — أُصلح باللحاق.

### ⬜ ما زال ناقصاً من §2 · still remaining

| بند | الحالة | ملاحظة |
|---|---|---|
| لوحة «basic/full» فعلياً | ⬜ PR-6 | `dashboard_level()` جاهزة؛ العرض في موجة اللوحات |
| القمع (≤10 دراسات لـGold) | ✅ PR-7 | `funnels.attach_study` يفرض السقف فعلياً؛ و`comparison_funnels` بقي خارج CRUD العامّ عمداً (مساره `funnels.py`) |
| دعوة مستخدم فرعي بالبريد | ⬜ PR-5 | الإنشاء يأخذ كلمة مرور مباشرة؛ الدعوة الموقَّعة تنتظر ناقل SMTP |
| تمييز «مالك حساب» عن عضو | ❌ قرار مالك | لا عمود له في مخطّط PR-1، فكل مستخدم `factory` يدير المقاعد اليوم |

---

## PR-3 — wallet, vault, ledger, funding

### ✅ مُنفَّذ سلفاً في PR-1
المحفظة، الدفتر **غير القابل للتعديل** (مُشغِّلات)، التمويل الذرّي من الخزنة
بقيدَين مختومَين بالأدمِن + تراجع كامل، `balance_after` لكل قيد،
`actor_user_id` غير فارغ، سقف المديونية + بوّابة السداد، `BEGIN IMMEDIATE` على
كل مسارات المال، ونقطة `POST /admin/fund`.

### ✅ نُفِّذ في PR-3 · shipped in PR-3 (`docs/PLATFORM_PR3.md`)

| بند | الموضع | الدليل |
|---|---|---|
| **تسعير التقارير كعملية فعلية** (`PRICE_REPORT_CENTS` صار مستهلَكاً) | `billing.charge_metered` + `reporting.generate_charged_report` + `POST /studies/{id}/report` | `test_platform_billing.py` (١٦ اختباراً) |
| خصم **خامل التكرار** بمفتاح مخزَّن في وصف قيد الدفتر (لا جدول جديد) | `billing.charge_key_description`/`already_charged` — نمط `jobs.already_billed` | نقرة مزدوجة ⇒ نفس القيد و`charged=false` والرصيد ثابت |
| بوّابتا المال: مديونية + كفاية رصيد (`allow_negative=False`) | `billing.charge_metered` | 402 بلا أي قيد — لا دَين مقابل ما لم يحدث |
| تقرير حملة من بيانات المستأجر الحقيقية بمصادر معلَنة | `reporting.build_campaign_report` | العدّادات لا تتسرّب عبر دراسة ولا حساب؛ `response_count` معلَن «كما هو مخزَّن» |

**قرار مالك مُثبَت:** مفتاح الخمول الافتراضي = **تقرير واحد مدفوع لكل دراسة في
اليوم** (المواصفة تسعّر «التقرير» ولا تحدّد متى يصير الثاني جديداً)؛ ومن يريد
تقريراً مدفوعاً ثانياً في نفس اليوم يمرّر `idempotency_key` صريحاً.

### ⬜ ناقص · still remaining

| بند | الحالة | ملاحظة |
|---|---|---|
| **تسعير نداءات API الزائدة** (`PRICE_API_CALL_CENTS`) | ⏸️ **مؤجَّل بقرار المالك — موجة مستقلّة** | يحتاج **قرارين** لا يملكهما المنفِّذ: (١) بناء سطح مفاتيح API (إصدار/تدوير/إبطال + خنق) — ميزة كاملة لا تُشتَقّ من المواصفة، فمصادقة المنصّة اليوم جلسات فقط ولا سطح برمجي إطلاقاً؛ (٢) **الحصّة المشمولة شهرياً** لـPlatinum — لا رقم لها في المواصفة ولا في `TierLimits`، وتحديدها قرار تسعير. الثابت يبقى غير مستهلَك حتى ذلك، **معلَناً لا منسيّاً**. |
| عرض الدفتر للعميل بصفحات | ⬜ PR-6 | `wallet.list_ledger` موجودة بحدٍّ؛ الترقيم في موجة اللوحات |

---

## PR-4 — studies, prospects, drafts, i18n

### ✅ مُنفَّذ سلفاً
CRUD كامل مُستأجَر للدراسات/العملاء/المسودّات (أعمدة `_en`/`_ar`، الخيار A من §8)،
حالات الدراسة `draft→in_progress`، `language_preference` للمستخدم والعميل،
تعبئة `{{first_name}}`، واختيار المسودّة بلغة العميل.

### ✅ نُفِّذ في PR-4 · shipped in PR-4 (`docs/PLATFORM_PR4.md`)

| بند | الموضع | الدليل |
|---|---|---|
| **`completed` كانتقال صريح** | `lifecycle.complete_study` + `POST /studies/{id}/complete` | يُرفَض 409 ما دام في الطابور معلّق — «مكتملة» ورسائلها ستخرج ادعاءٌ كاذب |
| **`archived` كسحبٍ حقيقي** | `lifecycle.archive_study` + `POST /studies/{id}/archive` | **يُلغي البريد المعلّق فعلاً** ويسجّل العدد تدقيقاً؛ المُرسَل يبقى؛ `sending` لا يُمَسّ (409 عابر) |
| الانتقالات ذرّية ومحروسة بالحالة | `BEGIN IMMEDIATE` + إعادة قراءة داخل القفل + `AND state = …` | أرشفتان متزامنتان ⇒ واحدة تفوز؛ والأرشفة لا تتسابق مع العامل (مُثبَتٌ باختبار تزامن حقيقي مُشغَّل بنافذة مُوسَّعة) |

**السبب الذي جعل هذا ليس مسكاً للدفاتر:** عامل الطابور **لا يفحص حالة الدراسة
إطلاقاً**. فلو كانت الأرشفة تغييرَ عمودٍ فقط لظلّت رسائل الدراسة المسحوبة
**تُرسَل وتُخصَم** — محروسٌ باختبارٍ يشغّل العامل الحقيقي بعد الأرشفة ويؤكّد صفر
إرسال وصفر خصم وصفر قيد موافقة (ومُثبَتٌ أنه يصطاد: بأرشفةٍ تجميلية يُرسِل العامل
الخمسة كلها).

**ملاحظة تاريخية:** فرعٌ أوّل (#191) شحن نسخةً أبسط — تبديل حالة بلا لمس
الطابور — دُمِجت في `main` قبل أن يُلاحَظ فرعٌ ثانٍ سابقٌ (#190، من جلسة
مختلفة) يحمل التصميم الصحيح أعلاه. هذا القسم يوثّق النسخة **المُصحَّحة** التي
استبدلتها؛ #190 أُغلق كمُستوعَب.

### ⬜ ناقص · still remaining
قواميس الترجمة المفتاحية (`"wallet.balance"` → …) وRTL — **كلّها طبقة عرض
(PR-6)**، لا شيء منها منطقُ خادم. و«إلغاء الأرشفة» (`archived` نهائية اليوم) لم
يُطلَب ولم يُبنَ — قرار مالك إن لزم.

---

## PR-5 — SMTP, email queue, consent, kill-switch, unsubscribe

### ✅ مُنفَّذ سلفاً
تهيئات SMTP مُشفَّرة عند التخزين، التحقّق قبل الإطلاق (ملكية + نشاط + رصيد)،
الطابور بمطالبة ذرّية، مفتاح القتل **يُفحَص لكل بريد** (يتخطّى ولا يُسقط ويستأنف
بالترتيب)، سجلّ الموافقة الحرفي، وقائمة القمع المقيَّدة بالحساب.

### ✅ نُفِّذ الآن · shipped now

| بند | الموضع | الدليل |
|---|---|---|
| **ناقل SMTP حقيقي** | `silk_platform/smtp_transport.py` (`send()`, stdlib `smtplib`+`email.mime`، `smtp_cls` مُدخَل حقناً)؛ `email_queue.sender()` يفكّ تعمية بيانات اعتماد `smtp_configs` ويناديه؛ موصول فعلياً بنبضة `scheduler.py` (`run_email_pass`، ليس على تقويم `due_jobs` — البريد يحتاج زمن استجابة بمقياس النبضة) | `tests/test_platform_smtp_delivery.py` |
| **مفتاح idempotency** | `smtp_transport.message_id(kind, row_id)` حتميّ — إعادة معالجة نفس الصفّ (الحاصد) تُنتج **نفس** Message-ID، فخادمٌ يُميِّز به يعامل المحاولتين كرسالة واحدة؛ SMTP نفسه بلا آلية تسليم-مرّة-واحدة أصيلة | `test_message_id_is_deterministic_per_row` |
| **حاصد الصفوف العالقة** | `email_queue.reap_stuck()` + عمود `claimed_at` (ترحيل 003) — صفٌّ عالق أقدم من `SILK_PLATFORM_EMAIL_STUCK_SECONDS` يُعاد `queued`، أو `failed` عند استنفاد `SILK_PLATFORM_EMAIL_MAX_ATTEMPTS` | `test_reap_stuck_requeues_recent_and_fails_exhausted` |
| **رابط إلغاء اشتراك موقَّع + صفحته الثنائية اللغة** | `silk_platform/unsubscribe.py` (HMAC عبر `tokens.sign`، بلا حالة خادم)، `GET /platform/unsubscribe` (عامّ بلا مصادقة)، مُدرَج تلقائياً كعنصر نائب `{{unsubscribe_link}}` في `email_queue.enqueue()` | `test_unsubscribe_valid_link_updates_suppression_and_consent`، `test_unsubscribe_then_future_send_is_suppressed` |
| **توصيل بريد إعادة تعيين كلمة المرور** | `smtp_transport.operator_config_from_env()` — SMTP تشغيلي منفصل عن `smtp_configs` المستأجَرة (يصدر قبل أي جلسة)؛ فشلٌ يُسجَّل تدقيقاً لا يُرفَع للردّ | `test_password_reset_sends_email_when_operator_smtp_configured`، `test_password_reset_email_failure_is_audited_not_raised` |

### ⬜ ناقص

| بند | الحالة | ملاحظة |
|---|---|---|
| صفحة `reset-password` نفسها | ⬜ PR-6 | البريد يحمل رابطاً لها (`{base}/reset-password?token=...`) لكن لا واجهة له بعد — طبقة عرض |

---

## PR-8 — الصور وفوترة التخزين وبحث التدقيق · images, storage billing, audit search

### ✅ نُفِّذ الآن · shipped now

| بند | الموضع | الدليل |
|---|---|---|
| **رفع صور حقيقي** (لا `size_bytes`/`ext` مصرَّح بهما من العميل) | `POST /platform/images` صار multipart حقيقياً (`UploadFile`)؛ الحجم = طول المحتوى المرفوع فعلاً، الامتداد مُقيَّد بقائمة بيضاء (`silk_platform/storage.py`) | `tests/test_platform_storage.py` |
| **خدمة `/files/...`** (كانت توقّع رابطاً لمسارٍ لا يخدم شيئاً منذ PR-1) | `GET /files/{storage_key:path}` — عامّ بلا مصادقة، التوقيع HMAC (المُتحقَّق منه **قبل** أي بحث DB أو لمس قرص) هو الحارس الوحيد، نفس نمط `/platform/unsubscribe` | `test_signed_url_then_serve_file_round_trip`، `test_serve_file_rejects_path_traversal_signed_for_a_fake_key` |
| **بحث تدقيق الحساب** (`/platform/audit` كان بلا مرشِّح `action` بخلاف `/admin/audit`) | `factory_audit` صار يقبل `?action=` بنفس نمط `admin_audit` | `test_factory_audit_filters_by_action` |

فوترة التخزين نفسها (`jobs.run_storage_billing`) كانت مُنفَّذة وخاملة التكرار
سلفاً — الآن `size_bytes` الذي تقرؤه **مقيسٌ فعلياً** لا مُصرَّحاً به، فالفاتورة
تعكس استهلاكاً حقيقياً لأوّل مرّة.

### ⬜ ناقص
لا شيء من قائمة PR-8 الأصلية. الحدّ الوحيد: حجم الرفع الأقصى ثابتٌ عملياتي
(`SILK_PLATFORM_MAX_IMAGE_BYTES`، افتراضه ١٠ ميجابايت) — تكبيره قرار مالك لو
احتاجته صورةٌ أكبر.

---

## PR-7 — القمع وبوّابته · the comparison funnel

### ✅ نُفِّذ الآن · shipped now (`docs/PLATFORM_PR7.md`)

| بند | الموضع | الدليل |
|---|---|---|
| **آلة حالات القمع الخمس** `compared→selected→extracted→drafted→sent` | `silk_platform/funnels.py` + ٨ نقاط تحت `/platform/funnels` | `tests/test_platform_funnels.py` (٣١ اختباراً) |
| **بوّابة الطبقة** (Gold/Platinum فقط) | `funnels.require_funnel_feature` ← `entitlements.require_feature` (لا مقارنة طبقات محلّية) | Basic/Silver ⇒ 403 بدعوة ترقية قابلة للقراءة آلياً |
| **سقف `funnel_max_studies` مفروضٌ فعلياً** | `funnels.attach_study` — يُقاس ويُكتب تحت `BEGIN IMMEDIATE` واحد | الحادية عشرة لـGold تُرفَض والعدد لا يتجاوز ١٠؛ Platinum بلا سقف |
| **عزل جدولَي الوصل بلا عمود مالك** | كل معرّف (دراسة/عميل/مسودّة) يُتحقَّق عبر `repository` **قبل** الكتابة | دراسة/عميل حسابٍ آخر ⇒ رفض + **صفر** صفّ وصل مكتوب |
| **`sent` تغيّر الواقع** لا العمود | `funnels.send` يصفّ بريداً فعلياً ثم يختم `sent_at` | العامل الحقيقي يُخرِج الرسائل ويُخصَم $0.05 لكلٍّ |
| بوّابتا المال قبل الصفّ | مديونية + كفاية رصيد بنفس ترتيب `launch_study` | 402 (لا 409) وصفر بريد مصفوف عند الرفض |
| عمودا القرار | ترحيل `004_funnel_selection_columns.sql` (`selected_study_id`, `draft_id`) | مفتاحان أجنبيان يمنعان حذف مسودّة/دراسة يشير إليها قمع |

**الدلالة مُتبنّاة بقرار المالك لا مُستنبَطة صمتاً:** المواصفة تسمّي الحالات الخمس
ولا تشرحها. القراءة المُعتمَدة مُشتقّة من المخطّط نفسه (جدولا الوصل +
`funnel_max_studies`) ومُوثَّقة كاملةً في `docs/PLATFORM_PR7.md` §١ — فمن يراجعها
لاحقاً يرى **ما استُنبِط** مقابل **ما كان مكتوباً**.

**توسيع حارس العزل:** `funnel_studies`/`funnel_prospects` أُدرِجا في
`TENANT_TABLES` **رغم غياب عمود المالك فيهما** — بلا ذلك لم يكن الحارس يراهما
إطلاقاً، فكتابةُ صفّ وصلٍ بمعرّف مستأجرٍ آخر تمرّ بلا أن يُحمِّر شيء. الإدراج
يُلزِم كل جملة عليهما بسببٍ مكتوب يشرح كيف تُفرَض الملكية.

### ⬜ ناقص
واجهة القمع (شاشات المقارنة/الاختيار) — طبقة عرض (PR-6).

---

## PR-6 — لوحات + نظام تصميم · **لم تبدأ** · not started

**تصحيحُ صفٍّ بائت (بلاغ مالك حيّ).** كان هذا القسم يقول «لا واجهة للمنصّة اليوم
إطلاقاً»، وهو غيرُ صحيحٍ منذ #196: شُحنت `web/platform.html`. لكنها **لوحةُ حالةٍ
قارئةٌ فقط**، لا واجهةُ تشغيل — وبقاءُ الصفّ على «لا شيء» أخفى الفرقَ بين
الاثنين، فدخل المالكُ الشاشةَ وسأل: «كيف أستعمل؟ لا يوجد أيّ خيار». الرقمُ
يُقاس ولا يُوصَف:

| | العدد |
|---|---|
| نقاط نهاية **تكتب** في `silk_platform/api.py` (POST/PATCH/DELETE) | **٢٩** |
| ما تستدعيه `web/platform.html` منها | **٢** — الدخول والخروج فقط |

فما شُحن في #196 هو الدخولُ + عرضُ (محفظة · طبقة · حصّة · مقاعد · دراسات ·
مستخدمون · دفتر). و**بلا زرٍّ واحد** لأيٍّ من: `POST /studies` (إنشاء دراسة) ·
`/studies/{id}/launch` (إطلاق) · `/report` · `/archive` · `/prospects` ·
`/smtp-configs` · `/funnels` + `extract|select|send` · `/users` (مستخدم فرعي) ·
`/admin/fund` (تمويل) · `/admin/accounts/{id}/tier` · `/admin/kill-switch`.

الباقي في PR-6: لوحة `basic`/`full` (`entitlements.dashboard_level()` جاهزة)،
قواميس الترجمة المفتاحية + RTL، صفحة `reset-password`، وواجهة القمع.
**ويحتاج قرارات شكل/هوية بصرية من المالك قبل البدء.**

**قيدُ ترتيبٍ مُلزِم لا اختيار:** محفظةُ المصنع تبدأ `$0.00`، وأيّ إطلاق يرتدّ
بـ`insufficient_balance`، والتمويلُ يمرّ عبر `/admin/fund` (مسارُ أدمِن). فأيّ
«واجهةُ مصنعٍ صالحةٌ للاستعمال» تلزمها نصفُ أدمِنٍ في **نفس** الموجة، وإلا شُحنت
شاشةٌ كلُّ أزرارها تُرجِع رفضاً.

**وعيبُ عرضٍ مرصود (ليس خطأ منطق):** الطبقة `silver` تُظهِر أربعةَ صفوفٍ
«غير متاح» (تصدير/API/علامة بيضاء ولوحة `basic`) — والمصفوفة صحيحة
(`models.TIER_LIMITS`: الثلاثةُ في `platinum` فقط). لكن «غير متاح» تُقرأ «مكسور»
بدل «ليست في خطّتك»؛ النصّ الصحيح يسمّي الباقةَ التي تفتحها.

---

## قاعدة التحديث · update rule
كل PR يُحدِّث هذا الملف **في نفس الفرع**: ينقل ما أنجزه إلى «مُنفَّذ» ويُصحِّح
«ناقص». الوثيقة التي تصف واقعاً قديماً أخطر من غيابها.
