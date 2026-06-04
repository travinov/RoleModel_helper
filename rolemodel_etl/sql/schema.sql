CREATE TABLE IF NOT EXISTS etl_run (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCESS', 'FAILED')),
    source_file TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    rows_read INTEGER NOT NULL DEFAULT 0 CHECK (rows_read >= 0),
    rows_loaded INTEGER NOT NULL DEFAULT 0 CHECK (rows_loaded >= 0),
    errors_count INTEGER NOT NULL DEFAULT 0 CHECK (errors_count >= 0)
);

CREATE TABLE IF NOT EXISTS snapshot (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL UNIQUE REFERENCES etl_run(id) ON DELETE CASCADE,
    snapshot_label TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_file TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    model_code TEXT,
    model_name TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_snapshot_single_active
    ON snapshot ((is_active))
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS profile (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    profile_code TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    profile_type TEXT,
    profile_raw TEXT NOT NULL,
    profile_justification_raw TEXT,
    UNIQUE (snapshot_id, profile_code)
);

CREATE TABLE IF NOT EXISTS profile_structure_path (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    profile_id BIGINT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    path_order INTEGER NOT NULL CHECK (path_order >= 1),
    path_raw TEXT NOT NULL,
    UNIQUE (snapshot_id, profile_id, path_order)
);

CREATE TABLE IF NOT EXISTS profile_structure_segment (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    profile_id BIGINT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    path_order INTEGER NOT NULL CHECK (path_order >= 1),
    segment_order INTEGER NOT NULL CHECK (segment_order >= 1),
    segment_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_department (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    profile_id BIGINT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    department_name TEXT NOT NULL,
    department_code TEXT,
    raw_line TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('PARSED', 'RAW_ONLY'))
);

CREATE TABLE IF NOT EXISTS profile_position (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    profile_id BIGINT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    position_name TEXT NOT NULL,
    position_code TEXT,
    raw_line TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('PARSED', 'RAW_ONLY'))
);

CREATE TABLE IF NOT EXISTS system (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    system_name_raw TEXT NOT NULL,
    ci_code TEXT,
    source_col_start INTEGER NOT NULL CHECK (source_col_start >= 1),
    source_col_end INTEGER NOT NULL CHECK (source_col_end >= source_col_start),
    UNIQUE (snapshot_id, system_name_raw),
    UNIQUE (snapshot_id, ci_code)
);

CREATE TABLE IF NOT EXISTS entitlement (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    system_id BIGINT NOT NULL REFERENCES system(id) ON DELETE CASCADE,
    source_col INTEGER NOT NULL CHECK (source_col >= 1),
    entitlement_type TEXT NOT NULL,
    entitlement_name TEXT NOT NULL DEFAULT '',
    header_raw TEXT NOT NULL,
    UNIQUE (snapshot_id, source_col)
);

CREATE TABLE IF NOT EXISTS profile_system_justification (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    profile_id BIGINT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    system_id BIGINT NOT NULL REFERENCES system(id) ON DELETE CASCADE,
    access_level SMALLINT NOT NULL CHECK (access_level IN (1, 2)),
    justification_text TEXT,
    raw_value TEXT NOT NULL,
    UNIQUE (snapshot_id, profile_id, system_id, access_level)
);

CREATE TABLE IF NOT EXISTS profile_entitlement_access (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    profile_id BIGINT NOT NULL REFERENCES profile(id) ON DELETE CASCADE,
    entitlement_id BIGINT NOT NULL REFERENCES entitlement(id) ON DELETE CASCADE,
    access_level SMALLINT NOT NULL CHECK (access_level IN (1, 2)),
    inline_comment TEXT,
    raw_value TEXT NOT NULL,
    justification_id BIGINT REFERENCES profile_system_justification(id) ON DELETE SET NULL,
    UNIQUE (snapshot_id, profile_id, entitlement_id)
);

CREATE TABLE IF NOT EXISTS etl_error (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES etl_run(id) ON DELETE CASCADE,
    snapshot_id BIGINT REFERENCES snapshot(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('ERROR', 'WARNING')),
    sheet_name TEXT NOT NULL,
    source_row INTEGER,
    source_col INTEGER,
    error_code TEXT NOT NULL,
    error_message TEXT NOT NULL,
    raw_value TEXT
);

CREATE TABLE IF NOT EXISTS system_alias (
    id BIGSERIAL PRIMARY KEY,
    system_id BIGINT NOT NULL REFERENCES system(id) ON DELETE CASCADE,
    alias_text TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    alias_source TEXT NOT NULL CHECK (alias_source IN ('MANUAL', 'AUTO_SEEDED')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (system_id, alias_normalized)
);

CREATE TABLE IF NOT EXISTS system_alias_candidate (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    system_id BIGINT NOT NULL REFERENCES system(id) ON DELETE CASCADE,
    alias_text TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    alias_source TEXT NOT NULL CHECK (alias_source IN ('AUTO_EXTRACTED')),
    alias_class TEXT NOT NULL CHECK (alias_class IN ('SAFE', 'AMBIGUOUS', 'UNSAFE')),
    collision_count INTEGER NOT NULL DEFAULT 1 CHECK (collision_count >= 1),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, system_id, alias_normalized)
);

CREATE TABLE IF NOT EXISTS department_alias_candidate (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id BIGINT NOT NULL REFERENCES snapshot(id) ON DELETE CASCADE,
    department_name TEXT NOT NULL,
    alias_text TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    alias_source TEXT NOT NULL CHECK (alias_source IN ('AUTO_EXTRACTED')),
    alias_class TEXT NOT NULL CHECK (alias_class IN ('SAFE', 'AMBIGUOUS', 'UNSAFE')),
    collision_count INTEGER NOT NULL DEFAULT 1 CHECK (collision_count >= 1),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (snapshot_id, department_name, alias_normalized)
);

CREATE TABLE IF NOT EXISTS chat_session (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CLOSED')),
    current_intent_type TEXT,
    resolved_system_id BIGINT REFERENCES system(id) ON DELETE SET NULL,
    resolved_profile_id BIGINT REFERENCES profile(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS chat_session_feedback (
    session_id UUID PRIMARY KEY REFERENCES chat_session(id) ON DELETE CASCADE,
    rating TEXT NOT NULL CHECK (rating IN ('UP', 'DOWN')),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_message (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('USER', 'ASSISTANT', 'TOOL')),
    message_text TEXT NOT NULL,
    structured_payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_slot_state (
    session_id UUID PRIMARY KEY REFERENCES chat_session(id) ON DELETE CASCADE,
    city_raw TEXT,
    city_normalized TEXT,
    department_raw TEXT,
    department_normalized TEXT,
    position_raw TEXT,
    position_normalized TEXT,
    system_raw TEXT,
    resolved_system_id BIGINT REFERENCES system(id) ON DELETE SET NULL,
    profile_candidates JSONB,
    resolved_profile_id BIGINT REFERENCES profile(id) ON DELETE SET NULL,
    needs_confirmation BOOLEAN NOT NULL DEFAULT FALSE,
    confirmation_topic TEXT,
    confirmation_options JSONB,
    pending_slot TEXT,
    requested_entitlement_raw TEXT,
    requested_entitlement_type_hint TEXT,
    last_intent_type TEXT,
    active_goal TEXT,
    resume_goal TEXT,
    conversation_phase TEXT,
    resume_phase TEXT,
    context_shift TEXT,
    system_query_raw TEXT,
    system_resolution_mode TEXT,
    instruction_mode TEXT,
    goal_stack JSONB,
    pending_question JSONB,
    state_revision BIGINT NOT NULL DEFAULT 0,
    context_snapshot JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tool_call_log (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
    input_payload JSONB,
    output_payload JSONB,
    status TEXT NOT NULL,
    result_summary TEXT,
    error_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_candidate_set (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    source_query TEXT NOT NULL,
    page_size INTEGER NOT NULL DEFAULT 5 CHECK (page_size >= 1),
    current_offset INTEGER NOT NULL DEFAULT 0 CHECK (current_offset >= 0),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'CLOSED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_candidate_option (
    id BIGSERIAL PRIMARY KEY,
    candidate_set_id BIGINT NOT NULL REFERENCES chat_candidate_set(id) ON DELETE CASCADE,
    option_key TEXT NOT NULL,
    option_label TEXT NOT NULL,
    option_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    rank_no INTEGER NOT NULL CHECK (rank_no >= 1)
);

CREATE TABLE IF NOT EXISTS chat_turn_interpretation (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_session(id) ON DELETE CASCADE,
    message_id BIGINT NOT NULL REFERENCES chat_message(id) ON DELETE CASCADE,
    dialog_act TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    entities JSONB NOT NULL DEFAULT '{}'::jsonb,
    slot_candidates JSONB NOT NULL DEFAULT '{}'::jsonb,
    goal_transition TEXT,
    context_shift TEXT,
    needs_clarification BOOLEAN NOT NULL DEFAULT FALSE,
    reasoning_trace_short TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
    references_pending_question BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE chat_turn_interpretation
    ADD COLUMN IF NOT EXISTS slot_candidates JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS goal_transition TEXT,
    ADD COLUMN IF NOT EXISTS context_shift TEXT,
    ADD COLUMN IF NOT EXISTS needs_clarification BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS reasoning_trace_short TEXT;

ALTER TABLE chat_slot_state
    ADD COLUMN IF NOT EXISTS active_goal TEXT,
    ADD COLUMN IF NOT EXISTS resume_goal TEXT,
    ADD COLUMN IF NOT EXISTS conversation_phase TEXT,
    ADD COLUMN IF NOT EXISTS resume_phase TEXT,
    ADD COLUMN IF NOT EXISTS context_shift TEXT,
    ADD COLUMN IF NOT EXISTS system_query_raw TEXT,
    ADD COLUMN IF NOT EXISTS system_resolution_mode TEXT,
    ADD COLUMN IF NOT EXISTS instruction_mode TEXT,
    ADD COLUMN IF NOT EXISTS goal_stack JSONB,
    ADD COLUMN IF NOT EXISTS pending_question JSONB,
    ADD COLUMN IF NOT EXISTS state_revision BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS context_snapshot JSONB;

ALTER TABLE tool_call_log
    ADD COLUMN IF NOT EXISTS result_summary TEXT;

CREATE INDEX IF NOT EXISTS idx_profile_profile_code ON profile (profile_code);
CREATE INDEX IF NOT EXISTS idx_profile_snapshot ON profile (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_system_ci_code ON system (ci_code);
CREATE INDEX IF NOT EXISTS idx_system_snapshot ON system (snapshot_id);
CREATE INDEX IF NOT EXISTS idx_entitlement_type ON entitlement (entitlement_type);
CREATE INDEX IF NOT EXISTS idx_pea_snapshot_level ON profile_entitlement_access (snapshot_id, access_level);
CREATE INDEX IF NOT EXISTS idx_etl_error_run_id ON etl_error (run_id);
CREATE INDEX IF NOT EXISTS idx_system_alias_norm ON system_alias (alias_normalized);
CREATE INDEX IF NOT EXISTS idx_system_alias_candidate_snapshot_class ON system_alias_candidate (snapshot_id, alias_class);
CREATE INDEX IF NOT EXISTS idx_system_alias_candidate_norm ON system_alias_candidate (alias_normalized);
CREATE INDEX IF NOT EXISTS idx_department_alias_candidate_snapshot_class ON department_alias_candidate (snapshot_id, alias_class);
CREATE INDEX IF NOT EXISTS idx_department_alias_candidate_norm ON department_alias_candidate (alias_normalized);
CREATE INDEX IF NOT EXISTS idx_chat_message_session ON chat_message (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_call_log_session ON tool_call_log (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_candidate_set_session ON chat_candidate_set (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_candidate_option_set_rank ON chat_candidate_option (candidate_set_id, rank_no);
CREATE INDEX IF NOT EXISTS idx_chat_turn_interpretation_session ON chat_turn_interpretation (session_id, created_at);
CREATE OR REPLACE VIEW v_active_snapshot AS
SELECT id, run_id, loaded_at, source_file, sheet_name, model_code, model_name
FROM snapshot
WHERE is_active = TRUE;

CREATE OR REPLACE VIEW v_profile_access_active AS
WITH active_snapshot AS (
    SELECT id
    FROM snapshot
    WHERE is_active = TRUE
    ORDER BY loaded_at DESC, id DESC
    LIMIT 1
)
SELECT
    pea.snapshot_id,
    p.id AS profile_id,
    p.profile_code,
    p.profile_name,
    p.profile_type,
    s.id AS system_id,
    s.ci_code,
    s.system_name_raw AS system_name,
    e.id AS entitlement_id,
    e.entitlement_type,
    e.entitlement_name,
    pea.access_level,
    psj.justification_text,
    pea.inline_comment,
    pea.raw_value AS access_raw_value,
    psj.raw_value AS justification_raw_value
FROM active_snapshot a
JOIN profile_entitlement_access pea ON pea.snapshot_id = a.id
JOIN profile p ON p.id = pea.profile_id
JOIN entitlement e ON e.id = pea.entitlement_id
JOIN system s ON s.id = e.system_id
LEFT JOIN profile_system_justification psj ON psj.id = pea.justification_id;

CREATE OR REPLACE VIEW v_access_profiles_active AS
SELECT *
FROM v_profile_access_active;

CREATE OR REPLACE VIEW v_profile_catalog_active AS
WITH active_snapshot AS (
    SELECT id
    FROM snapshot
    WHERE is_active = TRUE
    ORDER BY loaded_at DESC, id DESC
    LIMIT 1
)
SELECT
    p.id AS profile_id,
    p.profile_code,
    p.profile_name,
    p.profile_type,
    coalesce(string_agg(DISTINCT pss.segment_name, ' '), '') AS structure_text,
    coalesce(string_agg(DISTINCT pd.department_name, ' '), '') AS department_text,
    coalesce(string_agg(DISTINCT pp.position_name, ' '), '') AS position_text
FROM active_snapshot a
JOIN profile p ON p.snapshot_id = a.id
LEFT JOIN profile_structure_segment pss ON pss.profile_id = p.id
LEFT JOIN profile_department pd ON pd.profile_id = p.id
LEFT JOIN profile_position pp ON pp.profile_id = p.id
GROUP BY p.id, p.profile_code, p.profile_name, p.profile_type;

DROP MATERIALIZED VIEW IF EXISTS mv_access_search_active;
CREATE MATERIALIZED VIEW mv_access_search_active AS
SELECT
    v.*,
    lower(
        concat_ws(
            ' ',
            v.profile_code,
            v.profile_name,
            coalesce(v.profile_type, ''),
            coalesce(v.ci_code, ''),
            v.system_name,
            v.entitlement_type,
            v.entitlement_name,
            coalesce(v.justification_text, ''),
            coalesce(v.inline_comment, '')
        )
    ) AS search_text,
    to_tsvector(
        'russian',
        concat_ws(
            ' ',
            v.profile_code,
            v.profile_name,
            coalesce(v.profile_type, ''),
            coalesce(v.ci_code, ''),
            v.system_name,
            v.entitlement_type,
            v.entitlement_name,
            coalesce(v.justification_text, ''),
            coalesce(v.inline_comment, '')
        )
    ) AS search_tsv
FROM v_profile_access_active v;

CREATE INDEX IF NOT EXISTS idx_mv_access_search_profile_code ON mv_access_search_active (profile_code);
CREATE INDEX IF NOT EXISTS idx_mv_access_search_ci_code ON mv_access_search_active (ci_code);
CREATE INDEX IF NOT EXISTS idx_mv_access_search_access_level ON mv_access_search_active (access_level);
CREATE INDEX IF NOT EXISTS idx_mv_access_search_ent_type ON mv_access_search_active (entitlement_type);
CREATE INDEX IF NOT EXISTS idx_mv_access_search_tsv ON mv_access_search_active USING GIN (search_tsv);
