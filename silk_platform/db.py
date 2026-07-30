"""طبقة قاعدة بيانات المنصّة — Silk platform DB layer (SQLite, stdlib only).

قاعدة بيانات مستقلّة عن محرّك ذكاء السوق (`data/silk.db`) كي لا تختلط بيانات
المستأجرين ببيانات التحليل ولا تُمسّ أبداً. `SILK_PLATFORM_DB` يوجّه الملف،
ويُشتَقّ من `SILK_DATA_DIR` (المتغيّر الموحّد لكل المخازن) حين لا يُضبَط صراحةً.

A dedicated DB, separate from the market-intelligence store. Pure stdlib
(sqlite3 + glob); imports offline, never touches the network, never fabricates.
"""
from __future__ import annotations

import datetime
import glob
import os
import re
import sqlite3

_DEFAULT_PATH = "data/platform.db"
_MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "..", "migrations", "platform")


def now_iso() -> str:
    """طابع زمني UTC بدقّة الثانية — ISO-8601 UTC stamp (timezone-aware)."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path() -> str:
    """مسار قاعدة المنصّة وقت النداء — resolve at call time (env or default).

    `SILK_PLATFORM_DB` صريحٌ أولاً، ثم اشتقاق من `SILK_DATA_DIR`، وإلا الافتراضي.
    """
    explicit = os.environ.get("SILK_PLATFORM_DB", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("SILK_DATA_DIR", "").strip()
    if base:
        return os.path.join(base, "platform.db")
    return _DEFAULT_PATH


def connect(path: str | None = None) -> sqlite3.Connection:
    """افتح اتصالاً وفعّل قيود المفاتيح الأجنبية — open a connection.

    `row_factory=Row` للوصول بالاسم، و`PRAGMA foreign_keys=ON` كي تُفرَض قيود
    الـFK فعلياً (SQLite يعطّلها افتراضياً). المجلّد الأب يُنشأ عند الحاجة.
    """
    p = path or db_path()
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # انتظر القفل بدل رمي «database is locked» فوراً — كاتب SQLite وحيد،
    # فالمعاملات المتزامنة تتسلسل بانتظار مهلة بدل الفشل. Serialize writers.
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def apply_migrations(path: str | None = None) -> list[str]:
    """طبّق ترحيلات migrations/platform/NNN_*.sql بالترتيب مرّة واحدة لكلٍّ.

    يرجّع قائمة النسخ المطبَّقة حديثاً. جدول التتبّع `platform_migrations`
    يُنشئه الترحيل 001 نفسه (bootstrap-safe). Idempotent.
    """
    applied: list[str] = []
    files = sorted(glob.glob(os.path.join(_MIGRATIONS_DIR, "[0-9]*.sql")))
    conn = connect(path)
    try:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS platform_migrations (
                           version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)""")
        cur.execute("SELECT version FROM platform_migrations")
        done = {r[0] for r in cur.fetchall()}
        for f in files:
            version = re.match(r"(\d+)", os.path.basename(f)).group(1)
            if version in done:
                continue
            with open(f, encoding="utf-8") as fh:
                cur.executescript(fh.read())
            cur.execute("INSERT INTO platform_migrations (version, applied_at) "
                        "VALUES (?, ?)", (version, now_iso()))
            applied.append(version)
        conn.commit()
    finally:
        conn.close()
    return applied


_initialized: set[str] = set()


def init_db(path: str | None = None, *, force: bool = False) -> None:
    """هيّئ قاعدة المنصّة — apply all migrations (safe to call repeatedly).

    تُخزَّن المسارات المهيّأة كي لا يُعاد فحص الترحيلات لكل طلب. `force=True`
    يتجاهل الذاكرة (للاختبارات التي تعيد إنشاء نفس المسار). Cached per path.
    """
    p = path or db_path()
    if not force and p in _initialized:
        return
    apply_migrations(p)
    _initialized.add(p)
