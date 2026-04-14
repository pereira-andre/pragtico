import os
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ["APP_STORAGE_BACKEND"] = "local"
os.environ["RAG_INDEX_BACKEND"] = "local"
os.environ["MANEUVER_CASE_CAPTURE_ENVIRONMENT"] = "0"
os.environ["EXTERNAL_DATA_REFRESH_ENABLED"] = "0"

import app
from core import services
from domain.chat_actions import normalize_action_candidate
from flask import session
from core.helpers import answer_direct_operational_query, build_operational_chat_sources, build_scale_context
from domain.practice_experience import PRACTICE_EXPERIENCE_STATE_KEY
from storage import LocalStore


class _StubLocalWarningService:
    enabled = True

    def __init__(
        self,
        warnings: list[dict],
        *,
        stale: bool = False,
        error: str = "",
        cache_updated_at_label: str = "03/04/2026, 18:10",
        last_attempt_at_label: str = "03/04/2026, 18:10",
        probe_error: str = "",
    ) -> None:
        self._warnings = warnings
        self._stale = stale
        self._error = error
        self._cache_updated_at_label = cache_updated_at_label
        self._last_attempt_at_label = last_attempt_at_label
        self._probe_error = probe_error

    def list_warnings(self) -> list[dict]:
        return list(self._warnings)

    def probe_warnings(self) -> list[dict]:
        if self._probe_error:
            raise RuntimeError(self._probe_error)
        return list(self._warnings)

    def status(self) -> dict:
        return {
            "stale": self._stale,
            "error": self._error,
            "cache_updated_at_label": self._cache_updated_at_label,
            "last_attempt_at_label": self._last_attempt_at_label,
            "count": len(self._warnings),
        }

    def get_warning(self, warning_id: int) -> dict | None:
        for item in self._warnings:
            if item.get("id") == warning_id:
                return item
        return None


class _StubWaveService:
    enabled = True

    def __init__(
        self,
        *,
        probe_payload: dict | None = None,
        stale: bool = False,
        error: str = "",
        cache_updated_at_label: str = "03/04/2026, 18:10",
        last_attempt_at_label: str = "03/04/2026, 18:10",
        probe_error: str = "",
        station_name: str = "Sines",
    ) -> None:
        self._probe_payload = probe_payload or {}
        self._stale = stale
        self._error = error
        self._cache_updated_at_label = cache_updated_at_label
        self._last_attempt_at_label = last_attempt_at_label
        self._probe_error = probe_error
        self.station_name = station_name

    def probe_current_conditions(self) -> dict:
        if self._probe_error:
            raise RuntimeError(self._probe_error)
        return dict(self._probe_payload)

    def status(self) -> dict:
        return {
            "stale": self._stale,
            "error": self._error,
            "cache_updated_at_label": self._cache_updated_at_label,
            "last_attempt_at_label": self._last_attempt_at_label,
        }


class _StubTideService:
    def resolve_query_dates(self, question: str):
        del question
        return [datetime(2026, 4, 9).date()]

    def summary_for_date(self, target_date):
        del target_date
        return {
            "date": "2026-04-09",
            "date_label": "09/04/2026",
            "location": "Setúbal / Tróia",
            "events": [
                {"time": "01:48", "type": "Baixa-mar", "height_m": 1.3},
                {"time": "08:02", "type": "Preia-mar", "height_m": 2.4},
            ],
        }

    def context_for_question(self, question: str):
        del question
        return {
            "source_id": "T1",
            "document": "Marés Setúbal / Tróia",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "structured",
            "snippet": "Marés para 09/04/2026 em Setúbal / Tróia.",
            "text": "Marés para 09/04/2026 em Setúbal / Tróia.",
        }


class _StubWeatherService:
    enabled = True

    def get_forecast(self, days: int = 3):
        del days
        return {
            "location": {
                "name": "Setúbal",
                "localtime": "2026-04-09 13:07",
            },
            "current": {
                "condition": "Parcialmente nublado",
                "temp_c": 18.0,
                "wind_kts": 12.0,
                "gust_kts": 18.0,
                "wind_dir": "NW",
                "humidity": 67,
                "vis_km": 10,
                "precip_mm": 0.0,
            },
            "hourly_groups": [
                {
                    "date": "2026-04-09",
                    "date_label": "09/04/2026",
                    "hours": [
                        {
                            "time": "14:00",
                            "timestamp": "2026-04-09 14:00",
                            "condition": "Parcialmente nublado",
                            "temp_c": 18.5,
                            "wind_kts": 13.0,
                            "wind_dir": "NNE",
                            "chance_of_rain": 0,
                        },
                        {
                            "time": "18:00",
                            "timestamp": "2026-04-09 18:00",
                            "condition": "Nublado",
                            "temp_c": 17.0,
                            "wind_kts": 11.0,
                            "wind_dir": "N",
                            "chance_of_rain": 5,
                        },
                        {
                            "time": "23:00",
                            "timestamp": "2026-04-09 23:00",
                            "condition": "Encoberto",
                            "temp_c": 15.2,
                            "wind_kts": 8.0,
                            "wind_dir": "NNW",
                            "chance_of_rain": 10,
                        },
                    ],
                },
                {
                    "date": "2026-04-10",
                    "date_label": "10/04/2026",
                    "hours": [
                        {
                            "time": "00:00",
                            "timestamp": "2026-04-10 00:00",
                            "condition": "Encoberto",
                            "temp_c": 14.8,
                            "wind_kts": 7.5,
                            "wind_dir": "NW",
                            "chance_of_rain": 10,
                        }
                    ],
                },
            ],
        }

    def context_source(self):
        return {
            "source_id": "W1",
            "document": "WeatherAPI Setúbal",
            "chunk_id": 0,
            "score": 1.0,
            "retrieval_mode": "live_api",
            "snippet": "Meteorologia atual para Setúbal.",
            "text": "Meteorologia atual para Setúbal.",
        }

    def context_for_question(self, question: str):
        del question
        return self.context_source()

    def _resolve_query_dates(self, question: str, reference_date):
        del reference_date
        if "10 abril" in question.lower():
            return ["2026-04-10"]
        return ["2026-04-09"]

    def _resolve_query_times(self, question: str):
        if "00:00" in question:
            return ["00:00"]
        return []


class _StubWhatsAppService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        webhook_ready: bool = True,
        verify_ok: bool = True,
        allowed_numbers: set[str] | None = None,
        welcome_enabled: bool = False,
        welcome_message: str = "👋 Bem-vindo ao PRAGtico",
    ) -> None:
        self.enabled = enabled
        self.webhook_ready = webhook_ready
        self.verify_ok = verify_ok
        self.allowed_numbers = allowed_numbers or set()
        self.welcome_enabled = welcome_enabled
        self.welcome_message = welcome_message
        self.sent_messages: list[dict] = []
        self._outbound_counter = 122

    def verify_webhook(self, mode: str | None, token: str | None) -> bool:
        return self.verify_ok and (mode or "") == "subscribe" and bool(token)

    def parse_inbound_messages(self, payload: dict | None) -> list[dict]:
        return [
            event
            for event in self.parse_webhook_events(payload)
            if event.get("event_type") == "message_text"
        ]

    def parse_webhook_events(self, payload: dict | None) -> list[dict]:
        if not payload:
            return []
        parsed: list[dict] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = value.get("contacts", [])
                contact_map = {
                    str(contact.get("wa_id", "")): contact
                    for contact in contacts
                    if contact.get("wa_id")
                }
                for message in value.get("messages", []):
                    from_number = str(message.get("from", ""))
                    profile_name = ((contact_map.get(from_number) or {}).get("profile") or {}).get("name", "")
                    if message.get("type") == "text":
                        parsed.append(
                            {
                                "event_type": "message_text",
                                "message_id": str(message.get("id", "")),
                                "from_number": from_number,
                                "profile_name": profile_name,
                                "text": str((message.get("text") or {}).get("body") or ""),
                                "timestamp": str(message.get("timestamp") or ""),
                                "raw": message,
                            }
                        )
                    elif message.get("type") == "reaction":
                        parsed.append(
                            {
                                "event_type": "message_reaction",
                                "message_id": str(message.get("id", "")),
                                "target_message_id": str((message.get("reaction") or {}).get("message_id") or ""),
                                "from_number": from_number,
                                "profile_name": profile_name,
                                "emoji": str((message.get("reaction") or {}).get("emoji") or ""),
                                "timestamp": str(message.get("timestamp") or ""),
                                "raw": message,
                            }
                        )
                for status in value.get("statuses", []):
                    parsed.append(
                        {
                            "event_type": "message_status",
                            "event_id": ":".join(
                                part
                                for part in (
                                    str(status.get("id") or ""),
                                    str(status.get("status") or ""),
                                    str(status.get("timestamp") or ""),
                                )
                                if part
                            ),
                            "message_id": str(status.get("id") or ""),
                            "status": str(status.get("status") or ""),
                            "timestamp": str(status.get("timestamp") or ""),
                            "recipient_id": str(status.get("recipient_id") or ""),
                            "raw": status,
                        }
                    )
        return parsed

    def is_allowed_number(self, number: str | None) -> bool:
        if not self.allowed_numbers:
            return True
        return str(number or "") in self.allowed_numbers

    def build_test_reply(self, inbound: dict) -> str:
        return f"reply:{inbound.get('text', '')}"

    def build_welcome_message(self, inbound: dict | None = None) -> str:
        if not self.welcome_enabled:
            return ""
        return self.welcome_message

    def send_text_message(self, to_number: str, text: str, *, reply_to_message_id: str = "") -> dict:
        self._outbound_counter += 1
        self.sent_messages.append(
            {
                "to_number": to_number,
                "text": text,
                "reply_to_message_id": reply_to_message_id,
            }
        )
        return {"messages": [{"id": f"wamid.REPLY{self._outbound_counter}"}]}


class OperationalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        services.store = self.store

    def tearDown(self) -> None:
        services.store = self.original_store
        self.temp_dir.cleanup()

    def _create_entry(self, *, notes: str, eta: str = "2026-03-24T05:30:00+00:00") -> dict:
        return self.store.create_port_call(
            vessel_name="BELITAKI",
            eta=eta,
            created_by="admin",
            berth="TMS 2",
            last_port="Leixoes",
            next_port="Barcelona",
            notes=notes,
            vessel_imo="9152923",
            vessel_call_sign="D5OC2",
            vessel_flag="Liberia",
            vessel_type="Porta-contentores",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="16281",
            vessel_max_draft_m="9.94",
            vessel_dwt_t="22330",
        )

    def _move_port_call_in_port(self, port_call_id: str) -> dict:
        self.store.approve_port_call(port_call_id, decided_by="admin")
        return self.store.mark_port_call_arrived(
            port_call_id,
            arrived_at="2026-03-24T06:00:00+00:00",
            updated_by="admin",
        )

    def _archive_entry(
        self,
        *,
        vessel_name: str,
        eta: str,
        arrived_at: str,
        started_at: str,
        finished_at: str,
    ) -> dict:
        port_call = self.store.create_port_call(
            vessel_name=vessel_name,
            eta=eta,
            created_by="admin",
            berth="TMS 2",
            last_port="Leixoes",
            next_port="Barcelona",
            notes="Arquivo de teste.",
            vessel_imo="9152923",
            vessel_call_sign="D5OC2",
            vessel_flag="Liberia",
            vessel_type="Porta-contentores",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="16281",
            vessel_max_draft_m="9.94",
            vessel_dwt_t="22330",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            port_call["id"],
            arrived_at=arrived_at,
            updated_by="admin",
        )
        self.store.attach_entry_report(
            port_call["id"],
            updated_by="admin",
            maneuver_started_at=started_at,
            maneuver_finished_at=finished_at,
            draft_m="9.94",
            notes="Entrada concluída.",
        )
        return self.store.get_port_call(port_call["id"])

    def _login_client_as_admin(self, client) -> None:
        with client.session_transaction() as flask_session:
            flask_session["username"] = "admin"
            flask_session["role"] = "admin"

    def _write_knowledge_companion(self, document_name: str, payload: dict) -> Path:
        companion_dir = Path(self.store.knowledge_dir) / "companions"
        companion_dir.mkdir(parents=True, exist_ok=True)
        path = companion_dir / f"{Path(document_name).stem}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_complete_entry_with_real_times_only_confirms_maneuver(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, message = app.execute_pending_operational_action(
                {
                    "action": "complete_entry",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {
                        "maneuver_started_local": "2026-03-24T10:00",
                        "maneuver_finished_local": "2026-03-24T12:00",
                    },
                },
                username="admin",
                role="admin",
            )

        self.assertIn("entrada confirmada", message.lower())
        self.assertEqual(result["status"], "in_port")
        updated = self.store.get_port_call(port_call["id"])
        entry = next(item for item in updated["maneuver_history"] if item["type"] == "entry")
        self.assertEqual(entry["state"], "completed")
        self.assertFalse(entry.get("report_note"))
        self.assertFalse(entry.get("reported_draft_m"))
        self.assertFalse(entry.get("execution_started_at"))
        self.assertFalse(entry.get("execution_finished_at"))

    def test_finalize_completion_with_real_times_does_not_require_report_fields(self) -> None:
        port_call = self._create_entry(notes="Sem calado planeado")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "complete_entry",
                "target": {
                    "reference_code": port_call["reference_code"],
                    "vessel_name": "BELITAKI",
                    "maneuver_type": "entry",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T10:00",
                    "maneuver_finished_local": "2026-03-24T12:00",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["missing_fields"], [])


    def test_create_port_call_with_past_eta_is_rejected(self) -> None:
        with app.app.test_request_context("/"):
            session["role"] = "agente"
            with self.assertRaisesRegex(ValueError, "ETA não pode ser anterior à data/hora presente"):
                app.execute_pending_operational_action(
                    {
                        "action": "create_port_call",
                        "fields": {
                            "vessel_name": "MV SETUBAL PIONEER",
                            "eta_local": "02/04/2025, 06:30",
                            "berth": "Cais 3 – Terminal Multipurpose",
                            "last_port": "Sines (PT)",
                            "next_port": "Leixões (PT)",
                            "vessel_imo": "9876543",
                            "vessel_call_sign": "CQAB7",
                            "vessel_flag": "Portugal",
                            "vessel_type": "General Cargo",
                            "vessel_loa_m": "142.50",
                            "vessel_beam_m": "21.80",
                            "vessel_gt_t": "8950",
                            "vessel_dwt_t": "12400",
                            "vessel_max_draft_m": "7.20",
                            "notes": "Carga geral paletizada.",
                        },
                    },
                    username="admin",
                    role="agente",
                )

    def test_edit_port_call_with_explicit_past_eta_is_rejected(self) -> None:
        port_call = self._create_entry(eta="2026-04-02T05:30:00+00:00", notes="Teste")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "ETA não pode ser anterior à data/hora presente"):
                app.execute_pending_operational_action(
                    {
                        "action": "edit_port_call",
                        "port_call_id": port_call["id"],
                        "target": {"reference_code": port_call["reference_code"]},
                        "fields": {"eta_local": "02/04/2025, 06:30"},
                    },
                    username="admin",
                    role="admin",
                )

    def test_create_port_call_canonicalizes_known_berth_alias(self) -> None:
        with app.app.test_request_context("/"):
            session["role"] = "agente"
            result, _message = app.execute_pending_operational_action(
                {
                    "action": "create_port_call",
                    "fields": {
                        "vessel_name": "AUTO TESTE",
                        "eta_local": "2026-04-02T06:30",
                        "berth": "Cais 10 Autoeuropa",
                        "last_port": "Sines",
                        "next_port": "Vigo",
                        "vessel_imo": "9876543",
                        "vessel_call_sign": "CQAB7",
                        "vessel_flag": "Portugal",
                        "vessel_type": "General Cargo",
                        "vessel_loa_m": "142.50",
                        "vessel_beam_m": "21.80",
                        "vessel_gt_t": "8950",
                        "vessel_dwt_t": "12400",
                        "vessel_max_draft_m": "7.20",
                        "notes": "Carga geral paletizada.",
                    },
                },
                username="admin",
                role="agente",
            )

        self.assertEqual(result["berth"], "Cais 10 / Autoeuropa")

    def test_schedule_shift_uses_current_berth_as_origin_when_omitted(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, _message = app.execute_pending_operational_action(
                {
                    "action": "schedule_shift",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "shift"},
                    "fields": {
                        "planned_shift_at_local": "2026-04-02T08:30",
                        "destination_berth": "Cais 3 Terminal Multipurpose",
                        "draft_m": "9.94",
                        "tug_count": "2",
                        "notes": "Mudança operacional.",
                    },
                },
                username="admin",
                role="admin",
            )

        shift = next(item for item in result["maneuver_history"] if item["type"] == "shift")
        self.assertEqual(shift["origin"], "TMS 2")
        self.assertEqual(shift["destination"], "TMS 1 - Cais 3")
        self.assertIn("Origem: TMS 2", shift["plan_note"])

    def test_schedule_departure_uses_current_berth_as_origin_when_omitted(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, _message = app.execute_pending_operational_action(
                {
                    "action": "schedule_departure",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "departure"},
                    "fields": {
                        "planned_departure_at_local": "2026-04-02T09:10",
                        "next_port": "Barcelona",
                        "draft_m": "9.94",
                        "tug_count": "2",
                        "notes": "Saída operacional.",
                    },
                },
                username="admin",
                role="admin",
            )

        departure = next(item for item in result["maneuver_history"] if item["type"] == "departure")
        self.assertEqual(departure["origin"], "TMS 2")
        self.assertEqual(departure["destination"], "Barcelona")
        self.assertIn("Origem: TMS 2", departure["plan_note"])

    def test_scale_context_exposes_similar_cases_for_matching_maneuver_profile(self) -> None:
        historical = self._create_entry(notes="Entrada histórica", eta="2026-03-20T05:30:00+00:00")
        self.store.approve_port_call(historical["id"], decided_by="admin", approval_note="Aprovada sem incidentes.")
        historical_done = self.store.mark_port_call_arrived(
            historical["id"],
            arrived_at="2026-03-20T06:00:00+00:00",
            updated_by="admin",
        )
        historical_entry = next(item for item in historical_done["maneuver_history"] if item["type"] == "entry")
        self.store.update_maneuver_case_feedback(
            maneuver_id=historical_entry["id"],
            feedback_status="approved",
            feedback_note="Boa referência para este perfil.",
            feedback_by="admin",
        )

        current = self._create_entry(notes="Nova entrada", eta="2026-04-05T05:30:00+00:00")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            scale = build_scale_context(self.store.get_port_call(current["id"]))

        entry = next(item for item in scale["maneuvers"] if item["type"] == "entry")
        self.assertTrue(entry["similar_cases"])
        self.assertEqual(entry["similar_cases"][0]["reference_code"], historical["reference_code"])
        self.assertNotEqual(entry["similar_cases"][0]["maneuver_id"], entry["id"])
        self.assertEqual(entry["casebook_recommendation"]["status_key"], "positive")
        self.assertIn("Feedback", entry["casebook_recommendation"]["title"])
        self.assertEqual(entry["similar_cases"][0]["feedback_status"], "approved")
        self.assertTrue(entry["analysis_checklist"])
        self.assertTrue(any(item["title"] == "Disponibilidade do destino" for item in entry["analysis_checklist"]))

    def test_operational_chat_sources_include_casebook_when_scale_is_identified(self) -> None:
        historical = self._create_entry(notes="Entrada histórica", eta="2026-03-19T05:30:00+00:00")
        self.store.approve_port_call(historical["id"], decided_by="admin", approval_note="Aprovada com 2 rebocadores.")
        self.store.mark_port_call_arrived(
            historical["id"],
            arrived_at="2026-03-19T06:00:00+00:00",
            updated_by="admin",
        )

        current = self._create_entry(notes="Nova entrada", eta="2026-04-06T05:30:00+00:00")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            sources = build_operational_chat_sources(
                f"O que achas da entrada da escala {current['reference_code']}?"
            )

        casebook_sources = [item for item in sources if item.get("retrieval_mode") == "maneuver_casebook"]
        self.assertTrue(casebook_sources)
        self.assertIn(historical["reference_code"], casebook_sources[0]["snippet"])
        self.assertIn("recomendação histórica", casebook_sources[0]["snippet"])

    def test_direct_operational_query_returns_none_for_opinion_question(self) -> None:
        historical = self._create_entry(notes="Entrada histórica", eta="2026-03-18T05:30:00+00:00")
        self.store.approve_port_call(historical["id"], decided_by="admin", approval_note="Aprovada sem incidentes.")
        historical_done = self.store.mark_port_call_arrived(
            historical["id"],
            arrived_at="2026-03-18T06:00:00+00:00",
            updated_by="admin",
        )
        historical_entry = next(item for item in historical_done["maneuver_history"] if item["type"] == "entry")
        self.store.update_maneuver_case_feedback(
            maneuver_id=historical_entry["id"],
            feedback_status="approved",
            feedback_note="Boa referência.",
            feedback_by="admin",
        )

        current = self._create_entry(notes="Nova entrada", eta="2026-04-07T05:30:00+00:00")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            answer = answer_direct_operational_query(f"O que achas da entrada da escala {current['reference_code']}?")

        self.assertIsNone(answer)

    def test_complete_entry_rejects_occupied_quay(self) -> None:
        occupied = self._create_entry(notes="Primeira escala")
        self._move_port_call_in_port(occupied["id"])
        waiting = self._create_entry(notes="Segunda escala", eta="2026-04-02T05:30:00+00:00")
        self.store.approve_port_call(waiting["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "já está ocupado"):
                app.execute_pending_operational_action(
                    {
                        "action": "complete_entry",
                        "port_call_id": waiting["id"],
                        "target": {"maneuver_type": "entry"},
                        "fields": {},
                    },
                    username="admin",
                    role="admin",
                )

    def test_admin_can_edit_completed_maneuver_plan_for_archive_corrections(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            port_call["id"],
            arrived_at="2026-03-24T06:00:00+00:00",
            updated_by="admin",
        )
        entry = next(item for item in self.store.get_port_call(port_call["id"])["maneuver_history"] if item["type"] == "entry")

        updated = self.store.edit_maneuver_plan(
            port_call["id"],
            entry["id"],
            updated_by="admin",
            actor_role="admin",
            planned_at="2026-03-24T05:45:00+00:00",
            origin="Sines",
            destination="TMS 1 - Cais 3",
            draft_m="9.94",
            tug_count="2",
            constraints=["daylight"],
            plan_note="Correção histórica do planeamento.",
            change_reason="Correção de arquivo.",
        )

        updated_entry = next(item for item in updated["maneuver_history"] if item["id"] == entry["id"])
        self.assertEqual(updated_entry["state"], "completed")
        self.assertEqual(updated_entry["destination"], "TMS 1 - Cais 3")
        self.assertTrue(updated_entry["change_log"])

    def test_entry_report_accepts_scale_reference_passed_in_id_field(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "entry_report",
                "target": {
                    "maneuver_id": port_call["reference_code"],
                    "maneuver_type": "entry",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T10:00",
                    "maneuver_finished_local": "2026-03-24T12:00",
                    "draft_m": "9.94",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["port_call_id"], port_call["id"])
        self.assertEqual(finalized["target"]["reference_code"], port_call["reference_code"])
        self.assertEqual(finalized["target"]["maneuver_id"], "")
        self.assertEqual(finalized["missing_fields"], [])

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            result, message = app.execute_pending_operational_action(
                finalized,
                username="admin",
                role="admin",
            )

        self.assertIn("registo de entrada guardado", message.lower())
        self.assertEqual(result["status"], "in_port")

    def test_entry_report_accepts_maneuver_id_passed_in_reference_field(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        entry = next(item for item in self.store.get_port_call(port_call["id"])["maneuver_history"] if item["type"] == "entry")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "entry_report",
                "target": {
                    "reference_code": entry["id"][:8].upper(),
                    "maneuver_type": "entry",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T10:00",
                    "maneuver_finished_local": "2026-03-24T12:00",
                    "draft_m": "9.94",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["port_call_id"], port_call["id"])
        self.assertEqual(finalized["target"]["reference_code"], port_call["reference_code"])
        self.assertEqual(finalized["target"]["maneuver_id"], entry["id"])
        self.assertEqual(finalized["missing_fields"], [])

    def test_direct_operational_query_returns_real_maneuver_id_not_scale_reference(self) -> None:
        port_call = self.store.create_port_call(
            vessel_name="OCEAN BULKER",
            eta="2026-03-29T15:10:00+00:00",
            created_by="admin",
            berth="Teporset",
            last_port="Casablanca",
            next_port="Setubal",
            notes="Registo do agente · Entrada\nCalado: 10.8",
            vessel_imo="9999999",
            vessel_call_sign="CQ1234",
            vessel_flag="Malta",
            vessel_type="Graneleiro",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="16281",
            vessel_max_draft_m="10.8",
            vessel_dwt_t="22330",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        refreshed = self.store.get_port_call(port_call["id"])
        entry = next(item for item in refreshed["maneuver_history"] if item["type"] == "entry")

        with app.app.test_request_context("/"):
            session["role"] = "piloto"
            answer = answer_direct_operational_query("qual o id da manobra de entrada do OCEAN BULKER?")

        self.assertIsNotNone(answer)
        self.assertIn(entry["id"][:8].upper(), answer["answer"])
        self.assertNotIn(port_call["reference_code"], answer["answer"])

    def test_completed_maneuver_only_moves_to_archive_after_report(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            app.execute_pending_operational_action(
                {
                    "action": "complete_entry",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {},
                },
                username="admin",
                role="admin",
            )

            snapshot_without_report = self.store.get_port_activity_snapshot(window_days=3650)
            archived_ids = {item["port_call_id"] for item in snapshot_without_report["archived_maneuvers"]}
            planned_ids = {item["port_call_id"] for item in snapshot_without_report["planned_maneuvers"]}

            self.assertNotIn(port_call["id"], archived_ids)
            self.assertIn(port_call["id"], planned_ids)

            app.execute_pending_operational_action(
                {
                    "action": "entry_report",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {
                        "maneuver_started_local": "2026-03-24T10:00",
                        "maneuver_finished_local": "2026-03-24T12:00",
                        "draft_m": "9.94",
                        "notes": "Sem incidentes.",
                    },
                },
                username="admin",
                role="admin",
            )

        snapshot_with_report = self.store.get_port_activity_snapshot(window_days=3650)
        archived_ids = {item["port_call_id"] for item in snapshot_with_report["archived_maneuvers"]}
        planned_ids = {item["port_call_id"] for item in snapshot_with_report["planned_maneuvers"]}
        self.assertIn(port_call["id"], archived_ids)
        self.assertNotIn(port_call["id"], planned_ids)

    def test_aborted_maneuver_moves_to_archive_and_leaves_planning(self) -> None:
        port_call = self._create_entry(
            notes="Registo do agente · Entrada\nCalado: 9.94",
            eta="2026-04-02T05:30:00+00:00",
        )

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            app.execute_pending_operational_action(
                {
                    "action": "abort_entry",
                    "port_call_id": port_call["id"],
                    "target": {"maneuver_type": "entry"},
                    "fields": {
                        "aborted_reason": "Cancelada pelo agente.",
                    },
                },
                username="admin",
                role="admin",
            )

        snapshot = self.store.get_port_activity_snapshot(window_days=3650)
        archived_ids = {item["port_call_id"] for item in snapshot["archived_maneuvers"]}
        planned_ids = {item["port_call_id"] for item in snapshot["planned_maneuvers"]}

        self.assertIn(port_call["id"], archived_ids)
        self.assertNotIn(port_call["id"], planned_ids)

    def test_chat_ok_confirms_pending_action(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self.store.approve_port_call(port_call["id"], decided_by="admin")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            proposal = {
                "intent": "action",
                "action": "complete_entry",
                "port_call_id": port_call["id"],
                "target": {
                    "reference_code": port_call["reference_code"],
                    "vessel_name": port_call["vessel_name"],
                    "maneuver_type": "entry",
                },
                "fields": {},
                "missing_fields": [],
            }
            app.save_pending_chat_action("admin", conversation["id"], proposal, "Concluir entrada")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "ok",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "pending_action_confirmed")
        self.assertIsNone(payload["pending_action"])
        self.assertIn("entrada confirmada", payload["answer"].lower())
        updated = self.store.get_port_call(port_call["id"])
        entry = next(item for item in updated["maneuver_history"] if item["type"] == "entry")
        self.assertEqual(entry["state"], "completed")

    def test_maneuver_archive_defaults_to_latest_available_month(self) -> None:
        older = self._archive_entry(
            vessel_name="BELITAKI FEB",
            eta="2026-02-10T05:30:00+00:00",
            arrived_at="2026-02-10T06:00:00+00:00",
            started_at="2026-02-10T05:40:00+00:00",
            finished_at="2026-02-10T06:00:00+00:00",
        )
        latest = self._archive_entry(
            vessel_name="BELITAKI MAR",
            eta="2026-03-12T05:30:00+00:00",
            arrived_at="2026-03-12T06:00:00+00:00",
            started_at="2026-03-12T05:40:00+00:00",
            finished_at="2026-03-12T06:00:00+00:00",
        )

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get("/maneuvers/archive")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Março 2026", html)
        self.assertIn(latest["reference_code"], html)
        self.assertNotIn(older["reference_code"], html)

    def test_archive_billing_report_uses_filtered_scale_selection(self) -> None:
        archived = self._archive_entry(
            vessel_name="BELITAKI REPORT",
            eta="2026-03-18T05:30:00+00:00",
            arrived_at="2026-03-18T06:00:00+00:00",
            started_at="2026-03-18T05:40:00+00:00",
            finished_at="2026-03-18T06:00:00+00:00",
        )

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(
                f"/maneuvers/archive/report?year=2026&month=3&selection=scales&scale_ids={archived['id']}"
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Relatório de Faturação por Escala", html)
        self.assertIn(archived["reference_code"], html)
        self.assertIn("Imprimir / PDF", html)

    def test_maneuver_estimate_report_renders_formal_document(self) -> None:
        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(
                "/maneuvers/archive/estimate-report"
                "?vessel_name=MSC%20Lyria"
                "&vessel_type=contentores"
                "&gt=32540"
                "&stay_days=2"
                "&manoeuvres=entry,departure"
                "&surcharges=no_propulsion"
                "&reductions=regular_line"
                "&include_tup=1"
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Estimativa Formal de Pilotagem", html)
        self.assertIn("MSC Lyria", html)
        self.assertIn("Imprimir / PDF", html)

    def test_chat_message_feedback_api_returns_updated_label(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta operacional.",
            )
            response = client.post(
                f"/api/messages/{message['id']}/feedback",
                json={
                    "conversation_id": conversation["id"],
                    "feedback_status": "approved",
                    "feedback_note": "Boa resposta.",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["feedback_status"], "approved")
        self.assertEqual(payload["feedback_note"], "Boa resposta.")
        self.assertTrue(payload["feedback_updated_at"])
        self.assertTrue(payload["feedback_updated_at_label"])

    def test_chat_message_feedback_review_accepts_corrected_answer(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta operacional.",
                citations=[{"document": "IT-036_RegulacaoAgulhas.txt"}],
            )
            response = client.post(
                f"/api/messages/{message['id']}/feedback",
                json={
                    "conversation_id": conversation["id"],
                    "feedback_status": "review",
                    "feedback_note": "",
                    "feedback_correction": "A resposta correta deve mencionar o limite de 225 m.",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["feedback_status"], "review")
        self.assertEqual(
            payload["feedback_correction"],
            "A resposta correta deve mencionar o limite de 225 m.",
        )
        self.assertEqual(payload["feedback_correction_document"], "IT-036_RegulacaoAgulhas.txt")

    def test_chat_message_feedback_review_creates_operator_eval_case(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="Qual é a regra para compensação de agulhas dentro do Porto à noite?",
            )
            message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta incompleta.",
                citations=[{"document": "IT-036_RegulacaoAgulhas.txt"}],
            )
            response = client.post(
                f"/api/messages/{message['id']}/feedback",
                json={
                    "conversation_id": conversation["id"],
                    "feedback_status": "review",
                    "feedback_note": "Faltou o limite de LOA.",
                    "feedback_correction": "À noite a RA não se efetua com navios de LOA igual ou superior a 225 metros.",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = self.store.list_feedback_eval_cases()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["document"], "IT-036_RegulacaoAgulhas.txt")
        self.assertEqual(payload[0]["source"], "web")
        self.assertEqual(payload[0]["source_message_id"], message["id"])

    def test_chat_message_feedback_approved_removes_operator_eval_case(self) -> None:
        self.store.upsert_feedback_eval_case(
            source_message_id="msg-keep",
            document="IT-036_RegulacaoAgulhas.txt",
            question="Qual é a regra para compensação de agulhas dentro do Porto à noite?",
            expected_answer="À noite a RA não se efetua com navios de LOA igual ou superior a 225 metros.",
            expected_substrings=["225 metros"],
            source="web",
        )

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="Qual é a regra para compensação de agulhas dentro do Porto à noite?",
            )
            message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta corrigida.",
                citations=[{"document": "IT-036_RegulacaoAgulhas.txt"}],
            )
            self.store.update_message_feedback(
                "admin",
                conversation["id"],
                message["id"],
                "review",
                "Faltou o limite de LOA.",
                feedback_correction="À noite a RA não se efetua com navios de LOA igual ou superior a 225 metros.",
                feedback_updated_by="admin",
            )
            response = client.post(
                f"/api/messages/{message['id']}/feedback",
                json={
                    "conversation_id": conversation["id"],
                    "feedback_status": "approved",
                    "feedback_note": "Agora está certo.",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = self.store.list_feedback_eval_cases()
        self.assertEqual(payload, [])

    def test_chat_message_feedback_review_requires_note(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta operacional.",
            )
            response = client.post(
                f"/api/messages/{message['id']}/feedback",
                json={
                    "conversation_id": conversation["id"],
                    "feedback_status": "review",
                    "feedback_note": "",
                },
            )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("resposta corrigida", payload["error"].lower())

    def test_repeat_question_uses_reviewed_correction_when_available(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="qual é a distancia da barra ao outão?",
            )
            reviewed_message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="A distância da saída da Barra até ao Outão é de 3,00 milhas náuticas.",
            )
            self.store.update_message_feedback(
                "admin",
                conversation["id"],
                reviewed_message["id"],
                "review",
                "Valor anterior incorreto.",
                feedback_correction="A distância da saída da Barra até ao Outão é de 3,23 milhas náuticas.",
                feedback_updated_by="admin",
            )

            with patch.object(services.rag, "answer") as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "qual é a distancia da barra ao outão?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "review_correction_memory")
        self.assertIn("3,23 milhas náuticas", payload["answer"])
        answer_mock.assert_not_called()

    def test_reviewed_correction_overrides_specific_document_companion_when_same_rule_was_fixed(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="Qual é o comprimento máximo que um navio pode manobrar durante noite na LISNAVE?",
            )
            reviewed_message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Não. Navios com comprimento superior a 280 metros só podem manobrar durante o dia.",
                citations=[{"document": "IT-014_Lisnave.txt"}],
            )
            self.store.update_message_feedback(
                "admin",
                conversation["id"],
                reviewed_message["id"],
                "review",
                "Faltava responder ao limite máximo pedido.",
                feedback_correction=(
                    "280 metros. Na LISNAVE, navios com LOA até 280 metros podem manobrar "
                    "de noite; acima disso, as manobras ficam limitadas ao período diurno."
                ),
                feedback_correction_document="IT-014_Lisnave.txt",
                feedback_updated_by="admin",
            )

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "Qual é o comprimento máximo que um navio pode manobrar durante noite na LISNAVE?",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "review_correction_memory")
        self.assertIn("280 metros", payload["answer"])
        self.assertIn("período diurno", payload["answer"])

    def test_legacy_review_correction_commentary_is_normalized_before_reuse(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="Qual é o comprimento máximo que um navio pode manobrar durante noite na LISNAVE?",
            )
            reviewed_message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Não. Navios com comprimento superior a 280 metros só podem manobrar durante o dia.",
                citations=[{"document": "IT-014_Lisnave.txt"}],
            )
            self.store.update_message_feedback(
                "admin",
                conversation["id"],
                reviewed_message["id"],
                "review",
                "A formulação estava enviesada.",
                feedback_correction="280 m.",
                feedback_correction_document="IT-014_Lisnave.txt",
                feedback_updated_by="admin",
            )

            messages = self.store._read_messages()
            for item in messages:
                if item["id"] == reviewed_message["id"]:
                    item["feedback_correction"] = (
                        "Resposta está correta mas eu não especifiquei nenhum comprimento para dizeres não. "
                        "Mas sim o comprimento máximo de um navio permitido para manobrar à noite na LISNAVE e 280 m."
                    )
                    break
            self.store._write_messages(messages)

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "Qual é o comprimento máximo que um navio pode manobrar durante noite na LISNAVE?",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "review_correction_memory")
        self.assertEqual(
            payload["answer"],
            "O comprimento máximo de um navio permitido para manobrar à noite na LISNAVE é 280 m.",
        )

    def test_chat_weather_and_tides_question_prefers_live_operational_answer(self) -> None:
        with patch.object(services, "tide_service", _StubTideService()):
            with patch.object(services, "weather_service", _StubWeatherService()):
                with patch.object(services.rag, "answer") as answer_mock:
                    with app.app.test_client() as client:
                        with client.session_transaction() as flask_session:
                            flask_session["username"] = "admin"
                            flask_session["role"] = "admin"

                        response = client.post(
                            "/api/chat",
                            json={
                                "question": "Quais são as marés para hoje e as condições meteorológicas?",
                            },
                        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Marés para 09/04/2026", payload["answer"])
        self.assertIn("Condições meteorológicas atuais em Setúbal", payload["answer"])
        self.assertIn("Parcialmente nublado", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_current_weather_follow_up_prefers_live_operational_answer(self) -> None:
        with patch.object(services, "weather_service", _StubWeatherService()):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={
                            "question": "E as condições meteorológicas atuais no porto?",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Condições meteorológicas atuais em Setúbal", payload["answer"])
        self.assertIn("vento: 12.0 kts de nw", payload["answer"].lower())
        answer_mock.assert_not_called()

    def test_chat_weather_timeline_without_current_marker_uses_forecast_horizon(self) -> None:
        with patch.object(services, "weather_service", _StubWeatherService()):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={
                            "question": "Como vai estar a meteorologia no porto até às 00:00 dia 10 abril?",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Evolução prevista até 10/04/2026 00:00", payload["answer"])
        self.assertNotIn("Condições meteorológicas atuais", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_weather_current_with_horizon_returns_timeline_not_only_snapshot(self) -> None:
        with patch.object(services, "weather_service", _StubWeatherService()):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={
                            "question": "E as condições meteorológicas atuais no porto ao longo do dia (até as 00:00 dia 10 abril)?",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Evolução prevista até 10/04/2026 00:00", payload["answer"])
        self.assertIn("09/04/2026 18:00", payload["answer"])
        self.assertIn("10/04/2026 00:00", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_wave_question_prefers_live_operational_answer(self) -> None:
        wave_service = _StubWaveService(
            probe_payload={
                "last_reading_label": "09/04/2026, 13:10",
                "significant_height_label": "1.25m",
                "max_height_label": "2.10m",
                "mean_period_label": "7.8s",
                "max_observed_period_label": "9.4s",
                "direction": "W",
                "water_temp_label": "16.8°C",
            }
        )
        with patch.object(services, "wave_service", wave_service):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={
                            "question": "Como está a ondulação na barra neste momento?",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Leitura costeira atual", payload["answer"])
        self.assertIn("Altura significativa: 1.25m", payload["answer"])
        self.assertIn("Direção da ondulação: W", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_local_warnings_question_prefers_live_operational_answer(self) -> None:
        warnings = [
            {
                "display_code": "Anav nr 102",
                "subject": "Corrente forte na barra",
                "location": "Barra de Setúbal",
            },
            {
                "display_code": "Anav nr 103",
                "subject": "Trabalhos subaquáticos",
                "location": "Canal Norte",
            },
        ]
        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={
                            "question": "Quais são os avisos locais em vigor?",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Avisos locais em vigor", payload["answer"])
        self.assertIn("Anav nr 102", payload["answer"])
        self.assertIn("Corrente forte na barra", payload["answer"])
        answer_mock.assert_not_called()

    def test_repeat_question_with_reviewed_feedback_is_blocked_before_llm(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="qual é a distancia da barra ao outão?",
            )
            reviewed_message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="A distância da saída da Barra até ao Outão é de 3,00 milhas náuticas.",
            )
            self.store.update_message_feedback(
                "admin",
                conversation["id"],
                reviewed_message["id"],
                "review",
                "3,23 milhas náuticas = 5982 m",
            )

            with patch.object(services.rag, "answer") as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "qual é a distancia da barra ao outão?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "review_guard")
        self.assertIn("marcada para revisão", payload["answer"].lower())
        self.assertIn("3,23 milhas náuticas", payload["answer"])
        answer_mock.assert_not_called()

    def test_slash_rules_lists_available_rule_codes(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/regras",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rule")
        self.assertIn("015", payload["answer"])
        self.assertIn("IT-015 Fundeadouros", payload["answer"])
        self.assertIn("062", payload["answer"])
        self.assertNotIn("013", payload["answer"])

    def test_slash_rule_without_code_lists_available_rule_codes(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/regra",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rule")
        self.assertIn("Regras/instruções disponíveis", payload["answer"])
        self.assertIn("/regra 015", payload["answer"])

    def test_slash_rule_for_missing_code_returns_catalog(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/regra 013",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rule")
        self.assertIn("não encontrei a regra 013", payload["answer"].lower())
        self.assertIn("IT-015 Fundeadouros", payload["answer"])

    def test_chat_explicit_rule_code_targets_matching_knowledge_document(self) -> None:
        knowledge_path = Path(self.store.knowledge_dir) / "IT-036_RegulacaoAgulhas.txt"
        knowledge_path.write_text(
            (
                "DOCUMENTO: IT-036 — REGULAÇÃO DE AGULHAS\n"
                "Pergunta: Qual é a regra para compensação de agulhas dentro do Porto à noite?\n"
                "Resposta: À noite, a regulação de agulhas não se efetua com navios de LOA superior a 225 metros.\n"
                "Período noturno:\n"
                "RA permitida: LOA < 225 m.\n"
                "RA proibida: LOA >= 225 m.\n"
            ),
            encoding="utf-8",
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Resume a regra IT-36 sobre regulação de agulhas à noite.",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion")
        self.assertIn("LOA superior a 225 metros", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_rule_question_about_agulhas_targets_matching_knowledge_document(self) -> None:
        knowledge_path = Path(self.store.knowledge_dir) / "IT-036_RegulacaoAgulhas.txt"
        knowledge_path.write_text(
            (
                "DOCUMENTO: IT-036 — REGULAÇÃO DE AGULHAS\n"
                "Pergunta: Qual é a regra para compensação de agulhas dentro do Porto à noite?\n"
                "Resposta: À noite, a regulação de agulhas não se efetua com navios de LOA superior a 225 metros.\n"
            ),
            encoding="utf-8",
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Qual é a regra para compensação de agulhas dentro do Porto à noite?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion")
        self.assertIn("LOA superior a 225 metros", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_document_follow_up_reuses_last_cited_knowledge_document(self) -> None:
        knowledge_path = Path(self.store.knowledge_dir) / "IT-036_RegulacaoAgulhas.txt"
        knowledge_path.write_text(
            (
                "DOCUMENTO: IT-036 — REGULAÇÃO DE AGULHAS\n"
                "Pergunta: Qual é a regra para compensação de agulhas dentro do Porto à noite?\n"
                "Resposta: À noite, a regulação de agulhas não se efetua com navios de LOA superior a 225 metros.\n"
            ),
            encoding="utf-8",
        )
        self.store.list_documents()

        conversation = self.store.ensure_conversation(username="admin")
        self.store.append_chat_message(
            username="admin",
            conversation_id=conversation["id"],
            role="assistant",
            content="A regra está no IT-036.",
            citations=[
                {
                    "source_id": "S1",
                    "document": "IT-036_RegulacaoAgulhas.txt",
                    "chunk_id": 1,
                    "score": 0.99,
                    "retrieval_mode": "semantic",
                    "snippet": "Não se efetua RA de noite com navios de LOA superior a 225 metros.",
                }
            ],
        )

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Diz me o que diz esse documento sff",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion")
        self.assertIn("LOA superior a 225 metros", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_document_companion_answers_without_llm(self) -> None:
        document_name = "IT-036_RegulacaoAgulhas.txt"
        knowledge_path = Path(self.store.knowledge_dir) / document_name
        knowledge_path.write_text(
            "DOCUMENTO: IT-036 — REGULAÇÃO DE AGULHAS\nNão se efetua RA de noite com navios de LOA superior a 225 metros.\n",
            encoding="utf-8",
        )
        self._write_knowledge_companion(
            document_name,
            {
                "document": document_name,
                "title": "IT-036 Regulação de Agulhas",
                "aliases": ["IT-036", "IT-36", "compensação de agulhas"],
                "summary": "resume as condições operacionais da regulação de agulhas no Porto de Setúbal.",
                "key_points": [
                    "De noite a operação é proibida para LOA superior a 225 m."
                ],
                "faq": [
                    {
                        "question": "Qual é a regra para compensação de agulhas dentro do Porto à noite?",
                        "answer": "À noite, a regulação de agulhas não se efetua com navios de LOA superior a 225 metros.",
                        "keywords": ["agulhas", "noite", "compensação de agulhas"]
                    }
                ]
            },
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Qual é a regra para compensação de agulhas dentro do Porto à noite?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion")
        self.assertIn("LOA superior a 225 metros", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_document_companion_bypasses_review_guard_when_grounded_answer_exists(self) -> None:
        document_name = "IT-036_RegulacaoAgulhas.txt"
        knowledge_path = Path(self.store.knowledge_dir) / document_name
        knowledge_path.write_text(
            "DOCUMENTO: IT-036 — REGULAÇÃO DE AGULHAS\nNão se efetua RA de noite com navios de LOA superior a 225 metros.\n",
            encoding="utf-8",
        )
        self._write_knowledge_companion(
            document_name,
            {
                "document": document_name,
                "title": "IT-036 Regulação de Agulhas",
                "aliases": ["IT-036", "IT-36", "regulação de agulhas"],
                "summary": "resume as condições operacionais da regulação de agulhas no Porto de Setúbal.",
                "key_points": [
                    "De noite a RA não se efetua com LOA igual ou superior a 225 m."
                ],
                "faq": [
                    {
                        "question": "Qual é a regra para compensação de agulhas dentro do Porto à noite?",
                        "answer": "De noite, a RA não se efetua com navios de LOA igual ou superior a 225 metros. Abaixo desse limite continuam a aplicar-se as restantes condições de maré e espaço livre.",
                        "keywords": ["agulhas", "noite", "loa", "maré"]
                    }
                ]
            },
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="user",
                content="Qual é a regra para compensação de agulhas dentro do Porto à noite?",
            )
            reviewed_message = self.store.append_chat_message(
                username="admin",
                conversation_id=conversation["id"],
                role="assistant",
                content="Resposta antiga errada.",
            )
            self.store.update_message_feedback(
                "admin",
                conversation["id"],
                reviewed_message["id"],
                "review",
                "Feedback via reação WhatsApp: 👎",
            )

            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Qual é a regra para compensação de agulhas dentro do Porto à noite?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion")
        self.assertIn("LOA igual ou superior a 225 metros", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_approved_feedback_can_hint_target_document_for_summary_request(self) -> None:
        document_name = "IT-099_XQJ.txt"
        knowledge_path = Path(self.store.knowledge_dir) / document_name
        knowledge_path.write_text(
            "DOCUMENTO: IT-099 — XQJ\nRegra noturna: requer autorização expressa do Piloto Coordenador.\n",
            encoding="utf-8",
        )
        self._write_knowledge_companion(
            document_name,
            {
                "document": document_name,
                "title": "IT-099 XQJ",
                "aliases": ["IT-099", "IT-99", "XQJ"],
                "summary": "a operação XQJ à noite exige autorização expressa do Piloto Coordenador.",
                "key_points": [
                    "A operação noturna só avança com autorização expressa do Piloto Coordenador."
                ],
                "faq": []
            },
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            prior_conversation = self.store.ensure_conversation(username="admin")
            self.store.append_chat_message(
                username="admin",
                conversation_id=prior_conversation["id"],
                role="user",
                content="Explica as limitações dessa manobra à noite.",
            )
            approved_message = self.store.append_chat_message(
                username="admin",
                conversation_id=prior_conversation["id"],
                role="assistant",
                content="À noite, a operação XQJ exige autorização expressa do Piloto Coordenador.",
                citations=[
                    {
                        "source_id": "S1",
                        "document": document_name,
                        "chunk_id": 1,
                        "score": 0.99,
                        "retrieval_mode": "document_companion",
                        "snippet": "A operação XQJ à noite exige autorização expressa do Piloto Coordenador.",
                    }
                ],
            )
            self.store.update_message_feedback(
                "admin",
                prior_conversation["id"],
                approved_message["id"],
                "approved",
                "Resposta validada pelo operador.",
            )

            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Explica as limitações dessa manobra à noite.",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion")
        self.assertIn("autorização expressa do Piloto Coordenador", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_document_companion_summarizes_targeted_document_request(self) -> None:
        document_name = "IT-036_RegulacaoAgulhas.txt"
        knowledge_path = Path(self.store.knowledge_dir) / document_name
        knowledge_path.write_text(
            "DOCUMENTO: IT-036 — REGULAÇÃO DE AGULHAS\nResumo operacional.\n",
            encoding="utf-8",
        )
        self._write_knowledge_companion(
            document_name,
            {
                "document": document_name,
                "title": "IT-036 Regulação de Agulhas",
                "aliases": ["IT-036", "IT-36", "Regulação de Agulhas"],
                "summary": "a operação realiza-se nos fundeadouros e depende do LOA, da maré e do espaço livre.",
                "key_points": [
                    "Nos repontos de maré o limite é LOA inferior a 250 m.",
                    "Com corrente em marés mortas o limite é LOA inferior a 225 m.",
                    "De noite o limite crítico é LOA superior a 225 m."
                ],
                "faq": []
            },
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Diz me o que diz o IT-036 sff",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion")
        self.assertIn("Segundo o IT-036 Regulação de Agulhas", payload["answer"])
        self.assertIn("Pontos principais", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_lisnave_distance_question_prefers_operational_notes_companion(self) -> None:
        lisnave_document = "IT-014_Lisnave.txt"
        notas_document = "Notas_Pilotagem.txt"
        (Path(self.store.knowledge_dir) / lisnave_document).write_text(
            (
                "DOCUMENTO: IT-014 — ESTALEIRO LISNAVE\n"
                "Distância do Duque d'Alba da Ponte-Cais I até à boia número 14: 1000 metros.\n"
            ),
            encoding="utf-8",
        )
        (Path(self.store.knowledge_dir) / notas_document).write_text(
            (
                "DISTÂNCIAS TOTAIS DE PERCURSO\n"
                "Pilar nº 2 → LISNAVE (Estaleiros Mitrena, Canal Sul completo): 10,5 milhas náuticas.\n"
                "Pilar nº 2 → LISNAVE (com corta-mato / atalho): 10,0 milhas náuticas.\n"
            ),
            encoding="utf-8",
        )
        self._write_knowledge_companion(
            notas_document,
            {
                "document": notas_document,
                "title": "NOTAS OPERACIONAIS DE PILOTAGEM — PORTO DE SETÚBAL",
                "aliases": [
                    "Notas_Pilotagem",
                    "distancia barra lisnave",
                    "entrada da barra lisnave",
                    "pilar 2 lisnave",
                ],
                "summary": "distâncias totais de percurso usadas para planeamento de tempo.",
                "key_points": [
                    "Da entrada da Barra até à LISNAVE são 10,5 NM pelo Canal Sul completo."
                ],
                "faq": [
                    {
                        "question": "Qual é a distância da entrada da Barra até ao estaleiro da LISNAVE?",
                        "answer": "Tomando como referência operacional o Pilar n.º 2 da Barra, até à LISNAVE são 10,5 milhas náuticas pelo Canal Sul completo e cerca de 10,0 milhas pelo atalho.",
                        "keywords": [
                            "distancia",
                            "barra",
                            "lisnave",
                            "estaleiro",
                            "pilar 2",
                            "milhas nauticas",
                        ],
                    }
                ],
            },
        )
        self.store.list_documents()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Qual é a distância da entrada da Barra até ao estaleiro da LISNAVE?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "document_companion_global")
        self.assertIn("10,5", payload["answer"])
        self.assertIn("10,0", payload["answer"])
        self.assertNotIn("1000 metros", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_mixed_live_and_document_question_uses_llm_with_planned_sources(self) -> None:
        document_name = "IT-036_RegulacaoAgulhas.txt"
        knowledge_path = Path(self.store.knowledge_dir) / document_name
        knowledge_path.write_text(
            "DOCUMENTO: IT-036 — REGULAÇÃO DE AGULHAS\nDe noite, a RA não se efetua com LOA igual ou superior a 225 metros.\n",
            encoding="utf-8",
        )
        self._write_knowledge_companion(
            document_name,
            {
                "document": document_name,
                "title": "IT-036 Regulação de Agulhas",
                "aliases": ["IT-036", "IT-36", "regulação de agulhas"],
                "summary": "define os limites de LOA, maré e espaço livre para a regulação de agulhas.",
                "key_points": [
                    "De noite a RA não se efetua com LOA igual ou superior a 225 metros."
                ],
                "faq": [
                    {
                        "question": "O que diz o IT-036 sobre regulação de agulhas à noite?",
                        "answer": "De noite, a RA não se efetua com navios de LOA igual ou superior a 225 metros.",
                        "keywords": ["IT-036", "agulhas", "noite"],
                    }
                ],
            },
        )
        self.store.list_documents()

        with patch.object(services, "tide_service", _StubTideService()):
            with app.app.test_client() as client:
                self._login_client_as_admin(client)
                conversation = self.store.ensure_conversation(username="admin")
                with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                    services.rag,
                    "answer",
                    return_value={"answer": "Resposta combinada.", "sources": []},
                ) as answer_mock:
                    response = client.post(
                        "/api/chat",
                        json={
                            "conversation_id": conversation["id"],
                            "question": "Quais são as marés para hoje e o que diz o IT-036 sobre regulação de agulhas à noite?",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        answer_mock.assert_called_once()
        supplemental_sources = answer_mock.call_args.kwargs["supplemental_sources"]
        documents = {str(item.get("document") or "") for item in supplemental_sources}
        retrieval_modes = {str(item.get("retrieval_mode") or "") for item in supplemental_sources}
        self.assertIn("Marés live", documents)
        self.assertIn(document_name, documents)
        self.assertIn("live_planner", retrieval_modes)
        self.assertIn("document_companion", retrieval_modes)

    def test_chat_weather_followup_about_tug_sufficiency_uses_llm_with_history(self) -> None:
        with patch.object(services, "weather_service", _StubWeatherService()):
            with app.app.test_client() as client:
                self._login_client_as_admin(client)
                conversation = self.store.ensure_conversation(username="admin")
                self.store.append_chat_message(
                    username="admin",
                    conversation_id=conversation["id"],
                    role="user",
                    content="Vai entrar agora um navio roro com 176 m comprimento e 8,3m de calado. Quantos reboques me recomendarias?",
                )
                self.store.append_chat_message(
                    username="admin",
                    conversation_id=conversation["id"],
                    role="assistant",
                    content="Recomendaria 2 rebocadores pequenos, salvo confirmação adicional do vento atual e dos thrusters.",
                )

                with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                    services.rag,
                    "answer",
                    return_value={"answer": "Com o vento atual, dois rebocadores parecem suficientes.", "sources": []},
                ) as answer_mock:
                    response = client.post(
                        "/api/chat",
                        json={
                            "conversation_id": conversation["id"],
                            "question": "Avalia o vento que está atualmente em porto e diz me se os dois reboques são suficientes.",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        self.assertIn("dois rebocadores", payload["answer"])
        answer_mock.assert_called_once()
        supplemental_sources = answer_mock.call_args.kwargs["supplemental_sources"]
        documents = {str(item.get("document") or "") for item in supplemental_sources}
        retrieval_modes = {str(item.get("retrieval_mode") or "") for item in supplemental_sources}
        history = answer_mock.call_args.kwargs["history"]
        conversation_state = answer_mock.call_args.kwargs["conversation_state"]
        execution_plan = answer_mock.call_args.kwargs["execution_plan"]
        self.assertIn("Meteorologia live", documents)
        self.assertIn("Estado conversacional", documents)
        self.assertIn("conversation_state", retrieval_modes)
        self.assertEqual(execution_plan["primary_intent"], "live_reasoning")
        self.assertTrue(execution_plan["needs_answer_critic"])
        self.assertIn("Ro-Ro", conversation_state["summary"])
        self.assertIn("2 rebocador", conversation_state["summary"])
        self.assertEqual(history[-1]["content"], "Avalia o vento que está atualmente em porto e diz me se os dois reboques são suficientes.")
        self.assertEqual(history[-2]["role"], "assistant")
        self.assertIn("2 rebocadores", history[-2]["content"])

    def test_chat_ok_does_not_confirm_pending_report_with_missing_target(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "/registar-manobra Tipo de manobra: Entrada Início da manobra: 29/03/2026, 07:45 Fim da manobra: 29/03/2026, 08:30 Calado: 9,8 m Observações: 2 rebocadores",
                },
            )

            first_payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(first_payload["answer_origin"], "slash_template")
            self.assertIn("ref ou nome do navio", first_payload["pending_action"]["proposal"]["missing_fields"])

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "ok",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["answer_origin"], "pending_action_block")
        self.assertIn("Ainda faltam dados obrigatórios", payload["answer"])
        self.assertIsNotNone(payload["pending_action"])

    def test_finalize_edit_plan_reuses_existing_planned_time(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "edit_maneuver_plan",
                "target": {
                    "reference_code": port_call["reference_code"],
                    "vessel_name": port_call["vessel_name"],
                    "maneuver_type": "entry",
                },
                "fields": {
                    "change_reason": "piloto disponivel",
                    "pier": "Tanquisado (lado jusante)",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["missing_fields"], [])
        self.assertEqual(finalized["fields"]["planned_at_local"], "2026-03-24T05:30")
        self.assertEqual(finalized["fields"]["destination"], "Tanquisado (lado jusante)")

    def test_finalize_edit_plan_accepts_scale_id_prefix_in_reference_field(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "edit_maneuver_plan",
                "target": {
                    "reference_code": port_call["id"][:8],
                    "maneuver_type": "entry",
                },
                "fields": {
                    "planned_at_local": "2026-03-24T06:00",
                    "change_reason": "ajuste de janela",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertEqual(finalized["intent"], "action")
        self.assertEqual(finalized["port_call_id"], port_call["id"])
        self.assertEqual(finalized["target"]["reference_code"], port_call["reference_code"])

    def test_pending_approve_replaces_previous_edit_plan(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            proposal = app.finalize_operational_proposal(
                normalize_action_candidate(
                    {
                        "intent": "action",
                        "action": "edit_maneuver_plan",
                        "port_call_id": port_call["id"],
                        "target": {
                            "reference_code": port_call["reference_code"],
                            "vessel_name": port_call["vessel_name"],
                            "maneuver_type": "entry",
                        },
                        "fields": {
                            "change_reason": "piloto disponivel",
                            "pier": "Tanquisado (lado jusante)",
                        },
                        "missing_fields": [],
                    },
                    "admin",
                ),
                [self.store.get_port_call(port_call["id"])],
            )
            app.save_pending_chat_action("admin", conversation["id"], proposal, "Mudar cais")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": "aprova",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_replace")
        self.assertEqual(payload["pending_action"]["proposal"]["action"], "approve_entry")
        self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

    def test_piloto_edit_plan_via_chat_is_rejected(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])
        self.store.create_user(
            "piloto-teste",
            "secret1",
            "piloto",
            full_name="Piloto Teste",
            organization="APSS",
            email="piloto@example.com",
            phone="+351912345678",
        )
        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        current = self.store.get_port_call(port_call["id"])
        shift = next(item for item in current["maneuver_history"] if item["type"] == "shift")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "piloto-teste"
                flask_session["role"] = "piloto"

            conversation = self.store.ensure_conversation(username="piloto-teste")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        f"/editar-manobra\n"
                        f"ID da manobra: {shift['id'][:8].upper()}\n"
                        f"Ref: {port_call['reference_code']}\n"
                        f"Tipo de manobra: mudança\n"
                        f"Hora prevista: 24/03/2026, 08:30\n"
                        f"Origem: TMS 2\n"
                        f"Destino: TMS 1\n"
                        f"Calado: 9,94\n"
                        f"Rebocadores: 2\n"
                        f"Restrições: daylight\n"
                        f"Observações: Aguarda rebocador"
                    ),
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_rejected")
        self.assertIn("não está autorizada", payload["answer"].lower())

    def test_abort_shift_via_chat_allows_approved_maneuver_after_planned_time_and_keeps_berth(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])
        self.store.create_user(
            "piloto-aborto",
            "secret1",
            "piloto",
            full_name="Piloto Aborto",
            organization="APSS",
            email="piloto-aborto@example.com",
            phone="+351912345679",
        )
        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2000-01-01T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")
        current = self.store.get_port_call(port_call["id"])
        shift = next(item for item in current["maneuver_history"] if item["type"] == "shift")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "piloto-aborto"
                flask_session["role"] = "piloto"

            conversation = self.store.ensure_conversation(username="piloto-aborto")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        f"/abortar\n"
                        f"ID da manobra: {shift['id'][:8].upper()}\n"
                        f"Ref: {port_call['reference_code']}\n"
                        f"Tipo de manobra: mudança\n"
                        f"Motivo: nevoeiro"
                    ),
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

        self.assertEqual(response.status_code, 200)
        updated = self.store.get_port_call(port_call["id"])
        snapshot = self.store.get_port_activity_snapshot(window_days=3650)
        archived_ids = {item["port_call_id"] for item in snapshot["archived_maneuvers"]}
        shift = next(item for item in updated["maneuver_history"] if item["type"] == "shift")
        self.assertEqual(shift["state"], "aborted")
        self.assertEqual(shift["aborted_reason"], "nevoeiro")
        self.assertEqual(updated["berth"], "TMS 2")
        self.assertIn(port_call["id"], archived_ids)

    def test_edit_plan_confirmation_rejects_stale_maneuver_id(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        current = self.store.get_port_call(port_call["id"])
        entry = next(item for item in current["maneuver_history"] if item["type"] == "entry")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "não identifica a manobra a editar"):
                app.execute_pending_operational_action(
                    {
                        "action": "edit_maneuver_plan",
                        "port_call_id": port_call["id"],
                        "maneuver_id": "stale-id",
                        "target": {"maneuver_type": "entry"},
                        "fields": {
                            "planned_at_local": entry["planned_at"][:16],
                            "change_reason": "piloto disponivel",
                            "berth": "Tanquisado (lado jusante)",
                        },
                    },
                    username="admin",
                    role="admin",
                )

    def test_finalize_shift_report_requires_maneuver_id_when_multiple_shifts_match(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")
        self.store.mark_shift_completed(port_call["id"], shifted_at="2026-03-24T08:20:00+00:00", updated_by="admin")

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T12:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 2",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")

        proposal = normalize_action_candidate(
            {
                "intent": "action",
                "action": "shift_report",
                "target": {
                    "reference_code": port_call["reference_code"],
                    "maneuver_type": "shift",
                },
                "fields": {
                    "maneuver_started_local": "2026-03-24T08:05",
                    "maneuver_finished_local": "2026-03-24T08:20",
                    "draft_m": "9.94",
                },
                "missing_fields": [],
            },
            "admin",
        )

        finalized = app.finalize_operational_proposal(proposal, [self.store.get_port_call(port_call["id"])])

        self.assertIn("ID da manobra", finalized["missing_fields"])

    def test_delete_shift_without_id_is_blocked_when_multiple_shifts_match(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")
        self._move_port_call_in_port(port_call["id"])

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T08:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 1",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")
        self.store.mark_shift_completed(port_call["id"], shifted_at="2026-03-24T08:20:00+00:00", updated_by="admin")

        self.store.schedule_shift_plan(
            port_call["id"],
            planned_shift_at="2026-03-24T12:00:00+00:00",
            updated_by="admin",
            destination_berth="TMS 2",
        )
        self.store.approve_shift_plan(port_call["id"], decided_by="admin")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            with self.assertRaisesRegex(ValueError, "Indica o ID da manobra"):
                app.execute_pending_operational_action(
                    {
                        "action": "delete_maneuver",
                        "port_call_id": port_call["id"],
                        "target": {"maneuver_type": "shift"},
                        "fields": {},
                    },
                    username="admin",
                    role="admin",
                )

    def test_passa_a_previsto_reports_already_pending_when_it_is_already_planned(self) -> None:
        port_call = self._create_entry(notes="Registo do agente · Entrada\nCalado: 9.94")

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            proposal = app.propose_operational_action(
                f"passa a manobra do {port_call['vessel_name']} a previsto",
                "admin",
            )

        self.assertEqual(proposal["intent"], "unsupported")
        self.assertIn("já está prevista", proposal["reason"])

    def test_operational_consultation_question_uses_llm_instead_of_action_template(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                services.rag,
                "answer",
                return_value={"answer": "Deve embarcar com antecedência suficiente para não perder a maré.", "sources": []},
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": (
                            "Sabendo que o preia-mar hoje é às 15:13, a que horas deve embarcar piloto "
                            "para trazer um navio para a Lisnave?"
                        ),
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        self.assertNotEqual(payload["answer_origin"], "operational_clarification")
        self.assertIn("antecedência suficiente", payload["answer"])
        answer_mock.assert_called_once()

    def test_operational_consultation_question_for_lisnave_dry_dock_uses_llm(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                services.rag,
                "answer",
                return_value={"answer": "Para a Doca 21, a resposta deve seguir a janela documental aplicável.", "sources": []},
            ) as answer_mock:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Sabendo isso, tenho um navio para entrar para a doca 21. A que horas podemos marcar piloto para hoje?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        self.assertNotEqual(payload["answer_origin"], "operational_clarification")
        self.assertIn("Doca 21", payload["answer"])
        answer_mock.assert_called_once()

    def test_plain_text_operational_command_redirects_to_slash_commands(self) -> None:
        port_call = self._create_entry(notes="Entrada pendente")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": f"Aprova a entrada da escala {port_call['reference_code']}.",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_redirect")
        self.assertIsNone(payload["pending_action"])
        self.assertIn("/aprovar", payload["answer"])
        self.assertIn("/validar-manobra", payload["answer"])

    def test_slash_validate_maneuver_returns_structured_validation(self) -> None:
        port_call = self._create_entry(notes="Entrada pendente")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            conversation = self.store.ensure_conversation(username="admin")
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": f"/validar-manobra {port_call['reference_code']} entrada",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_validation")
        self.assertIn("Validação da", payload["answer"])
        self.assertIn("Checklist operacional determinística do portal", payload["answer"])
        self.assertIn(port_call["reference_code"], payload["answer"])

    def test_widget_create_conversation_api_returns_widget_payload(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            response = client.post("/api/conversations")

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertIn("conversation", payload)
        self.assertIn("conversations", payload)
        self.assertEqual(payload["conversation"]["id"], payload["conversations"][0]["id"])
        self.assertEqual(payload["messages"], [])
        self.assertIsNone(payload["pending_action"])

    def test_widget_get_conversation_api_returns_selected_messages(self) -> None:
        first = self.store.create_conversation("admin", title="Primeira conversa")
        second = self.store.create_conversation("admin", title="Segunda conversa")
        self.store.append_chat_message("admin", second["id"], "user", "Estado da maré?")
        self.store.append_chat_message("admin", second["id"], "assistant", "Preia-mar às 15:13.")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            response = client.get(f"/api/conversations/{second['id']}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["conversation"]["id"], second["id"])
        self.assertEqual(payload["conversation"]["title"], "Estado da maré?")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["content"], "Estado da maré?")
        self.assertEqual(payload["messages"][1]["content"], "Preia-mar às 15:13.")
        self.assertEqual(payload["conversations"][0]["id"], second["id"])
        self.assertEqual(payload["conversations"][1]["id"], first["id"])

    def test_widget_rename_conversation_api_updates_title(self) -> None:
        conversation = self.store.create_conversation("admin")

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            response = client.post(
                f"/api/conversations/{conversation['id']}/rename",
                json={"title": "Janela de marés Lisnave"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["conversation"]["id"], conversation["id"])
        self.assertEqual(payload["conversation"]["title"], "Janela de marés Lisnave")
        self.assertEqual(payload["conversations"][0]["title"], "Janela de marés Lisnave")
        self.assertIsNone(payload["pending_action"])

    def test_chat_archive_page_renders_compact_export_actions(self) -> None:
        conversation = self.store.create_conversation("admin", title="Arquivo operacional")

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(f"/conversations?conversation_id={conversation['id']}")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Exportar", html)
        self.assertIn("export.txt", html)
        self.assertIn("export.pdf", html)
        self.assertIn("Nova", html)

    def test_conversation_export_txt_returns_plain_text_transcript(self) -> None:
        conversation = self.store.create_conversation("admin", title="Janela de marés")
        self.store.append_chat_message("admin", conversation["id"], "user", "Qual é a maré?")
        self.store.append_chat_message("admin", conversation["id"], "assistant", "Preia-mar às 15:13.")

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(f"/conversations/{conversation['id']}/export.txt")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.mimetype)
        body = response.get_data(as_text=True)
        self.assertIn("Conversa:", body)
        self.assertIn("Qual é a maré?", body)
        self.assertIn("Preia-mar às 15:13.", body)

    def test_conversation_export_pdf_returns_pdf_bytes(self) -> None:
        conversation = self.store.create_conversation("admin", title="Estado operacional")
        self.store.append_chat_message("admin", conversation["id"], "user", "Resumo do porto?")
        self.store.append_chat_message("admin", conversation["id"], "assistant", "Duas entradas e uma saída.")

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            response = client.get(f"/conversations/{conversation['id']}/export.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF-1.4"))

    def test_local_warnings_report_txt_respects_filters_and_selection(self) -> None:
        warnings = [
            {
                "id": 101,
                "display_code": "Anav nr 101",
                "subject": "Dragagem no canal",
                "location": "Canal Norte",
                "description_text": "Operação de dragagem até novo aviso.",
                "excerpt": "Operação de dragagem até novo aviso.",
                "status_label": "Em vigor",
                "start_date_label": "03 abr 2026",
                "end_date_label": "10 abr 2026",
                "start_date_iso": "2026-04-03T08:00:00+00:00",
                "end_date_iso": "2026-04-10T17:00:00+00:00",
                "has_attachments": False,
                "attachments": [],
            },
            {
                "id": 102,
                "display_code": "Anav nr 102",
                "subject": "Sondagens na barra",
                "location": "Barra",
                "description_text": "Sondagens hidrográficas com embarcação de apoio.",
                "excerpt": "Sondagens hidrográficas com embarcação de apoio.",
                "status_label": "Em vigor",
                "start_date_label": "04 abr 2026",
                "end_date_label": "12 abr 2026",
                "start_date_iso": "2026-04-04T08:00:00+00:00",
                "end_date_iso": "2026-04-12T17:00:00+00:00",
                "has_attachments": True,
                "attachments": [{"name": "Croqui", "url": "https://example.test/croqui.pdf"}],
            },
        ]

        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            with app.app.test_client() as client:
                self._login_client_as_admin(client)
                response = client.get("/warnings/local/report.txt?q=sondagens&warning_ids=102")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response.mimetype)
        body = response.get_data(as_text=True)
        self.assertIn("Anav nr 102", body)
        self.assertNotIn("Anav nr 101", body)
        self.assertIn("Croqui", body)

    def test_local_warnings_report_pdf_returns_pdf_bytes(self) -> None:
        warnings = [
            {
                "id": 201,
                "display_code": "Anav nr 201",
                "subject": "Balizagem temporária",
                "location": "Canal Sul",
                "description_text": "Balizagem provisória em vigor.",
                "excerpt": "Balizagem provisória em vigor.",
                "status_label": "Em vigor",
                "start_date_label": "03 abr 2026",
                "end_date_label": "05 abr 2026",
                "start_date_iso": "2026-04-03T08:00:00+00:00",
                "end_date_iso": "2026-04-05T17:00:00+00:00",
                "has_attachments": False,
                "attachments": [],
            },
        ]

        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            with app.app.test_client() as client:
                self._login_client_as_admin(client)
                response = client.get("/warnings/local/report.pdf")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertTrue(response.data.startswith(b"%PDF-1.4"))

    def test_local_warnings_page_renders_selection_actions(self) -> None:
        warnings = [
            {
                "id": 301,
                "display_code": "Anav nr 301",
                "subject": "Corrente forte na barra",
                "location": "Barra",
                "description_text": "Corrente forte com recomendação de prudência.",
                "excerpt": "Corrente forte com recomendação de prudência.",
                "status_label": "Em vigor",
                "start_date_label": "03 abr 2026",
                "end_date_label": "06 abr 2026",
                "start_date_iso": "2026-04-03T08:00:00+00:00",
                "end_date_iso": "2026-04-06T17:00:00+00:00",
                "has_attachments": False,
                "attachments": [],
            },
        ]

        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            with app.app.test_client() as client:
                self._login_client_as_admin(client)
                response = client.get("/warnings/local")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Selecionar tudo filtrado", html)
        self.assertIn("warnings-export-pdf", html)

    def test_local_warnings_page_shows_offline_when_source_probe_fails(self) -> None:
        warning_service = _StubLocalWarningService(
            [],
            error="Ligação ao Instituto Hidrográfico recusada neste ambiente.",
            cache_updated_at_label="",
            last_attempt_at_label="03/04/2026, 18:22",
            probe_error="Ligação ao Instituto Hidrográfico recusada neste ambiente.",
        )

        with patch.object(services, "local_warning_service", warning_service):
            with app.app.test_client() as client:
                self._login_client_as_admin(client)
                response = client.get("/warnings/local")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Indisponível", html)
        self.assertIn("Ligação ao Instituto Hidrográfico recusada neste ambiente.", html)
        self.assertNotIn("sem cache local", html)

    def test_admin_status_page_uses_clear_operational_labels(self) -> None:
        warning_service = _StubLocalWarningService(
            [],
            error="Ligação ao Instituto Hidrográfico recusada neste ambiente.",
            cache_updated_at_label="",
            last_attempt_at_label="03/04/2026, 18:35",
            probe_error="Ligação ao Instituto Hidrográfico recusada neste ambiente.",
        )
        wave_service = _StubWaveService(
            error="Ligação ao Instituto Hidrográfico recusada neste ambiente.",
            cache_updated_at_label="",
            last_attempt_at_label="03/04/2026, 18:35",
            probe_error="Ligação ao Instituto Hidrográfico recusada neste ambiente.",
        )

        with patch.object(services, "local_warning_service", warning_service):
            with patch.object(services, "wave_service", wave_service):
                with app.app.test_client() as client:
                    self._login_client_as_admin(client)
                    response = client.get("/admin/status")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Estado da plataforma", html)
        self.assertIn("Embed web público", html)
        self.assertIn("Indisponível", html)
        self.assertIn("Última tentativa 03/04/2026, 18:35", html)
        self.assertIn("Ligação ao Instituto Hidrográfico recusada neste ambiente.", html)
        self.assertNotIn("Ir para utilizadores", html)
        self.assertNotIn("estado sem cache local", html)
        self.assertNotIn("sem API", html)

    def test_whatsapp_webhook_verification_returns_challenge(self) -> None:
        whatsapp_service = _StubWhatsAppService()

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with app.app.test_client() as client:
                response = client.get(
                    "/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=test-token&hub.challenge=abc123"
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "abc123")

    def test_whatsapp_webhook_receives_text_and_sends_test_reply(self) -> None:
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.TEST123",
                                        "from": "351962063664",
                                        "timestamp": "1712165400",
                                        "type": "text",
                                        "text": {"body": "teste"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with patch("core.chat_runtime.refresh_knowledge_state") as mock_refresh:
                with patch("core.chat_runtime.answer_direct_operational_query") as mock_answer_direct:
                    mock_refresh.return_value = None
                    mock_answer_direct.return_value = {
                        "answer": "Resposta do bot",
                        "sources": [],
                        "answer_origin": "operational_lookup",
                    }
                    with app.app.test_client() as client:
                        response = client.post("/webhooks/whatsapp", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 1)
        self.assertEqual(whatsapp_service.sent_messages[0]["to_number"], "351962063664")
        self.assertEqual(whatsapp_service.sent_messages[0]["text"], "Resposta do bot")
        self.assertEqual(whatsapp_service.sent_messages[0]["reply_to_message_id"], "wamid.TEST123")
        username = "whatsapp-351962063664@pragtico.local"
        conversations = self.store.list_conversations(username)
        self.assertEqual(len(conversations), 1)
        messages = self.store.list_messages(username, conversations[0]["id"])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["external_message_id"], "wamid.TEST123")
        self.assertEqual(messages[0]["channel"], "whatsapp")
        self.assertEqual(messages[1]["external_message_id"], "wamid.REPLY123")
        self.assertEqual(messages[1]["external_reply_to_id"], "wamid.TEST123")
        self.assertEqual(messages[1]["channel_metadata"]["last_status"], "accepted")
        channel_events = self.store._read_channel_events()
        self.assertEqual(len(channel_events), 2)
        self.assertEqual(channel_events[0]["event_type"], "incoming_text")
        self.assertEqual(channel_events[1]["event_type"], "outgoing_text")

    def test_whatsapp_webhook_sends_welcome_only_once_per_contact(self) -> None:
        whatsapp_service = _StubWhatsAppService(
            allowed_numbers={"351962063664"},
            welcome_enabled=True,
            welcome_message="👋 Bem-vindo ao PRAGtico\n\nEm que posso ajudar?",
        )
        first_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.TEST123",
                                        "from": "351962063664",
                                        "timestamp": "1712165400",
                                        "type": "text",
                                        "text": {"body": "olá"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        second_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.TEST124",
                                        "from": "351962063664",
                                        "timestamp": "1712165401",
                                        "type": "text",
                                        "text": {"body": "qual é a maré?"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with patch("core.chat_runtime.refresh_knowledge_state") as mock_refresh:
                with patch("core.chat_runtime.answer_direct_operational_query") as mock_answer_direct:
                    mock_refresh.return_value = None
                    mock_answer_direct.return_value = {
                        "answer": "Resposta do bot",
                        "sources": [],
                        "answer_origin": "operational_lookup",
                    }
                    with app.app.test_client() as client:
                        first = client.post("/webhooks/whatsapp", json=first_payload)
                        second = client.post("/webhooks/whatsapp", json=second_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 3)
        self.assertEqual(
            [item["text"] for item in whatsapp_service.sent_messages],
            [
                "👋 Bem-vindo ao PRAGtico\n\nEm que posso ajudar?",
                "Resposta do bot",
                "Resposta do bot",
            ],
        )
        username = "whatsapp-351962063664@pragtico.local"
        conversations = self.store.list_conversations(username)
        self.assertEqual(len(conversations), 1)
        messages = self.store.list_messages(username, conversations[0]["id"])
        self.assertEqual(len(messages), 5)
        self.assertEqual(messages[1]["channel_metadata"]["message_kind"], "welcome")
        channel_events = self.store._read_channel_events()
        self.assertEqual(
            [item["event_type"] for item in channel_events],
            [
                "incoming_text",
                "outgoing_welcome",
                "outgoing_text",
                "incoming_text",
                "outgoing_text",
            ],
        )

    def test_whatsapp_webhook_ignores_numbers_outside_whitelist(self) -> None:
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351911111111"})
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.TEST123",
                                        "from": "351962063664",
                                        "timestamp": "1712165400",
                                        "type": "text",
                                        "text": {"body": "teste"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with app.app.test_client() as client:
                response = client.post("/webhooks/whatsapp", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ignored"], 1)
        self.assertEqual(whatsapp_service.sent_messages, [])

    def test_whatsapp_webhook_deduplicates_same_message_id(self) -> None:
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.TEST123",
                                        "from": "351962063664",
                                        "timestamp": "1712165400",
                                        "type": "text",
                                        "text": {"body": "teste"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with patch("core.chat_runtime.refresh_knowledge_state") as mock_refresh:
                with patch("core.chat_runtime.answer_direct_operational_query") as mock_answer_direct:
                    mock_refresh.return_value = None
                    mock_answer_direct.return_value = {
                        "answer": "Resposta do bot",
                        "sources": [],
                        "answer_origin": "operational_lookup",
                    }
                    with app.app.test_client() as client:
                        first = client.post("/webhooks/whatsapp", json=payload)
                        second = client.post("/webhooks/whatsapp", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["duplicates"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 1)

    def test_whatsapp_reaction_updates_feedback_on_target_message(self) -> None:
        username = "whatsapp-351962063664@pragtico.local"
        self.store.create_user(
            username=username,
            password="secret",
            role="piloto",
            full_name="Andre",
            organization="WhatsApp",
            email=username,
            phone="+351962063664",
        )
        conversation = self.store.create_conversation(username)
        self.store.append_chat_message(
            username,
            conversation["id"],
            "user",
            "Qual é a regra para compensação de agulhas dentro do Porto à noite?",
            channel="whatsapp",
            channel_user_id="351962063664",
        )
        message = self.store.append_chat_message(
            username,
            conversation["id"],
            "assistant",
            "Resposta do bot",
            channel="whatsapp",
            channel_user_id="351962063664",
            external_message_id="wamid.REPLY123",
        )
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.REACT123",
                                        "from": "351962063664",
                                        "timestamp": "1712165401",
                                        "type": "reaction",
                                        "reaction": {
                                            "message_id": "wamid.REPLY123",
                                            "emoji": "👍",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with app.app.test_client() as client:
                response = client.post("/webhooks/whatsapp", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["feedback_applied"], 1)
        updated_messages = self.store.list_messages(username, conversation["id"])
        updated = next(item for item in updated_messages if item["id"] == message["id"])
        self.assertEqual(updated["feedback_status"], "approved")
        self.assertIn("WhatsApp", updated["feedback_note"])
        channel_events = self.store._read_channel_events()
        self.assertEqual(channel_events[0]["event_type"], "incoming_reaction")

    def test_whatsapp_review_reaction_collects_followup_correction(self) -> None:
        username = "whatsapp-351962063664@pragtico.local"
        self.store.create_user(
            username=username,
            password="secret",
            role="piloto",
            full_name="Andre",
            organization="WhatsApp",
            email=username,
            phone="+351962063664",
        )
        conversation = self.store.create_conversation(username)
        self.store.append_chat_message(
            username,
            conversation["id"],
            "user",
            "Qual é a regra para compensação de agulhas dentro do Porto à noite?",
            channel="whatsapp",
            channel_user_id="351962063664",
        )
        message = self.store.append_chat_message(
            username,
            conversation["id"],
            "assistant",
            "Resposta do bot",
            channel="whatsapp",
            channel_user_id="351962063664",
            external_message_id="wamid.REPLY123",
            citations=[{"document": "IT-036_RegulacaoAgulhas.txt"}],
        )
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        reaction_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.REACT124",
                                        "from": "351962063664",
                                        "timestamp": "1712165401",
                                        "type": "reaction",
                                        "reaction": {
                                            "message_id": "wamid.REPLY123",
                                            "emoji": "👎",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        correction_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.TEXT125",
                                        "from": "351962063664",
                                        "timestamp": "1712165410",
                                        "type": "text",
                                        "text": {
                                            "body": "À noite a RA não se efetua com navios de LOA igual ou superior a 225 metros."
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with app.app.test_client() as client:
                reaction_response = client.post("/webhooks/whatsapp", json=reaction_payload)
                correction_response = client.post("/webhooks/whatsapp", json=correction_payload)

        self.assertEqual(reaction_response.status_code, 200)
        self.assertEqual(correction_response.status_code, 200)
        updated_messages = self.store.list_messages(username, conversation["id"])
        updated = next(item for item in updated_messages if item["id"] == message["id"])
        self.assertEqual(updated["feedback_status"], "review")
        self.assertIn("225 metros", updated["feedback_correction"])
        self.assertEqual(updated["feedback_correction_document"], "IT-036_RegulacaoAgulhas.txt")
        eval_payload = self.store.list_feedback_eval_cases()
        self.assertEqual(len(eval_payload), 1)
        self.assertEqual(eval_payload[0]["source"], "whatsapp")
        self.assertEqual(eval_payload[0]["source_message_id"], message["id"])
        self.assertIsNone(
            self.store.get_runtime_state("whatsapp:feedback-correction:351962063664")
        )
        self.assertEqual(len(whatsapp_service.sent_messages), 2)
        self.assertIn("Qual seria a resposta correta", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("Correção guardada", whatsapp_service.sent_messages[1]["text"])


class AdminDocumentPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        self.original_manual_authoring_enabled = app.app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"]
        services.store = self.store
        app.app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"] = False

    def tearDown(self) -> None:
        services.store = self.original_store
        app.app.config["MANUAL_KNOWLEDGE_AUTHORING_ENABLED"] = self.original_manual_authoring_enabled
        self.temp_dir.cleanup()

    def _set_admin_session(self, client) -> str:
        with client.session_transaction() as flask_session:
            flask_session["username"] = "admin"
            flask_session["role"] = "admin"
            flask_session["_csrf_token"] = "test-token"
        return "test-token"

    def test_manual_document_creation_route_is_disabled(self) -> None:
        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)

            response = client.post(
                "/documents",
                data={
                    "csrf_token": csrf_token,
                    "title": "Meteorologia e Mares",
                    "content": "Conteúdo não oficial.",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.store.list_documents(), [])

    def test_manual_document_edit_route_is_disabled(self) -> None:
        filename = self.store.save_document("Norma de Seguranca", "Versão oficial.")

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)

            response = client.post(
                f"/documents/{filename}/edit",
                data={
                    "csrf_token": csrf_token,
                    "content": "Versão alterada no browser.",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIn("Versão oficial.", self.store.get_document_text(filename))

    def test_bulk_delete_documents_removes_selected_files(self) -> None:
        first = self.store.save_document("Documento A", "Primeiro conteúdo.")
        second = self.store.save_document("Documento B", "Segundo conteúdo.")

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            response = client.post(
                "/documents/bulk-delete",
                data={
                    "csrf_token": csrf_token,
                    "return_to": "/admin/documents?q=Documento",
                    "document_names": [first],
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(self.store.get_document(first))
        self.assertIsNotNone(self.store.get_document(second))

    def test_document_detail_preserves_return_to_catalog(self) -> None:
        filename = self.store.save_document("Manual Operacional", "Texto oficial.")

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get(f"/documents/{filename}?return_to=/admin/documents?q=manual")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("/admin/documents?q=manual", html)

    def test_admin_documents_page_renders_bulk_actions_and_filters(self) -> None:
        self.store.save_document("Manual Operacional", "Texto oficial.")

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/documents?q=Manual")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Eliminar selecionados", html)
        self.assertIn("Documentos indexados", html)

    def test_admin_bot_page_renders_eval_progress_and_recent_feedback(self) -> None:
        knowledge_dir = Path(self.store.knowledge_dir)
        (knowledge_dir / "evals").mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "companions").mkdir(parents=True, exist_ok=True)
        (knowledge_dir / "IT-036_RegulacaoAgulhas.txt").write_text(
            "IT-036 Regulacao de Agulhas\n\nRegras operacionais.",
            encoding="utf-8",
        )
        (knowledge_dir / "companions" / "IT-036_RegulacaoAgulhas.json").write_text(
            json.dumps(
                {
                    "title": "IT-036 - Regulacao de Agulhas",
                    "summary": "Regras aplicaveis a RA no Porto de Setubal.",
                    "faq": [
                        {
                            "question": "Qual e a regra para compensacao de agulhas dentro do Porto a noite?",
                            "answer": "A noite a RA nao se efetua com navios de LOA igual ou superior a 225 metros.",
                            "keywords": ["agulhas", "noite", "225", "loa"],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        (knowledge_dir / "evals" / "critical_document_companion_evals.json").write_text(
            json.dumps(
                [
                    {
                        "document": "IT-036_RegulacaoAgulhas.txt",
                        "question": "Qual e a regra para compensacao de agulhas dentro do Porto a noite?",
                        "expected_answer": "A noite a RA nao se efetua com navios de LOA igual ou superior a 225 metros.",
                        "expected_substrings": ["225 metros"],
                    }
                ],
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        self.store.upsert_feedback_eval_case(
            source_message_id="msg-123",
            document="IT-036_RegulacaoAgulhas.txt",
            question="Qual e a regra para compensacao de agulhas dentro do Porto a noite?",
            expected_answer="A noite a RA nao se efetua com navios de LOA igual ou superior a 225 metros.",
            expected_substrings=["225 metros"],
            feedback_note="Resposta corrigida manualmente.",
            updated_by="admin",
            source="whatsapp",
        )

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/bot")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Bot e evals", html)
        self.assertIn("IT-036_RegulacaoAgulhas.txt", html)
        self.assertIn("WhatsApp", html)
        self.assertIn("225 metros", html)
        self.assertIn("Inputs que o bot pode reutilizar", html)
        self.assertIn("Experiência prática importada", html)

    def test_admin_casebooks_redirects_to_bot_page_anchor(self) -> None:
        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/casebooks?case_type=entry")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/bot?case_type=entry#casebooks", response.headers["Location"])

    def test_admin_imports_practice_json_as_structured_experience_for_bot(self) -> None:
        payload = BytesIO(
            json.dumps(
                {
                    "kind": "pragtico.practice_maneuver_experience",
                    "version": 1,
                    "stats": {
                        "raw_rows": 2,
                        "pattern_count": 1,
                        "maneuver_types_label": "Entrada (2)",
                    },
                    "records": [
                        {
                            "id": "practice-test",
                            "maneuver_id": "practice-test",
                            "port_call_id": "",
                            "reference_code": "EXP-TEST",
                            "source_type": "practice_import",
                            "source_label": "Experiência prática importada",
                            "source_filename": "practice_maneuver_experience.json",
                            "vessel_name": "Padrão Contentores · TMS 2",
                            "maneuver_type": "entry",
                            "maneuver_type_label": "Entrada",
                            "current_state": "completed",
                            "current_state_label": "Realizada",
                            "origin_label": "Fora",
                            "destination_label": "TMS 2",
                            "latest_event_at": "2026-04-10T07:10:00+00:00",
                            "case_summary": "Entrada | Contentores | TMS 2 | 2 caso(s) | rebocadores mais comuns 2",
                            "practice_summary": "Entrada | Contentores | TMS 2 | 2 caso(s) | rebocadores mais comuns 2",
                            "practice_metrics": {
                                "case_count": 2,
                                "date_range": "2026-04-10 a 2026-04-10",
                                "duration_median_label": "1.2 h",
                                "dominant_tug_count": "2",
                                "tug_distribution_label": "2 (2)",
                                "vessel_examples": ["Elbtower", "Lisbon Trader"],
                                "comments": ["Bow avariado, usar rebocador."],
                                "loa_band": "150-200m",
                                "beam_band": "20-25m",
                                "draft_band": "6-8m",
                            },
                            "vessel_snapshot": {
                                "type": "Contentores",
                                "loa_m": "151",
                                "beam_m": "22",
                                "gt_t": "11800",
                                "max_draft_m": "7.4",
                            },
                            "planning_snapshot": {
                                "origin": "Fora",
                                "destination": "TMS 2",
                                "planned_draft_m": "7.4",
                                "tug_count": "2",
                                "plan_note": "Entrada | Contentores | TMS 2",
                            },
                            "decision_snapshot": {
                                "decision": "approved",
                                "state": "completed",
                                "approval_note": "Padrão agregado.",
                            },
                            "execution_snapshot": {
                                "report_note": "Bow avariado, usar rebocador.",
                                "reported_by": "experiência importada",
                            },
                            "outcome_snapshot": {
                                "state": "completed",
                                "state_label": "Realizada",
                                "decision_flags": [],
                            },
                            "environment_snapshot": {
                                "latest": {"status": "not_captured", "source": "practice_import"},
                            },
                            "feature_snapshot": {
                                "maneuver_type": "entry",
                                "origin": "Fora",
                                "destination": "TMS 2",
                                "origin_key": "fora",
                                "destination_key": "tms 2",
                                "origin_is_anchorage": False,
                                "destination_is_anchorage": False,
                                "origin_is_known_berth": False,
                                "destination_is_known_berth": True,
                                "vessel_type": "Contentores",
                                "vessel_type_key": "contentores",
                                "vessel_loa_m": 151,
                                "vessel_beam_m": 22,
                                "vessel_gt_t": 11800,
                                "planned_draft_m": 7.4,
                                "reported_draft_m": 7.4,
                                "bow_thruster": "unknown",
                                "stern_thruster": "unknown",
                                "tug_count": "2",
                                "constraints": [],
                                "wave_sensitive": True,
                            },
                            "feedback_status": "review",
                            "feedback_note": "",
                            "created_at": "2026-04-10T07:10:00+00:00",
                            "updated_at": "2026-04-10T07:10:00+00:00",
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
        )
        knowledge_source = Path(self.store.knowledge_dir) / "practice_maneuver_experience.json"
        knowledge_source.parent.mkdir(parents=True, exist_ok=True)
        knowledge_source.write_bytes(payload.getvalue())

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            response = client.post(
                "/admin/bot/practice-experience/import",
                data={
                    "csrf_token": csrf_token,
                    "return_to": "/admin/bot#casebooks",
                    "replace_source": "1",
                    "feedback_status": "approved",
                },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.store.list_documents(), [])
        state = self.store.get_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY)
        self.assertIsNotNone(state)
        self.assertEqual(len(state["records"]), 1)
        record = state["records"][0]
        self.assertEqual(record["feedback_status"], "approved")
        self.assertEqual(record["source_type"], "practice_import")
        self.assertNotIn("Piloto Teste", json.dumps(record, ensure_ascii=False))

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            review_response = client.post(
                f"/admin/bot/practice-experience/{record['id']}/feedback",
                data={
                    "csrf_token": csrf_token,
                    "return_to": "/admin/bot#casebooks",
                    "feedback_status": "review",
                    "feedback_note": "Confirmar antes de usar.",
                },
            )
            approve_response = client.post(
                f"/admin/bot/practice-experience/{record['id']}/feedback",
                data={
                    "csrf_token": csrf_token,
                    "return_to": "/admin/bot#casebooks",
                    "feedback_status": "approved",
                    "feedback_note": "Validado para apoio à decisão.",
                },
            )

        self.assertEqual(review_response.status_code, 302)
        self.assertEqual(approve_response.status_code, 302)
        state = self.store.get_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY)
        record = state["records"][0]
        self.assertEqual(record["feedback_status"], "approved")
        self.assertEqual(record["feedback_note"], "Validado para apoio à decisão.")

        current = self.store.create_port_call(
            vessel_name="Demo Container",
            eta="2026-04-20T05:30:00+00:00",
            created_by="admin",
            berth="TMS 2",
            last_port="Leixoes",
            next_port="Barcelona",
            notes="Nova escala.",
            vessel_imo="9234567",
            vessel_call_sign="DCON7",
            vessel_flag="Portugal",
            vessel_type="Contentores",
            vessel_loa_m="151",
            vessel_beam_m="22",
            vessel_gt_t="11800",
            vessel_max_draft_m="7.4",
            vessel_dwt_t="16000",
        )

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            sources = build_operational_chat_sources(
                f"O que achas da entrada da escala {current['reference_code']}?"
            )

        snippets = "\n".join(
            item.get("snippet", "")
            for item in sources
            if item.get("retrieval_mode") == "maneuver_casebook"
        )
        self.assertIn("Experiência prática importada", snippets)
        self.assertIn("rebocadores mais usados: 2", snippets)

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            clear_response = client.post(
                "/admin/bot/practice-experience/clear",
                data={"csrf_token": csrf_token, "return_to": "/admin/bot#casebooks"},
            )

        self.assertEqual(clear_response.status_code, 302)
        self.assertEqual(self.store.get_runtime_state(PRACTICE_EXPERIENCE_STATE_KEY)["records"], [])


class PortalLiveNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        services.store = self.store
        self.store.update_user_profile(
            "admin",
            full_name="Andre Pereira",
            organization="APSS",
            email="admin@apss.pt",
            phone="+351 900 000 001",
        )

    def tearDown(self) -> None:
        services.store = self.original_store
        self.temp_dir.cleanup()

    def _set_session(self, client, *, username: str, role: str) -> str:
        with client.session_transaction() as flask_session:
            flask_session["username"] = username
            flask_session["role"] = role
            flask_session["_csrf_token"] = "portal-token"
        return "portal-token"

    def test_dashboard_nav_initials_link_points_to_profile(self) -> None:
        with app.app.test_client() as client:
            self._set_session(client, username="admin", role="admin")
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/profile"', html)
        self.assertIn('aria-label="Abrir perfil de Andre Pereira"', html)

    def test_dashboard_footer_exposes_contact_link(self) -> None:
        with app.app.test_client() as client:
            self._set_session(client, username="admin", role="admin")
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('href="/contact"', html)
        self.assertIn("2202880@estudante.uab.pt", html)
        self.assertIn('value="/contact"', html)

    def test_contact_page_renders_support_email_and_academic_scope(self) -> None:
        with app.app.test_client() as client:
            response = client.get("/contact")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("2202880@estudante.uab.pt", html)
        self.assertIn("Projeto académico", html)
        self.assertIn("Universidade Aberta", html)

    def test_approve_route_emits_live_notification_in_feed(self) -> None:
        port_call = self.store.create_port_call(
            vessel_name="BELITAKI",
            eta="2026-03-24T05:30:00+00:00",
            created_by="admin",
            berth="TMS 2",
            last_port="Leixoes",
            next_port="Barcelona",
            notes="Entrada planeada.",
            vessel_imo="9152923",
            vessel_call_sign="D5OC2",
            vessel_flag="Liberia",
            vessel_type="Porta-contentores",
            vessel_loa_m="179.23",
            vessel_beam_m="25.3",
            vessel_gt_t="16281",
            vessel_max_draft_m="9.94",
            vessel_dwt_t="22330",
        )

        with app.app.test_client() as client:
            csrf_token = self._set_session(client, username="admin", role="admin")
            approve = client.post(
                f"/port-calls/{port_call['id']}/approve",
                data={"csrf_token": csrf_token},
            )
            feed = client.get("/api/portal-live-feed?since=2000-01-01T00:00:00+00:00")

        self.assertEqual(approve.status_code, 302)
        self.assertEqual(feed.status_code, 200)
        payload = feed.get_json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertIn("aprovada - APSS", payload["items"][0]["message"])
        self.assertIn("BELITAKI", payload["items"][0]["message"])
        self.assertIn(f"/port-calls/{port_call['id']}/maneuvers/", payload["items"][0]["url"])

    def test_agent_feed_only_returns_notifications_for_same_agency_scope(self) -> None:
        self.store.create_user(
            "agencia@example.com",
            "secret123",
            "agente",
            full_name="Agencia X",
            organization="Agencia X",
            email="agencia@example.com",
            phone="+351 900 000 111",
        )
        self.store.record_channel_event(
            channel="portal_live",
            event_type="maneuver_created",
            payload={
                "message": "Visivel",
                "scope_organization_key": "agencia x",
                "url": "/dashboard",
            },
        )
        self.store.record_channel_event(
            channel="portal_live",
            event_type="maneuver_created",
            payload={
                "message": "Oculta",
                "scope_organization_key": "outra agencia",
                "url": "/dashboard",
            },
        )

        with app.app.test_client() as client:
            self._set_session(client, username="agencia@example.com", role="agente")
            response = client.get("/api/portal-live-feed?since=2000-01-01T00:00:00+00:00")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([item["message"] for item in payload["items"]], ["Visivel"])


class AgentPortActivityVisibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        services.store = self.store
        self.store.create_user(
            "agencia@example.com",
            "secret123",
            "agente",
            full_name="Agencia X",
            organization="Agencia X",
            email="agencia@example.com",
            phone="+351 900 000 111",
        )
        self.store.create_user(
            "outra@example.com",
            "secret123",
            "agente",
            full_name="Agencia Y",
            organization="Agencia Y",
            email="outra@example.com",
            phone="+351 900 000 222",
        )

    def tearDown(self) -> None:
        services.store = self.original_store
        self.temp_dir.cleanup()

    def _set_session(self, client, *, username: str, role: str) -> str:
        with client.session_transaction() as flask_session:
            flask_session["username"] = username
            flask_session["role"] = role
            flask_session["_csrf_token"] = "scope-token"
        return "scope-token"

    def _create_port_call(self, *, vessel_name: str, created_by: str, eta: str) -> dict:
        return self.store.create_port_call(
            vessel_name=vessel_name,
            eta=eta,
            created_by=created_by,
            berth="TMS 2",
            last_port="Sines",
            next_port="Vigo",
            notes="Escala de teste.",
            vessel_imo="9876543",
            vessel_call_sign="CQAB7",
            vessel_flag="Portugal",
            vessel_type="General Cargo",
            vessel_loa_m="142.50",
            vessel_beam_m="21.80",
            vessel_gt_t="8950",
            vessel_max_draft_m="7.20",
            vessel_dwt_t="12400",
        )

    def test_dashboard_is_shared_for_agents_but_scale_registry_remains_scoped(self) -> None:
        self._create_port_call(
            vessel_name="AGENCY STAR",
            created_by="agencia@example.com",
            eta="2026-04-09T08:00:00+00:00",
        )
        self._create_port_call(
            vessel_name="OTHER PLANNER",
            created_by="outra@example.com",
            eta="2026-04-09T09:00:00+00:00",
        )
        other_in_port = self._create_port_call(
            vessel_name="OTHER QUAY",
            created_by="outra@example.com",
            eta="2026-04-09T07:00:00+00:00",
        )
        self.store.approve_port_call(other_in_port["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            other_in_port["id"],
            arrived_at="2026-04-09T07:30:00+00:00",
            updated_by="admin",
        )

        with app.app.test_client() as client:
            self._set_session(client, username="agencia@example.com", role="agente")
            dashboard_response = client.get("/dashboard")
            register_response = client.get("/port-calls/register")

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(register_response.status_code, 200)

        dashboard_html = dashboard_response.get_data(as_text=True)
        self.assertIn("AGENCY STAR", dashboard_html)
        self.assertIn("OTHER PLANNER", dashboard_html)
        self.assertIn("OTHER QUAY", dashboard_html)

        register_html = register_response.get_data(as_text=True)
        self.assertIn("AGENCY STAR", register_html)
        self.assertNotIn("OTHER PLANNER", register_html)
        self.assertNotIn("OTHER QUAY", register_html)


class DashboardPlanningWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        services.store = self.store

    def tearDown(self) -> None:
        services.store = self.original_store
        self.temp_dir.cleanup()

    def _set_session(self, client, *, username: str, role: str) -> None:
        with client.session_transaction() as flask_session:
            flask_session["username"] = username
            flask_session["role"] = role
            flask_session["_csrf_token"] = "dashboard-token"

    def test_dashboard_planning_includes_active_departures_beyond_traffic_window(self) -> None:
        now = datetime.now(timezone.utc)
        eta = (now - timedelta(days=1)).isoformat()
        ata = (now - timedelta(hours=20)).isoformat()
        planned_departure = (now + timedelta(days=12)).isoformat()

        port_call = self.store.create_port_call(
            vessel_name="LONG STAY",
            eta=eta,
            created_by="admin",
            berth="TMS 2",
            last_port="Sines",
            next_port="Vigo",
            notes="Escala de longa estadia.",
            vessel_imo="9876543",
            vessel_call_sign="CQAB7",
            vessel_flag="Portugal",
            vessel_type="General Cargo",
            vessel_loa_m="142.50",
            vessel_beam_m="21.80",
            vessel_gt_t="8950",
            vessel_max_draft_m="7.20",
            vessel_dwt_t="12400",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(port_call["id"], arrived_at=ata, updated_by="admin")
        planned = self.store.schedule_departure_plan(
            port_call["id"],
            planned_departure_at=planned_departure,
            updated_by="admin",
            next_port="Barcelona",
        )
        departure = next(item for item in planned["maneuver_history"] if item["type"] == "departure")

        with app.app.test_client() as client:
            self._set_session(client, username="admin", role="admin")
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("LONG STAY", html)
        self.assertIn(f"/port-calls/{port_call['id']}/maneuvers/{departure['id']}", html)

    def test_dashboard_recent_departures_includes_completed_future_departures_within_window(self) -> None:
        now = datetime.now(timezone.utc)
        eta = (now - timedelta(days=1)).isoformat()
        ata = (now - timedelta(hours=20)).isoformat()
        departed_at = (now + timedelta(days=1)).isoformat()

        port_call = self.store.create_port_call(
            vessel_name="FUTURE DEPARTURE",
            eta=eta,
            created_by="admin",
            berth="TMS 2",
            last_port="Sines",
            next_port="Vigo",
            notes="Escala de teste.",
            vessel_imo="9876543",
            vessel_call_sign="CQAB7",
            vessel_flag="Portugal",
            vessel_type="General Cargo",
            vessel_loa_m="142.50",
            vessel_beam_m="21.80",
            vessel_gt_t="8950",
            vessel_max_draft_m="7.20",
            vessel_dwt_t="12400",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(port_call["id"], arrived_at=ata, updated_by="admin")
        self.store.schedule_departure_plan(
            port_call["id"],
            planned_departure_at=departed_at,
            updated_by="admin",
            next_port="Barcelona",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_departed(
            port_call["id"],
            departed_at=departed_at,
            updated_by="admin",
            next_port="Barcelona",
        )

        with app.app.test_client() as client:
            self._set_session(client, username="admin", role="admin")
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("FUTURE DEPARTURE", html)
        self.assertIn(port_call["reference_code"], html)

    def test_dashboard_in_port_cards_render_vessel_type_icon(self) -> None:
        now = datetime.now(timezone.utc)
        port_call = self.store.create_port_call(
            vessel_name="ICONIC CARGO",
            eta=(now - timedelta(days=1)).isoformat(),
            created_by="admin",
            berth="TMS 2",
            last_port="Sines",
            next_port="Vigo",
            notes="Escala com iconografia.",
            vessel_imo="9876543",
            vessel_call_sign="CQAB7",
            vessel_flag="Portugal",
            vessel_type="Contentores",
            vessel_loa_m="142.50",
            vessel_beam_m="21.80",
            vessel_gt_t="8950",
            vessel_max_draft_m="7.20",
            vessel_dwt_t="12400",
        )
        self.store.approve_port_call(port_call["id"], decided_by="admin")
        self.store.mark_port_call_arrived(
            port_call["id"],
            arrived_at=(now - timedelta(hours=16)).isoformat(),
            updated_by="admin",
        )

        with app.app.test_client() as client:
            self._set_session(client, username="admin", role="admin")
            response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("ICONIC CARGO", html)
        self.assertIn("Contentores", html)
        self.assertIn("contentores.png", html)


class PortCallJsonImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        services.store = self.store

    def tearDown(self) -> None:
        services.store = self.original_store
        self.temp_dir.cleanup()

    def _set_admin_session(self, client) -> str:
        with client.session_transaction() as flask_session:
            flask_session["username"] = "admin"
            flask_session["role"] = "admin"
            flask_session["_csrf_token"] = "json-token"
        return "json-token"

    def _set_agent_session(self, client) -> str:
        self.store.create_user(
            "agencia@example.com",
            "secret123",
            "agente",
            full_name="Agencia X",
            organization="Agencia X",
            email="agencia@example.com",
            phone="+351 900 000 111",
            whatsapp_number="351900000111",
            whatsapp_opt_in=True,
        )
        with client.session_transaction() as flask_session:
            flask_session["username"] = "agencia@example.com"
            flask_session["role"] = "agente"
            flask_session["_csrf_token"] = "json-token"
        return "json-token"

    def test_import_port_call_json_is_admin_only(self) -> None:
        with app.app.test_client() as client:
            csrf_token = self._set_agent_session(client)
            response = client.post(
                "/port-calls/import-json",
                data={"csrf_token": csrf_token, "payload_json": "{}"},
                headers={"Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.store.get_port_activity_snapshot(window_days=30)["arrivals"], [])

    def test_agent_register_page_hides_json_import(self) -> None:
        with app.app.test_client() as client:
            self._set_agent_session(client)
            response = client.get("/port-calls/register")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("Importar escala por JSON", html)
        self.assertNotIn("Importar navios por JSON", html)
        self.assertNotIn("/port-calls/import-json", html)
        self.assertNotIn("/port-calls/vessels/import-json", html)

    def test_admin_register_page_shows_json_import(self) -> None:
        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/port-calls/register")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Importar escala por JSON", html)
        self.assertIn("/port-calls/import-json", html)
        self.assertIn("Importar navios por JSON", html)
        self.assertIn("/port-calls/vessels/import-json", html)

    def test_import_vessel_catalog_json_is_admin_only(self) -> None:
        with app.app.test_client() as client:
            csrf_token = self._set_agent_session(client)
            response = client.post(
                "/port-calls/vessels/import-json",
                data={"csrf_token": csrf_token, "payload_json": "{}"},
                headers={"Accept": "application/json"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.store.get_runtime_state("port_call_vessel_catalog"))

    def test_admin_import_vessel_catalog_json_populates_selector(self) -> None:
        payload = """
        {
          "vessels": [
            {
              "vessel_name": "Catalog Star",
              "vessel_imo": "9234567",
              "vessel_call_sign": "CSAB7",
              "vessel_flag": "Portugal",
              "vessel_type": "Contentores",
              "vessel_loa_m": "142.5",
              "vessel_beam_m": "21.8",
              "vessel_gt_t": "8950",
              "vessel_dwt_t": "12400",
              "vessel_max_draft_m": "7.2",
              "vessel_bow_thruster": "yes",
              "vessel_stern_thruster": "no",
              "service_rate_profile": "Linha regular",
              "regular_line_calls_365d": 12,
              "pilotage_up_rate": "0.0042",
              "tup_reduction_profile": "regular_line",
              "service_notes": "Perfil de teste."
            }
          ]
        }
        """
        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            response = client.post(
                "/port-calls/vessels/import-json",
                data={"csrf_token": csrf_token, "payload_json": payload},
            )
            register_response = client.get("/port-calls/register")

        self.assertEqual(response.status_code, 302)
        state = self.store.get_runtime_state("port_call_vessel_catalog")
        self.assertIsNotNone(state)
        catalog_item = next(item for item in state["items"] if item["vessel_name"] == "Catalog Star")
        self.assertEqual(catalog_item["regular_line_calls_365d"], "12")
        self.assertEqual(catalog_item["service_rate_profile"], "Linha regular")
        html = register_response.get_data(as_text=True)
        self.assertIn("Navio frequente", html)
        self.assertIn("Catalog Star", html)
        self.assertIn("Linha regular", html)

    def test_agent_cannot_see_or_post_admin_scale_edit(self) -> None:
        eta = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        with app.app.test_client() as client:
            csrf_token = self._set_agent_session(client)
            port_call = self.store.create_port_call(
                vessel_name="Agent Vessel",
                eta=eta,
                created_by="agencia@example.com",
                berth="Secil W",
                last_port="Sines",
                next_port="Vigo",
                notes="Teste.",
                vessel_imo="9234501",
                vessel_call_sign="AVES7",
                vessel_flag="Portugal",
                vessel_type="Contentores",
                vessel_loa_m="142.5",
                vessel_beam_m="21.8",
                vessel_gt_t="8950",
                vessel_max_draft_m="7.2",
                vessel_dwt_t="12400",
            )
            detail_response = client.get(f"/port-calls/{port_call['id']}")
            edit_response = client.post(
                f"/port-calls/{port_call['id']}/edit",
                data={"csrf_token": csrf_token},
                headers={"Accept": "application/json"},
            )

        self.assertEqual(detail_response.status_code, 200)
        html = detail_response.get_data(as_text=True)
        self.assertNotIn("Editar escala e navio", html)
        self.assertNotIn("Contacto piloto", html)
        self.assertIn("+351 900 000 111", html)
        self.assertIn("351900000111", html)
        self.assertEqual(edit_response.status_code, 403)

    def test_import_port_call_json_from_textarea(self) -> None:
        payload = """
        {
          "vessel_name": "MSC Lyria",
          "eta": "2026-04-20T14:30:00+01:00",
          "berth": "Secil W",
          "last_port": "Sines",
          "next_port": "Vigo",
          "vessel_imo": "9723345",
          "vessel_call_sign": "CQAN7",
          "vessel_flag": "Madeira",
          "vessel_type": "Graneis sólidos",
          "vessel_loa_m": "189.9",
          "vessel_beam_m": "32.2",
          "vessel_gt_t": "32540",
          "vessel_dwt_t": "38600",
          "vessel_max_draft_m": "11.8",
          "vessel_bow_thruster": true,
          "vessel_stern_thruster": "unknown",
          "draft_m": "11.2",
          "tug_count": 2,
          "constraints": ["daylight"],
          "service_rate_profile": "Linha regular",
          "regular_line_calls_365d": 10,
          "tup_reduction_profile": "regular_line",
          "notes": "Janela de maré confirmada com agente."
        }
        """

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            response = client.post(
                "/port-calls/import-json",
                data={
                    "csrf_token": csrf_token,
                    "payload_json": payload,
                },
            )

        self.assertEqual(response.status_code, 302)
        activity = self.store.get_port_activity_snapshot(window_days=30)
        created = next(item for item in activity["arrivals"] if item["vessel_name"] == "MSC Lyria")
        self.assertEqual(created["berth"], "Secil W")
        current = self.store.get_port_call(created["id"])
        self.assertEqual(current["vessel_imo"], "9723345")
        self.assertEqual(current["vessel_bow_thruster"], "yes")
        state = self.store.get_runtime_state("port_call_vessel_catalog")
        catalog_item = next(item for item in state["items"] if item["vessel_name"] == "MSC Lyria")
        self.assertEqual(catalog_item["service_rate_profile"], "Linha regular")
        self.assertEqual(catalog_item["regular_line_calls_365d"], "10")

    def test_import_port_call_json_from_file_accepts_nested_scale_object(self) -> None:
        payload = b"""
        {
          "scale": {
                "vessel_name": "Atlantic Trader",
                "eta_local": "2026-04-21T08:15",
                "berth": "TMS 2",
                "last_port": "Leixoes",
                "next_port": "Casablanca",
            "vessel_imo": "9152923",
            "vessel_call_sign": "D5OC2",
            "vessel_flag": "Liberia",
            "vessel_type": "Roll-on/Roll-off",
            "vessel_loa_m": "179.23",
            "vessel_beam_m": "25.3",
            "vessel_gt_t": "16281",
            "vessel_dwt_t": "22330",
            "vessel_max_draft_m": "9.94",
            "vessel_bow_thruster": "no",
            "vessel_stern_thruster": "yes",
            "draft_m": "9.5",
            "tug_count": 1,
            "constraints": ["gas"],
            "notes": "Operacao sensivel."
          }
        }
        """

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            response = client.post(
                "/port-calls/import-json",
                data={
                    "csrf_token": csrf_token,
                    "payload_file": (BytesIO(payload), "escala.json"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        activity = self.store.get_port_activity_snapshot(window_days=30)
        created = next(item for item in activity["arrivals"] if item["vessel_name"] == "Atlantic Trader")
        current = self.store.get_port_call(created["id"])
        self.assertEqual(current["berth"], "TMS 2")
        self.assertEqual(current["vessel_stern_thruster"], "yes")
        entry = next(item for item in current["maneuver_history"] if item["type"] == "entry")
        self.assertIn("Operacao sensivel.", entry["plan_note"])

    def test_import_port_call_json_accepts_blank_constraints_and_trailing_comma(self) -> None:
        payload = """
        {
          "vessel_name": "ARKLOW GLOBE",
          "eta": "2026-04-20T11:15:00+01:00",
          "berth": "Secil W",
          "last_port": "Sines",
          "next_port": "Vigo",
          "vessel_imo": "9874105",
          "vessel_call_sign": "PGWG",
          "vessel_flag": "Rotterdam",
          "vessel_type": "Graneis sólidos",
          "vessel_loa_m": "87.4",
          "vessel_beam_m": "15",
          "vessel_gt_t": "2999",
          "vessel_dwt_t": "5150",
          "vessel_max_draft_m": "6,26",
          "vessel_bow_thruster": "yes",
          "vessel_stern_thruster": "no",
          "draft_m": "4",
          "tug_count": 0,
          "constraints": ,
          "notes": "Janela de maré confirmada com pilotos.",
        }
        """

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            response = client.post(
                "/port-calls/import-json",
                data={
                    "csrf_token": csrf_token,
                    "payload_json": payload,
                },
            )

        self.assertEqual(response.status_code, 302)
        activity = self.store.get_port_activity_snapshot(window_days=30)
        created = next(item for item in activity["arrivals"] if item["vessel_name"] == "ARKLOW GLOBE")
        current = self.store.get_port_call(created["id"])
        entry = next(item for item in current["maneuver_history"] if item["type"] == "entry")
        self.assertEqual(entry["constraints"], [])


if __name__ == "__main__":
    unittest.main()
