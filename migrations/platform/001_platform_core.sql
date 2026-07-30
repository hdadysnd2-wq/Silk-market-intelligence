-- PR-1 — المخطط الأساسي لمنصّة سِلك متعددة المستأجرين · Silk multi-tenant core.
-- SQL محمول (SQLite الآن، Postgres لاحقاً بلا إعادة عمل) — كل الكيانات
-- المُستأجَرة تحمل owner_id/account_id مفهرساً؛ العزل يُفرَض في طبقة البيانات.
-- Portable SQL. Every tenant-scoped row carries an indexed account FK; isolation
-- is enforced at the data layer, never only in the UI. Money is INTEGER cents.
--
-- تصميم مُسبَق لكل PRs (2–8): الجداول كلها هنا كي تنزلق الموجات اللاحقة بلا
-- إعادة ترحيل — PR-2 يستهلك أعمدة الحصّة، PR-3 المحفظة/الدفتر، إلخ.

CREATE TABLE IF NOT EXISTS platform_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- ── الحسابات (المستأجرون) + خزنة سِلك · accounts (tenants) + Silk vault ──────
-- kind='silk' هو الحساب المُشغِّل الداخلي وهو نفسه الخزنة (is_vault=1)؛
-- kind='factory' حساب عميل. المستخدمون الداخليون (admin/analyst) ينتمون
-- لحساب سِلك، ومستخدمو المصنع لحساباتهم.
CREATE TABLE IF NOT EXISTS accounts (
    id                        INTEGER PRIMARY KEY,
    name                      TEXT NOT NULL,
    kind                      TEXT NOT NULL CHECK (kind IN ('silk','factory')),
    is_vault                  INTEGER NOT NULL DEFAULT 0,
    tier                      TEXT NOT NULL DEFAULT 'basic'
                                  CHECK (tier IN ('basic','silver','gold','platinum')),
    -- عدّاد الحصّة: مدى الحياة لـ Basic (لا يُصفَّر أبداً) + عدّاد شهري للبقية.
    lifetime_study_count      INTEGER NOT NULL DEFAULT 0,
    current_month_study_count INTEGER NOT NULL DEFAULT 0,
    quota_period              TEXT,   -- 'YYYY-MM' الذي يخصّه العدّاد الشهري
    is_active                 INTEGER NOT NULL DEFAULT 1,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);
-- خزنة/حساب سِلك واحد فقط · at most one vault.
CREATE UNIQUE INDEX IF NOT EXISTS ux_accounts_vault
    ON accounts(is_vault) WHERE is_vault = 1;

-- ── المستخدمون · users (login identity is globally unique) ────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY,
    account_id          INTEGER NOT NULL REFERENCES accounts(id),
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,          -- bcrypt($2b$12$) أو scrypt، لا نصّ صريح أبداً
    role                TEXT NOT NULL CHECK (role IN ('silk_admin','silk_analyst','factory')),
    first_name          TEXT,
    last_name           TEXT,
    language_preference TEXT NOT NULL DEFAULT 'en' CHECK (language_preference IN ('ar','en')),
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_users_account ON users(account_id);

-- ── الجلسات · sessions (hashed token, sliding 24h inactivity expiry) ──────────
CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    token_hash       TEXT NOT NULL UNIQUE,   -- sha256(raw) — الخام لا يُخزَّن أبداً
    ip_address       TEXT,
    user_agent       TEXT,
    created_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    last_activity_at TEXT NOT NULL
);
-- لا فهرس على token_hash: قيد UNIQUE أعلاه يبني فهرسه الضمني أصلاً، وفهرس
-- ثانٍ مطابق يُصان عند كل دخول/خروج بلا فائدة استعلام.
-- No index on token_hash — the UNIQUE constraint's implicit index covers it.
CREATE INDEX IF NOT EXISTS ix_sessions_user  ON sessions(user_id);

-- ── رموز إعادة تعيين كلمة المرور · single-use, time-limited reset tokens ──────
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_reset_user ON password_reset_tokens(user_id);

-- ── إعدادات النظام (مفتاح القتل العام) · system settings (global kill-switch) ─
CREATE TABLE IF NOT EXISTS system_settings (
    key                TEXT PRIMARY KEY,
    value              TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    updated_by_user_id INTEGER
);

-- ── سجلّ التدقيق · audit_log (admin: global; factory: own account only) ───────
CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER,
    account_id    INTEGER,
    action        TEXT NOT NULL,
    resource_type TEXT,
    resource_id   TEXT,
    changes       TEXT,   -- JSON {before, after}
    ip_address    TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_account ON audit_log(account_id);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_log(created_at);

-- ── المحافظ · wallets (one per account; cents) ───────────────────────────────
CREATE TABLE IF NOT EXISTS wallets (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
    balance         INTEGER NOT NULL DEFAULT 0,   -- cents
    lifetime_funded INTEGER NOT NULL DEFAULT 0,
    lifetime_spent  INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- ── دفتر الأستاذ · ledger_entries (IMMUTABLE — enforced by triggers) ─────────
CREATE TABLE IF NOT EXISTS ledger_entries (
    id             INTEGER PRIMARY KEY,
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    actor_user_id  INTEGER,   -- non-null for every user-triggered op
    operation_type TEXT NOT NULL CHECK (operation_type IN (
                       'email_sent','report_generated','wallet_funded',
                       'api_call','storage_charge','comparison_report','draft_email')),
    amount         INTEGER NOT NULL,   -- cents; negative=debit, positive=credit
    balance_after  INTEGER NOT NULL,   -- snapshot of owning account balance
    description    TEXT,
    metadata       TEXT,   -- JSON (study_id, prospect_id, ...)
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ledger_account ON ledger_entries(account_id);
CREATE INDEX IF NOT EXISTS ix_ledger_actor   ON ledger_entries(actor_user_id);
-- عدم القابلية للتعديل بنيوياً · immutability enforced in the DB, not just code.
CREATE TRIGGER IF NOT EXISTS trg_ledger_no_update
    BEFORE UPDATE ON ledger_entries
    BEGIN SELECT RAISE(ABORT, 'ledger_entries is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_ledger_no_delete
    BEFORE DELETE ON ledger_entries
    BEGIN SELECT RAISE(ABORT, 'ledger_entries is immutable'); END;

-- ── تهيئات SMTP · smtp_configs (credentials encrypted at rest) ───────────────
CREATE TABLE IF NOT EXISTS smtp_configs (
    id           INTEGER PRIMARY KEY,
    owner_id     INTEGER NOT NULL REFERENCES accounts(id),
    label        TEXT,
    host         TEXT NOT NULL,
    port         INTEGER NOT NULL,
    username_enc TEXT,   -- encrypted — NEVER plaintext
    password_enc TEXT,   -- encrypted — NEVER plaintext
    from_email   TEXT NOT NULL,
    from_name    TEXT,
    use_tls      INTEGER NOT NULL DEFAULT 1,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_smtp_owner ON smtp_configs(owner_id);

-- ── الدراسات · studies ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS studies (
    id                 INTEGER PRIMARY KEY,
    owner_id           INTEGER NOT NULL REFERENCES accounts(id),
    title_en           TEXT,
    title_ar           TEXT,
    description_en     TEXT,
    description_ar     TEXT,
    state              TEXT NOT NULL DEFAULT 'draft'
                           CHECK (state IN ('draft','in_progress','completed','archived')),
    target_count       INTEGER NOT NULL DEFAULT 0,
    response_count     INTEGER NOT NULL DEFAULT 0,
    smtp_config_id     INTEGER REFERENCES smtp_configs(id),
    created_by_user_id INTEGER REFERENCES users(id),
    launched_at        TEXT,
    completed_at       TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_studies_owner ON studies(owner_id);
CREATE INDEX IF NOT EXISTS ix_studies_state ON studies(state);

-- ── العملاء المحتملون · prospects (email UNIQUE per account, not globally) ────
CREATE TABLE IF NOT EXISTS prospects (
    id                  INTEGER PRIMARY KEY,
    owner_id            INTEGER NOT NULL REFERENCES accounts(id),
    email               TEXT NOT NULL,
    first_name          TEXT,
    last_name           TEXT,
    company             TEXT,
    industry            TEXT,
    language_preference TEXT NOT NULL DEFAULT 'en' CHECK (language_preference IN ('ar','en')),
    tags                TEXT,   -- JSON array
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (owner_id, email)
);
CREATE INDEX IF NOT EXISTS ix_prospects_owner ON prospects(owner_id);

-- ── المسودّات · drafts ({{first_name}} placeholders, A/B, _en/_ar) ────────────
CREATE TABLE IF NOT EXISTS drafts (
    id         INTEGER PRIMARY KEY,
    owner_id   INTEGER NOT NULL REFERENCES accounts(id),
    study_id   INTEGER REFERENCES studies(id),
    subject_en TEXT,
    subject_ar TEXT,
    body_en    TEXT,
    body_ar    TEXT,
    version    TEXT NOT NULL DEFAULT 'A' CHECK (version IN ('A','B')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_drafts_owner ON drafts(owner_id);
CREATE INDEX IF NOT EXISTS ix_drafts_study ON drafts(study_id);

-- ── الصور · images (storage_key = "{account_id}/{uuid}.ext") ──────────────────
CREATE TABLE IF NOT EXISTS images (
    id                 INTEGER PRIMARY KEY,
    owner_id           INTEGER NOT NULL REFERENCES accounts(id),
    filename           TEXT,
    storage_key        TEXT NOT NULL UNIQUE,
    mime_type          TEXT,
    size_bytes         INTEGER NOT NULL DEFAULT 0,
    uploaded_by_user_id INTEGER REFERENCES users(id),
    alt_text_en        TEXT,
    alt_text_ar        TEXT,
    uploaded_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_images_owner ON images(owner_id);

-- ── قمع المقارنة · comparison funnels (Gold/Platinum only; join tables) ───────
CREATE TABLE IF NOT EXISTS comparison_funnels (
    id         INTEGER PRIMARY KEY,
    owner_id   INTEGER NOT NULL REFERENCES accounts(id),
    state      TEXT NOT NULL DEFAULT 'compared'
                   CHECK (state IN ('compared','selected','extracted','drafted','sent')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_funnels_owner ON comparison_funnels(owner_id);

CREATE TABLE IF NOT EXISTS funnel_studies (
    funnel_id INTEGER NOT NULL REFERENCES comparison_funnels(id),
    study_id  INTEGER NOT NULL REFERENCES studies(id),
    PRIMARY KEY (funnel_id, study_id)
);
CREATE TABLE IF NOT EXISTS funnel_prospects (
    funnel_id   INTEGER NOT NULL REFERENCES comparison_funnels(id),
    prospect_id INTEGER NOT NULL REFERENCES prospects(id),
    PRIMARY KEY (funnel_id, prospect_id)
);

-- ── سجلّ الموافقة · consent_registry (one row per email sent; no-delete) ──────
CREATE TABLE IF NOT EXISTS consent_registry (
    id                 INTEGER PRIMARY KEY,
    prospect_email     TEXT NOT NULL,
    study_id           INTEGER,
    sending_account_id INTEGER NOT NULL REFERENCES accounts(id),
    approving_user_id  INTEGER,
    message_verbatim   TEXT NOT NULL,   -- exact subject + body sent
    sent_at            TEXT,
    consent_granted_at TEXT,
    unsubscribed_at    TEXT,            -- set later on unsubscribe click
    unsubscribe_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_consent_account ON consent_registry(sending_account_id);
CREATE INDEX IF NOT EXISTS ix_consent_email   ON consent_registry(prospect_email);
-- append-only: the verbatim record can never be deleted (unsubscribe UPDATES only).
CREATE TRIGGER IF NOT EXISTS trg_consent_no_delete
    BEFORE DELETE ON consent_registry
    BEGIN SELECT RAISE(ABORT, 'consent_registry is append-only'); END;

-- ── قائمة القمع (مقيّدة بالحساب) · account-scoped suppression list ───────────
CREATE TABLE IF NOT EXISTS suppression_list (
    id         INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    email      TEXT NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, email)
);

-- ── طابور البريد · email_queue (worker checks kill-switch PER email) ─────────
CREATE TABLE IF NOT EXISTS email_queue (
    id             INTEGER PRIMARY KEY,
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    study_id       INTEGER REFERENCES studies(id),
    prospect_id    INTEGER,
    prospect_email TEXT NOT NULL,
    draft_id       INTEGER,
    smtp_config_id INTEGER,
    subject        TEXT,
    body           TEXT,
    -- 'sending' حالة مطالبة وسيطة: العامل يطالب بالصفّ ذرّياً قبل الإرسال فلا
    -- يرسله مرورَان متزاملان مرّتين. A claim state so two passes can't double-send.
    status         TEXT NOT NULL DEFAULT 'queued'
                       CHECK (status IN ('queued','sending','sent','failed','suppressed')),
    actor_user_id  INTEGER,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    queued_at      TEXT NOT NULL,
    sent_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_queue_account ON email_queue(account_id);
-- المركَّب (status, id) يخدم أيضاً كل شرط على status وحده (عمود رائد)، فلا
-- حاجة لفهرس status منفرد. The composite's leading column serves status-only scans.
CREATE INDEX IF NOT EXISTS ix_queue_order   ON email_queue(status, id);
