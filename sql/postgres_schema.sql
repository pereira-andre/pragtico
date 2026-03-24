CREATE TABLE IF NOT EXISTS app_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'agente', 'piloto')),
    full_name TEXT NOT NULL DEFAULT '',
    organization TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
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
    notes TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_status TEXT;

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_note TEXT NOT NULL DEFAULT '';

ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS feedback_updated_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS app_runtime_state (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS conversations_username_updated_idx
    ON conversations (username, updated_at DESC);

CREATE INDEX IF NOT EXISTS messages_conversation_created_idx
    ON messages (conversation_id, created_at ASC);

CREATE INDEX IF NOT EXISTS port_calls_status_eta_idx
    ON port_calls (status, eta ASC);

CREATE INDEX IF NOT EXISTS port_calls_status_departure_idx
    ON port_calls (status, departure_at DESC);
