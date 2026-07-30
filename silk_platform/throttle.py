"""خنق محاولات الدخول — cross-process login throttle (platform DB backed).

بلاغ المالك: حالةٌ على مستوى الوحدة (dict) تنفصل لكل worker process، فسقف «١٠
محاولات» يصير ١٠×عدد العمّال فعلياً، ويُصفَّر كلّياً عند كل إعادة نشر. النشر
الحالي عملية uvicorn واحدة (Dockerfile/railway.json بلا `--workers`)، لكن إضافة
عامل ثانٍ لاحقاً كانت ستُضعف الحماية **صامتةً**. الحالة هنا في قاعدة المنصّة:
مشتركة بين كل العمليات وتصمد لإعادة التشغيل، فلا يعتمد الأمان على طوبولوجيا النشر.

Shared, restart-durable throttle state. No Redis: the platform DB is already the
one shared store (stdlib-first, per the repo's settled decisions).
"""
from __future__ import annotations

import datetime
import os
import sqlite3

MAX_FAILURES = 10
WINDOW_S = 300


def _limits() -> tuple[int, int]:
    """السقف والنافذة (قابلان للضبط بالبيئة) — max failures and window seconds."""
    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        try:
            return max(1, int(raw)) if raw else default
        except ValueError:
            return default
    return (_int("SILK_PLATFORM_LOGIN_MAX_FAILURES", MAX_FAILURES),
            _int("SILK_PLATFORM_LOGIN_WINDOW_S", WINDOW_S))


def identity(email: str, ip: str | None) -> str:
    """هويّة الخنق — normalized (email|ip) key."""
    return f"{(email or '').strip().lower()}|{ip or '-'}"


def _cutoff(window_s: int) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - datetime.timedelta(seconds=window_s)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def is_throttled(conn: sqlite3.Connection, ident: str) -> bool:
    """هل بلغت الهويّة السقف في النافذة؟ — shared across every worker process."""
    max_failures, window_s = _limits()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM login_attempts WHERE identity = ? "
        "AND created_at >= ?", (ident, _cutoff(window_s))).fetchone()
    return int(row["c"] if hasattr(row, "keys") else row[0]) >= max_failures


def record_failure(conn: sqlite3.Connection, ident: str) -> None:
    """سجّل محاولة فاشلة — durable, visible to all workers immediately."""
    _, window_s = _limits()
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    conn.execute("INSERT INTO login_attempts (identity, created_at) VALUES (?,?)",
                 (ident, now))
    # طهّر ما خرج من النافذة لهذه الهويّة (الجدول لا ينمو بلا حدّ).
    conn.execute("DELETE FROM login_attempts WHERE identity = ? AND created_at < ?",
                 (ident, _cutoff(window_s)))
    conn.commit()


def clear(conn: sqlite3.Connection, ident: str) -> None:
    """امسح عدّاد هويّة بعد دخول ناجح — reset on success."""
    conn.execute("DELETE FROM login_attempts WHERE identity = ?", (ident,))
    conn.commit()


def prune(conn: sqlite3.Connection) -> int:
    """احذف كل ما خرج من النافذة — housekeeping job; returns rows removed."""
    _, window_s = _limits()
    cur = conn.execute("DELETE FROM login_attempts WHERE created_at < ?",
                       (_cutoff(window_s),))
    conn.commit()
    return cur.rowcount
