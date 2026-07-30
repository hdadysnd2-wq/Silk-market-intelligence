-- خنق الدخول مشتركاً بين العمليات · cross-process login throttle state.
-- الحالة في الذاكرة تنفصل لكل worker (فتضعف الحماية بمقدار عددها) وتُمحى عند
-- كل إعادة نشر. القاعدة مشتركة بين كل العمليات وتصمد للإقلاع، فالسقف يبقى
-- سقفاً واحداً حقيقياً أيًّا كان عدد العمّال.
-- In-memory throttle state is per-worker (protection ÷ N) and dies on redeploy;
-- the DB is shared across processes and survives restarts.

CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY,
    identity   TEXT NOT NULL,   -- بريد + IP مطبَّعان · normalized email|ip
    created_at TEXT NOT NULL    -- ISO-8601 UTC (نافذة ثابتة تُقاس عليه)
);
CREATE INDEX IF NOT EXISTS ix_login_attempts_identity
    ON login_attempts(identity, created_at);
