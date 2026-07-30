-- طابع زمن المطالبة بصفّ البريد · when a worker pass claimed this row.
-- بلا هذا لا يُمكن تمييز صفٍّ عالقٍ في 'sending' (عملية تعطّلت بعد المطالبة
-- وقبل تحديث الحالة) عن صفٍّ يُعالَج الآن فعلاً — الحاصد (PR-5) يحتاج عمراً
-- ليقرّره. NULL لصفوف queued/sent/failed/suppressed؛ يُختَم عند المطالبة
-- ويُمسَح عند إعادة الطرح للطابور.
-- Needed to distinguish a genuinely-stuck 'sending' row (crashed mid-claim)
-- from one actively in flight; the PR-5 reaper compares this to a staleness
-- threshold. NULL outside 'sending'; stamped on claim, cleared on re-queue.

ALTER TABLE email_queue ADD COLUMN claimed_at TEXT;
