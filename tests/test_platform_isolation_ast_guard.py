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
TENANT_TABLES = {
    "studies", "prospects", "drafts", "images", "smtp_configs",
    "comparison_funnels", "wallets", "ledger_entries", "email_queue",
    "suppression_list", "consent_registry",
}

# قيود المالك المقبولة في نصّ الجملة.
OWNER_PREDICATES = ("owner_id", "account_id", "sending_account_id")

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
        for node in ast.walk(tree):
            # نصّ حرفيّ مفرد
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
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
        if any(p in sql for p in OWNER_PREDICATES):
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
                 if _tables_touched(s) and not any(p in s for p in OWNER_PREDICATES)
                 and not _is_allowlisted(m, s)]
        assert found, "the guard failed to catch a deliberately unscoped query"
    finally:
        globals()["_PKG"] = pathlib.Path(__file__).resolve().parent.parent / "silk_platform"
