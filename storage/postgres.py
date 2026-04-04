"""PostgreSQL storage backend."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Dict, List, Optional

from werkzeug.datastructures import FileStorage
from werkzeug.security import check_password_hash, generate_password_hash
from core.validators import normalize_thruster_state, validate_datetime_range, validate_operational_feedback_status

from domain.document_processing import (
    build_preview,
    ensure_unique_filename,
    extract_text_from_path,
    file_metadata,
    format_bytes,
    infer_document_type,
    is_allowed_document,
    is_text_editable,
    iso_now,
    read_text_file,
    sanitize_upload_filename,
    slugify,
)

from .base import BaseStore
from .maneuver_case_helpers import (
    _capture_live_environment_sources,
    build_maneuver_case,
    decorate_maneuver_case,
    rank_similar_maneuver_cases,
)
from .constants import (
    ALLOWED_FEEDBACK_STATUSES,
    DEFAULT_CONVERSATION_TITLE,
    FEEDBACK_APPROVED,
    PASSWORD_HASH_METHOD,
    PORT_CALL_APPROVAL_ABORTED,
    PORT_CALL_APPROVAL_APPROVED,
    PORT_CALL_APPROVAL_PENDING,
    PORT_CALL_STATUS_IN_PORT,
    PORT_CALL_STATUS_SCHEDULED,
)
from .port_call_helpers import (
    _build_port_activity_snapshot,
    _can_abort_departure_plan,
    _can_abort_port_call,
    _can_abort_shift_plan,
    _can_edit_maneuver_plan,
    _decorate_port_call,
    _default_port_calls,
    _latest_maneuver,
    _latest_reportable_maneuver,
    _normalize_maneuver_record,
    _normalize_port_call_record,
    _sync_port_call_from_history,
    _append_maneuver_change_log,
)
from .utils import (
    _build_actor_snapshot,
    _clean_text,
    _conversation_title_from_text,
    _normalize_user_profile_payload,
    _normalize_username,
    _text_similarity,
    _utc_iso_to_label,
    _validate_required_operational_profile,
    _validate_required_vessel_profile,
    is_legacy_system_markdown_document,
    normalize_constraint_codes,
)

logger = logging.getLogger(__name__)


class PostgresStore(BaseStore):
    backend_name = "postgres"

    def __init__(self, database_url: str, knowledge_dir: str) -> None:
        self.database_url = database_url
        self.knowledge_dir = knowledge_dir
        os.makedirs(self.knowledge_dir, exist_ok=True)
        self._ensure_schema()
        self._seed_defaults()
        self._remove_legacy_seed_markdown_documents()
        self._sync_document_records()
        self._rebuild_maneuver_cases(capture_live_environment=False)

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Instala `psycopg[binary]` para usar o backend postgres.") from exc
        max_wait_seconds = max(float(os.getenv("DATABASE_CONNECT_MAX_WAIT_SECONDS", "45")), 0.0)
        retry_interval_seconds = max(float(os.getenv("DATABASE_CONNECT_RETRY_INTERVAL_SECONDS", "2")), 0.1)
        started_at = time.monotonic()
        last_exc = None

        while True:
            try:
                return psycopg.connect(self.database_url, row_factory=dict_row)
            except Exception as exc:
                last_exc = exc
                elapsed = time.monotonic() - started_at
                if elapsed >= max_wait_seconds:
                    raise RuntimeError(
                        "Falha ao ligar ao PostgreSQL durante o arranque. "
                        "Confirma DATABASE_URL e se a base Railway já está pronta."
                    ) from exc
                logger.warning(
                    "PostgreSQL ainda não está pronto; nova tentativa em %.1fs (%s)",
                    retry_interval_seconds,
                    exc,
                )
                time.sleep(retry_interval_seconds)

    def _port_call_select_clause(self) -> str:
        return """
            SELECT
                id::text AS id,
                vessel_name,
                vessel_short_name,
                vessel_imo,
                vessel_call_sign,
                vessel_flag,
                vessel_type,
                vessel_loa_m,
                vessel_beam_m,
                vessel_gt_t,
                vessel_max_draft_m,
                vessel_dwt_t,
                vessel_bow_thruster,
                vessel_stern_thruster,
                status,
                approval_status,
                approval_note,
                aborted_reason,
                decided_by,
                decided_at,
                eta,
                ata,
                planned_departure_at,
                departure_plan_note,
                departure_at,
                planned_shift_at,
                shift_plan_note,
                shift_at,
                shift_origin_berth,
                shift_destination_berth,
                shift_approval_status,
                shift_approval_note,
                shift_aborted_reason,
                shift_decided_by,
                shift_decided_at,
                maneuver_history,
                berth,
                last_port,
                next_port,
                created_by,
                notes,
                created_at,
                updated_at
            FROM port_calls
        """

    def _row_to_port_call_record(self, row: Optional[Dict]) -> Optional[Dict]:
        if not row:
            return None
        return {
            **row,
            "eta": row["eta"].isoformat() if row.get("eta") else None,
            "ata": row["ata"].isoformat() if row.get("ata") else None,
            "planned_departure_at": row["planned_departure_at"].isoformat() if row.get("planned_departure_at") else None,
            "departure_at": row["departure_at"].isoformat() if row.get("departure_at") else None,
            "planned_shift_at": row["planned_shift_at"].isoformat() if row.get("planned_shift_at") else None,
            "shift_at": row["shift_at"].isoformat() if row.get("shift_at") else None,
            "shift_decided_at": row["shift_decided_at"].isoformat() if row.get("shift_decided_at") else None,
            "maneuver_history": row.get("maneuver_history") or [],
            "decided_at": row["decided_at"].isoformat() if row.get("decided_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _row_to_maneuver_case_record(self, row: Optional[Dict]) -> Optional[Dict]:
        if not row:
            return None
        return {
            **row,
            "planned_at": row["planned_at"].isoformat() if row.get("planned_at") else None,
            "decided_at": row["decided_at"].isoformat() if row.get("decided_at") else None,
            "completed_at": row["completed_at"].isoformat() if row.get("completed_at") else None,
            "reported_at": row["reported_at"].isoformat() if row.get("reported_at") else None,
            "latest_event_at": row["latest_event_at"].isoformat() if row.get("latest_event_at") else None,
            "feedback_updated_at": row["feedback_updated_at"].isoformat() if row.get("feedback_updated_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _row_to_chat_message_record(self, row: Optional[Dict]) -> Optional[Dict]:
        if not row:
            return None
        return {
            **row,
            "channel": row.get("channel") or "web",
            "channel_user_id": row.get("channel_user_id") or "",
            "external_message_id": row.get("external_message_id") or "",
            "external_reply_to_id": row.get("external_reply_to_id") or "",
            "channel_metadata": row.get("channel_metadata") or {},
            "created_at": row["created_at"].isoformat(),
            "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
            "feedback_updated_at": (
                row["feedback_updated_at"].isoformat() if row.get("feedback_updated_at") else None
            ),
            "feedback_updated_at_label": (
                _utc_iso_to_label(row["feedback_updated_at"].isoformat())
                if row.get("feedback_updated_at") else ""
            ),
        }

    def _upsert_maneuver_case(self, conn, record: Dict) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO maneuver_cases (
                    maneuver_id,
                    port_call_id,
                    reference_code,
                    vessel_name,
                    maneuver_type,
                    current_state,
                    origin_label,
                    destination_label,
                    planned_at,
                    decided_at,
                    completed_at,
                    reported_at,
                    latest_event_at,
                    case_summary,
                    vessel_snapshot,
                    scale_snapshot,
                    planning_snapshot,
                    decision_snapshot,
                    execution_snapshot,
                    outcome_snapshot,
                    environment_snapshot,
                    feature_snapshot,
                    change_log,
                    feedback_status,
                    feedback_note,
                    feedback_updated_by,
                    feedback_updated_at,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (maneuver_id) DO UPDATE SET
                    port_call_id = EXCLUDED.port_call_id,
                    reference_code = EXCLUDED.reference_code,
                    vessel_name = EXCLUDED.vessel_name,
                    maneuver_type = EXCLUDED.maneuver_type,
                    current_state = EXCLUDED.current_state,
                    origin_label = EXCLUDED.origin_label,
                    destination_label = EXCLUDED.destination_label,
                    planned_at = EXCLUDED.planned_at,
                    decided_at = EXCLUDED.decided_at,
                    completed_at = EXCLUDED.completed_at,
                    reported_at = EXCLUDED.reported_at,
                    latest_event_at = EXCLUDED.latest_event_at,
                    case_summary = EXCLUDED.case_summary,
                    vessel_snapshot = EXCLUDED.vessel_snapshot,
                    scale_snapshot = EXCLUDED.scale_snapshot,
                    planning_snapshot = EXCLUDED.planning_snapshot,
                    decision_snapshot = EXCLUDED.decision_snapshot,
                    execution_snapshot = EXCLUDED.execution_snapshot,
                    outcome_snapshot = EXCLUDED.outcome_snapshot,
                    environment_snapshot = EXCLUDED.environment_snapshot,
                    feature_snapshot = EXCLUDED.feature_snapshot,
                    change_log = EXCLUDED.change_log,
                    feedback_status = COALESCE(NULLIF(maneuver_cases.feedback_status, ''), EXCLUDED.feedback_status),
                    feedback_note = CASE
                        WHEN maneuver_cases.feedback_status <> '' THEN maneuver_cases.feedback_note
                        ELSE EXCLUDED.feedback_note
                    END,
                    feedback_updated_by = CASE
                        WHEN maneuver_cases.feedback_status <> '' THEN maneuver_cases.feedback_updated_by
                        ELSE EXCLUDED.feedback_updated_by
                    END,
                    feedback_updated_at = COALESCE(maneuver_cases.feedback_updated_at, EXCLUDED.feedback_updated_at),
                    created_at = COALESCE(maneuver_cases.created_at, EXCLUDED.created_at),
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    record["maneuver_id"],
                    record["port_call_id"],
                    record.get("reference_code", ""),
                    record.get("vessel_name", ""),
                    record.get("maneuver_type", ""),
                    record.get("current_state", "pending"),
                    record.get("origin_label", ""),
                    record.get("destination_label", ""),
                    record.get("planned_at"),
                    record.get("decided_at"),
                    record.get("completed_at"),
                    record.get("reported_at"),
                    record.get("latest_event_at"),
                    record.get("case_summary", ""),
                    json.dumps(record.get("vessel_snapshot", {})),
                    json.dumps(record.get("scale_snapshot", {})),
                    json.dumps(record.get("planning_snapshot", {})),
                    json.dumps(record.get("decision_snapshot", {})),
                    json.dumps(record.get("execution_snapshot", {})),
                    json.dumps(record.get("outcome_snapshot", {})),
                    json.dumps(record.get("environment_snapshot", {})),
                    json.dumps(record.get("feature_snapshot", {})),
                    json.dumps(record.get("change_log", [])),
                    record.get("feedback_status", ""),
                    record.get("feedback_note", ""),
                    record.get("feedback_updated_by", ""),
                    record.get("feedback_updated_at"),
                    record.get("created_at") or iso_now(),
                    record.get("updated_at") or iso_now(),
                ),
            )

    def _list_raw_maneuver_cases(
        self,
        conn,
        *,
        limit: Optional[int] = None,
        maneuver_type: Optional[str] = None,
        state: Optional[str] = None,
        port_call_id: Optional[str] = None,
    ) -> List[Dict]:
        conditions = []
        params: List[object] = []
        if maneuver_type:
            conditions.append("maneuver_type = %s")
            params.append(maneuver_type.strip().lower())
        if state:
            conditions.append("current_state = %s")
            params.append(state.strip().lower())
        if port_call_id:
            conditions.append("port_call_id = %s")
            params.append(port_call_id)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = "LIMIT %s" if limit is not None else ""
        if limit is not None:
            params.append(max(limit, 0))
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    maneuver_id,
                    port_call_id::text AS port_call_id,
                    reference_code,
                    vessel_name,
                    maneuver_type,
                    current_state,
                    origin_label,
                    destination_label,
                    planned_at,
                    decided_at,
                    completed_at,
                    reported_at,
                    latest_event_at,
                    case_summary,
                    vessel_snapshot,
                    scale_snapshot,
                    planning_snapshot,
                    decision_snapshot,
                    execution_snapshot,
                    outcome_snapshot,
                    environment_snapshot,
                    feature_snapshot,
                    change_log,
                    feedback_status,
                    feedback_note,
                    feedback_updated_by,
                    feedback_updated_at,
                    created_at,
                    updated_at
                FROM maneuver_cases
                {where_clause}
                ORDER BY latest_event_at DESC NULLS LAST, updated_at DESC
                {limit_clause}
                """,
                tuple(params),
            )
            return [self._row_to_maneuver_case_record(row) for row in cur.fetchall()]

    def _sync_maneuver_cases_for_port_call(
        self,
        conn,
        port_call_record: Dict,
        *,
        capture_live_environment: bool,
    ) -> None:
        decorated = _decorate_port_call(port_call_record)
        existing_cases = {
            item["maneuver_id"]: item
            for item in self._list_raw_maneuver_cases(conn, port_call_id=decorated["id"])
            if item.get("maneuver_id")
        }
        active_ids = [
            maneuver.get("id")
            for maneuver in decorated.get("maneuver_history", []) or []
            if maneuver.get("id")
        ]
        with conn.cursor() as cur:
            if active_ids:
                cur.execute(
                    "DELETE FROM maneuver_cases WHERE port_call_id = %s AND maneuver_id <> ALL(%s)",
                    (decorated["id"], active_ids),
                )
            else:
                cur.execute("DELETE FROM maneuver_cases WHERE port_call_id = %s", (decorated["id"],))

        weather_forecast = None
        wave_conditions = None
        if capture_live_environment:
            weather_forecast, wave_conditions = _capture_live_environment_sources()

        for maneuver in decorated.get("maneuver_history", []) or []:
            if not maneuver.get("id"):
                continue
            self._upsert_maneuver_case(
                conn,
                build_maneuver_case(
                    decorated,
                    maneuver,
                    existing_case=existing_cases.get(maneuver["id"]),
                    capture_live_environment=capture_live_environment,
                    weather_forecast=weather_forecast,
                    wave_conditions=wave_conditions,
                ),
            )

    def _rebuild_maneuver_cases(self, *, capture_live_environment: bool) -> None:
        with self._connect() as conn:
            existing_cases = {
                item["maneuver_id"]: item
                for item in self._list_raw_maneuver_cases(conn)
                if item.get("maneuver_id")
            }
            with conn.cursor() as cur:
                cur.execute(self._port_call_select_clause())
                rows = cur.fetchall()
            weather_forecast = None
            wave_conditions = None
            if capture_live_environment:
                weather_forecast, wave_conditions = _capture_live_environment_sources()
            active_ids: List[str] = []
            for row in rows:
                payload = self._row_to_port_call_record(row)
                if not payload:
                    continue
                decorated = _decorate_port_call(_normalize_port_call_record(payload))
                for maneuver in decorated.get("maneuver_history", []) or []:
                    if not maneuver.get("id"):
                        continue
                    active_ids.append(maneuver["id"])
                    self._upsert_maneuver_case(
                        conn,
                        build_maneuver_case(
                            decorated,
                            maneuver,
                            existing_case=existing_cases.get(maneuver["id"]),
                            capture_live_environment=capture_live_environment,
                            weather_forecast=weather_forecast,
                            wave_conditions=wave_conditions,
                        ),
                    )
            with conn.cursor() as cur:
                if active_ids:
                    cur.execute("DELETE FROM maneuver_cases WHERE maneuver_id <> ALL(%s)", (active_ids,))
                else:
                    cur.execute("DELETE FROM maneuver_cases")
            conn.commit()

    def _fetch_port_call_record(self, conn, port_call_id: str, for_update: bool = False) -> Optional[Dict]:
        query = f"{self._port_call_select_clause()} WHERE id = %s"
        if for_update:
            query += " FOR UPDATE"
        with conn.cursor() as cur:
            cur.execute(query, (port_call_id,))
            row = cur.fetchone()
        payload = self._row_to_port_call_record(row)
        if not payload:
            return None
        return _normalize_port_call_record(payload)

    def _save_port_call_record(self, conn, record: Dict) -> Dict:
        payload = _sync_port_call_from_history(_normalize_port_call_record(record))
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE port_calls
                SET
                    vessel_name = %s,
                    vessel_short_name = %s,
                    vessel_imo = %s,
                    vessel_call_sign = %s,
                    vessel_flag = %s,
                    vessel_type = %s,
                    vessel_loa_m = %s,
                    vessel_beam_m = %s,
                    vessel_gt_t = %s,
                    vessel_max_draft_m = %s,
                    vessel_dwt_t = %s,
                    vessel_bow_thruster = %s,
                    vessel_stern_thruster = %s,
                    status = %s,
                    approval_status = %s,
                    approval_note = %s,
                    aborted_reason = %s,
                    decided_by = %s,
                    decided_at = %s,
                    eta = %s,
                    ata = %s,
                    planned_departure_at = %s,
                    departure_plan_note = %s,
                    departure_at = %s,
                    planned_shift_at = %s,
                    shift_plan_note = %s,
                    shift_at = %s,
                    shift_origin_berth = %s,
                    shift_destination_berth = %s,
                    shift_approval_status = %s,
                    shift_approval_note = %s,
                    shift_aborted_reason = %s,
                    shift_decided_by = %s,
                    shift_decided_at = %s,
                    maneuver_history = %s::jsonb,
                    berth = %s,
                    last_port = %s,
                    next_port = %s,
                    created_by = %s,
                    notes = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING
                    id::text AS id,
                    vessel_name,
                    vessel_short_name,
                    vessel_imo,
                    vessel_call_sign,
                    vessel_flag,
                    vessel_type,
                    vessel_loa_m,
                    vessel_beam_m,
                    vessel_gt_t,
                    vessel_max_draft_m,
                    vessel_dwt_t,
                    vessel_bow_thruster,
                    vessel_stern_thruster,
                    status,
                    approval_status,
                    approval_note,
                    aborted_reason,
                    decided_by,
                    decided_at,
                    eta,
                    ata,
                    planned_departure_at,
                    departure_plan_note,
                    departure_at,
                    planned_shift_at,
                    shift_plan_note,
                    shift_at,
                    shift_origin_berth,
                    shift_destination_berth,
                    shift_approval_status,
                    shift_approval_note,
                    shift_aborted_reason,
                    shift_decided_by,
                    shift_decided_at,
                    maneuver_history,
                    berth,
                    last_port,
                    next_port,
                    created_by,
                    notes,
                    created_at,
                    updated_at
                """,
                (
                    payload.get("vessel_name"),
                    payload.get("vessel_short_name", ""),
                    payload.get("vessel_imo", ""),
                    payload.get("vessel_call_sign", ""),
                    payload.get("vessel_flag", ""),
                    payload.get("vessel_type", ""),
                    payload.get("vessel_loa_m", ""),
                    payload.get("vessel_beam_m", ""),
                    payload.get("vessel_gt_t", ""),
                    payload.get("vessel_max_draft_m", ""),
                    payload.get("vessel_dwt_t", ""),
                    payload.get("vessel_bow_thruster", "unknown"),
                    payload.get("vessel_stern_thruster", "unknown"),
                    payload.get("status"),
                    payload.get("approval_status"),
                    payload.get("approval_note", ""),
                    payload.get("aborted_reason", ""),
                    payload.get("decided_by"),
                    payload.get("decided_at"),
                    payload.get("eta"),
                    payload.get("ata"),
                    payload.get("planned_departure_at"),
                    payload.get("departure_plan_note", ""),
                    payload.get("departure_at"),
                    payload.get("planned_shift_at"),
                    payload.get("shift_plan_note", ""),
                    payload.get("shift_at"),
                    payload.get("shift_origin_berth", ""),
                    payload.get("shift_destination_berth", ""),
                    payload.get("shift_approval_status"),
                    payload.get("shift_approval_note", ""),
                    payload.get("shift_aborted_reason", ""),
                    payload.get("shift_decided_by"),
                    payload.get("shift_decided_at"),
                    json.dumps(payload.get("maneuver_history", [])),
                    payload.get("berth", ""),
                    payload.get("last_port", ""),
                    payload.get("next_port", ""),
                    payload.get("created_by", "system"),
                    payload.get("notes", ""),
                    payload["id"],
                ),
            )
            row = cur.fetchone()
        if not row:
            raise ValueError("Manobra não encontrada.")
        saved = self._row_to_port_call_record(row)
        if not saved:
            raise ValueError("Manobra não encontrada.")
        self._sync_maneuver_cases_for_port_call(conn, saved, capture_live_environment=True)
        return saved

    def _mutate_port_call(self, port_call_id: str, mutator) -> Dict:
        with self._connect() as conn:
            current = self._fetch_port_call_record(conn, port_call_id, for_update=True)
            if not current:
                raise ValueError("Manobra não encontrada.")
            updated = mutator(current)
            updated["updated_at"] = iso_now()
            saved = self._save_port_call_record(conn, updated)
            conn.commit()
        return _decorate_port_call(saved)

    def _ensure_schema(self) -> None:
        schema_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sql", "postgres_schema.sql")
        with open(schema_path, "r", encoding="utf-8") as handle:
            schema_sql = handle.read()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(schema_sql)
            conn.commit()

    def _seed_defaults(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                for username, password, role in (
                    ("admin", "admin123", "admin"),
                    ("agente", "agente123", "agente"),
                    ("piloto", "piloto123", "piloto"),
                ):
                    cur.execute(
                        """
                        INSERT INTO app_users (username, password_hash, role, full_name, organization, email, phone)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username) DO NOTHING
                        """,
                        (username, generate_password_hash(password, method=PASSWORD_HASH_METHOD), role, "", "", "", ""),
                    )
                cur.execute("SELECT COUNT(*) AS total FROM port_calls")
                port_calls_count = int(cur.fetchone()["total"])
            conn.commit()

        if port_calls_count == 0:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    for item in _default_port_calls():
                        cur.execute(
                            """
                            INSERT INTO port_calls (
                                id, vessel_name, status, approval_status, approval_note, aborted_reason,
                                decided_by, decided_at, eta, ata, planned_departure_at, departure_plan_note, departure_at,
                                planned_shift_at, shift_plan_note, shift_at, shift_origin_berth, shift_destination_berth,
                                shift_approval_status, shift_approval_note, shift_aborted_reason, shift_decided_by, shift_decided_at, berth,
                                last_port, next_port, created_by, notes, created_at, updated_at
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                            """,
                            (
                                item["id"],
                                item["vessel_name"],
                                item["status"],
                                item.get("approval_status", PORT_CALL_APPROVAL_PENDING),
                                item.get("approval_note", ""),
                                item.get("aborted_reason", ""),
                                item.get("decided_by"),
                                item.get("decided_at"),
                                item.get("eta"),
                                item.get("ata"),
                                item.get("planned_departure_at"),
                                item.get("departure_plan_note", ""),
                                item.get("departure_at"),
                                item.get("planned_shift_at"),
                                item.get("shift_plan_note", ""),
                                item.get("shift_at"),
                                item.get("shift_origin_berth", ""),
                                item.get("shift_destination_berth", ""),
                                item.get("shift_approval_status", PORT_CALL_APPROVAL_PENDING),
                                item.get("shift_approval_note", ""),
                                item.get("shift_aborted_reason", ""),
                                item.get("shift_decided_by"),
                                item.get("shift_decided_at"),
                                item.get("berth"),
                                item.get("last_port"),
                                item.get("next_port"),
                                item.get("created_by", "system"),
                                item.get("notes", ""),
                                item.get("created_at"),
                                item.get("updated_at"),
                            ),
                        )
                conn.commit()

    def _remove_legacy_seed_markdown_documents(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, uploaded_by, preview, file_path
                    FROM documents
                    """
                )
                rows = cur.fetchall()

                removed_names = []
                for row in rows:
                    text = ""
                    file_path = row.get("file_path")
                    if file_path and os.path.exists(file_path):
                        try:
                            text = read_text_file(file_path)
                        except OSError:
                            text = ""
                    if not is_legacy_system_markdown_document(
                        name=row.get("name"),
                        uploaded_by=row.get("uploaded_by"),
                        preview=row.get("preview"),
                        text=text,
                    ):
                        continue
                    if file_path and os.path.exists(file_path):
                        os.remove(file_path)
                    cur.execute("DELETE FROM documents WHERE name = %s", (row["name"],))
                    removed_names.append(row["name"])

            conn.commit()

        for name in os.listdir(self.knowledge_dir):
            path = os.path.join(self.knowledge_dir, name)
            if not os.path.isfile(path) or name in removed_names:
                continue
            try:
                text = read_text_file(path)
            except OSError:
                continue
            if is_legacy_system_markdown_document(name=name, text=text):
                os.remove(path)

    def _upsert_document_record(self, record: Dict, file_path: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview, file_path
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET
                        original_name = EXCLUDED.original_name,
                        doc_type = EXCLUDED.doc_type,
                        size_bytes = EXCLUDED.size_bytes,
                        updated_at = EXCLUDED.updated_at,
                        uploaded_by = EXCLUDED.uploaded_by,
                        preview = EXCLUDED.preview,
                        file_path = EXCLUDED.file_path
                    """,
                    (
                        record["name"],
                        record["original_name"],
                        record["doc_type"],
                        record["size_bytes"],
                        record["updated_at"],
                        record["created_at"],
                        record["uploaded_by"],
                        record["preview"],
                        file_path,
                    ),
                )
            conn.commit()

    def _sync_document_records(self) -> None:
        existing_records = {}
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, original_name, doc_type, size_bytes, updated_at, created_at, uploaded_by, preview
                    FROM documents
                    """
                )
                for row in cur.fetchall():
                    existing_records[row["name"]] = row

        seen_names = set()
        for name in sorted(os.listdir(self.knowledge_dir)):
            path = os.path.join(self.knowledge_dir, name)
            if not os.path.isfile(path) or not is_allowed_document(name):
                continue
            seen_names.add(name)
            meta = file_metadata(path)
            previous = existing_records.get(name, {})
            preview = previous.get("preview", "")
            previous_updated_at = previous.get("updated_at")
            if hasattr(previous_updated_at, "isoformat"):
                previous_updated_at = previous_updated_at.isoformat()
            if (
                previous.get("size_bytes") != meta["size_bytes"]
                or previous_updated_at != meta["updated_at"]
                or not preview
            ):
                try:
                    text = extract_text_from_path(path)
                    preview = build_preview(text)
                except Exception as exc:
                    preview = f"Erro ao extrair conteúdo: {exc}"
            record = {
                "name": name,
                "original_name": previous.get("original_name", name),
                "doc_type": infer_document_type(name),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": previous.get("created_at", meta["updated_at"]),
                "uploaded_by": previous.get("uploaded_by", "system"),
                "preview": preview,
            }
            self._upsert_document_record(record, path)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM documents")
                existing_names = {row["name"] for row in cur.fetchall()}
                stale_names = sorted(existing_names - seen_names)
                for name in stale_names:
                    cur.execute("DELETE FROM documents WHERE name = %s", (name,))
            conn.commit()

    def list_users(self) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, role, full_name, organization, email, phone, profile_completed_at
                    FROM app_users
                    ORDER BY username
                    """
                )
                rows = cur.fetchall()
        return [_normalize_user_profile_payload(row) for row in rows]

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        full_name: str = "",
        organization: str = "",
        email: str = "",
        phone: str = "",
    ) -> Dict:
        username = _normalize_username(username)
        if len(username) < 3:
            raise ValueError("O email deve ter pelo menos 3 caracteres.")
        if len(password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        profile = _normalize_user_profile_payload(
            {
                "username": username,
                "role": role,
                "full_name": full_name,
                "organization": organization,
                "email": email,
                "phone": phone,
            }
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM app_users WHERE username = %s", (username,))
                if cur.fetchone():
                    raise ValueError("Esse utilizador ja existe.")
                cur.execute(
                    """
                    INSERT INTO app_users (
                        username, password_hash, role, full_name, organization, email, phone, profile_completed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        username,
                        generate_password_hash(password, method=PASSWORD_HASH_METHOD),
                        role,
                        profile["full_name"],
                        profile["organization"],
                        profile["email"],
                        profile["phone"],
                        profile["profile_completed_at"],
                    ),
                )
            conn.commit()
        return profile

    def authenticate(self, username: str, password: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, password_hash, role, full_name, organization, email, phone, profile_completed_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (_normalize_username(username),),
                )
                user = cur.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            return _normalize_user_profile_payload(user)
        return None

    def get_user_profile(self, username: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, role, full_name, organization, email, phone, profile_completed_at
                    FROM app_users
                    WHERE username = %s
                    """,
                    (_normalize_username(username),),
                )
                row = cur.fetchone()
        return _normalize_user_profile_payload(row) if row else None

    def set_user_role(self, username: str, role: str) -> Dict:
        username = _normalize_username(username)
        if role not in {"admin", "agente", "piloto"}:
            raise ValueError("Role invalido.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_users
                    SET role = %s,
                        profile_completed_at = CASE
                            WHEN COALESCE(full_name, '') <> ''
                             AND COALESCE(organization, '') <> ''
                             AND COALESCE(email, '') <> ''
                             AND COALESCE(phone, '') <> ''
                            THEN COALESCE(profile_completed_at, NOW())
                            ELSE NULL
                        END
                    WHERE username = %s
                    RETURNING username, role, full_name, organization, email, phone, profile_completed_at
                    """,
                    (role, username),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Utilizador não encontrado.")
        return _normalize_user_profile_payload(row)

    def reset_user_password(self, username: str, new_password: str) -> bool:
        username = _normalize_username(username)
        if len(new_password) < 6:
            raise ValueError("A password deve ter pelo menos 6 caracteres.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE app_users SET password_hash = %s WHERE username = %s",
                    (generate_password_hash(new_password, method=PASSWORD_HASH_METHOD), username),
                )
                conn.commit()
                return cur.rowcount > 0

    def delete_user(self, username: str) -> None:
        normalized_username = _normalize_username(username)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT username, role
                    FROM app_users
                    WHERE username = %s
                    """,
                    (normalized_username,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError("Utilizador não encontrado.")
                if row["role"] == "admin":
                    cur.execute("SELECT COUNT(*) AS total FROM app_users WHERE role = 'admin'")
                    admin_total = cur.fetchone()["total"]
                    if admin_total <= 1:
                        raise ValueError("Não podes apagar o último admin.")
                cur.execute("DELETE FROM app_users WHERE username = %s", (normalized_username,))
            conn.commit()

    def update_user_profile(
        self,
        username: str,
        *,
        full_name: str,
        organization: str,
        email: str,
        phone: str,
    ) -> Dict:
        profile = _normalize_user_profile_payload(
            {
                "username": username,
                "full_name": full_name,
                "organization": organization,
                "email": email,
                "phone": phone,
                "role": (self.get_user_profile(username) or {}).get("role", "piloto"),
            }
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app_users
                    SET
                        full_name = %s,
                        organization = %s,
                        email = %s,
                        phone = %s,
                        profile_completed_at = %s
                    WHERE username = %s
                    RETURNING username, role, full_name, organization, email, phone, profile_completed_at
                    """,
                    (
                        profile["full_name"],
                        profile["organization"],
                        profile["email"],
                        profile["phone"],
                        profile["profile_completed_at"],
                        _normalize_username(username),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Utilizador não encontrado.")
        return _normalize_user_profile_payload(row)

    def save_document(self, title: str, content: str, created_by: str = "manual") -> str:
        filename = ensure_unique_filename(self.knowledge_dir, f"{slugify(title)}.md")
        path = os.path.join(self.knowledge_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")
        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(content),
                "editable": is_text_editable(filename),
            },
            path,
        )
        return filename

    def save_uploaded_document(self, uploaded_file: FileStorage, created_by: str) -> str:
        filename = sanitize_upload_filename(uploaded_file.filename or "")
        if not is_allowed_document(filename):
            raise ValueError("Formato não suportado. Usa .pdf, .md, .txt, .docx ou .csv.")

        path = os.path.join(self.knowledge_dir, filename)
        stem, suffix = os.path.splitext(path)
        temp_path = f"{stem}.upload-{uuid.uuid4().hex}{suffix}"
        uploaded_file.save(temp_path)

        try:
            text = extract_text_from_path(temp_path)
        except Exception as exc:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise ValueError(f"Falha ao processar ficheiro: {exc}") from exc
        if not text.strip():
            os.remove(temp_path)
            raise ValueError("Não foi possível extrair texto útil do ficheiro.")

        os.replace(temp_path, path)

        meta = file_metadata(path)
        self._upsert_document_record(
            {
                "name": filename,
                "original_name": uploaded_file.filename or filename,
                "doc_type": infer_document_type(filename),
                "size_bytes": meta["size_bytes"],
                "updated_at": meta["updated_at"],
                "created_at": iso_now(),
                "uploaded_by": created_by,
                "preview": build_preview(text),
                "editable": is_text_editable(filename),
            },
            path,
        )
        return filename

    def list_documents(self) -> List[Dict]:
        self._sync_document_records()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview
                    FROM documents
                    ORDER BY updated_at DESC, name
                    """
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "size_label": format_bytes(row["size_bytes"]),
                "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
                "editable": is_text_editable(row["name"]),
            }
            for row in rows
        ]

    def get_document(self, name: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        name, original_name, doc_type, size_bytes, updated_at, created_at,
                        uploaded_by, preview, file_path
                    FROM documents
                    WHERE name = %s
                    """,
                    (name,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            **row,
            "size_label": format_bytes(row["size_bytes"]),
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
            "editable": is_text_editable(row["name"]),
        }

    def get_document_text(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return extract_text_from_path(record["file_path"])

    def get_document_file_path(self, name: str) -> str:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        return record["file_path"]

    def update_document_text(self, name: str, content: str, updated_by: str) -> Dict:
        if not content.strip():
            raise ValueError("O conteúdo não pode estar vazio.")
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if not is_text_editable(name):
            raise ValueError("Este tipo de ficheiro não pode ser editado no browser.")

        with open(record["file_path"], "w", encoding="utf-8") as handle:
            handle.write(content.strip() + "\n")
        meta = file_metadata(record["file_path"])

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE documents
                    SET
                        size_bytes = %s,
                        updated_at = %s,
                        uploaded_by = %s,
                        preview = %s
                    WHERE name = %s
                    """,
                    (
                        meta["size_bytes"],
                        meta["updated_at"],
                        updated_by,
                        build_preview(content),
                        name,
                    ),
                )
            conn.commit()
        return self.get_document(name)

    def delete_document(self, name: str) -> None:
        record = self.get_document(name)
        if not record:
            raise ValueError("Documento não encontrado.")
        if os.path.exists(record["file_path"]):
            os.remove(record["file_path"])
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM documents WHERE name = %s", (name,))
            conn.commit()

    def list_conversations(self, username: str) -> List[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text AS id, username, title, created_at, updated_at
                    FROM conversations
                    WHERE username = %s
                    ORDER BY updated_at DESC
                    """,
                    (username,),
                )
                rows = cur.fetchall()
        return [
            {
                **row,
                "updated_at": row["updated_at"].isoformat(),
                "created_at": row["created_at"].isoformat(),
                "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
                "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
            }
            for row in rows
        ]

    def create_conversation(self, username: str, title: str = DEFAULT_CONVERSATION_TITLE) -> Dict:
        conversation_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversations (id, username, title)
                    VALUES (%s, %s, %s)
                    RETURNING id::text AS id, username, title, created_at, updated_at
                    """,
                    (conversation_id, username, title),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            **row,
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
        }

    def rename_conversation(self, username: str, conversation_id: str, title: str) -> Dict:
        clean_title = " ".join(title.strip().split())
        if not clean_title:
            raise ValueError("O título da conversa não pode ficar vazio.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE conversations
                    SET title = %s, updated_at = NOW()
                    WHERE id = %s AND username = %s
                    RETURNING id::text AS id, username, title, created_at, updated_at
                    """,
                    (clean_title, conversation_id, username),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise ValueError("Conversa não encontrada.")
        return {
            **row,
            "updated_at": row["updated_at"].isoformat(),
            "created_at": row["created_at"].isoformat(),
            "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
            "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
        }

    def clear_conversation(self, username: str, conversation_id: str) -> None:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa não encontrada.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM messages WHERE conversation_id = %s",
                    (conversation_id,),
                )
                cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()

    def delete_conversation(self, username: str, conversation_id: str) -> Optional[str]:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa não encontrada.")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM conversations WHERE id = %s AND username = %s",
                    (conversation_id, username),
                )
            conn.commit()
        remaining = self.list_conversations(username)
        return remaining[0]["id"] if remaining else None

    def ensure_conversation(self, username: str, conversation_id: Optional[str] = None) -> Dict:
        if conversation_id:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id::text AS id, username, title, created_at, updated_at
                        FROM conversations
                        WHERE id = %s AND username = %s
                        """,
                        (conversation_id, username),
                    )
                    row = cur.fetchone()
            if row:
                return {
                    **row,
                    "updated_at": row["updated_at"].isoformat(),
                    "created_at": row["created_at"].isoformat(),
                    "created_at_label": _utc_iso_to_label(row["created_at"].isoformat()),
                    "updated_at_label": _utc_iso_to_label(row["updated_at"].isoformat()),
                }

        conversations = self.list_conversations(username)
        if conversations:
            return conversations[0]
        return self.create_conversation(username)

    def list_messages(self, username: str, conversation_id: str) -> List[Dict]:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            return []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    FROM messages
                    WHERE conversation_id = %s
                    ORDER BY created_at ASC
                    """,
                    (conversation_id,),
                )
                rows = cur.fetchall()
        return [self._row_to_chat_message_record(row) for row in rows if row]

    def append_chat_message(
        self,
        username: str,
        conversation_id: str,
        role: str,
        content: str,
        citations: Optional[List[Dict]] = None,
        *,
        channel: str = "web",
        channel_user_id: str = "",
        external_message_id: str = "",
        external_reply_to_id: str = "",
        channel_metadata: Optional[Dict] = None,
    ) -> Dict:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")
        message_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO messages (
                        id,
                        conversation_id,
                        role,
                        content,
                        citations,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb)
                    RETURNING
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    """,
                    (
                        message_id,
                        conversation_id,
                        role,
                        content,
                        json.dumps(citations or []),
                        (channel or "web").strip() or "web",
                        (channel_user_id or "").strip(),
                        (external_message_id or "").strip(),
                        (external_reply_to_id or "").strip(),
                        json.dumps(channel_metadata or {}),
                    ),
                )
                row = cur.fetchone()

                title_hint = None
                if role == "user":
                    cur.execute(
                        """
                        SELECT
                            title,
                            COUNT(*) FILTER (WHERE role = 'user') AS user_message_count
                        FROM conversations c
                        LEFT JOIN messages m ON m.conversation_id = c.id
                        WHERE c.id = %s
                        GROUP BY c.title
                        """,
                        (conversation_id,),
                    )
                    stats = cur.fetchone()
                    if stats and (
                        stats["title"] == DEFAULT_CONVERSATION_TITLE
                        or stats["user_message_count"] <= 1
                    ):
                        title_hint = _conversation_title_from_text(content)

                if title_hint:
                    cur.execute(
                        "UPDATE conversations SET title = %s, updated_at = NOW() WHERE id = %s",
                        (title_hint, conversation_id),
                    )
                else:
                    cur.execute(
                        "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                        (conversation_id,),
                    )
            conn.commit()
        message = self._row_to_chat_message_record(row)
        if not message:
            raise ValueError("Falha ao gravar mensagem.")
        return message

    def update_message_channel_metadata(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        *,
        channel: Optional[str] = None,
        channel_user_id: Optional[str] = None,
        external_message_id: Optional[str] = None,
        external_reply_to_id: Optional[str] = None,
        channel_metadata: Optional[Dict] = None,
    ) -> Dict:
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE messages
                    SET
                        channel = COALESCE(%s, channel),
                        channel_user_id = COALESCE(%s, channel_user_id),
                        external_message_id = COALESCE(%s, external_message_id),
                        external_reply_to_id = COALESCE(%s, external_reply_to_id),
                        channel_metadata = COALESCE(channel_metadata, '{}'::jsonb) || %s::jsonb
                    WHERE id = %s AND conversation_id = %s
                    RETURNING
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    """,
                    (
                        (channel or "").strip() or None if channel is not None else None,
                        (channel_user_id or "").strip() if channel_user_id is not None else None,
                        (external_message_id or "").strip() if external_message_id is not None else None,
                        (external_reply_to_id or "").strip() if external_reply_to_id is not None else None,
                        json.dumps(channel_metadata or {}),
                        message_id,
                        conversation_id,
                    ),
                )
                row = cur.fetchone()
                cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()
        message = self._row_to_chat_message_record(row)
        if not message:
            raise ValueError("Mensagem não encontrada.")
        return message

    def find_message_by_channel_message_id(self, channel: str, external_message_id: str) -> Optional[Dict]:
        clean_channel = (channel or "").strip() or "web"
        clean_external_id = (external_message_id or "").strip()
        if not clean_external_id:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        m.id::text AS id,
                        m.conversation_id::text AS conversation_id,
                        c.username,
                        m.role,
                        m.content,
                        m.citations,
                        m.created_at,
                        m.feedback_status,
                        m.feedback_note,
                        m.feedback_updated_at,
                        m.channel,
                        m.channel_user_id,
                        m.external_message_id,
                        m.external_reply_to_id,
                        m.channel_metadata
                    FROM messages m
                    JOIN conversations c ON c.id = m.conversation_id
                    WHERE m.channel = %s
                      AND m.external_message_id = %s
                    LIMIT 1
                    """,
                    (clean_channel, clean_external_id),
                )
                row = cur.fetchone()
        return self._row_to_chat_message_record(row)

    def get_runtime_state(self, key: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT value
                    FROM app_runtime_state
                    WHERE key = %s
                    """,
                    (key,),
                )
                row = cur.fetchone()
        value = row["value"] if row else None
        return value if isinstance(value, dict) else None

    def set_runtime_state(self, key: str, value: Dict) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO app_runtime_state (key, value, updated_at)
                    VALUES (%s, %s::jsonb, NOW())
                    ON CONFLICT (key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    (key, json.dumps(value or {})),
                )
            conn.commit()
        return value

    def delete_runtime_state(self, key: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app_runtime_state WHERE key = %s", (key,))
            conn.commit()

    def record_channel_event(
        self,
        *,
        channel: str,
        event_type: str,
        payload: Dict,
        username: str = "",
        conversation_id: str = "",
        local_message_id: str = "",
        channel_user_id: str = "",
        external_event_id: str = "",
        external_message_id: str = "",
    ) -> Dict:
        event_id = str(uuid.uuid4())
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO channel_events (
                        id,
                        channel,
                        event_type,
                        username,
                        conversation_id,
                        local_message_id,
                        channel_user_id,
                        external_event_id,
                        external_message_id,
                        payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING
                        id::text AS id,
                        channel,
                        event_type,
                        username,
                        conversation_id::text AS conversation_id,
                        local_message_id::text AS local_message_id,
                        channel_user_id,
                        external_event_id,
                        external_message_id,
                        payload,
                        created_at
                    """,
                    (
                        event_id,
                        (channel or "").strip() or "unknown",
                        (event_type or "").strip() or "unknown",
                        _normalize_username(username),
                        conversation_id or None,
                        local_message_id or None,
                        (channel_user_id or "").strip(),
                        (external_event_id or "").strip(),
                        (external_message_id or "").strip(),
                        json.dumps(payload or {}),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return {
            **row,
            "created_at": row["created_at"].isoformat(),
        }

    def update_message_feedback(
        self,
        username: str,
        conversation_id: str,
        message_id: str,
        feedback_status: str,
        feedback_note: str = "",
    ) -> Dict:
        if feedback_status not in ALLOWED_FEEDBACK_STATUSES:
            raise ValueError("Estado de feedback inválido.")
        conversation = self.ensure_conversation(username, conversation_id)
        if conversation["id"] != conversation_id:
            raise ValueError("Conversa inválida para este utilizador.")

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE messages
                    SET
                        feedback_status = %s,
                        feedback_note = %s,
                        feedback_updated_at = NOW()
                    WHERE id = %s AND conversation_id = %s AND role = 'assistant'
                    RETURNING
                        id::text AS id,
                        conversation_id::text AS conversation_id,
                        role,
                        content,
                        citations,
                        created_at,
                        feedback_status,
                        feedback_note,
                        feedback_updated_at,
                        channel,
                        channel_user_id,
                        external_message_id,
                        external_reply_to_id,
                        channel_metadata
                    """,
                    (feedback_status, feedback_note.strip(), message_id, conversation_id),
                )
                row = cur.fetchone()
                cur.execute(
                    "UPDATE conversations SET updated_at = NOW() WHERE id = %s",
                    (conversation_id,),
                )
            conn.commit()
        message = self._row_to_chat_message_record(row)
        if not message:
            raise ValueError("Mensagem não encontrada.")
        return message

    def find_feedback_matches(
        self,
        username: str,
        question: str,
        limit: int = 3,
        feedback_statuses: Optional[set[str]] = None,
    ) -> List[Dict]:
        allowed_statuses = {
            (status or "").strip().lower()
            for status in (feedback_statuses or {FEEDBACK_APPROVED})
            if (status or "").strip()
        }
        allowed_statuses &= ALLOWED_FEEDBACK_STATUSES
        if not allowed_statuses:
            return []

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        assistant.id::text AS message_id,
                        assistant.conversation_id::text AS conversation_id,
                        assistant.content AS answer,
                        assistant.citations,
                        assistant.feedback_status,
                        assistant.feedback_note,
                        assistant.feedback_updated_at,
                        user_msg.content AS question
                    FROM messages assistant
                    JOIN conversations c ON c.id = assistant.conversation_id
                    JOIN LATERAL (
                        SELECT content
                        FROM messages
                        WHERE conversation_id = assistant.conversation_id
                          AND role = 'user'
                          AND created_at <= assistant.created_at
                        ORDER BY created_at DESC
                        LIMIT 1
                    ) user_msg ON TRUE
                    WHERE c.username = %s
                      AND assistant.role = 'assistant'
                      AND assistant.feedback_status = ANY(%s)
                    ORDER BY assistant.feedback_updated_at DESC NULLS LAST, assistant.created_at DESC
                    """,
                    (username, sorted(allowed_statuses)),
                )
                rows = cur.fetchall()

        matches = []
        for row in rows:
            score = _text_similarity(question, row.get("question", ""))
            if score < 0.35:
                continue
            matches.append(
                {
                    **row,
                    "similarity": round(score, 3),
                    "feedback_updated_at": (
                        row["feedback_updated_at"].isoformat() if row["feedback_updated_at"] else None
                    ),
                }
            )
        matches.sort(
            key=lambda item: (
                item["similarity"],
                item.get("feedback_updated_at") or "",
            ),
            reverse=True,
        )
        return matches[:limit]

    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"{self._port_call_select_clause()} ORDER BY COALESCE(eta, ata, departure_at) ASC NULLS LAST, vessel_name")
                rows = cur.fetchall()
        records = [self._row_to_port_call_record(row) for row in rows]
        return _build_port_activity_snapshot(records, window_days=window_days)

    def list_maneuver_cases(
        self,
        *,
        limit: int = 100,
        maneuver_type: Optional[str] = None,
        state: Optional[str] = None,
        port_call_id: Optional[str] = None,
    ) -> List[Dict]:
        with self._connect() as conn:
            rows = self._list_raw_maneuver_cases(
                conn,
                limit=limit,
                maneuver_type=maneuver_type,
                state=state,
                port_call_id=port_call_id,
            )
        return [decorate_maneuver_case(item) for item in rows]

    def get_maneuver_case(self, maneuver_id: str) -> Optional[Dict]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        maneuver_id,
                        port_call_id::text AS port_call_id,
                        reference_code,
                        vessel_name,
                        maneuver_type,
                        current_state,
                        origin_label,
                        destination_label,
                        planned_at,
                        decided_at,
                        completed_at,
                        reported_at,
                        latest_event_at,
                        case_summary,
                        vessel_snapshot,
                        scale_snapshot,
                        planning_snapshot,
                        decision_snapshot,
                        execution_snapshot,
                        outcome_snapshot,
                        environment_snapshot,
                        feature_snapshot,
                        change_log,
                        created_at,
                        updated_at
                    FROM maneuver_cases
                    WHERE maneuver_id = %s
                    """,
                    (maneuver_id,),
                )
                row = cur.fetchone()
        payload = self._row_to_maneuver_case_record(row)
        return decorate_maneuver_case(payload) if payload else None

    def find_similar_maneuver_cases(
        self,
        *,
        maneuver_type: str,
        origin: str = "",
        destination: str = "",
        vessel_type: str = "",
        vessel_loa_m: str = "",
        bow_thruster: str = "",
        stern_thruster: str = "",
        tug_count: str = "",
        limit: int = 5,
    ) -> List[Dict]:
        with self._connect() as conn:
            rows = self._list_raw_maneuver_cases(conn, limit=500, maneuver_type=maneuver_type)
        return rank_similar_maneuver_cases(
            rows,
            maneuver_type=maneuver_type,
            origin=origin,
            destination=destination,
            vessel_type=vessel_type,
            vessel_loa_m=vessel_loa_m,
            bow_thruster=bow_thruster,
            stern_thruster=stern_thruster,
            tug_count=tug_count,
            limit=limit,
        )

    def update_maneuver_case_feedback(
        self,
        *,
        maneuver_id: str,
        feedback_status: str,
        feedback_note: str = "",
        feedback_by: str = "",
    ) -> Dict:
        feedback_status = validate_operational_feedback_status(feedback_status)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE maneuver_cases
                    SET
                        feedback_status = %s,
                        feedback_note = %s,
                        feedback_updated_by = %s,
                        feedback_updated_at = NOW(),
                        updated_at = NOW()
                    WHERE maneuver_id = %s
                    RETURNING
                        maneuver_id,
                        port_call_id::text AS port_call_id,
                        reference_code,
                        vessel_name,
                        maneuver_type,
                        current_state,
                        origin_label,
                        destination_label,
                        planned_at,
                        decided_at,
                        completed_at,
                        reported_at,
                        latest_event_at,
                        case_summary,
                        vessel_snapshot,
                        scale_snapshot,
                        planning_snapshot,
                        decision_snapshot,
                        execution_snapshot,
                        outcome_snapshot,
                        environment_snapshot,
                        feature_snapshot,
                        change_log,
                        feedback_status,
                        feedback_note,
                        feedback_updated_by,
                        feedback_updated_at,
                        created_at,
                        updated_at
                    """,
                    (feedback_status.strip().lower(), feedback_note.strip(), feedback_by.strip(), maneuver_id),
                )
                row = cur.fetchone()
            conn.commit()
        payload = self._row_to_maneuver_case_record(row)
        if not payload:
            raise ValueError("Caso operacional não encontrado.")
        return decorate_maneuver_case(payload)

    def clear_port_calls(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM port_calls")
                removed = int(cur.fetchone()["total"])
                cur.execute("DELETE FROM port_calls")
            conn.commit()
        return removed

    def get_port_call(self, port_call_id: str) -> Dict:
        with self._connect() as conn:
            payload = self._fetch_port_call_record(conn, port_call_id)
        if not payload:
            raise ValueError("Escala não encontrada.")
        return _decorate_port_call(payload)

    def edit_port_call(
        self,
        port_call_id: str,
        *,
        updated_by: str,
        vessel_name: Optional[str] = None,
        eta: Optional[str] = None,
        berth: Optional[str] = None,
        last_port: Optional[str] = None,
        next_port: Optional[str] = None,
        notes: Optional[str] = None,
        constraints: Optional[List[str]] = None,
        vessel_short_name: Optional[str] = None,
        vessel_imo: Optional[str] = None,
        vessel_call_sign: Optional[str] = None,
        vessel_flag: Optional[str] = None,
        vessel_type: Optional[str] = None,
        vessel_loa_m: Optional[str] = None,
        vessel_beam_m: Optional[str] = None,
        vessel_gt_t: Optional[str] = None,
        vessel_max_draft_m: Optional[str] = None,
        vessel_dwt_t: Optional[str] = None,
        vessel_bow_thruster: Optional[str] = None,
        vessel_stern_thruster: Optional[str] = None,
    ) -> Dict:
        def mutator(current: Dict) -> Dict:
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry")
            if not entry:
                raise ValueError("Escala sem manobra de entrada associada.")

            updated_vessel_name = _clean_text(vessel_name) if vessel_name is not None else current.get("vessel_name", "")
            if len(updated_vessel_name) < 2:
                raise ValueError("Indica o nome do navio.")
            updated_berth = _clean_text(berth) if berth is not None else current.get("berth", "")
            updated_last_port = _clean_text(last_port) if last_port is not None else current.get("last_port", "")
            updated_next_port = _clean_text(next_port) if next_port is not None else current.get("next_port", "")
            updated_eta = eta.strip() if eta is not None else (entry.get("planned_at") or current.get("eta") or "")
            if not updated_eta:
                raise ValueError("O ETA é obrigatório.")

            vessel_profile = {
                "vessel_short_name": _clean_text(vessel_short_name) if vessel_short_name is not None else current.get("vessel_short_name", ""),
                "vessel_imo": _clean_text(vessel_imo) if vessel_imo is not None else current.get("vessel_imo", ""),
                "vessel_call_sign": _clean_text(vessel_call_sign) if vessel_call_sign is not None else current.get("vessel_call_sign", ""),
                "vessel_flag": _clean_text(vessel_flag) if vessel_flag is not None else current.get("vessel_flag", ""),
                "vessel_type": _clean_text(vessel_type) if vessel_type is not None else current.get("vessel_type", ""),
                "vessel_loa_m": _clean_text(vessel_loa_m) if vessel_loa_m is not None else current.get("vessel_loa_m", ""),
                "vessel_beam_m": _clean_text(vessel_beam_m) if vessel_beam_m is not None else current.get("vessel_beam_m", ""),
                "vessel_gt_t": _clean_text(vessel_gt_t) if vessel_gt_t is not None else current.get("vessel_gt_t", ""),
                "vessel_max_draft_m": _clean_text(vessel_max_draft_m) if vessel_max_draft_m is not None else current.get("vessel_max_draft_m", ""),
                "vessel_dwt_t": _clean_text(vessel_dwt_t) if vessel_dwt_t is not None else current.get("vessel_dwt_t", ""),
                "vessel_bow_thruster": normalize_thruster_state(
                    vessel_bow_thruster if vessel_bow_thruster is not None else current.get("vessel_bow_thruster", "unknown"),
                    "Bow thruster",
                ),
                "vessel_stern_thruster": normalize_thruster_state(
                    vessel_stern_thruster if vessel_stern_thruster is not None else current.get("vessel_stern_thruster", "unknown"),
                    "Stern thruster",
                ),
            }
            _validate_required_vessel_profile(vessel_profile)
            _validate_required_operational_profile(
                {
                    "berth": updated_berth,
                    "last_port": updated_last_port,
                    "next_port": updated_next_port,
                },
                (
                    ("berth", "cais previsto"),
                    ("last_port", "porto anterior"),
                    ("next_port", "próximo destino"),
                ),
            )

            current.update(
                {
                    "vessel_name": updated_vessel_name,
                    **vessel_profile,
                    "berth": updated_berth,
                    "last_port": updated_last_port,
                    "next_port": updated_next_port,
                }
            )
            if notes is not None:
                current["notes"] = notes.strip()
                entry["plan_note"] = notes.strip()
            entry["planned_at"] = updated_eta
            entry["origin"] = updated_last_port
            entry["destination"] = updated_berth
            if constraints is not None:
                entry["constraints"] = normalize_constraint_codes(constraints)
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def delete_port_call(self, port_call_id: str) -> Dict:
        with self._connect() as conn:
            current = self._fetch_port_call_record(conn, port_call_id, for_update=True)
            if not current:
                raise ValueError("Escala não encontrada.")
            with conn.cursor() as cur:
                cur.execute("DELETE FROM port_calls WHERE id = %s", (port_call_id,))
            conn.commit()
        return _decorate_port_call(current)

    def create_port_call(
        self,
        vessel_name: str,
        eta: str,
        created_by: str,
        constraints: Optional[List[str]] = None,
        berth: str = "",
        last_port: str = "",
        next_port: str = "",
        notes: str = "",
        vessel_short_name: str = "",
        vessel_imo: str = "",
        vessel_call_sign: str = "",
        vessel_flag: str = "",
        vessel_type: str = "",
        vessel_loa_m: str = "",
        vessel_beam_m: str = "",
        vessel_gt_t: str = "",
        vessel_max_draft_m: str = "",
        vessel_dwt_t: str = "",
        vessel_bow_thruster: str = "unknown",
        vessel_stern_thruster: str = "unknown",
    ) -> Dict:
        clean_name = _clean_text(vessel_name)
        creator_username = _normalize_username(created_by) or "system"
        creator_profile = self.get_user_profile(creator_username)
        if len(clean_name) < 2:
            raise ValueError("Indica o nome do navio.")
        if not eta.strip():
            raise ValueError("O ETA é obrigatório.")
        vessel_profile = {
            "vessel_short_name": _clean_text(vessel_short_name),
            "vessel_imo": _clean_text(vessel_imo),
            "vessel_call_sign": _clean_text(vessel_call_sign),
            "vessel_flag": _clean_text(vessel_flag),
            "vessel_type": _clean_text(vessel_type),
            "vessel_loa_m": _clean_text(vessel_loa_m),
            "vessel_beam_m": _clean_text(vessel_beam_m),
            "vessel_gt_t": _clean_text(vessel_gt_t),
            "vessel_max_draft_m": _clean_text(vessel_max_draft_m),
            "vessel_dwt_t": _clean_text(vessel_dwt_t),
            "vessel_bow_thruster": normalize_thruster_state(vessel_bow_thruster, "Bow thruster"),
            "vessel_stern_thruster": normalize_thruster_state(vessel_stern_thruster, "Stern thruster"),
        }
        _validate_required_vessel_profile(vessel_profile)
        _validate_required_operational_profile(
            {
                "berth": berth,
                "last_port": last_port,
                "next_port": next_port,
            },
            (
                ("berth", "cais previsto"),
                ("last_port", "porto anterior"),
                ("next_port", "próximo destino"),
            ),
        )

        record = {
            "id": str(uuid.uuid4()),
            "vessel_name": clean_name,
            **vessel_profile,
            "status": PORT_CALL_STATUS_SCHEDULED,
            "approval_status": PORT_CALL_APPROVAL_PENDING,
            "approval_note": "",
            "aborted_reason": "",
            "decided_by": None,
            "decided_at": None,
            "eta": eta,
            "ata": None,
            "planned_departure_at": None,
            "departure_plan_note": "",
            "departure_at": None,
            "planned_shift_at": None,
            "shift_plan_note": "",
            "shift_at": None,
            "shift_origin_berth": "",
            "shift_destination_berth": "",
            "shift_approval_status": PORT_CALL_APPROVAL_PENDING,
            "shift_approval_note": "",
            "shift_aborted_reason": "",
            "shift_decided_by": None,
            "shift_decided_at": None,
            "berth": _clean_text(berth),
            "last_port": _clean_text(last_port),
            "next_port": _clean_text(next_port),
            "created_by": creator_username,
            "created_by_profile": _build_actor_snapshot(creator_profile, username=creator_username),
            "notes": notes.strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        record["maneuver_history"] = [
            _normalize_maneuver_record(
                {
                    "type": "entry",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": eta,
                    "completed_at": None,
                    "origin": record["last_port"],
                    "destination": record["berth"],
                    "plan_note": notes.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": record["created_by"],
                    "created_by_profile": record["created_by_profile"],
                    "created_at": record["created_at"],
                    "updated_at": record["updated_at"],
                },
                fallback_created_by=record["created_by"],
            )
        ]
        record = _sync_port_call_from_history(record)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO port_calls (
                        id, vessel_name, vessel_short_name, vessel_imo, vessel_call_sign, vessel_flag, vessel_type,
                        vessel_loa_m, vessel_beam_m, vessel_gt_t, vessel_max_draft_m, vessel_dwt_t,
                        vessel_bow_thruster, vessel_stern_thruster,
                        status, approval_status, approval_note, aborted_reason,
                        decided_by, decided_at, eta, ata, planned_departure_at, departure_plan_note, departure_at,
                        planned_shift_at, shift_plan_note, shift_at, shift_origin_berth, shift_destination_berth,
                        shift_approval_status, shift_approval_note, shift_aborted_reason, shift_decided_by, shift_decided_at,
                        maneuver_history, berth, last_port, next_port, created_by, notes, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        record["id"],
                        record["vessel_name"],
                        record["vessel_short_name"],
                        record["vessel_imo"],
                        record["vessel_call_sign"],
                        record["vessel_flag"],
                        record["vessel_type"],
                        record["vessel_loa_m"],
                        record["vessel_beam_m"],
                        record["vessel_gt_t"],
                        record["vessel_max_draft_m"],
                        record["vessel_dwt_t"],
                        record["vessel_bow_thruster"],
                        record["vessel_stern_thruster"],
                        record["status"],
                        record["approval_status"],
                        record["approval_note"],
                        record["aborted_reason"],
                        record["decided_by"],
                        record["decided_at"],
                        record["eta"],
                        record["ata"],
                        record["planned_departure_at"],
                        record["departure_plan_note"],
                        record["departure_at"],
                        record["planned_shift_at"],
                        record["shift_plan_note"],
                        record["shift_at"],
                        record["shift_origin_berth"],
                        record["shift_destination_berth"],
                        record["shift_approval_status"],
                        record["shift_approval_note"],
                        record["shift_aborted_reason"],
                        record["shift_decided_by"],
                        record["shift_decided_at"],
                        json.dumps(record["maneuver_history"]),
                        record["berth"],
                        record["last_port"],
                        record["next_port"],
                        record["created_by"],
                        record["notes"],
                        record["created_at"],
                        record["updated_at"],
                    ),
                )
            self._sync_maneuver_cases_for_port_call(conn, record, capture_live_environment=True)
            conn.commit()
        return self.get_port_call(record["id"])

    def approve_port_call(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            target = None
            if current["status"] == PORT_CALL_STATUS_SCHEDULED:
                target = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING})
            elif current["status"] == PORT_CALL_STATUS_IN_PORT:
                target = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING})
            else:
                raise ValueError("Só podes aprovar manobras ainda não executadas.")
            if not target:
                raise ValueError("Não existe manobra pendente para aprovar.")
            target["state"] = PORT_CALL_APPROVAL_APPROVED
            target["approval_note"] = approval_note.strip()
            target["aborted_reason"] = ""
            target["decided_by"] = actor_username
            target["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            target["decided_at"] = iso_now()
            target["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def edit_maneuver_plan(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        actor_role: str,
        planned_at: str,
        origin: str,
        destination: str,
        draft_m: str,
        tug_count: str,
        constraints: Optional[List[str]] = None,
        plan_note: str = "",
        change_reason: str,
    ) -> Dict:
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)

        def mutator(current: Dict) -> Dict:
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") == "completed" and (actor_role or "").strip().lower() != "admin":
                raise ValueError("A manobra concluída já só pode ser ajustada no registo.")
            if not _can_edit_maneuver_plan(target, actor_role):
                raise ValueError("Depois de validada, esta manobra só pode ser editada por piloto.")
            target["planned_at"] = planned_at
            target["origin"] = _clean_text(origin)
            target["destination"] = _clean_text(destination)
            target["planned_draft_m"] = (draft_m or "").strip()
            target["tug_count"] = (tug_count or "").strip()
            target["plan_observations"] = (plan_note or "").strip()
            target["constraints"] = normalize_constraint_codes(constraints)
            target["plan_note"] = (plan_note or "").strip()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary="Planeamento atualizado.",
            )
            if target.get("type") == "entry":
                current["last_port"] = target["origin"]
                current["berth"] = target["destination"]
            elif target.get("type") == "departure":
                current["next_port"] = target["destination"]
            elif target.get("type") == "shift":
                current["shift_origin_berth"] = target["origin"]
                current["shift_destination_berth"] = target["destination"]
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def edit_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        change_reason: str,
    ) -> Dict:
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )

        def mutator(current: Dict) -> Dict:
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada na escala.")
            if target.get("state") != "completed":
                raise ValueError("Só podes editar o registo de manobras já concluídas.")
            target["report_note"] = (notes or "").strip()
            target["execution_started_at"] = maneuver_started_at
            target["execution_finished_at"] = maneuver_finished_at
            target["reported_draft_m"] = draft_m.strip()
            target["reported_by"] = target.get("reported_by") or actor_username
            target["reported_by_profile"] = target.get("reported_by_profile") or _build_actor_snapshot(actor_profile, username=actor_username)
            target["reported_at"] = target.get("reported_at") or iso_now()
            target["updated_at"] = iso_now()
            _append_maneuver_change_log(
                target,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary=f"Registo revisto. Calado: {draft_m}.",
            )
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def delete_maneuver(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
    ) -> Dict:
        with self._connect() as conn:
            current = self._fetch_port_call_record(conn, port_call_id, for_update=True)
            if not current:
                raise ValueError("Escala não encontrada.")
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada.")
            if target.get("type") == "entry":
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM port_calls WHERE id = %s", (port_call_id,))
                conn.commit()
                return _decorate_port_call(current)
            current["maneuver_history"] = [m for m in current.get("maneuver_history", []) if m.get("id") != maneuver_id]
            current["updated_at"] = iso_now()
            saved = self._save_port_call_record(conn, current)
            conn.commit()
        return _decorate_port_call(saved)

    def delete_maneuver_report(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
    ) -> Dict:
        def mutator(current: Dict) -> Dict:
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada.")
            target["report_note"] = ""
            target["execution_started_at"] = None
            target["execution_finished_at"] = None
            target["reported_draft_m"] = ""
            target["reported_by"] = None
            target["reported_by_profile"] = _build_actor_snapshot(None)
            target["reported_at"] = None
            target["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def schedule_departure_plan(
        self,
        port_call_id: str,
        planned_departure_at: str,
        updated_by: str,
        next_port: str = "",
        constraints: Optional[List[str]] = None,
        departure_plan_note: str = "",
    ) -> Dict:
        if not planned_departure_at.strip():
            raise ValueError("A hora prevista de saída é obrigatória.")
        destination = " ".join(next_port.strip().split())
        if not destination:
            raise ValueError("Indica o próximo destino da saída.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear saída para navios que estão em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma saída ativa para esta escala.")
            departure = _normalize_maneuver_record(
                {
                    "type": "departure",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_departure_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": departure_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(departure)
            current["next_port"] = destination
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def abort_departure_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da saída é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Não existe manobra de saída planeada para este navio.")
            if (
                departure.get("state") == PORT_CALL_APPROVAL_PENDING
                and not _can_abort_departure_plan({"planned_departure_at": departure.get("planned_at")})
            ):
                raise ValueError("A saída só pode ser abortada com pelo menos 1 hora de antecedência.")
            departure["state"] = PORT_CALL_APPROVAL_ABORTED
            departure["aborted_reason"] = reason
            departure["decided_by"] = actor_username
            departure["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["decided_at"] = iso_now()
            departure["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def mark_port_call_arrived(
        self,
        port_call_id: str,
        arrived_at: str,
        updated_by: str,
        berth: str = "",
        notes: str = "",
    ) -> Dict:
        if not arrived_at.strip():
            raise ValueError("A hora real de chegada é obrigatória.")
        def mutator(current: Dict) -> Dict:
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes confirmar entrada de manobras previstas.")
            entry["state"] = "completed"
            entry["completed_at"] = arrived_at
            if berth.strip():
                entry["destination"] = " ".join(berth.strip().split())
                current["berth"] = " ".join(berth.strip().split())
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def mark_port_call_departed(
        self,
        port_call_id: str,
        departed_at: str,
        updated_by: str,
        next_port: str = "",
        notes: str = "",
    ) -> Dict:
        if not departed_at.strip():
            raise ValueError("A hora de saída é obrigatória.")
        def mutator(current: Dict) -> Dict:
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                raise ValueError("Só podes registar saída de navios que estão em porto e com manobra aprovada.")
            departure["state"] = "completed"
            departure["completed_at"] = departed_at
            if next_port.strip():
                destination = " ".join(next_port.strip().split())
                departure["destination"] = destination
                current["next_port"] = destination
            if notes.strip():
                existing = current.get("notes", "").strip()
                current["notes"] = f"{existing} | {notes.strip()}".strip(" |")
            departure["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def attach_entry_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            entry = (
                next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
                if maneuver_id else
                _latest_reportable_maneuver(current.get("maneuver_history", []), "entry")
            )
            if not entry:
                raise ValueError("Só podes registar a entrada depois da manobra estar concluída.")
            if maneuver_id and entry.get("type") != "entry":
                raise ValueError("O ID indicado não corresponde a uma manobra de entrada.")
            if maneuver_id and entry.get("state") not in {"approved", "completed"}:
                raise ValueError("Só podes registar a entrada depois da manobra estar concluída.")
            if maneuver_id and (entry.get("report_note") or "").strip():
                raise ValueError("Essa manobra já tem registo. Usa editar registo.")
            if entry.get("state") == "approved":
                entry["state"] = "completed"
                entry["completed_at"] = maneuver_finished_at
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            entry["report_note"] = note
            entry["execution_started_at"] = maneuver_started_at
            entry["execution_finished_at"] = maneuver_finished_at
            entry["reported_draft_m"] = draft_m.strip()
            entry["reported_by"] = actor_username
            entry["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["reported_at"] = iso_now()
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def attach_departure_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            departure = (
                next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
                if maneuver_id else
                _latest_reportable_maneuver(current.get("maneuver_history", []), "departure")
            )
            if not departure:
                raise ValueError("Só podes registar a saída depois da manobra estar aprovada.")
            if maneuver_id and departure.get("type") != "departure":
                raise ValueError("O ID indicado não corresponde a uma manobra de saída.")
            if maneuver_id and departure.get("state") not in {"approved", "completed"}:
                raise ValueError("Só podes registar a saída depois da manobra estar aprovada.")
            if maneuver_id and (departure.get("report_note") or "").strip():
                raise ValueError("Essa manobra já tem registo. Usa editar registo.")
            if departure.get("state") == "approved":
                departure["state"] = "completed"
                departure["completed_at"] = maneuver_finished_at
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            departure["report_note"] = note
            departure["execution_started_at"] = maneuver_started_at
            departure["execution_finished_at"] = maneuver_finished_at
            departure["reported_draft_m"] = draft_m.strip()
            departure["reported_by"] = actor_username
            departure["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            departure["reported_at"] = iso_now()
            departure["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def schedule_shift_plan(
        self,
        port_call_id: str,
        planned_shift_at: str,
        updated_by: str,
        destination_berth: str,
        constraints: Optional[List[str]] = None,
        shift_plan_note: str = "",
    ) -> Dict:
        if not planned_shift_at.strip():
            raise ValueError("A hora prevista da mudança é obrigatória.")
        destination = " ".join(destination_berth.strip().split())
        if not destination:
            raise ValueError("Indica o cais de destino da mudança.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            if current["status"] != PORT_CALL_STATUS_IN_PORT:
                raise ValueError("Só podes planear mudança para navios em porto.")
            if _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma mudança ativa para esta escala.")
            shift = _normalize_maneuver_record(
                {
                    "type": "shift",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_shift_at,
                    "completed_at": None,
                    "origin": current.get("berth", ""),
                    "destination": destination,
                    "plan_note": shift_plan_note.strip(),
                    "approval_note": "",
                    "aborted_reason": "",
                    "constraints": constraints or [],
                    "decided_by": None,
                    "decided_at": None,
                    "report_note": "",
                    "created_by": actor_username or current.get("created_by", "system"),
                    "created_by_profile": _build_actor_snapshot(actor_profile, username=actor_username),
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                },
                fallback_created_by=actor_username or current.get("created_by", "system"),
            )
            current["maneuver_history"].append(shift)
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def approve_shift_plan(self, port_call_id: str, decided_by: str, approval_note: str = "") -> Dict:
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Só podes aprovar mudanças ainda não executadas.")
            shift["state"] = PORT_CALL_APPROVAL_APPROVED
            shift["approval_note"] = approval_note.strip()
            shift["aborted_reason"] = ""
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def abort_shift_plan(
        self,
        port_call_id: str,
        updated_by: str,
        aborted_reason: str,
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de aborto da mudança é obrigatório.")
        actor_username = _normalize_username(updated_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("Não existe manobra de mudança planeada para este navio.")
            if (
                shift.get("state") == PORT_CALL_APPROVAL_PENDING
                and not _can_abort_shift_plan({"planned_shift_at": shift.get("planned_at")})
            ):
                raise ValueError("A mudança só pode ser abortada com pelo menos 1 hora de antecedência.")
            shift["state"] = PORT_CALL_APPROVAL_ABORTED
            shift["aborted_reason"] = reason
            shift["decided_by"] = actor_username
            shift["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["decided_at"] = iso_now()
            shift["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def mark_shift_completed(
        self,
        port_call_id: str,
        shifted_at: str,
        updated_by: str,
    ) -> Dict:
        if not shifted_at.strip():
            raise ValueError("A hora real da mudança é obrigatória.")
        def mutator(current: Dict) -> Dict:
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                raise ValueError("A mudança tem de estar aprovada antes de ser concluída.")
            shift["state"] = "completed"
            shift["completed_at"] = shifted_at
            shift["updated_at"] = iso_now()
            if shift.get("destination"):
                current["berth"] = shift["destination"]
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def attach_shift_report(
        self,
        port_call_id: str,
        updated_by: str,
        maneuver_started_at: str,
        maneuver_finished_at: str,
        draft_m: str,
        notes: str,
        maneuver_id: Optional[str] = None,
    ) -> Dict:
        note = notes.strip()
        if not maneuver_started_at.strip() or not maneuver_finished_at.strip():
            raise ValueError("O início e o fim da manobra são obrigatórios.")
        if not draft_m.strip():
            raise ValueError("O calado da manobra é obrigatório.")
        validate_datetime_range(
            maneuver_started_at,
            maneuver_finished_at,
            started_label="Início da manobra",
            finished_label="Fim da manobra",
        )
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            shift = (
                next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
                if maneuver_id else
                _latest_reportable_maneuver(current.get("maneuver_history", []), "shift")
            )
            if not shift:
                raise ValueError("Só podes registar a mudança depois da manobra estar aprovada.")
            if maneuver_id and shift.get("type") != "shift":
                raise ValueError("O ID indicado não corresponde a uma manobra de mudança.")
            if maneuver_id and shift.get("state") not in {"approved", "completed"}:
                raise ValueError("Só podes registar a mudança depois da manobra estar aprovada.")
            if maneuver_id and (shift.get("report_note") or "").strip():
                raise ValueError("Essa manobra já tem registo. Usa editar registo.")
            if shift.get("state") == "approved":
                shift["state"] = "completed"
                shift["completed_at"] = maneuver_finished_at
            existing = current.get("notes", "").strip()
            current["notes"] = f"{existing}\n\n{note}".strip()
            shift["report_note"] = note
            shift["execution_started_at"] = maneuver_started_at
            shift["execution_finished_at"] = maneuver_finished_at
            shift["reported_draft_m"] = draft_m.strip()
            shift["reported_by"] = actor_username
            shift["reported_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            shift["reported_at"] = iso_now()
            shift["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def abort_port_call(
        self,
        port_call_id: str,
        decided_by: str,
        aborted_reason: str,
        approval_note: str = "",
    ) -> Dict:
        reason = aborted_reason.strip()
        if not reason:
            raise ValueError("O motivo de manobra abortada é obrigatório.")
        actor_username = _normalize_username(decided_by) or "agente"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                raise ValueError("Só podes abortar manobras ainda não executadas.")
            if (
                entry.get("state") == PORT_CALL_APPROVAL_PENDING
                and not _can_abort_port_call({"eta": entry.get("planned_at")})
            ):
                raise ValueError("A manobra só pode ser abortada com pelo menos 2 horas de antecedência.")
            entry["state"] = PORT_CALL_APPROVAL_ABORTED
            entry["approval_note"] = approval_note.strip()
            entry["aborted_reason"] = reason
            entry["decided_by"] = actor_username
            entry["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["decided_at"] = iso_now()
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)


def create_store(data_dir: str, knowledge_dir: str) -> BaseStore:
    backend = os.getenv("APP_STORAGE_BACKEND", "local").strip().lower()
    if backend == "postgres":
        database_url = os.getenv("DATABASE_URL", "").strip()
        if not database_url:
            raise RuntimeError("Define DATABASE_URL para usar APP_STORAGE_BACKEND=postgres.")
        return PostgresStore(database_url=database_url, knowledge_dir=knowledge_dir)
    from .local import LocalStore
    return LocalStore(data_dir=data_dir, knowledge_dir=knowledge_dir)
