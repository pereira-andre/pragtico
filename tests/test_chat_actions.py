import unittest

from chat_actions import (
    action_for_maneuver_type,
    canonicalize_action_name,
    extract_pending_field_updates,
    extract_json_object,
    infer_maneuver_type,
    looks_like_operational_command,
    merge_action_candidate,
    normalize_action_candidate,
    normalize_action_fields,
    resolve_maneuver,
    resolve_port_call,
    visible_port_calls_from_activity,
)


class ChatActionsTests(unittest.TestCase):
    def test_operational_command_detection_separates_queries_from_actions(self) -> None:
        self.assertTrue(looks_like_operational_command("Aprova a entrada da escala REF-001."))
        self.assertTrue(looks_like_operational_command("Regista a saída do navio hoje às 18:00."))
        self.assertTrue(
            looks_like_operational_command("mete a manobra do BELITAKI a prevista, mas para as 10:00 do mesmo dia")
        )
        self.assertFalse(looks_like_operational_command("Qual é a regra de reboques no cais?"))
        self.assertFalse(looks_like_operational_command("Mostra o arquivo de manobras do mês passado."))

    def test_extract_json_object_accepts_fenced_or_plain_json(self) -> None:
        plain = extract_json_object('{"intent":"action","action":"approve_entry"}')
        fenced = extract_json_object('```json\n{"intent":"question","action":""}\n```')

        self.assertEqual(plain["action"], "approve_entry")
        self.assertEqual(fenced["intent"], "question")

    def test_normalize_action_candidate_rejects_action_outside_role(self) -> None:
        candidate = normalize_action_candidate(
            {
                "intent": "action",
                "action": "approve_entry",
                "target": {"reference_code": "REF-001", "maneuver_type": "entry"},
                "fields": {},
                "missing_fields": [],
            },
            "agente",
        )

        self.assertEqual(candidate["intent"], "unsupported")

    def test_canonicalize_action_name_maps_generic_approve_alias(self) -> None:
        self.assertEqual(canonicalize_action_name("approve_maneuver"), "approve_entry")
        self.assertEqual(canonicalize_action_name("approve_port_call", "shift"), "approve_shift")

    def test_normalize_action_candidate_accepts_generic_approve_for_admin(self) -> None:
        candidate = normalize_action_candidate(
            {
                "intent": "action",
                "action": "approve_maneuver",
                "target": {"vessel_name": "BELITAKI"},
                "fields": {},
                "missing_fields": [],
            },
            "admin",
        )

        self.assertEqual(candidate["intent"], "action")
        self.assertEqual(candidate["action"], "approve_entry")

    def test_normalize_action_fields_maps_llm_aliases_for_create_port_call(self) -> None:
        fields = normalize_action_fields(
            "create_port_call",
            {
                "imo": 9152923,
                "call_sign": "D5OC2",
                "flag": "Libéria",
                "loa": 179.23,
                "breadth": 25.3,
                "gt": 16281,
                "dwt": 22330,
                "draft": 9.94,
                "eta": "2026-03-24T05:30",
                "planned_quay": "TMS 2",
                "last_port_of_call": "Leixões",
                "next_port_of_call": "Barcelona",
                "operational_note": "Sem nota",
            },
        )

        self.assertEqual(fields["vessel_imo"], "9152923")
        self.assertEqual(fields["vessel_call_sign"], "D5OC2")
        self.assertEqual(fields["vessel_flag"], "Libéria")
        self.assertEqual(fields["vessel_loa_m"], "179.23")
        self.assertEqual(fields["vessel_beam_m"], "25.3")
        self.assertEqual(fields["vessel_gt_t"], "16281")
        self.assertEqual(fields["vessel_dwt_t"], "22330")
        self.assertEqual(fields["vessel_max_draft_m"], "9.94")
        self.assertEqual(fields["draft_m"], "9.94")
        self.assertEqual(fields["eta_local"], "2026-03-24T05:30")
        self.assertEqual(fields["berth"], "TMS 2")
        self.assertEqual(fields["last_port"], "Leixões")
        self.assertEqual(fields["next_port"], "Barcelona")
        self.assertEqual(fields["notes"], "Sem nota")

    def test_normalize_action_candidate_recomputes_required_missing_fields(self) -> None:
        candidate = normalize_action_candidate(
            {
                "intent": "action",
                "action": "create_port_call",
                "target": {},
                "fields": {
                    "vessel_name": "BELITAKI",
                    "imo": "9152923",
                    "call_sign": "D5OC2",
                    "flag": "Libéria",
                    "vessel_type": "Porta-contentores",
                    "loa": "179.23",
                    "breadth": "25.3",
                    "gt": "16281",
                    "dwt": "22330",
                    "draft": "9.94",
                    "eta": "2026-03-24T05:30",
                    "berth": "TMS 2",
                    "last_port_of_call": "Leixões",
                    "next_port_of_call": "Barcelona",
                },
                "missing_fields": ["operational_note"],
            },
            "agente",
        )

        self.assertEqual(candidate["intent"], "action")
        self.assertEqual(candidate["missing_fields"], [])

    def test_normalize_action_candidate_accepts_nested_grouped_create_payload(self) -> None:
        candidate = normalize_action_candidate(
            {
                "intent": "action",
                "action": "create_port_call",
                "target": {"vessel_name": "BELITAKI"},
                "fields": {
                    "port_call_data": {
                        "imo": "9152923",
                        "call_sign": "D5OC2",
                        "flag": "Libéria",
                        "vessel_type": "Porta-contentores",
                        "vessel_loa": 179.23,
                        "vessel_beam": 25.3,
                        "vessel_gt": 16281,
                        "vessel_dwt": 22330,
                        "vessel_draft": 9.94,
                    },
                    "entry_data": {
                        "eta": "2026-03-24T05:30",
                        "planned_quay": "TMS 2",
                        "port_of_origin": "Leixões",
                        "port_of_destination": "Barcelona",
                        "constraints": [],
                    },
                    "operational_notes": "cais previsto: TMS 2",
                },
                "missing_fields": [],
            },
            "agente",
        )

        self.assertEqual(candidate["intent"], "action")
        self.assertEqual(candidate["fields"]["vessel_name"], "BELITAKI")
        self.assertEqual(candidate["fields"]["vessel_imo"], "9152923")
        self.assertEqual(candidate["fields"]["vessel_call_sign"], "D5OC2")
        self.assertEqual(candidate["fields"]["vessel_beam_m"], "25.3")
        self.assertEqual(candidate["fields"]["vessel_max_draft_m"], "9.94")
        self.assertEqual(candidate["fields"]["eta_local"], "2026-03-24T05:30")
        self.assertEqual(candidate["fields"]["berth"], "TMS 2")
        self.assertEqual(candidate["fields"]["next_port"], "Barcelona")
        self.assertEqual(candidate["missing_fields"], [])

    def test_normalize_action_candidate_validates_schedule_departure_fields(self) -> None:
        candidate = normalize_action_candidate(
            {
                "intent": "action",
                "action": "schedule_departure",
                "target": {"reference_code": "SET-002", "maneuver_type": "departure"},
                "fields": {
                    "etd": "2026-03-24T18:30",
                    "next_port_of_call": "Barcelona",
                },
                "missing_fields": [],
            },
            "agente",
        )

        self.assertEqual(candidate["intent"], "action")
        self.assertEqual(candidate["fields"]["planned_departure_at_local"], "2026-03-24T18:30")
        self.assertEqual(candidate["fields"]["next_port"], "Barcelona")
        self.assertEqual(candidate["missing_fields"], [])

    def test_normalize_action_candidate_reports_missing_fields_for_reports(self) -> None:
        candidate = normalize_action_candidate(
            {
                "intent": "action",
                "action": "entry_report",
                "target": {"reference_code": "SET-003", "maneuver_type": "entry"},
                "fields": {
                    "start_time": "2026-03-24T05:35",
                },
                "missing_fields": [],
            },
            "piloto",
        )

        self.assertEqual(candidate["intent"], "action")
        self.assertCountEqual(
            candidate["missing_fields"],
            ["fim da manobra", "calado"],
        )

    def test_merge_action_candidate_completes_pending_proposal(self) -> None:
        existing = normalize_action_candidate(
            {
                "intent": "action",
                "action": "schedule_departure",
                "target": {"reference_code": "SET-002", "maneuver_type": "departure"},
                "fields": {
                    "next_port_of_call": "Barcelona",
                },
                "missing_fields": [],
            },
            "agente",
        )
        updates = normalize_action_candidate(
            {
                "intent": "action",
                "action": "schedule_departure",
                "target": {},
                "fields": {
                    "etd": "2026-03-24T18:30",
                },
                "missing_fields": [],
            },
            "agente",
        )

        merged = merge_action_candidate(existing, updates, "agente")

        self.assertEqual(merged["action"], "schedule_departure")
        self.assertEqual(merged["fields"]["next_port"], "Barcelona")
        self.assertEqual(merged["fields"]["planned_departure_at_local"], "2026-03-24T18:30")
        self.assertEqual(merged["missing_fields"], [])

    def test_merge_action_candidate_promotes_top_level_reason_to_change_reason(self) -> None:
        existing = normalize_action_candidate(
            {
                "intent": "action",
                "action": "edit_maneuver_plan",
                "target": {"reference_code": "SET-002", "maneuver_type": "entry"},
                "fields": {
                    "planned_at_local": "2026-03-24T18:30",
                },
                "missing_fields": [],
            },
            "admin",
        )
        updates = {
            "intent": "action",
            "action": "edit_maneuver_plan",
            "target": {},
            "fields": {},
            "reason": "piloto disponível",
            "confidence": 0.9,
        }

        merged = merge_action_candidate(existing, updates, "admin")

        self.assertEqual(merged["fields"]["change_reason"], "piloto disponível")
        self.assertEqual(merged["missing_fields"], [])

    def test_extract_pending_field_updates_handles_time_only_for_existing_plan(self) -> None:
        proposal = {
            "action": "edit_maneuver_plan",
            "fields": {
                "planned_at_local": "2026-03-24T05:30",
            },
        }

        updates = extract_pending_field_updates(
            "motivo da alteração: disponibilidade piloto, planned_at_local= 10:00.",
            proposal,
        )

        self.assertEqual(updates["change_reason"], "disponibilidade piloto")
        self.assertEqual(updates["planned_at_local"], "2026-03-24T10:00")

    def test_extract_pending_field_updates_accepts_reason_with_verb(self) -> None:
        proposal = {
            "action": "edit_maneuver_plan",
            "fields": {
                "planned_at_local": "2026-03-24T05:30",
            },
        }

        updates = extract_pending_field_updates(
            "o motivo da alteração é piloto disponível",
            proposal,
        )

        self.assertEqual(updates["change_reason"], "piloto disponivel")

    def test_normalize_action_fields_maps_reason_and_planned_datetime_for_edit_plan(self) -> None:
        fields = normalize_action_fields(
            "edit_maneuver_plan",
            {
                "planned_datetime": "2026-03-24T10:00",
                "reason": "so temos piloto a essa hora",
            },
        )

        self.assertEqual(fields["planned_at_local"], "2026-03-24T10:00")
        self.assertEqual(fields["change_reason"], "so temos piloto a essa hora")

    def test_normalize_action_fields_maps_eta_alias_for_edit_plan(self) -> None:
        fields = normalize_action_fields(
            "edit_maneuver_plan",
            {
                "estimated_time_of_arrival": "2026-03-24T10:00",
                "reason": "disponibilidade piloto",
            },
        )

        self.assertEqual(fields["eta_local"], "2026-03-24T10:00")
        self.assertEqual(fields["planned_at_local"], "2026-03-24T10:00")
        self.assertEqual(fields["change_reason"], "disponibilidade piloto")

    def test_normalize_action_fields_maps_pier_alias_to_berth(self) -> None:
        fields = normalize_action_fields(
            "edit_maneuver_plan",
            {
                "pier": "Tanquisado (lado jusante)",
                "reason": "piloto disponivel",
            },
        )

        self.assertEqual(fields["berth"], "Tanquisado (lado jusante)")
        self.assertEqual(fields["change_reason"], "piloto disponivel")

    def test_resolve_port_call_matches_reference_and_vessel_name(self) -> None:
        port_calls = [
            {"id": "1", "reference_code": "SET-001", "vessel_name": "MSC Lyria"},
            {"id": "2", "reference_code": "SET-002", "vessel_name": "Atlantic Trader"},
        ]

        by_reference = resolve_port_call(port_calls, {"reference_code": "SET-001"})
        by_name = resolve_port_call(port_calls, {"vessel_name": "atlantic trader"})

        self.assertEqual(by_reference["id"], "1")
        self.assertEqual(by_name["id"], "2")

    def test_resolve_maneuver_prefers_latest_matching_state(self) -> None:
        port_call = {
            "maneuver_history": [
                {
                    "id": "m1",
                    "type": "departure",
                    "state": "pending",
                    "planned_at": "2026-03-23T10:00:00+00:00",
                    "updated_at": "2026-03-23T09:00:00+00:00",
                    "created_at": "2026-03-23T08:00:00+00:00",
                },
                {
                    "id": "m2",
                    "type": "departure",
                    "state": "approved",
                    "planned_at": "2026-03-23T12:00:00+00:00",
                    "updated_at": "2026-03-23T11:00:00+00:00",
                    "created_at": "2026-03-23T10:00:00+00:00",
                },
                {
                    "id": "m3",
                    "type": "departure",
                    "state": "completed",
                    "planned_at": "2026-03-23T07:00:00+00:00",
                    "completed_at": "2026-03-23T08:00:00+00:00",
                    "updated_at": "2026-03-23T08:10:00+00:00",
                    "created_at": "2026-03-23T06:00:00+00:00",
                },
            ]
        }

        edit_plan = resolve_maneuver(port_call, "edit_maneuver_plan", "departure")
        edit_report = resolve_maneuver(port_call, "edit_maneuver_report", "departure")

        self.assertEqual(edit_plan["id"], "m2")
        self.assertEqual(edit_report["id"], "m3")

    def test_visible_port_calls_deduplicates_activity_rows(self) -> None:
        activity = {
            "arrivals": [{"id": "1", "reference_code": "SET-001", "vessel_name": "MSC Lyria"}],
            "departure_candidates": [{"id": "1", "reference_code": "SET-001", "vessel_name": "MSC Lyria"}],
            "in_port": [{"id": "2", "reference_code": "SET-002", "vessel_name": "Atlantic Trader"}],
        }

        rows = visible_port_calls_from_activity(activity)

        self.assertEqual(len(rows), 2)
        self.assertCountEqual([item["id"] for item in rows], ["1", "2"])

    def test_visible_port_calls_includes_rows_only_present_in_planned_maneuvers(self) -> None:
        activity = {
            "planned_maneuvers": [
                {
                    "id": "entry-plan-1",
                    "port_call_id": "1",
                    "reference_code": "SET-001",
                    "vessel_name": "BELITAKI",
                    "planned_value": "2026-03-24T10:00:00+00:00",
                }
            ]
        }

        rows = visible_port_calls_from_activity(activity)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "1")
        self.assertEqual(rows[0]["vessel_name"], "BELITAKI")

    def test_infer_maneuver_type_returns_unique_matching_type(self) -> None:
        port_call = {
            "maneuver_history": [
                {
                    "id": "m1",
                    "type": "entry",
                    "state": "pending",
                    "planned_at": "2026-03-24T10:00:00+00:00",
                    "updated_at": "2026-03-24T09:00:00+00:00",
                    "created_at": "2026-03-24T08:00:00+00:00",
                }
            ]
        }

        maneuver_type = infer_maneuver_type(port_call, "edit_maneuver_plan")

        self.assertEqual(maneuver_type, "entry")

    def test_infer_maneuver_type_works_for_approval_actions(self) -> None:
        port_call = {
            "maneuver_history": [
                {
                    "id": "m1",
                    "type": "entry",
                    "state": "pending",
                    "planned_at": "2026-03-24T10:00:00+00:00",
                    "updated_at": "2026-03-24T09:00:00+00:00",
                    "created_at": "2026-03-24T08:00:00+00:00",
                }
            ]
        }

        maneuver_type = infer_maneuver_type(port_call, "approve_shift")

        self.assertEqual(maneuver_type, "entry")

    def test_action_for_maneuver_type_retargets_approval_family(self) -> None:
        self.assertEqual(action_for_maneuver_type("approve_shift", "entry"), "approve_entry")
        self.assertEqual(action_for_maneuver_type("abort_departure", "shift"), "abort_shift")
        self.assertEqual(action_for_maneuver_type("shift_report", "entry"), "entry_report")


if __name__ == "__main__":
    unittest.main()
