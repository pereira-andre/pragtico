CREATE TABLE IF NOT EXISTS app_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'agente', 'piloto')),
    full_name TEXT NOT NULL DEFAULT '',
    organization TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    whatsapp_number TEXT NOT NULL DEFAULT '',
    whatsapp_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
    whatsapp_opt_in_at TIMESTAMPTZ,
    profile_completed_at TIMESTAMPTZ
);

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS full_name TEXT NOT NULL DEFAULT '';

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS organization TEXT NOT NULL DEFAULT '';

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS email TEXT NOT NULL DEFAULT '';

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS phone TEXT NOT NULL DEFAULT '';

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS whatsapp_number TEXT NOT NULL DEFAULT '';

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS whatsapp_opt_in BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS whatsapp_opt_in_at TIMESTAMPTZ;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS profile_completed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS documents (
    name TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by TEXT NOT NULL,
    preview TEXT NOT NULL,
    file_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS port_calls (
    id UUID PRIMARY KEY,
    vessel_name TEXT NOT NULL,
    vessel_short_name TEXT NOT NULL DEFAULT '',
    vessel_imo TEXT NOT NULL DEFAULT '',
    vessel_call_sign TEXT NOT NULL DEFAULT '',
    vessel_flag TEXT NOT NULL DEFAULT '',
    vessel_type TEXT NOT NULL DEFAULT '',
    vessel_loa_m TEXT NOT NULL DEFAULT '',
    vessel_beam_m TEXT NOT NULL DEFAULT '',
    vessel_gt_t TEXT NOT NULL DEFAULT '',
    vessel_max_draft_m TEXT NOT NULL DEFAULT '',
    vessel_dwt_t TEXT NOT NULL DEFAULT '',
    vessel_bow_thruster TEXT NOT NULL DEFAULT 'unknown',
    vessel_stern_thruster TEXT NOT NULL DEFAULT 'unknown',
    status TEXT NOT NULL CHECK (status IN ('scheduled', 'in_port', 'departed')),
    approval_status TEXT NOT NULL DEFAULT 'pending' CHECK (approval_status IN ('pending', 'approved', 'aborted')),
    approval_note TEXT NOT NULL DEFAULT '',
    aborted_reason TEXT NOT NULL DEFAULT '',
    decided_by TEXT,
    decided_at TIMESTAMPTZ,
    eta TIMESTAMPTZ,
    ata TIMESTAMPTZ,
    planned_departure_at TIMESTAMPTZ,
    departure_plan_note TEXT NOT NULL DEFAULT '',
    departure_at TIMESTAMPTZ,
    planned_shift_at TIMESTAMPTZ,
    shift_plan_note TEXT NOT NULL DEFAULT '',
    shift_at TIMESTAMPTZ,
    shift_origin_berth TEXT NOT NULL DEFAULT '',
    shift_destination_berth TEXT NOT NULL DEFAULT '',
    shift_approval_status TEXT NOT NULL DEFAULT 'pending' CHECK (shift_approval_status IN ('pending', 'approved', 'aborted')),
    shift_approval_note TEXT NOT NULL DEFAULT '',
    shift_aborted_reason TEXT NOT NULL DEFAULT '',
    shift_decided_by TEXT,
    shift_decided_at TIMESTAMPTZ,
    maneuver_history JSONB NOT NULL DEFAULT '[]'::jsonb,
    berth TEXT,
    last_port TEXT,
    next_port TEXT,
    created_by TEXT NOT NULL,
    change_log JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS maneuver_cases (
    maneuver_id TEXT PRIMARY KEY,
    port_call_id UUID NOT NULL REFERENCES port_calls(id) ON DELETE CASCADE,
    reference_code TEXT NOT NULL,
    vessel_name TEXT NOT NULL,
    maneuver_type TEXT NOT NULL,
    current_state TEXT NOT NULL,
    origin_label TEXT NOT NULL DEFAULT '',
    destination_label TEXT NOT NULL DEFAULT '',
    planned_at TIMESTAMPTZ,
    decided_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    reported_at TIMESTAMPTZ,
    latest_event_at TIMESTAMPTZ,
    case_summary TEXT NOT NULL DEFAULT '',
    vessel_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    scale_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    planning_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    environment_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    feature_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    change_log JSONB NOT NULL DEFAULT '[]'::jsonb,
    feedback_status TEXT NOT NULL DEFAULT '',
    feedback_note TEXT NOT NULL DEFAULT '',
    feedback_updated_by TEXT NOT NULL DEFAULT '',
    feedback_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS feedback_status TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS feedback_note TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS feedback_updated_by TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS feedback_updated_at TIMESTAMPTZ;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_short_name TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_imo TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_call_sign TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_flag TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_type TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_loa_m TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_beam_m TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_gt_t TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_max_draft_m TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_dwt_t TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_bow_thruster TEXT NOT NULL DEFAULT 'unknown';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS vessel_stern_thruster TEXT NOT NULL DEFAULT 'unknown';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS approval_status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS approval_note TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS aborted_reason TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS decided_by TEXT;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS change_log JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS planned_departure_at TIMESTAMPTZ;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS departure_plan_note TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS planned_shift_at TIMESTAMPTZ;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_plan_note TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_at TIMESTAMPTZ;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_origin_berth TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_destination_berth TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_approval_status TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_approval_note TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_aborted_reason TEXT NOT NULL DEFAULT '';

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_decided_by TEXT;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS shift_decided_at TIMESTAMPTZ;

ALTER TABLE port_calls
    ADD COLUMN IF NOT EXISTS maneuver_history JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS reference_code TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS vessel_name TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS maneuver_type TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS current_state TEXT NOT NULL DEFAULT 'pending';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS origin_label TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS destination_label TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS planned_at TIMESTAMPTZ;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS reported_at TIMESTAMPTZ;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS latest_event_at TIMESTAMPTZ;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS case_summary TEXT NOT NULL DEFAULT '';

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS vessel_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS scale_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS planning_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS decision_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS execution_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS outcome_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS environment_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS feature_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE maneuver_cases
    ADD COLUMN IF NOT EXISTS change_log JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL REFERENCES app_users(username) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'Nova conversa',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    feedback_status TEXT,
    feedback_note TEXT NOT NULL DEFAULT '',
    feedback_correction TEXT NOT NULL DEFAULT '',
    feedback_correction_document TEXT NOT NULL DEFAULT '',
    feedback_error_type TEXT NOT NULL DEFAULT '',
    feedback_scope TEXT NOT NULL DEFAULT '',
    feedback_destination TEXT NOT NULL DEFAULT '',
    feedback_criticality TEXT NOT NULL DEFAULT '',
    feedback_updated_by TEXT NOT NULL DEFAULT '',
    feedback_updated_at TIMESTAMPTZ,
    channel TEXT NOT NULL DEFAULT 'web',
    channel_user_id TEXT NOT NULL DEFAULT '',
    external_message_id TEXT NOT NULL DEFAULT '',
    external_reply_to_id TEXT NOT NULL DEFAULT '',
    channel_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_status TEXT;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_note TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_correction TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_correction_document TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_error_type TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_scope TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_destination TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_criticality TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_updated_by TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_updated_at TIMESTAMPTZ;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS channel TEXT NOT NULL DEFAULT 'web';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS channel_user_id TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS external_message_id TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS external_reply_to_id TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS channel_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS channel_events (
    id UUID PRIMARY KEY,
    channel TEXT NOT NULL,
    event_type TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    local_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    channel_user_id TEXT NOT NULL DEFAULT '',
    external_event_id TEXT NOT NULL DEFAULT '',
    external_message_id TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_runtime_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feedback_eval_cases (
    id UUID PRIMARY KEY,
    source_message_id TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL,
    question TEXT NOT NULL,
    expected_answer TEXT NOT NULL,
    expected_substrings JSONB NOT NULL DEFAULT '[]'::jsonb,
    feedback_note TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS feedback_eval_cases_source_message_id_idx
    ON feedback_eval_cases (source_message_id)
    WHERE source_message_id <> '';

CREATE UNIQUE INDEX IF NOT EXISTS feedback_eval_cases_document_question_idx
    ON feedback_eval_cases (document, question);

CREATE INDEX IF NOT EXISTS conversations_username_updated_idx
    ON conversations (username, updated_at DESC);

CREATE INDEX IF NOT EXISTS messages_conversation_created_idx
    ON messages (conversation_id, created_at ASC);

CREATE UNIQUE INDEX IF NOT EXISTS messages_channel_external_uidx
    ON messages (channel, external_message_id)
    WHERE external_message_id <> '';

CREATE INDEX IF NOT EXISTS channel_events_channel_created_idx
    ON channel_events (channel, created_at DESC);

CREATE INDEX IF NOT EXISTS channel_events_external_message_idx
    ON channel_events (channel, external_message_id);

CREATE INDEX IF NOT EXISTS port_calls_status_eta_idx
    ON port_calls (status, eta ASC);

CREATE INDEX IF NOT EXISTS port_calls_status_departure_idx
    ON port_calls (status, departure_at DESC);

CREATE INDEX IF NOT EXISTS maneuver_cases_port_call_idx
    ON maneuver_cases (port_call_id, latest_event_at DESC);

CREATE INDEX IF NOT EXISTS maneuver_cases_type_state_idx
    ON maneuver_cases (maneuver_type, current_state, latest_event_at DESC);
