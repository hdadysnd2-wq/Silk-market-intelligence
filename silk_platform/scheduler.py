"""مجدول مهام المنصّة — in-process job scheduler (PR-2).

خيطٌ داخل العملية لا خدمة cron منفصلة — نفس سابقة `silk_collectors.start_scheduler`
وسببها البنيوي: وحدة تخزين Railway تُركَّب على خدمة واحدة فقط، فمهمّةٌ في خدمة
أخرى لن ترى قاعدة المنصّة. Opt-in، وخيط daemon، وفشلُ تشغيلةٍ يُسجَّل ولا يقتل الخيط.

المواعيد من المواصفة (§12):
- تصفير الحصص الشهري: أوّل الشهر 00:00 UTC (يتخطّى Basic).
- فوترة التخزين: أوّل الشهر 01:00 UTC.
- تنظيف الجلسات: يومياً 02:00 UTC (+ تقليم عدّادات الخنق).

منطق «هل حلّ الموعد؟» دالّة نقيّة (`due_jobs`) تأخذ الوقت وسجلّ آخر تشغيلة،
فتُختبَر بلا نوم ولا خيوط. The due-time logic is pure and unit-testable.
"""
from __future__ import annotations

import datetime
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()

# اسم المهمّة → (يوم الشهر أو None ليومية، ساعة UTC)
JOB_SCHEDULE: dict[str, tuple[int | None, int]] = {
    "monthly_quota_reset": (1, 0),    # أوّل الشهر 00:00
    "storage_billing": (1, 1),        # أوّل الشهر 01:00
    "session_cleanup": (None, 2),     # يومياً 02:00
}


def _slot(name: str, now: datetime.datetime) -> str:
    """مُعرِّف الفتحة الحالية للمهمّة — a stable id for the current due window.

    الشهرية تُعرَّف بـ'YYYY-MM' واليومية بـ'YYYY-MM-DD'، فيصير «هل نُفِّذت في
    هذه الفتحة؟» مقارنةَ نصٍّ واحدة — وتصير المهمّة **خاملة التكرار** حتى لو
    استُدعيت الحلقة مراراً في نفس الساعة. Slot id ⇒ idempotent within a window.
    """
    day, _hour = JOB_SCHEDULE[name]
    if day is None:
        return now.strftime("%Y-%m-%d")
    return now.strftime("%Y-%m")


def due_jobs(now: datetime.datetime, last_run: dict[str, str]) -> list[str]:
    """المهام التي حلّ موعدها ولم تُنفَّذ في فتحتها — pure; no I/O, no sleeping.

    `last_run` يربط اسم المهمّة بمُعرِّف آخر فتحة نُفِّذت فيها.

    **اللحاق بعد التوقّف** (catch-up): المهمّة الشهرية تستحقّ التشغيل في أي يوم
    **بعد** موعدها من نفس الشهر لا في يومه فقط. لو كانت الحاوية متوقّفة يوم ١
    (نشر، إعادة تشغيل، تعطّل) لكانت فوترة التخزين تُسقَط لذلك الشهر كلّه صمتاً —
    والدفتر غير قابل للتعديل فلا تُصحَّح لاحقاً. المهمّتان الشهريّتان محروستان
    بمفتاح الفترة (خاملتا التكرار) فالتشغيل المتأخّر آمن وأصحّ من الإسقاط.
    A monthly job missed because the process was down still runs later that
    month; both monthly jobs are period-guarded, so late execution is safe.
    """
    out: list[str] = []
    for name, (day, hour) in JOB_SCHEDULE.items():
        if day is None:                       # يومية · daily
            if now.hour < hour:
                continue
        else:                                 # شهرية + لحاق · monthly with catch-up
            if now.day < day:
                continue
            if now.day == day and now.hour < hour:
                continue
        if last_run.get(name) == _slot(name, now):
            continue          # نُفِّذت سلفاً في هذه الفتحة
        out.append(name)
    return out


def run_job(name: str) -> object:
    """نفّذ مهمّةً واحدة باتصالها الخاص — returns the job's result.

    كل مهمّة تفتح اتصالها وتغلقه، فتشغيلةٌ فاشلة لا تترك اتصالاً معلّقاً.
    """
    from . import auth, db, jobs, throttle
    conn = db.connect()
    try:
        if name == "monthly_quota_reset":
            return jobs.run_monthly_quota_reset(conn)
        if name == "storage_billing":
            return jobs.run_storage_billing(conn)
        if name == "session_cleanup":
            removed = auth.cleanup_expired_sessions(conn)
            pruned = throttle.prune(conn)      # تقليم عدّادات الخنق المنتهية
            return {"sessions_removed": removed, "throttle_pruned": pruned}
        raise ValueError(f"unknown job: {name}")
    finally:
        conn.close()


def run_email_pass(conn, *, sender=None) -> dict:
    """مرور واحد على طابور البريد — reap stuck rows, then process the queue.

    يُستدعى كل نبضة مجدول (لا آلة تقويم كـ`due_jobs`): البريد يحتاج زمن
    استجابة بمقياس النبضة لا اليوم/الشهر. الحاصد **قبل** المعالجة كي تُعاد
    الصفوف العالقة إلى `queued` في نفس المرور الذي يُعالجها.
    Runs every poll tick, not on due_jobs' calendar — email needs poll-scale
    latency. Reap before process so a just-requeued stuck row is picked up
    in the same pass.
    """
    from . import email_queue

    def _env_int(name: str, default: int) -> int:
        # فارغ (اتفاق .env.example) لا يعني صفراً — يعني «غير مضبوط» فيرجع
        # الافتراضي؛ `int("")` كان يرفع فيُسقِط كل نبضة صامتاً إلى الأبد.
        # Empty means unset, not zero — bare int() would raise every tick.
        raw = os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else default
        except ValueError:
            return default

    stale_s = _env_int("SILK_PLATFORM_EMAIL_STUCK_SECONDS", 900)
    max_attempts = _env_int("SILK_PLATFORM_EMAIL_MAX_ATTEMPTS", 5)
    reaped = email_queue.reap_stuck(conn, stale_after_seconds=stale_s,
                                    max_attempts=max_attempts)
    processed = email_queue.process_queue(conn, sender=sender or email_queue.sender)
    return {"reaped": reaped, "processed": processed}


def run_due(now: datetime.datetime | None = None,
            last_run: dict[str, str] | None = None) -> dict[str, object]:
    """نفّذ كل ما حلّ موعده وحدّث سجلّ الفتحات — one pass; testable directly.

    يُعدِّل `last_run` في مكانه (المُنادي يحتفظ به). فشلُ مهمّةٍ يُسجَّل ولا
    يمنع البقيّة، و**لا يُوسَم كمنفَّذ** كي تُعاد المحاولة في المرور التالي.
    A failed job is not marked done, so the next pass retries it.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    last_run = last_run if last_run is not None else {}
    results: dict[str, object] = {}
    for name in due_jobs(now, last_run):
        try:
            results[name] = run_job(name)
            last_run[name] = _slot(name, now)
            log.info("platform job %s done: %s", name, results[name])
        except Exception as exc:  # noqa: BLE001 — تشغيلة تفشل، الخيط يبقى حيّاً
            results[name] = f"failed: {exc}"
            log.warning("platform job %s failed: %s", name, exc)
    return results


def start(poll_seconds: float | None = None):
    """ابدأ خيط المجدول — opt-in via SILK_PLATFORM_SCHEDULER=1; idempotent.

    غير مضبوط ⇒ **لا خيط إطلاقاً**: تشغيلة اختبار أو تطوير لا تكتب فواتير ولا
    تصفّر حصصاً في قاعدة أحد. يرجّع الخيط، أو None عند التعطيل/التكرار.
    Unset ⇒ no thread at all (tests and dev never bill or reset silently).
    """
    global _started
    if os.environ.get("SILK_PLATFORM_SCHEDULER") != "1":
        return None
    with _lock:
        if _started:
            return None
        _started = True

    try:
        poll = float(poll_seconds if poll_seconds is not None
                     else os.environ.get("SILK_PLATFORM_SCHEDULER_POLL_S", "300"))
    except (TypeError, ValueError):
        poll = 300.0
    poll = max(1.0, poll)   # حلقة بلا نوم تحرق نواة · never a spin loop

    last_run: dict[str, str] = {}

    def _loop() -> None:
        from . import db
        while True:
            try:
                run_due(last_run=last_run)
            except Exception as exc:  # noqa: BLE001 — الحلقة تبقى حيّة
                log.warning("platform scheduler pass failed: %s", exc)
            try:
                conn = db.connect()
                try:
                    run_email_pass(conn)
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001 — نبضة تفشل، الخيط يبقى حيّاً
                log.warning("platform email pass failed: %s", exc)
            time.sleep(poll)

    thread = threading.Thread(target=_loop, name="silk-platform-scheduler",
                              daemon=True)
    thread.start()
    log.info("platform scheduler started (poll=%.0fs)", poll)
    return thread
