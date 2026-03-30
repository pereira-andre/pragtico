import unittest

from domain.chat_actions import (
    action_for_maneuver_type,
    build_action_reply_template,
    build_port_call_reply_template,
    build_slash_help,
    canonicalize_action_name,
    extract_pending_field_updates,
    extract_pending_target_updates,
    extract_json_object,
    format_action_summary,
    infer_maneuver_type,
    looks_like_operational_command,
    looks_like_slash_command,
    merge_action_candidate,
    normalize_action_candidate,
    normalize_action_fields,
    parse_slash_command,
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
        self.assertTrue(looks_like_slash_command("/help"))

    def test_parse_slash_help_returns_help_intent(self) -> None:
        parsed = parse_slash_command("/help", "admin")

        self.assertEqual(parsed["intent"], "help")
        self.assertIn("/registar-escala", parsed["answer"])
        self.assertIn("/avisos-locais", parsed["answer"])
        self.assertIn("/ondulacao", parsed["answer"])

    def test_parse_slash_local_warnings_returns_query_intent(self) -> None:
        parsed = parse_slash_command("/avisos-locais", "piloto")

        self.assertEqual(parsed["intent"], "query")
        self.assertEqual(parsed["command"], "local_warnings")

    def test_parse_slash_wave_returns_query_intent(self) -> None:
        parsed = parse_slash_command("/ondulacao", "piloto")

        self.assertEqual(parsed["intent"], "query")
        self.assertEqual(parsed["command"], "wave")

    def test_parse_slash_rule_returns_query_intent(self) -> None:
        parsed = parse_slash_command("/regra 015", "piloto")

        self.assertEqual(parsed["intent"], "query")
        self.assertEqual(parsed["command"], "rule")
        self.assertIn("015", parsed["argument"])

    def test_parse_slash_register_scale_builds_action_proposal(self) -> None:
        parsed = parse_slash_command(
            "/registar-escala\nNome do navio: BELITAKI\nETA de chegada: 2026-03-24T05:30\nCais previsto: TMS 2\nÚltimo porto: Leixões\nPróximo destino: Barcelona\nIMO: 9152923\nIndicativo: D5OC2\nBandeira: Libéria\nTipo de navio: Porta-contentores\nLOA (m): 179.23\nBoca (m): 25.3\nGT (t): 16281\nDWT (t): 22330\nCalado máximo (m): 9.94",
            "agente",
        )

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["action"], "create_port_call")
        self.assertEqual(parsed["proposal"]["fields"]["vessel_name"], "BELITAKI")
        self.assertEqual(parsed["proposal"]["fields"]["berth"], "TMS 2")
        self.assertEqual(parsed["proposal"]["fields"]["next_port"], "Barcelona")
        self.assertNotIn("destination", parsed["proposal"]["fields"])

    def test_parse_slash_create_maneuver_template_does_not_request_maneuver_id(self) -> None:
        parsed = parse_slash_command("/criar-manobra", "agente")

        self.assertEqual(parsed["intent"], "template")
        self.assertNotIn("ID da manobra", parsed["answer"])
        self.assertIn("ID é gerado automaticamente", parsed["answer"])

    def test_parse_slash_edit_maneuver_with_ref_requests_change_reason(self) -> None:
        parsed = parse_slash_command(
            "/editar-manobra Ref: BF757B7F Tipo de manobra: entrada Hora prevista: 29/03/2026, 20:00 Origem: Casablanca Destino: Cais 8 Calado: 11 Rebocadores: 5 Restrições: daylight Observações: Pedir 2 lanchas amarração",
            "agente",
        )

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["target"]["reference_code"], "BF757B7F")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_type"], "entry")
        self.assertNotIn("reference_code", parsed["proposal"]["fields"])
        self.assertNotIn("maneuver_type", parsed["proposal"]["fields"])
        self.assertEqual(parsed["proposal"]["missing_fields"], ["motivo da alteração"])

    def test_parse_slash_edit_maneuver_template_mentions_change_reason(self) -> None:
        parsed = parse_slash_command("/editar-manobra", "agente")

        self.assertEqual(parsed["intent"], "template")
        self.assertIn("Motivo da alteração", parsed["answer"])

    def test_parse_slash_edit_maneuver_is_allowed_for_piloto(self) -> None:
        parsed = parse_slash_command(
            "/editar-manobra Ref: BF757B7F Tipo de manobra: entrada Hora prevista: 29/03/2026, 20:00 Motivo da alteração: ajuste operacional",
            "piloto",
        )

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["action"], "edit_maneuver_plan")

    def test_parse_slash_edit_report_template_mentions_change_reason(self) -> None:
        parsed = parse_slash_command("/editar-registo-manobra", "admin")

        self.assertEqual(parsed["intent"], "template")
        self.assertIn("Motivo da alteração", parsed["answer"])

    def test_parse_slash_approve_without_target_returns_template(self) -> None:
        parsed = parse_slash_command("/aprovar", "piloto")

        self.assertEqual(parsed["intent"], "template")
        self.assertIn("Tipo de manobra", parsed["answer"])
        self.assertIn("Ref:", parsed["answer"])

    def test_parse_slash_approve_accepts_positional_ref_and_type(self) -> None:
        parsed = parse_slash_command("/aprovar BF757B7F entrada", "piloto")

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["action"], "approve_entry")
        self.assertEqual(parsed["proposal"]["target"]["reference_code"], "BF757B7F")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_type"], "entry")

    def test_parse_slash_approve_prefers_last_positional_identifier_as_reference(self) -> None:
        parsed = parse_slash_command("/aprovar BF757B7F PTSET26OCEA1C3808 entrada", "piloto")

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["action"], "approve_entry")
        self.assertEqual(parsed["proposal"]["target"]["reference_code"], "PTSET26OCEA1C3808")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_id"], "bf757b7f")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_type"], "entry")

    def test_parse_slash_approve_accepts_maneuver_id_only(self) -> None:
        parsed = parse_slash_command("/aprovar 7f3c2a91", "piloto")

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["action"], "approve_entry")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_id"], "7f3c2a91")

    def test_parse_slash_register_report_accepts_positional_target_with_multiline_fields(self) -> None:
        parsed = parse_slash_command(
            "/registar-manobra PTSET26OCEA1C3808 BF757B7F\n"
            "Tipo de manobra: entrada\n"
            "Início da manobra: 29/03/2026, 16:20\n"
            "Fim da manobra: 29/03/2026, 17:35\n"
            "Calado: 10,8\n"
            "Observações: Sem incidentes.",
            "piloto",
        )

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["action"], "entry_report")
        self.assertEqual(parsed["proposal"]["target"]["reference_code"], "PTSET26OCEA1C3808")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_id"], "bf757b7f")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_type"], "entry")

    def test_extract_pending_target_updates_accepts_plain_id_label(self) -> None:
        target = extract_pending_target_updates("ID: 7f3c2a91")

        self.assertEqual(target["maneuver_id"], "7f3c2a91")

    def test_parse_slash_register_report_without_ref_returns_template_with_proposal(self) -> None:
        parsed = parse_slash_command(
            "/registar-manobra Tipo de manobra: Entrada Início da manobra: 29/03/2026, 07:45 Fim da manobra: 29/03/2026, 08:30 Calado: 9,8 m Observações: 2 rebocadores",
            "piloto",
        )

        self.assertEqual(parsed["intent"], "template")
        self.assertEqual(parsed["proposal"]["action"], "entry_report")
        self.assertIn("ref ou nome do navio", parsed["proposal"]["missing_fields"])
        self.assertEqual(parsed["proposal"]["fields"]["maneuver_started_local"], "29/03/2026, 07:45")
        self.assertEqual(parsed["proposal"]["fields"]["maneuver_finished_local"], "29/03/2026, 08:30")

    def test_extract_pending_target_updates_parses_ref_and_maneuver_type(self) -> None:
        target = extract_pending_target_updates("Ref: SET-221\nTipo de manobra: saída")

        self.assertEqual(target["reference_code"], "SET-221")
        self.assertEqual(target["maneuver_type"], "departure")

    def test_extract_pending_target_updates_parses_maneuver_id(self) -> None:
        target = extract_pending_target_updates("ID da manobra: 7f3c2a91")

        self.assertEqual(target["maneuver_id"], "7f3c2a91")

    def test_parse_slash_register_report_as_agent_is_rejected_explicitly(self) -> None:
        parsed = parse_slash_command(
            "/registar-manobra Tipo de manobra: Entrada Início da manobra: 29/03/2026, 07:45 Fim da manobra: 29/03/2026, 08:30 Calado: 9,8 m Observações: 2 rebocadores",
            "agente",
        )

        self.assertEqual(parsed["intent"], "unsupported")
        self.assertIn("não está autorizada", parsed["answer"].lower())

    def test_parse_slash_register_report_with_maneuver_id_does_not_require_ref_or_type(self) -> None:
        parsed = parse_slash_command(
            "/registar-manobra ID da manobra: 7f3c2a91 Início da manobra: 29/03/2026, 07:45 Fim da manobra: 29/03/2026, 08:30 Calado: 9,8 m",
            "piloto",
        )

        self.assertEqual(parsed["intent"], "action")
        self.assertEqual(parsed["proposal"]["target"]["maneuver_id"], "7f3c2a91")

    def test_build_slash_help_respects_role(self) -> None:
        help_text = build_slash_help("agente")

        self.assertIn("/registar-escala", help_text)
        self.assertNotIn("/apagar-escala", help_text)
        self.assertIn("ID da manobra é automático", help_text)

    def test_build_slash_help_for_piloto_mentions_edit_maneuver(self) -> None:
        help_text = build_slash_help("piloto")

        self.assertIn("/editar-manobra", help_text)
        self.assertNotIn("/criar-manobra", help_text)

    def test_build_action_reply_template_for_report_mentions_id_and_ref_plus_type(self) -> None:
        template = build_action_reply_template("entry_report")

        self.assertIn("ID da manobra", template)
        self.assertIn("Ref: ", template)
        self.assertIn("Tipo de manobra", template)

    def test_build_action_reply_template_for_abort_mentions_id_and_ref_plus_type(self) -> None:
        template = build_action_reply_template("abort_entry")

        self.assertIn("ID da manobra", template)
        self.assertIn("Motivo: ", template)

    def test_parse_slash_edit_scale_with_maneuver_payload_redirects_to_correct_command(self) -> None:
        parsed = parse_slash_command(
            "/editar-escala Ref: BF757B7F Tipo de manobra: entrada Hora prevista: 29/03/2026, 20:00 Origem: Casablanca Destino: Cais 8",
            "admin",
        )

        self.assertEqual(parsed["intent"], "unsupported")
        self.assertIn("/editar-manobra", parsed["answer"])

    def test_parse_slash_create_maneuver_entry_redirects_to_edit(self) -> None:
        parsed = parse_slash_command(
            "/criar-manobra Ref: SET-221 Tipo de manobra: entrada Hora prevista: 29/03/2026, 20:00",
            "agente",
        )

        self.assertEqual(parsed["intent"], "unsupported")
        self.assertIn("/editar-manobra", parsed["answer"])

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

    def test_resolve_maneuver_accepts_unique_id_prefix(self) -> None:
        port_call = {
            "maneuver_history": [
                {"id": "7f3c2a91-aaaa-bbbb-cccc-111111111111", "type": "entry", "state": "completed"},
                {"id": "91d44e00-aaaa-bbbb-cccc-222222222222", "type": "entry", "state": "approved"},
            ]
        }

        maneuver = resolve_maneuver(port_call, "edit_maneuver_report", "entry", "7f3c2a91")

        self.assertEqual(maneuver["id"], "7f3c2a91-aaaa-bbbb-cccc-111111111111")

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

    def test_normalize_action_fields_moves_numeric_vessel_name_to_imo(self) -> None:
        fields = normalize_action_fields(
            "create_port_call",
            {
                "nome do navio": "9627814",
                "eta": "2026-03-24T05:30",
                "planned_quay": "TMS 2",
                "last_port_of_call": "Leixões",
                "next_port_of_call": "Barcelona",
            },
        )

        self.assertEqual(fields["vessel_imo"], "9627814")
        self.assertEqual(fields["vessel_name"], "")

    def test_format_action_summary_does_not_duplicate_missing_prompt_block(self) -> None:
        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "create_port_call",
                "target": {},
                "fields": {
                    "vessel_name": "BELITAKI",
                    "eta": "2026-03-24T05:30",
                },
                "missing_fields": [],
            },
            "agente",
        )

        summary = format_action_summary(proposal)

        self.assertEqual(summary.count("Dados ainda em falta:"), 1)
        self.assertEqual(summary.count("Se preferires, responde já neste formato e eu trato do registo:"), 1)
        self.assertNotIn("Faltam estes campos:", summary)

    def test_format_action_summary_uses_portuguese_labels_for_maneuver_and_constraints(self) -> None:
        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "edit_maneuver_plan",
                "target": {
                    "reference_code": "PTSET26OCEA1C3808",
                    "vessel_name": "OCEAN BULKER",
                    "maneuver_id": "b78cb93f",
                    "maneuver_type": "shift",
                },
                "fields": {
                    "planned_at_local": "29/03/2026, 23:10",
                    "constraints": ["daylight"],
                    "change_reason": "Ajuste operacional",
                },
                "reason": "Comando explícito /editar-manobra.",
                "missing_fields": [],
            },
            "admin",
        )

        summary = format_action_summary(proposal)

        self.assertIn("Manobra: mudança.", summary)
        self.assertIn("restrições: daylight.", summary)
        self.assertIn("Nota: Comando explícito /editar-manobra.", summary)
        self.assertNotIn("/editar-manobra..", summary)

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

    def test_merge_action_candidate_preserves_existing_optional_fields_on_reason_only_update(self) -> None:
        existing = parse_slash_command(
            "/editar-manobra ID da manobra: 7f3c2a91 Tipo de manobra: mudança Hora prevista: 29/03/2026, 20:00 Origem: Teporset Destino: Fundeadouro Tróia Restrições: daylight Observações: Aguarda bom tempo",
            "agente",
        )["proposal"]
        updates = normalize_action_candidate(
            {
                "intent": "action",
                "action": "edit_maneuver_plan",
                "target": existing["target"],
                "fields": extract_pending_field_updates("Motivo da alteração: janela operacional", existing),
                "missing_fields": [],
            },
            "agente",
        )

        merged = merge_action_candidate(existing, updates, "agente")

        self.assertEqual(merged["fields"]["change_reason"], "janela operacional")
        self.assertEqual(merged["fields"]["notes"], "Aguarda bom tempo")
        self.assertEqual(merged["fields"]["constraints"], ["daylight"])

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

        self.assertEqual(updates["change_reason"], "piloto disponível")

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

    def test_resolve_port_call_accepts_id_prefix_in_reference_field(self) -> None:
        port_calls = [
            {"id": "bf757b7f-aaaa-bbbb-cccc-111111111111", "reference_code": "SET-001", "vessel_name": "MSC Lyria"},
            {"id": "91d44e00-aaaa-bbbb-cccc-222222222222", "reference_code": "SET-002", "vessel_name": "Atlantic Trader"},
        ]

        resolved = resolve_port_call(port_calls, {"reference_code": "BF757B7F"})

        self.assertEqual(resolved["reference_code"], "SET-001")

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

    def test_resolve_port_call_accepts_short_maneuver_id_from_planned_rows(self) -> None:
        port_calls = [
            {
                "id": "pc1",
                "port_call_id": "pc1",
                "reference_code": "PTSET26OCEA1C3808",
                "vessel_name": "OCEA",
                "maneuver_id": "bf757b7f-1234-5678-9999-aaaaaaaaaaaa",
            }
        ]

        resolved = resolve_port_call(port_calls, {"maneuver_id": "BF757B7F"})

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["id"], "pc1")

    def test_build_port_call_reply_template_has_no_missing_prefix(self) -> None:
        template = build_port_call_reply_template(["ETA", "cais previsto"])

        self.assertNotIn("Faltam estes campos:", template)
        self.assertIn("Nome: ", template)

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
