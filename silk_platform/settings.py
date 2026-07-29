"""إعدادات النظام العامة — global system settings incl. the kill-switch.

مفتاح القتل العام: منطقي في system_settings. حين يكون مُفعَّلاً يتوقّف كل
بريد صادر على مستوى المنصّة (يُفحَص لكل بريد وقت الإرسال)، ويُسجَّل تفعيله في
التدقيق بمعرّف الأدمِن. Admin-only toggle; checked per email at send time.
"""
from __future__ import annotations

import sqlite3

from . import audit
from .db import now_iso

KILL_SWITCH_KEY = "kill_switch"


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM system_settings WHERE key = ?",
                       (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str,
                *, updated_by_user_id: int | None = None) -> None:
    conn.execute(
        "INSERT INTO system_settings (key, value, updated_at, updated_by_user_id) "
        "VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at, updated_by_user_id = excluded.updated_by_user_id",
        (key, value, now_iso(), updated_by_user_id))
    conn.commit()


def kill_switch_on(conn: sqlite3.Connection) -> bool:
    """هل مفتاح القتل مُفعَّل؟ — read the global kill-switch flag."""
    return get_setting(conn, KILL_SWITCH_KEY, "0") == "1"


def set_kill_switch(conn: sqlite3.Connection, on: bool, *,
                    admin_user_id: int) -> None:
    """بدّل مفتاح القتل وسجّل — toggle + audit-log the activation with admin id."""
    set_setting(conn, KILL_SWITCH_KEY, "1" if on else "0",
                updated_by_user_id=admin_user_id)
    audit.record(conn, action="kill_switch_toggled", user_id=admin_user_id,
                 resource_type="system_settings", resource_id=KILL_SWITCH_KEY,
                 changes={"on": bool(on)})
    conn.commit()
