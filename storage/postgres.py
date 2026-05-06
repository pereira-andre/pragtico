"""PostgreSQL storage backend."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, List, Optional

from domain.document_processing import (
    build_preview,
    extract_text_from_path,
    file_metadata,
    infer_document_type,
    is_allowed_document,
    iso_now,
    read_text_file,
)
from .base import BaseStore
from .maneuver_case_helpers import (
    _capture_live_environment_sources,
    build_maneuver_case,
)
from .constants import (
    PORT_CALL_APPROVAL_PENDING,
)
from .port_call_helpers import (
    _decorate_port_call,
    _default_port_calls,
    _normalize_port_call_record,
    _sync_port_call_from_history,
)
from .postgres_chat import PostgresChatMixin
from .postgres_documents import PostgresDocumentMixin
from .postgres_feedback import PostgresFeedbackMixin
from .postgres_maneuver_cases import PostgresManeuverCaseMixin
from .postgres_port_calls import PostgresPortCallMixin
from .postgres_users import PostgresUserMixin
from .utils import (
    _utc_iso_to_label,
    is_legacy_system_markdown_document,
)

logger = logging.getLogger(__name__)

class PostgresStore(
    PostgresPortCallMixin,
    PostgresManeuverCaseMixin,
    PostgresFeedbackMixin,
    PostgresChatMixin,
    PostgresDocumentMixin,
    PostgresUserMixin,
    BaseStore,
):
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
                change_log,
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
            "change_log": row.get("change_log") or [],
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
            "feedback_correction": row.get("feedback_correction") or "",
            "feedback_correction_document": row.get("feedback_correction_document") or "",
            "feedback_error_type": row.get("feedback_error_type") or "",
            "feedback_scope": row.get("feedback_scope") or "",
            "feedback_destination": row.get("feedback_destination") or "",
            "feedback_criticality": row.get("feedback_criticality") or "",
            "feedback_updated_by": row.get("feedback_updated_by") or "",
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

    def _row_to_feedback_eval_case_record(self, row: Optional[Dict]) -> Optional[Dict]:
        if not row:
            return None
        return {
            **row,
            "expected_substrings": list(row.get("expected_substrings") or []),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
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
                    change_log = %s::jsonb,
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
                    change_log,
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
                    json.dumps(payload.get("change_log", [])),
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



def create_store(data_dir: str, knowledge_dir: str) -> BaseStore:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Define DATABASE_URL para arrancar a aplicação em Railway.")
    return PostgresStore(database_url=database_url, knowledge_dir=knowledge_dir)
