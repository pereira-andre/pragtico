"""Port call and maneuver write operations for the PostgreSQL store."""

from __future__ import annotations

import json
import uuid
from typing import Dict, List, Optional

from core.validators import normalize_thruster_state, validate_datetime_range
from domain.document_processing import iso_now

from .constants import (
    PORT_CALL_APPROVAL_ABORTED,
    PORT_CALL_APPROVAL_APPROVED,
    PORT_CALL_APPROVAL_PENDING,
    PORT_CALL_STATUS_IN_PORT,
    PORT_CALL_STATUS_SCHEDULED,
)
from .port_call_helpers import (
    _append_maneuver_change_log,
    _append_scale_change_log,
    _can_edit_maneuver_plan,
    _decorate_port_call,
    _latest_maneuver,
    _latest_reportable_maneuver,
    _normalize_maneuver_record,
    _normalize_port_call_record,
    _remove_embedded_report_note,
    _replace_embedded_report_note,
    _sync_port_call_from_history,
    can_plan_followup_maneuver_status,
)
from .utils import (
    _build_actor_snapshot,
    _clean_text,
    _normalize_username,
    _validate_required_operational_profile,
    _validate_required_vessel_profile,
    normalize_constraint_codes,
)


def _find_active_duplicate_port_call(
    records: List[Dict],
    *,
    clean_imo: str = "",
    clean_call_sign: str = "",
) -> tuple[str, Dict] | None:
    """Find a duplicate whose normalized port-call state is still active."""
    active_statuses = {PORT_CALL_STATUS_SCHEDULED, PORT_CALL_STATUS_IN_PORT}
    for record in records:
        normalized = _normalize_port_call_record(record)
        if normalized.get("status") not in active_statuses:
            continue
        if clean_imo and _clean_text(str(normalized.get("vessel_imo", ""))) == clean_imo:
            return "imo", normalized
        if clean_call_sign and _clean_text(str(normalized.get("vessel_call_sign", ""))) == clean_call_sign:
            return "call_sign", normalized
    return None


class PostgresPortCallMixin:
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
        change_reason: str = "",
    ) -> Dict:
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
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
            _append_scale_change_log(
                current,
                actor_username=actor_username,
                actor_profile=actor_profile,
                reason=change_reason,
                summary="Escala e ficha do navio atualizadas.",
            )
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

        # --- Verificar duplicados em escalas realmente ativas (scheduled / in_port normalizado) ---
        clean_imo = _clean_text(vessel_imo)
        clean_cs = _clean_text(vessel_call_sign)
        if clean_imo or clean_cs:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    conditions = []
                    params: list = []
                    if clean_imo:
                        conditions.append("vessel_imo = %s")
                        params.append(clean_imo)
                    if clean_cs:
                        conditions.append("vessel_call_sign = %s")
                        params.append(clean_cs)
                    where = " OR ".join(conditions)
                    cur.execute(
                        f"{self._port_call_select_clause()} WHERE {where}",
                        params,
                    )
                    candidates = [
                        payload
                        for payload in (self._row_to_port_call_record(row) for row in cur.fetchall())
                        if payload
                    ]
                    duplicate = _find_active_duplicate_port_call(
                        candidates,
                        clean_imo=clean_imo,
                        clean_call_sign=clean_cs,
                    )
                    if duplicate:
                        duplicate_type, dup = duplicate
                        dup_name = dup.get("vessel_name", "")
                        if duplicate_type == "imo":
                            raise ValueError(f"Já existe uma escala ativa com o IMO {clean_imo} ({dup_name}).")
                        raise ValueError(f"Já existe uma escala ativa com o indicativo {clean_cs} ({dup_name}).")

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
            "change_log": [],
            "notes": notes.strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        _append_scale_change_log(
            record,
            actor_username=creator_username,
            actor_profile=creator_profile,
            reason="Registo inicial da escala",
            summary="Escala criada e entrada inicial marcada.",
            require_reason=False,
        )
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
                        maneuver_history, berth, last_port, next_port, created_by, change_log, notes, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
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
                        json.dumps(record["change_log"]),
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
            if target.get("state") not in {"completed", PORT_CALL_APPROVAL_ABORTED}:
                raise ValueError("Só podes editar o registo de manobras concluídas ou abortadas.")
            previous_note = target.get("report_note", "")
            target["report_note"] = (notes or "").strip()
            target["execution_started_at"] = maneuver_started_at
            target["execution_finished_at"] = maneuver_finished_at
            if target.get("state") == "completed":
                target["completed_at"] = maneuver_finished_at
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
            current["notes"] = _replace_embedded_report_note(
                current.get("notes", ""),
                previous_note,
                target["report_note"],
            )
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def delete_maneuver(
        self,
        port_call_id: str,
        maneuver_id: str,
        *,
        updated_by: str,
        force: bool = False,
    ) -> Dict:
        with self._connect() as conn:
            current = self._fetch_port_call_record(conn, port_call_id, for_update=True)
            if not current:
                raise ValueError("Escala não encontrada.")
            target = next((m for m in current.get("maneuver_history", []) if m.get("id") == maneuver_id), None)
            if not target:
                raise ValueError("Manobra não encontrada.")
            if not force and target.get("state") != PORT_CALL_APPROVAL_PENDING:
                raise ValueError("Só podes cancelar manobras pendentes. Depois da aprovação usa abortar.")
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
            previous_note = target.get("report_note", "")
            target["report_note"] = ""
            target["execution_started_at"] = None
            target["execution_finished_at"] = None
            target["reported_draft_m"] = ""
            target["reported_by"] = None
            target["reported_by_profile"] = _build_actor_snapshot(None)
            target["reported_at"] = None
            target["updated_at"] = iso_now()
            current["notes"] = _remove_embedded_report_note(current.get("notes", ""), previous_note)
            return current

        return self._mutate_port_call(port_call_id, mutator)

    def schedule_entry_plan(
        self,
        port_call_id: str,
        planned_entry_at: str,
        updated_by: str,
        origin_port: str = "",
        destination_berth: str = "",
        constraints: Optional[List[str]] = None,
        entry_plan_note: str = "",
        draft_m: str = "",
        tug_count: str = "",
    ) -> Dict:
        if not planned_entry_at.strip():
            raise ValueError("A hora prevista de entrada é obrigatória.")
        origin = " ".join(origin_port.strip().split())
        destination = " ".join(destination_berth.strip().split())
        if not origin:
            raise ValueError("Indica o porto anterior/origem da entrada.")
        if not destination:
            raise ValueError("Indica o cais previsto da entrada.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)

        def mutator(current: Dict) -> Dict:
            if current["status"] != PORT_CALL_STATUS_SCHEDULED:
                raise ValueError("Só podes criar nova entrada enquanto a escala ainda está prevista.")
            if _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING, PORT_CALL_APPROVAL_APPROVED}):
                raise ValueError("Já existe uma entrada ativa para esta escala.")
            entry = _normalize_maneuver_record(
                {
                    "type": "entry",
                    "state": PORT_CALL_APPROVAL_PENDING,
                    "planned_at": planned_entry_at,
                    "completed_at": None,
                    "origin": origin,
                    "destination": destination,
                    "planned_draft_m": (draft_m or "").strip(),
                    "tug_count": (tug_count or "").strip(),
                    "plan_note": entry_plan_note.strip(),
                    "plan_observations": entry_plan_note.strip(),
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
            current["maneuver_history"].append(entry)
            current["eta"] = planned_entry_at
            current["last_port"] = origin
            current["berth"] = destination
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
        draft_m: str = "",
        tug_count: str = "",
    ) -> Dict:
        if not planned_departure_at.strip():
            raise ValueError("A hora prevista de saída é obrigatória.")
        destination = " ".join(next_port.strip().split())
        if not destination:
            raise ValueError("Indica o próximo destino da saída.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            if not can_plan_followup_maneuver_status(current.get("status")):
                raise ValueError("Só podes planear saída para escalas previstas ou navios em porto.")
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
                    "planned_draft_m": (draft_m or "").strip(),
                    "tug_count": (tug_count or "").strip(),
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
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            departure = _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not departure:
                if _latest_maneuver(current.get("maneuver_history", []), "departure", {PORT_CALL_APPROVAL_PENDING}):
                    raise ValueError("A saída ainda está pendente. Cancela a marcação antes da aprovação; aborto só depois de aprovada.")
                raise ValueError("Não existe saída aprovada para abortar.")
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
            if maneuver_id and entry.get("state") not in {"approved", "completed", "aborted"}:
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
            if maneuver_id and departure.get("state") not in {"approved", "completed", "aborted"}:
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
        draft_m: str = "",
        tug_count: str = "",
    ) -> Dict:
        if not planned_shift_at.strip():
            raise ValueError("A hora prevista da mudança é obrigatória.")
        destination = " ".join(destination_berth.strip().split())
        if not destination:
            raise ValueError("Indica o cais de destino da mudança.")
        actor_username = _normalize_username(updated_by) or "system"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            if not can_plan_followup_maneuver_status(current.get("status")):
                raise ValueError("Só podes planear mudança para escalas previstas ou navios em porto.")
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
                    "planned_draft_m": (draft_m or "").strip(),
                    "tug_count": (tug_count or "").strip(),
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
        actor_username = _normalize_username(updated_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            shift = _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_IN_PORT or not shift:
                if _latest_maneuver(current.get("maneuver_history", []), "shift", {PORT_CALL_APPROVAL_PENDING}):
                    raise ValueError("A mudança ainda está pendente. Cancela a marcação antes da aprovação; aborto só depois de aprovada.")
                raise ValueError("Não existe mudança aprovada para abortar.")
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
            if maneuver_id and shift.get("state") not in {"approved", "completed", "aborted"}:
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
        actor_username = _normalize_username(decided_by) or "piloto"
        actor_profile = self.get_user_profile(actor_username)
        def mutator(current: Dict) -> Dict:
            entry = _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_APPROVED})
            if current["status"] != PORT_CALL_STATUS_SCHEDULED or not entry:
                if _latest_maneuver(current.get("maneuver_history", []), "entry", {PORT_CALL_APPROVAL_PENDING}):
                    raise ValueError("A entrada ainda está pendente. Cancela a marcação antes da aprovação; aborto só depois de aprovada.")
                raise ValueError("Não existe entrada aprovada para abortar.")
            entry["state"] = PORT_CALL_APPROVAL_ABORTED
            entry["approval_note"] = approval_note.strip()
            entry["aborted_reason"] = reason
            entry["decided_by"] = actor_username
            entry["decided_by_profile"] = _build_actor_snapshot(actor_profile, username=actor_username)
            entry["decided_at"] = iso_now()
            entry["updated_at"] = iso_now()
            return current

        return self._mutate_port_call(port_call_id, mutator)
