"""Maneuver case queries for the PostgreSQL store."""

from __future__ import annotations

from typing import Dict, List, Optional

from core.validators import validate_operational_feedback_status
from domain.practice_experience import PRACTICE_EXPERIENCE_ACTIVE_STATUSES, list_practice_experience_records

from .maneuver_case_helpers import decorate_maneuver_case, rank_similar_maneuver_cases
from .port_call_helpers import _build_port_activity_snapshot


class PostgresManeuverCaseMixin:
    def get_port_activity_snapshot(self, window_days: int = 5) -> Dict:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"{self._port_call_select_clause()} "
                    "ORDER BY COALESCE(eta, ata, departure_at) ASC NULLS LAST, vessel_name"
                )
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
        environment_signature: Optional[Dict] = None,
        strict_route: bool = True,
        limit: int = 5,
    ) -> List[Dict]:
        with self._connect() as conn:
            rows = self._list_raw_maneuver_cases(conn, limit=500, maneuver_type=maneuver_type)
        rows.extend(
            list_practice_experience_records(
                self,
                feedback_statuses=PRACTICE_EXPERIENCE_ACTIVE_STATUSES,
            )
        )
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
            environment_signature=environment_signature,
            strict_route=strict_route,
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
