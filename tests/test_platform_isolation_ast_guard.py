"""حارس بنيوي للعزل — AST guard: no unscoped SQL on tenant tables.

**لماذا:** عزل المستأجرين كان مضموناً بمراجعةٍ يدوية فقط. كل استعلام في
`silk_platform/` يمرّ اليوم عبر `repository.py` أو يحمل قيد المالك صراحةً — لكن
لا شيء **آلي** يمنع PR قادماً من إضافة `SELECT * FROM studies WHERE id = ?` بلا
قيد مالك، فيصير كل مستأجر يقرأ دراسات الآخرين. مراجعةٌ يدوية تنسى؛ هذا الاختبار
لا ينسى.

**كيف:** يقرأ AST كل وحدة في `silk_platform/`، يستخرج كل نصّ SQL حرفيّ، ويؤكّد
أن أي جملة تمسّ جدولاً مُستأجَراً إمّا:
  (أ) تحمل قيد مالك (`owner_id` / `account_id` / `sending_account_id`)، أو
  (ب) مُدرَجة في `_INTENTIONAL_GLOBAL` بسببٍ مكتوب (قراءات مجمّعة للأدمِن/المحلّل،
      أو عامل الطابور الذي يعمل عبر الحسابات بحكم وظيفته).

إضافةُ استعلام غير مُنطَّق تُسقِط الاختبار وتُجبر قراراً صريحاً: إمّا تُنطِّقه،
أو تُدرِجه بسبب. Adding an unscoped query fails CI and forces a deliberate choice.
"""
import ast
import pathlib
import re

import pytest

_PKG = pathlib.Path(__file__).resolve().parent.parent / "silk_platform"

# الجداول التي تحمل عمود مالك (مُستأجَرة) — من الترحيل 001.
# `users` مُدرَج منذ PR-2: صار للمنصّة مسارُ إدارة مستخدمين فرعيين يكتب في هذا
# الجدول، فاستعلامٌ غير منطَّق فيه يعني قراءة/تعديل مستخدمي مستأجرٍ آخر — وهو
# أخطر من تسريب دراسة لأنه مسار صلاحية. Added in PR-2 with sub-user management.
TENANT_TABLES = {
    "studies", "prospects", "drafts", "images", "smtp_configs",
    "comparison_funnels", "wallets", "ledger_entries", "email_queue",
    "suppression_list", "consent_registry", "users",
    # جدولا وصل القمع مُدرَجان منذ PR-7 **رغم أنهما بلا عمود مالك**: مفتاحهما
    # مركَّب فقط، فلو تُركا خارج القائمة لما رآهما الحارس إطلاقاً — و
    # `INSERT INTO funnel_studies` بمعرّف دراسةٍ لحسابٍ آخر يربط بيانات مستأجرٍ
    # بقمع مستأجر ثانٍ بلا أن يُحمِّر شيء. إدراجهما يُلزِم كل جملة عليهما بسببٍ
    # مكتوب يشرح **كيف** تُفرَض الملكية، فيصير الخطر موثَّقاً لا خفيّاً.
    # Included despite having NO owner column: otherwise the guard would never
    # see them, and a cross-tenant join-table INSERT would pass silently.
    "funnel_studies", "funnel_prospects",
}

# أعمدة المالك · owner columns.
OWNER_COLUMNS = ("owner_id", "account_id", "sending_account_id")


def _has_owner_predicate(sql: str) -> bool:
    """هل يقيّد المالكَ **فعلاً**؟ — is the owner column an actual constraint?

    الفحص السابق كان «هل يظهر اسم عمود المالك في النصّ؟» فكان
    `SELECT u.account_id AS account_id FROM users WHERE token = ?` يمرّ لأن الاسم
    ظهر في قائمة الإسقاط — لا في قيدٍ. حارسٌ يُخدَع بذكر الاسم يعطي طمأنينة
    كاذبة، فالتمييز الآن على **شكل** الاستعمال:

    - `INSERT`: وجود العمود في قائمة الأعمدة **هو** الشكل الصحيح (الملكية تُكتب).
    - غيرها (`SELECT`/`UPDATE`/`DELETE`): يجب أن يكون العمود في مقارنة —
      `account_id = ?` أو `IN (...)` أو `IS NULL` — أي قيداً لا إسقاطاً.

    Distinguishes a real constraint from a mere mention of the column name.
    """
    if re.match(r"\s*INSERT\b", sql, re.IGNORECASE):
        return any(col in sql for col in OWNER_COLUMNS)
    return any(re.search(rf"\b{col}\s*(=|!=|<>|\bIN\b|\bIS\b)", sql, re.IGNORECASE)
               for col in OWNER_COLUMNS)

# استثناءات مقصودة **مقيَّدة بالوحدة**: (الوحدة، مقتطف) → السبب.
# تقييد الوحدة يُبقي الحارس حادّاً: تحويلات حالة الطابور مسموحة في عامل الطابور
# (معرّفات الصفوف من مسحه الخاص، لا من طلب) وتبقى **مرفوضة** لو أضافها أحد في
# `api.py` على مسار طلبٍ مستأجَر. Module-scoped so a request path stays guarded.
_INTENTIONAL_GLOBAL: dict[tuple[str, str], str] = {
    # عامل الطابور مهمّة خلفية تعمل عبر كل الحسابات بحكم وظيفتها (لا طلب مستأجر).
    ("email_queue.py", "SELECT * FROM email_queue WHERE status = 'queued'"):
        "background worker processes all accounts by design (not a tenant request)",
    ("email_queue.py", "SELECT COUNT(*) AS c FROM email_queue WHERE status = 'queued'"):
        "worker summary counter across accounts (background job)",
    # تحويلات حالة الصفّ داخل حلقة العامل: `id` يأتي من مسح العامل نفسه، ولا
    # يُقبَل من مدخلات المستخدم في أي مسار.
    ("email_queue.py", "UPDATE email_queue SET status"):
        "worker-owned row-id state transitions; ids come from the worker's own "
        "scan and are never user-supplied",
    ("email_queue.py", "SELECT * FROM smtp_configs WHERE id = ?"):
        "worker reads the smtp config the study was launched with (already "
        "ownership-validated at launch time)",
    ("email_queue.py", "SELECT id, attempts FROM email_queue WHERE status = 'sending'"):
        "PR-5 reaper scan for stuck rows is a background job across all "
        "accounts by design, same rationale as the queued-row scan above",
    # فوترة التخزين تجمع لكل حساب بـGROUP BY owner_id ثم تشحن كل حساب على حدة.
    ("jobs.py", "FROM images GROUP BY owner_id"):
        "monthly billing aggregates per account via GROUP BY owner_id",
    # مقاييس الأدمِن/المحلّل: مجمّعات بلا معرّفات ولا PII (COUNT فقط).
    ("api.py", "SELECT COUNT(*) AS c FROM studies WHERE state = 'in_progress'"):
        "admin metric: platform-wide count, no ids and no PII",
    ("api.py", "SELECT state, COUNT(*) AS c FROM studies GROUP BY state"):
        "analyst aggregate: counts by state, no ids and no PII",
    ("api.py", "SELECT industry, COUNT(*) AS prospects FROM prospects"):
        "analyst aggregate: counts by industry, no ids and no PII",
    # فحص ملكية بعد الجلب: يُقرأ الصفّ بمعرّفه ثم يُقارَن owner_id في بايثون
    # ويُرفَض 422 عند عدم التطابق (`_validate_smtp_binding`).
    ("api.py", "SELECT * FROM smtp_configs WHERE id = ?"):
        "ownership verified immediately after fetch in _validate_smtp_binding "
        "(rejects with 422 when owner_id differs)",
    ("api.py", "SELECT storage_key, mime_type FROM images WHERE storage_key = ?"):
        "GET /files is a public signed-URL route (PR-8), same trust model as "
        "/platform/unsubscribe — the HMAC signature verified just above is "
        "the actual authorization; storage_key is UNIQUE platform-wide (not "
        "per-account) so an owner predicate doesn't apply to this lookup",
    # ── مسارات الهويّة على `users` · identity paths (PR-2) ───────────────────
    # الدخول وإعادة التعيين **يجب** أن تكون عالمية: البريد هو هويّة الدخول وهو
    # فريد على مستوى المنصّة، فالحساب غير معروف بعد قبل أن تُحلّ الهويّة. تقييدها
    # بحساب يعني استحالة تسجيل الدخول أصلاً.
    ("auth.py", "SELECT * FROM users WHERE email = ?"):
        "login must resolve a globally-unique email before any account is known "
        "(the account is a RESULT of authentication, not an input to it)",
    ("auth.py", "SELECT id FROM users WHERE email = ?"):
        "password-reset request resolves the global login identity; the endpoint "
        "returns 200 regardless so it leaks no existence",
    ("auth.py", "SELECT language_preference FROM users WHERE email = ?"):
        "same global-identity resolution as issue_reset_token, for the reset "
        "email's language — called only after that lookup already succeeded",
    ("auth.py", "SELECT id FROM users WHERE id = ?"):
        "admin-issued reset (silk_admin-only route) targets a user by id across "
        "accounts by design; the route itself is role-walled",
    ("auth.py", "UPDATE users SET password_hash"):
        "reset confirmation is authenticated by the single-use token itself, not "
        "by a session, so no account context exists at that point",
    ("auth.py", "SELECT s.*, u.account_id AS account_id"):
        "session resolution is keyed by the sha256 token hash (unguessable) and "
        "is what PRODUCES the account context every other query scopes by",
    # ── التأسيس والجهوزيّة · bootstrap + readiness ────────────────────────────
    # كلاهما يعمل **قبل وجود أي سياق طلب**: `maybe_seed` عند الإقلاع (لا مستخدم
    # ولا حساب بعد — الحساب **نتيجةُ** البذر لا مدخلٌ له)، و`readiness` تُغذّي
    # `/health` بعددٍ على مستوى المنصّة. فلا account_id يُنطَّق به أصلاً.
    # لا يُرجَع أي صفّ ولا بريد — منطقيّ/عدديّ فقط، فلا سطح تسريب.
    ("bootstrap.py", "SELECT 1 FROM users WHERE role = 'silk_admin' LIMIT 1"):
        "boot-time seed predicate: runs before any request context exists, so no "
        "account is known yet (the accounts are the RESULT of seeding). Returns a "
        "boolean only — never a row",
    ("bootstrap.py", "SELECT COUNT(*) FROM users"):
        "platform-wide readiness counter for /health (answers 'was this DB ever "
        "seeded?'). A count, not identities — /health is public so it must never "
        "expose emails",
    ("seed.py", "SELECT id FROM users WHERE role = 'silk_admin' LIMIT 1"):
        "bootstrap lookup for the seeded silk_admin; runs at seed time before any "
        "request context exists",
    ("users.py", "SELECT 1 FROM users WHERE id = ?"):
        "exists_anywhere returns a BOOLEAN only (never a row) so the endpoint can "
        "audit a cross-tenant attempt while still answering 404 to the client",
    # ── قمع المقارنة (PR-7) · comparison funnels ──────────────────────────────
    ("funnels.py", "SELECT 1 FROM comparison_funnels WHERE id = ?"):
        "exists_anywhere returns a BOOLEAN only (never a row) so the endpoint can "
        "audit a cross-tenant attempt while still answering 404 — same pattern "
        "and same rationale as users.exists_anywhere above",
    # جدولا الوصل بلا عمود مالك: الملكية تُفرَض **قبل** كل كتابة هنا — القمع عبر
    # funnels.get (منطَّق بالمالك) وكل معرّف دراسة/عميل/مسودّة عبر repository
    # (منطَّق بالمالك)، فمعرّفٌ لحسابٍ آخر يُرفَض قبل أن يصل هذه الجُمَل.
    ("funnels.py", "SELECT study_id FROM funnel_studies WHERE funnel_id = ?"):
        "join table has no owner column; the funnel_id was ownership-verified via "
        "funnels.get() (owner-scoped) before this read, and no row enters "
        "funnel_studies without its study passing repository's owner predicate",
    ("funnels.py", "SELECT prospect_id FROM funnel_prospects WHERE funnel_id = ?"):
        "join table has no owner column; funnel_id ownership-verified via "
        "funnels.get() before this read, and rows only enter after each prospect "
        "id passed repository.prospects().get(account_id, ...)",
    ("funnels.py", "INSERT INTO funnel_studies (funnel_id, study_id)"):
        "both ids are ownership-verified immediately before this write: the funnel "
        "via funnels.get(account_id, ...) and the study via "
        "repository.studies(conn).get(account_id, ...) — a foreign id raises first",
    ("funnels.py", "DELETE FROM funnel_studies WHERE funnel_id = ? AND study_id = ?"):
        "the funnel was ownership-verified via funnels.get(account_id, ...) above; "
        "a foreign funnel_id never reaches here, and rowcount==0 is reported as "
        "not_attached rather than silently succeeding",
    ("funnels.py", "INSERT OR IGNORE INTO funnel_prospects (funnel_id, prospect_id)"):
        "every prospect id is verified via repository.prospects().get(account_id, "
        "...) and the funnel via funnels.get(account_id, ...) before this write; "
        "a foreign id raises prospect_not_found first",
}

_SQL_RE = re.compile(r"\b(SELECT|INSERT\s+INTO|INSERT\s+OR\s+IGNORE\s+INTO|UPDATE|DELETE\s+FROM)\b",
                     re.IGNORECASE)


def _iter_sql_literals():
    """كل نصّ SQL حرفيّ في الحزمة — (module, statement) for every SQL string.

    يجمع النصوص المتلاصقة (implicit concatenation) لأن الجُمَل مكتوبة على أسطر،
    فقيد `WHERE owner_id = ?` قد يكون في جزء تالٍ من نفس الجملة.
    """
    for path in sorted(_PKG.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # أجزاء الـf-string الحرفيّة تُزار **مرّتين** بـ`ast.walk`: مرّة داخل
        # `JoinedStr` ومرّة كعقدة `Constant` مستقلّة. بلا استثنائها كان
        # `f"UPDATE users SET {cols} WHERE account_id = ?"` يُبلَّغ عنه كجملة
        # مقطوعة «UPDATE users SET» بلا قيد مالك — **إنذار كاذب** يدفع لإدراج
        # استثناء لا حاجة له، وكل استثناء زائد يوسّع الثقب الحقيقي.
        # f-string fragments are visited twice by ast.walk; count them once.
        in_fstring = {id(v) for node in ast.walk(tree)
                      if isinstance(node, ast.JoinedStr)
                      for v in node.values if isinstance(v, ast.Constant)}
        for node in ast.walk(tree):
            # نصّ حرفيّ مفرد (وليس جزءاً من f-string سبق جمعه)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in in_fstring:
                    continue
                if _SQL_RE.search(node.value):
                    yield path.name, " ".join(node.value.split())
            # f-string / تلاصق: اجمع كل الأجزاء الحرفيّة في عقدة واحدة
            elif isinstance(node, ast.JoinedStr):
                parts = [v.value for v in node.values
                         if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                joined = " ".join(" ".join(p.split()) for p in parts)
                if _SQL_RE.search(joined):
                    yield path.name, joined


def _tables_touched(sql: str) -> set[str]:
    """الجداول المُستأجَرة التي تمسّها الجملة — tenant tables referenced."""
    found = set()
    for table in TENANT_TABLES:
        if re.search(rf"\b(FROM|INTO|UPDATE|JOIN)\s+{table}\b", sql, re.IGNORECASE):
            found.add(table)
    return found


def _is_allowlisted(module: str, sql: str) -> bool:
    """مُدرَجٌ بسببٍ **لهذه الوحدة** — allowlisted for this module specifically."""
    return any(mod == module and snippet in sql
               for (mod, snippet) in _INTENTIONAL_GLOBAL)


def test_every_tenant_query_is_owner_scoped_or_declared():
    """كل استعلام على جدول مُستأجَر منطَّقٌ بالمالك أو مُعلَن بسبب.

    الفشل هنا يعني: أضفتَ استعلاماً يمسّ بيانات مستأجر بلا قيد مالك. أضِف
    `AND owner_id = ?` (أو مرّ عبر `repository.TenantRepository`)، أو — إن كان
    عالمياً بقصد — أدرِجه في `_INTENTIONAL_GLOBAL` بسببٍ مكتوب.
    """
    violations = []
    for module, sql in _iter_sql_literals():
        # `repository.py` هو طبقة العزل نفسها: جُمَله مبنيّة بعمود المالك
        # (`{self.owner_col}`) الذي لا يظهر نصّاً حرفياً — تغطّيه اختبارات العزل.
        if module == "repository.py":
            continue
        tables = _tables_touched(sql)
        if not tables:
            continue
        if _has_owner_predicate(sql):
            continue
        if _is_allowlisted(module, sql):
            continue
        violations.append(f"{module}: {sql[:120]}")
    assert not violations, (
        "unscoped SQL on tenant table(s) — add an owner predicate, route through "
        "repository.TenantRepository, or declare it in _INTENTIONAL_GLOBAL with a "
        "reason:\n  " + "\n  ".join(violations))


def test_repository_is_the_only_place_building_tenant_sql_dynamically():
    """بناء SQL بأسماء جداول مُتغيّرة محصورٌ في طبقة العزل وحدها.

    f-string يضع اسم جدول أو عمود مالك من متغيّر هو بابٌ لتخطّي النطاق؛ يجوز في
    `repository.py` (حيث القيد مبنيّ بنيوياً) ويُمنَع في غيره.
    """
    offenders = []
    for path in sorted(_PKG.glob("*.py")):
        if path.name == "repository.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            literal = " ".join(v.value for v in node.values
                              if isinstance(v, ast.Constant)
                              and isinstance(v.value, str))
            if not _SQL_RE.search(literal):
                continue
            # اسم جدول مُستأجَر يأتي من تعبير (لا نصّ) ⇒ رفض.
            if re.search(r"\b(FROM|INTO|UPDATE|JOIN)\s*$", literal.strip(),
                         re.IGNORECASE):
                offenders.append(f"{path.name}: interpolated table name")
    assert not offenders, (
        "dynamic tenant-table SQL outside repository.py:\n  " + "\n  ".join(offenders))


def test_allowlist_entries_all_have_reasons():
    """كل استثناء يحمل سبباً غير فارغ — an undocumented exemption is a hole."""
    for (module, snippet), reason in _INTENTIONAL_GLOBAL.items():
        assert module.endswith(".py"), f"allowlist key must name a module: {module}"
        assert reason and len(reason) > 20, f"weak/missing reason for: {snippet}"


def test_guard_actually_catches_an_unscoped_query(tmp_path, monkeypatch):
    """الحارس نفسه يُختبَر: استعلام غير منطَّق **يجب** أن يُلتقَط.

    حارسٌ لا يُثبَت أنه يصطاد شيئاً قد يكون خاملاً بلا أن يعلم أحد
    («الاختبار الأخضر الفارغ»). نزرع وحدةً مخالفة ونؤكّد الالتقاط.
    """
    fake = tmp_path / "silk_platform_fake"
    fake.mkdir()
    (fake / "bad.py").write_text(
        'def leak(conn, sid):\n'
        '    return conn.execute("SELECT * FROM studies WHERE id = ?", (sid,))\n',
        encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "_PKG", fake, raising=False)
    globals()["_PKG"] = fake
    try:
        found = [f"{m}: {s}" for m, s in _iter_sql_literals()
                 if _tables_touched(s) and not _has_owner_predicate(s)
                 and not _is_allowlisted(m, s)]
        assert found, "the guard failed to catch a deliberately unscoped query"
    finally:
        globals()["_PKG"] = pathlib.Path(__file__).resolve().parent.parent / "silk_platform"
