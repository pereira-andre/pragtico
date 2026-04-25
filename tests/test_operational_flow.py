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
from domain.event_reports import build_event_report_template, parse_event_report_command, register_event_report
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

    def _warning_code(self, item: dict) -> str:
        code = str(item.get("code") or "").strip()
        if code:
            return code
        display_code = str(item.get("display_code") or "").strip()
        return display_code.replace("Anav nr ", "").strip()

    def browse_text(self) -> str:
        if not self._warnings:
            return "Sem avisos locais em vigor."
        lines = [f"Avisos locais em vigor ({len(self._warnings)}):"]
        for item in self._warnings:
            lines.append(
                f"- {item.get('display_code', '--')} · {item.get('subject', '--')} · {item.get('location', '--')}"
            )
        lines.extend(
            [
                "",
                "Para consultar um aviso específico usa o código do aviso.",
                "Exemplo: /avisos-locais 88/26",
            ]
        )
        return "\n".join(lines)

    def detail_text(self, query: str) -> str:
        clean_query = " ".join((query or "").strip().split())
        matches = []
        for item in self._warnings:
            code = self._warning_code(item)
            if clean_query in {str(item.get("id") or ""), code, f"Anav nr {code}"}:
                matches.append(item)
                continue
            if clean_query.isdigit() and code.split("/", 1)[0] == clean_query:
                matches.append(item)
        if not clean_query:
            return self.browse_text()
        if not matches:
            return (
                f'Não encontrei nenhum aviso local para "{clean_query}".\n'
                "Usa /avisos-locais para ver todos os avisos em vigor."
            )
        if len(matches) > 1:
            lines = [
                f'Encontrei mais do que um aviso para "{clean_query}". Usa o código completo.',
                "",
                "Candidatos:",
            ]
            for item in matches:
                lines.append(
                    f"- {item.get('display_code', '--')} · {item.get('subject', '--')} · {item.get('location', '--')}"
                )
            lines.extend(["", f"Exemplo: /avisos-locais {self._warning_code(matches[0]) or '88/26'}"])
            return "\n".join(lines)
        warning = matches[0]
        lines = [
            warning.get("display_code", "--"),
            f"Estado: {warning.get('status_label', '--')}",
            f"Assunto: {warning.get('subject', '--')}",
            f"Local: {warning.get('location', '--')}",
            f"Período: {warning.get('start_date_label', '--')} até {warning.get('end_date_label', '--')}",
        ]
        if warning.get("description_text"):
            lines.extend(["", "Descrição:", warning["description_text"]])
        return "\n".join(lines)


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


class _StubHazardWeatherService(_StubWeatherService):
    def __init__(self, current: dict) -> None:
        self._current = current

    def get_forecast(self, days: int = 3):
        forecast = super().get_forecast(days=days)
        forecast["current"].update(self._current)
        return forecast


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
        self.media_downloads: dict[str, dict] = {}

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
                    elif message.get("type") in {"image", "document"}:
                        media = message.get(message.get("type")) or {}
                        parsed.append(
                            {
                                "event_type": "message_media",
                                "message_id": str(message.get("id", "")),
                                "from_number": from_number,
                                "profile_name": profile_name,
                                "media_kind": str(message.get("type", "")),
                                "media_id": str(media.get("id") or ""),
                                "mime_type": str(media.get("mime_type") or ""),
                                "caption": str(media.get("caption") or ""),
                                "filename": str(media.get("filename") or ""),
                                "timestamp": str(message.get("timestamp") or ""),
                                "raw": message,
                            }
                        )
                    elif message.get("type") == "location":
                        location = message.get("location") or {}
                        parsed.append(
                            {
                                "event_type": "message_location",
                                "message_id": str(message.get("id", "")),
                                "from_number": from_number,
                                "profile_name": profile_name,
                                "latitude": location.get("latitude"),
                                "longitude": location.get("longitude"),
                                "location_name": str(location.get("name") or ""),
                                "location_address": str(location.get("address") or ""),
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

    def download_media(self, media_id: str) -> dict:
        payload = self.media_downloads.get(media_id)
        if payload is None:
            raise RuntimeError(f"media not found: {media_id}")
        return payload


class OperationalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.store = LocalStore(data_dir=str(base / "data"), knowledge_dir=str(base / "knowledge"))
        self.original_store = services.store
        self.original_event_reports_dir = os.environ.get("EVENT_REPORTS_DIR")
        os.environ["EVENT_REPORTS_DIR"] = str(base / "reportes")
        services.store = self.store

    def tearDown(self) -> None:
        services.store = self.original_store
        if self.original_event_reports_dir is None:
            os.environ.pop("EVENT_REPORTS_DIR", None)
        else:
            os.environ["EVENT_REPORTS_DIR"] = self.original_event_reports_dir
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

    def _login_client_as_role(self, client, role: str, username: str = "") -> None:
        clean_role = (role or "").strip().lower() or "piloto"
        with client.session_transaction() as flask_session:
            flask_session["username"] = username or clean_role
            flask_session["role"] = clean_role

    def _future_local_value(self, *, days: int = 7, hour: int = 6, minute: int = 30) -> str:
        future = datetime.now(timezone.utc) + timedelta(days=days)
        future = future.astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)
        return future.strftime("%Y-%m-%dT%H:%M")

    def _write_knowledge_companion(self, document_name: str, payload: dict) -> Path:
        companion_dir = Path(self.store.knowledge_dir) / "companions"
        companion_dir.mkdir(parents=True, exist_ok=True)
        path = companion_dir / f"{Path(document_name).stem}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def _copy_tug_guidance(self) -> Path:
        source = Path(__file__).resolve().parents[1] / "knowledge" / "tug_operational_guidance.json"
        target = Path(self.store.knowledge_dir) / "tug_operational_guidance.json"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    def _copy_safety_limits(self) -> Path:
        source = Path(__file__).resolve().parents[1] / "knowledge" / "operational_safety_limits.json"
        target = Path(self.store.knowledge_dir) / "operational_safety_limits.json"
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    def _whatsapp_text_payload(
        self,
        *,
        message_id: str,
        from_number: str = "351962063664",
        profile_name: str = "Andre",
        text: str,
        timestamp: str = "1712165400",
    ) -> dict:
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": from_number, "profile": {"name": profile_name}}],
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": from_number,
                                        "timestamp": timestamp,
                                        "type": "text",
                                        "text": {"body": text},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

    def _whatsapp_location_payload(
        self,
        *,
        message_id: str,
        from_number: str = "351962063664",
        profile_name: str = "Andre",
        latitude: float = 38.5244,
        longitude: float = -8.8882,
        timestamp: str = "1712165460",
    ) -> dict:
        return {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": from_number, "profile": {"name": profile_name}}],
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": from_number,
                                        "timestamp": timestamp,
                                        "type": "location",
                                        "location": {
                                            "latitude": latitude,
                                            "longitude": longitude,
                                            "name": "Porto de Setubal",
                                            "address": "Setubal",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

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
                        "eta_local": self._future_local_value(days=7, hour=6, minute=30),
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
                        "planned_shift_at_local": self._future_local_value(days=7, hour=8, minute=30),
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
                        "planned_departure_at_local": self._future_local_value(days=7, hour=9, minute=10),
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

    def test_lisnave_dock_checklist_requires_at_least_four_tugs(self) -> None:
        port_call = self.store.create_port_call(
            vessel_name="DOCK TEST",
            eta="2026-04-08T05:30:00+00:00",
            created_by="admin",
            berth="D33",
            last_port="Sines",
            next_port="Lisboa",
            notes="Calado: 5.4\nRebocadores: 3\nObservações: entrada para doca.",
            vessel_imo="9123456",
            vessel_call_sign="D33T",
            vessel_flag="Portugal",
            vessel_type="Carga Geral",
            vessel_loa_m="120",
            vessel_beam_m="19",
            vessel_gt_t="6500",
            vessel_max_draft_m="5.4",
            vessel_dwt_t="9000",
        )

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            scale = build_scale_context(self.store.get_port_call(port_call["id"]))

        entry = next(item for item in scale["maneuvers"] if item["type"] == "entry")
        lisnave_items = [item for item in entry["analysis_checklist"] if item["title"] == "Lisnave - doca"]
        self.assertEqual(lisnave_items[0]["status"], "caution")
        self.assertIn("pelo menos 4 rebocadores", lisnave_items[0]["detail"])
        self.assertIn("3 previsto", lisnave_items[0]["detail"])
        self.assertTrue(
            any(
                item["title"] == "Orientação Lisnave" and "proa a norte" in item["detail"]
                for item in entry["analysis_checklist"]
            )
        )

    def test_lisnave_rule_source_is_added_for_dock_questions(self) -> None:
        with app.app.test_request_context("/"):
            session["role"] = "admin"
            sources = build_operational_chat_sources("Quantos rebocadores para entrada na D33 da Lisnave?")

        rule_sources = [item for item in sources if item.get("retrieval_mode") == "operational_rule"]
        self.assertTrue(rule_sources)
        self.assertIn("mínimo 4 rebocadores", rule_sources[0]["snippet"])
        self.assertIn("Hidrolift", rule_sources[0]["snippet"])
        self.assertIn("proa a norte", rule_sources[0]["snippet"])

    def test_tug_guidance_source_is_added_for_tug_decision_questions(self) -> None:
        self._copy_tug_guidance()

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            sources = build_operational_chat_sources(
                "Quantos reboques para entrada de RORO de 180m com vento norte?"
            )

        tug_sources = [item for item in sources if item.get("retrieval_mode") == "operational_tug_guidance"]
        self.assertTrue(tug_sources)
        self.assertIn("Ro-Ro com vento Norte a entrar: 3 rebocadores", tug_sources[0]["snippet"])
        self.assertIn("IT-016 confirma/agrava", tug_sources[0]["snippet"])

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

    def test_approved_feedback_is_synthesized_instead_of_replayed_verbatim(self) -> None:
        conversation = self.store.ensure_conversation(username="admin")
        self.store.append_chat_message(
            username="admin",
            conversation_id=conversation["id"],
            role="user",
            content="Como devo preparar a entrada de um navio para a doca 33 da Lisnave?",
            channel="whatsapp",
        )
        approved_message = self.store.append_chat_message(
            username="admin",
            conversation_id=conversation["id"],
            role="assistant",
            content="Têm sempre pelo menos 4 reboques porque estão sempre dois de cada lado a empurrar.",
            channel="whatsapp",
        )
        self.store.update_message_feedback(
            "admin",
            conversation["id"],
            approved_message["id"],
            "approved",
            "Regra operacional validada.",
            feedback_updated_by="admin",
        )

        with patch.object(services.rag, "can_generate", return_value=True), patch.object(
            services.rag,
            "answer",
            return_value={
                "answer": "Para Doca 33, planeia pelo menos 4 rebocadores e valida a entrada atravessada à corrente.",
                "sources": [],
            },
        ) as answer_mock:
            with app.app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["username"] = "admin"
                    flask_session["role"] = "admin"
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Como devo preparar a entrada de um navio para a doca 33 da Lisnave?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        self.assertIn("pelo menos 4 rebocadores", payload["answer"])
        self.assertNotEqual(payload["answer"], approved_message["content"])
        answer_mock.assert_called_once()
        self.assertEqual(answer_mock.call_args.kwargs["trusted_answers"][0]["answer"], approved_message["content"])

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
        self.store.approve_port_call(port_call["id"], decided_by="admin")

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

    def test_slash_register_and_edit_scale_flow_via_chat(self) -> None:
        eta_local = self._future_local_value(days=7, hour=6, minute=30)

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        "/registar-escala\n"
                        "Nome do navio: AUTO TESTE\n"
                        f"ETA de chegada: {eta_local}\n"
                        "Cais previsto: Cais 10 Autoeuropa\n"
                        "Último porto: Sines\n"
                        "Próximo destino: Vigo\n"
                        "IMO: 9876543\n"
                        "Indicativo: CQAB7\n"
                        "Bandeira: Portugal\n"
                        "Tipo de navio: General Cargo\n"
                        "LOA (m): 142.50\n"
                        "Boca (m): 21.80\n"
                        "GT (t): 8950\n"
                        "DWT (t): 12400\n"
                        "Calado máximo (m): 7.20\n"
                        "Observações: Escala criada via slash."
                    ),
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["action"], "create_port_call")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["refresh_required"])
            self.assertEqual(payload["conversation_id"], conversation["id"])
            port_call = self.store.get_port_call(payload["port_call_id"])

            self.assertEqual(port_call["berth"], "Cais 10 / Autoeuropa")
            self.assertEqual(port_call["next_port"], "Vigo")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        "/editar-escala\n"
                        f"Ref: {port_call['reference_code']}\n"
                        "Próximo destino: Valencia\n"
                        "Motivo da alteração: Ajuste comercial"
                    ),
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["action"], "edit_port_call")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

        self.assertEqual(response.status_code, 200)
        updated = self.store.get_port_call(port_call["id"])
        self.assertEqual(updated["next_port"], "Valencia")

    def test_slash_edit_entry_and_create_departure_flow_via_chat(self) -> None:
        port_call = self._create_entry(notes="Escala criada para smoke test slash.")
        entry_planned_local = self._future_local_value(days=7, hour=7, minute=15)
        departure_planned_local = self._future_local_value(days=8, hour=8, minute=45)
        arrived_at = (datetime.now(timezone.utc) + timedelta(days=7, hours=1)).replace(microsecond=0).isoformat()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        "/editar-manobra\n"
                        f"Ref: {port_call['reference_code']}\n"
                        "Tipo de manobra: entrada\n"
                        f"Hora prevista: {entry_planned_local}\n"
                        "Origem: Sines\n"
                        "Destino: TMS 2\n"
                        "Calado: 9.94\n"
                        "Rebocadores: 2\n"
                        "Restrições: daylight\n"
                        "Observações: Ajuste via slash.\n"
                        "Motivo da alteração: Ajuste de janela"
                    ),
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["action"], "edit_maneuver_plan")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertTrue(payload["refresh_required"])
            self.assertEqual(payload["conversation_id"], conversation["id"])

            self.store.approve_port_call(port_call["id"], decided_by="admin")
            self.store.mark_port_call_arrived(
                port_call["id"],
                arrived_at=arrived_at,
                updated_by="admin",
            )

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        "/criar-manobra\n"
                        f"Ref: {port_call['reference_code']}\n"
                        "Tipo de manobra: saída\n"
                        f"Hora prevista: {departure_planned_local}\n"
                        "Destino: Valencia\n"
                        "Calado: 9.80\n"
                        "Rebocadores: 2\n"
                        "Restrições: daylight\n"
                        "Observações: Saída criada via slash."
                    ),
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["action"], "schedule_departure")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

        self.assertEqual(response.status_code, 200)
        updated = self.store.get_port_call(port_call["id"])
        entry = next(item for item in updated["maneuver_history"] if item["type"] == "entry")
        departure = next(item for item in updated["maneuver_history"] if item["type"] == "departure")
        self.assertTrue(entry["planned_at"].startswith(entry_planned_local))
        self.assertEqual(entry["tug_count"], "2")
        self.assertEqual(departure["state"], "pending")
        self.assertEqual(departure["origin"], "TMS 2")
        self.assertEqual(departure["destination"], "Valencia")

    def test_slash_delete_scale_flow_via_chat(self) -> None:
        eta_local = self._future_local_value(days=7, hour=6, minute=30)

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        "/registar-escala\n"
                        "Nome do navio: AUTO DELETE\n"
                        f"ETA de chegada: {eta_local}\n"
                        "Cais previsto: TMS 2\n"
                        "Último porto: Sines\n"
                        "Próximo destino: Vigo\n"
                        "IMO: 9876544\n"
                        "Indicativo: CQAB8\n"
                        "Bandeira: Portugal\n"
                        "Tipo de navio: General Cargo\n"
                        "LOA (m): 142.50\n"
                        "Boca (m): 21.80\n"
                        "GT (t): 8950\n"
                        "DWT (t): 12400\n"
                        "Calado máximo (m): 7.20\n"
                        "Observações: Escala para apagar via slash."
                    ),
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            port_call = self.store.get_port_call(payload["port_call_id"])

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": f"/apagar-escala\nRef: {port_call['reference_code']}",
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["action"], "delete_port_call")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store._read_port_calls(), [])

    def test_slash_cancel_maneuver_alias_flow_via_chat(self) -> None:
        port_call = self._create_entry(notes="Escala criada para cancelar manobra via slash.")
        departure_planned_local = self._future_local_value(days=8, hour=8, minute=45)
        arrived_at = (datetime.now(timezone.utc) + timedelta(days=7, hours=1)).replace(microsecond=0).isoformat()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            self.store.approve_port_call(port_call["id"], decided_by="admin")
            self.store.mark_port_call_arrived(
                port_call["id"],
                arrived_at=arrived_at,
                updated_by="admin",
            )

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        "/criar-manobra\n"
                        f"Ref: {port_call['reference_code']}\n"
                        "Tipo de manobra: saída\n"
                        f"Hora prevista: {departure_planned_local}\n"
                        "Destino: Valencia\n"
                        "Calado: 9.80\n"
                        "Rebocadores: 2\n"
                        "Restrições: daylight\n"
                        "Observações: Saída para cancelar via slash."
                    ),
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            updated = self.store.get_port_call(payload["port_call_id"])
            departure = next(item for item in updated["maneuver_history"] if item["type"] == "departure")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": f"/cancelar-manobra\nID da manobra: {departure['id'][:8].upper()}",
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["action"], "delete_maneuver")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

        self.assertEqual(response.status_code, 200)
        final_port_call = self.store.get_port_call(port_call["id"])
        self.assertFalse(any(item["type"] == "departure" for item in final_port_call["maneuver_history"]))

    def test_slash_abort_maneuver_alias_flow_via_chat(self) -> None:
        port_call = self._create_entry(notes="Escala criada para abortar manobra via alias.")
        self._move_port_call_in_port(port_call["id"])

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
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")

            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": conversation["id"],
                    "question": (
                        "/abortar-manobra\n"
                        f"ID da manobra: {shift['id'][:8].upper()}\n"
                        f"Ref: {port_call['reference_code']}\n"
                        "Tipo de manobra: mudança\n"
                        "Motivo: nevoeiro"
                    ),
                },
            )

            payload = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(payload["answer_origin"], "slash_proposal")
            self.assertEqual(payload["pending_action"]["proposal"]["action"], "abort_shift")
            self.assertEqual(payload["pending_action"]["proposal"]["missing_fields"], [])

            response = client.post(
                "/api/chat/pending-action/confirm",
                json={"conversation_id": conversation["id"]},
            )

        self.assertEqual(response.status_code, 200)
        updated = self.store.get_port_call(port_call["id"])
        shift = next(item for item in updated["maneuver_history"] if item["type"] == "shift")
        self.assertEqual(shift["state"], "aborted")
        self.assertEqual(shift["aborted_reason"], "nevoeiro")
        self.assertEqual(updated["berth"], "TMS 2")

    def test_chat_empty_question_returns_error_code(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            response = client.post("/api/chat", json={"question": "   "})

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], 1001)
        self.assertEqual(payload["error_ref"], "#ERR-1001")

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
            (Path(self.store.knowledge_dir) / "berth_profiles.json").write_text(
                (Path(__file__).resolve().parents[1] / "knowledge" / "berth_profiles.json").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
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
        self.assertIn("é 280 metros", payload["answer"])
        self.assertIn("limite noturno de LOA", payload["answer"])
        self.assertIn("perfil operacional da instalação LISNAVE / Estaleiros Mitrena", payload["answer"])
        self.assertIn("período diurno", payload["answer"])
        self.assertNotEqual(
            (
                "280 metros. Na LISNAVE, navios com LOA até 280 metros podem manobrar "
                "de noite; acima disso, as manobras ficam limitadas ao período diurno."
            ),
            payload["answer"],
        )

    def test_slash_consult_scale_maneuver_and_vessel_queries(self) -> None:
        port_call = self._create_entry(notes="Consulta slash.")
        maneuver_id = port_call["maneuver_history"][0]["id"]

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            scale_response = client.post(
                "/api/chat",
                json={"question": f"/consultar-escala {port_call['reference_code']}"},
            )
            maneuver_response = client.post(
                "/api/chat",
                json={"question": f"/consultar-manobra {maneuver_id[:8]}"},
            )
            vessel_response = client.post(
                "/api/chat",
                json={"question": "/consultar-navio 9152923"},
            )
            cost_response = client.post(
                "/api/chat",
                json={"question": f"/consultar-manobra-custo {maneuver_id[:8]}"},
            )

        self.assertEqual(scale_response.status_code, 200)
        self.assertIn("Escala", scale_response.get_json()["answer"])
        self.assertIn("BELITAKI", scale_response.get_json()["answer"])
        self.assertEqual(maneuver_response.status_code, 200)
        self.assertIn("Manobra", maneuver_response.get_json()["answer"])
        self.assertNotIn("Total pilotagem", maneuver_response.get_json()["answer"])
        self.assertEqual(vessel_response.status_code, 200)
        self.assertIn("IMO: 9152923", vessel_response.get_json()["answer"])
        self.assertEqual(cost_response.status_code, 200)
        self.assertIn("Total pilotagem", cost_response.get_json()["answer"])

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

    def test_slash_tides_uses_topic_layout(self) -> None:
        with patch.object(services, "tide_service", _StubTideService()):
            with app.app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["username"] = "admin"
                    flask_session["role"] = "admin"

                response = client.post("/api/chat", json={"question": "/mares"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_tides")
        self.assertIn("🌕 Marés para 09/04/2026", payload["answer"])
        self.assertIn("\n- 01:48 — Baixa-mar de 1.3 m", payload["answer"])
        self.assertIn("\n- 08:02 — Preia-mar de 2.4 m", payload["answer"])

    def test_slash_local_warnings_lists_all_and_shows_selection_hint(self) -> None:
        warnings = [
            {
                "id": 88,
                "code": "88/26",
                "display_code": "Anav nr 88/26",
                "status_label": "Em vigor",
                "subject": "Trabalhos de mergulho",
                "location": "Portinho da Arrábida",
                "start_date_label": "29 abr 2026",
                "end_date_label": "01 mai 2026",
                "description_text": "Operação subaquática em curso.",
            },
            {
                "id": 77,
                "code": "77/26",
                "display_code": "Anav nr 77/26",
                "status_label": "Em vigor",
                "subject": "Pesca desportiva",
                "location": "Praia Atlântica",
                "start_date_label": "02 mai 2026",
                "end_date_label": "03 mai 2026",
                "description_text": "Prova de pesca desportiva.",
            },
        ]
        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            with app.app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["username"] = "admin"
                    flask_session["role"] = "admin"

                response = client.post("/api/chat", json={"question": "/avisos-locais"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_local_warnings")
        self.assertIn("Avisos locais em vigor (2)", payload["answer"])
        self.assertIn("Anav nr 88/26 · Trabalhos de mergulho · Portinho da Arrábida", payload["answer"])
        self.assertIn("Anav nr 77/26 · Pesca desportiva · Praia Atlântica", payload["answer"])
        self.assertIn("/avisos-locais 88/26", payload["answer"])

    def test_slash_local_warnings_returns_specific_warning_detail(self) -> None:
        warnings = [
            {
                "id": 88,
                "code": "88/26",
                "display_code": "Anav nr 88/26",
                "status_label": "Em vigor",
                "subject": "Trabalhos de mergulho",
                "location": "Portinho da Arrábida",
                "start_date_label": "29 abr 2026",
                "end_date_label": "01 mai 2026",
                "description_text": "Operação subaquática em curso.",
            },
            {
                "id": 77,
                "code": "77/26",
                "display_code": "Anav nr 77/26",
                "status_label": "Em vigor",
                "subject": "Pesca desportiva",
                "location": "Praia Atlântica",
                "start_date_label": "02 mai 2026",
                "end_date_label": "03 mai 2026",
                "description_text": "Prova de pesca desportiva.",
            },
        ]
        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            with app.app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["username"] = "admin"
                    flask_session["role"] = "admin"

                response = client.post("/api/chat", json={"question": "/avisos-locais 88/26"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_local_warnings")
        self.assertIn("Anav nr 88/26", payload["answer"])
        self.assertIn("Assunto: Trabalhos de mergulho", payload["answer"])
        self.assertIn("Local: Portinho da Arrábida", payload["answer"])
        self.assertIn("Descrição:\nOperação subaquática em curso.", payload["answer"])

    def test_slash_local_warnings_flags_ambiguous_numeric_code(self) -> None:
        warnings = [
            {
                "id": 77,
                "code": "77/26",
                "display_code": "Anav nr 77/26",
                "status_label": "Em vigor",
                "subject": "Pesca desportiva",
                "location": "Praia Atlântica",
                "start_date_label": "02 mai 2026",
                "end_date_label": "03 mai 2026",
                "description_text": "Prova de pesca desportiva.",
            },
            {
                "id": 177,
                "code": "77/25",
                "display_code": "Anav nr 77/25",
                "status_label": "Em vigor",
                "subject": "Fogo de artifício",
                "location": "Praia do Ouro",
                "start_date_label": "01 jul 2025",
                "end_date_label": "02 jul 2025",
                "description_text": "Evento pirotécnico.",
            },
        ]
        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            with app.app.test_client() as client:
                with client.session_transaction() as flask_session:
                    flask_session["username"] = "admin"
                    flask_session["role"] = "admin"

                response = client.post("/api/chat", json={"question": "/avisos-locais 77"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "slash_local_warnings")
        self.assertIn('Encontrei mais do que um aviso para "77"', payload["answer"])
        self.assertIn("Anav nr 77/26", payload["answer"])
        self.assertIn("Anav nr 77/25", payload["answer"])

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

    def test_chat_tempo_em_setubal_prefers_weather_not_static_documents(self) -> None:
        with patch.object(services, "weather_service", _StubWeatherService()):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={"question": "Como está o tempo em Setúbal?"},
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Condições meteorológicas atuais em Setúbal", payload["answer"])
        self.assertIn("Parcialmente nublado", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_current_weather_reports_fog_suspension(self) -> None:
        self._copy_safety_limits()

        weather_service = _StubHazardWeatherService(
            {
                "condition": "Nevoeiro",
                "wind_kts": 8.0,
                "gust_kts": 10.0,
                "vis_km": 0.7,
            }
        )
        with patch.object(services, "weather_service", weather_service):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={"question": "Qual é a visibilidade atual no porto?"},
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Manobras suspensas neste momento", payload["answer"])
        self.assertIn("visibilidade restaurada", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_current_weather_reports_wind_suspension_above_30_kts(self) -> None:
        self._copy_safety_limits()

        weather_service = _StubHazardWeatherService(
            {
                "condition": "Céu limpo",
                "wind_kts": 31.0,
                "gust_kts": 34.0,
                "vis_km": 10,
            }
        )
        with patch.object(services, "weather_service", weather_service):
            with patch.object(services.rag, "answer") as answer_mock:
                with app.app.test_client() as client:
                    with client.session_transaction() as flask_session:
                        flask_session["username"] = "admin"
                        flask_session["role"] = "admin"

                    response = client.post(
                        "/api/chat",
                        json={"question": "Qual é o vento atual no porto?"},
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertIn("Manobras suspensas neste momento", payload["answer"])
        self.assertIn("superior a 30", payload["answer"])
        self.assertIn("abaixo de 25", payload["answer"])
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

    def test_chat_local_warnings_count_question_returns_only_total(self) -> None:
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
            {
                "display_code": "Anav nr 104",
                "subject": "Fogo de artifício",
                "location": "Praia do Ouro",
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
                            "question": "Quantos avisos locais existem em vigor?",
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "operational_live")
        self.assertEqual(payload["answer"], "⚠️ Existem 3 aviso(s) locais em vigor.")
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

    def test_chat_lisnave_questions_use_specific_companion_intent(self) -> None:
        document_name = "IT-014_Lisnave.txt"
        (Path(self.store.knowledge_dir) / document_name).write_text(
            (Path(__file__).resolve().parents[1] / "knowledge" / document_name).read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )
        companion_payload = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "knowledge"
                / "companions"
                / "IT-014_Lisnave.json"
            ).read_text(encoding="utf-8")
        )
        self._write_knowledge_companion(document_name, companion_payload)
        self.store.list_documents()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                draft_response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Qual é o calado máximo para um navio que vai para a LISNAVE?",
                    },
                )
                inventory_response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Quais são os cais e as docas da LISNAVE?",
                    },
                )
                detail_response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Dá-me mais detalhes sobre a LISNAVE",
                    },
                )

        self.assertEqual(draft_response.status_code, 200)
        draft_payload = draft_response.get_json()
        self.assertEqual(draft_payload["answer_origin"], "document_companion")
        self.assertIn("Não há um calado máximo único", draft_payload["answer"])
        self.assertIn("Cais III-B 8,60 m", draft_payload["answer"])
        self.assertNotIn("período diurno", draft_payload["answer"])

        self.assertEqual(inventory_response.status_code, 200)
        inventory_payload = inventory_response.get_json()
        self.assertEqual(inventory_payload["answer_origin"], "document_companion")
        self.assertIn("Docas secas 20, 21 e 22", inventory_payload["answer"])
        self.assertIn("Hidrolift com acesso às Docas secas 31, 32 e 33", inventory_payload["answer"])
        self.assertNotEqual(
            "O Cais III-B, com sonda ao ZH de 8,60 metros a 10 metros da face do cais.",
            inventory_payload["answer"],
        )

        self.assertEqual(detail_response.status_code, 200)
        detail_payload = detail_response.get_json()
        self.assertEqual(detail_payload["answer_origin"], "document_companion")
        self.assertIn("zona de reparação e construção naval", detail_payload["answer"])
        self.assertIn("Para calado", detail_payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_lisnave_night_loa_decision_prefers_berth_profile_over_companion(self) -> None:
        document_name = "IT-014_Lisnave.txt"
        repo_knowledge = Path(__file__).resolve().parents[1] / "knowledge"
        (Path(self.store.knowledge_dir) / document_name).write_text(
            (repo_knowledge / document_name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (Path(self.store.knowledge_dir) / "berth_profiles.json").write_text(
            (repo_knowledge / "berth_profiles.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        companion_payload = json.loads(
            (repo_knowledge / "companions" / "IT-014_Lisnave.json").read_text(encoding="utf-8")
        )
        self._write_knowledge_companion(document_name, companion_payload)
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
                        "question": "À noite psso manobrar um navio com 285m de comprimento na Lisnave?",
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "berth_profile")
        self.assertTrue(payload["answer"].startswith("Nao."))
        self.assertIn("285 m", payload["answer"])
        self.assertIn("280 m", payload["answer"])
        self.assertIn("periodo diurno", payload["answer"])
        answer_mock.assert_not_called()

    def test_chat_teporset_followup_changes_focus_between_overview_and_rules(self) -> None:
        document_name = "IT-062_Teporset.txt"
        repo_knowledge = Path(__file__).resolve().parents[1] / "knowledge"
        (Path(self.store.knowledge_dir) / document_name).write_text(
            (repo_knowledge / document_name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        companion_payload = json.loads(
            (repo_knowledge / "companions" / "IT-062_Teporset.json").read_text(encoding="utf-8")
        )
        self._write_knowledge_companion(document_name, companion_payload)
        self.store.list_documents()

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=False), patch.object(
                services.rag,
                "answer",
            ) as answer_mock:
                overview_response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "O que me podes dizer sobre o cais da Teporset em termos gerais?",
                    },
                )
                rules_response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Quais são as regras para o cais da Teporset?",
                    },
                )

        self.assertEqual(overview_response.status_code, 200)
        overview_payload = overview_response.get_json()
        self.assertEqual(overview_payload["answer_origin"], "document_companion")
        self.assertFalse(overview_payload["answer"].startswith("O que me podes dizer"))
        self.assertIn("Terminal Portuário de Setúbal", overview_payload["answer"])
        self.assertIn("calado calculado", overview_payload["answer"])

        self.assertEqual(rules_response.status_code, 200)
        rules_payload = rules_response.get_json()
        self.assertEqual(rules_payload["answer_origin"], "document_companion")
        self.assertFalse(rules_payload["answer"].startswith("Quais são as regras"))
        self.assertIn("7,4 metros", rules_payload["answer"])
        self.assertIn("11,0 metros", rules_payload["answer"])
        self.assertIn("Piloto Coordenador", rules_payload["answer"])
        self.assertNotEqual(overview_payload["answer"], rules_payload["answer"])
        self.assertNotEqual(rules_payload["answer"], "164 metros de comprimento físico.")
        answer_mock.assert_not_called()

    def test_chat_port_facility_inventory_uses_canonical_companion_without_duplicate_aliases(self) -> None:
        repo_knowledge = Path(__file__).resolve().parents[1] / "knowledge"
        for document_name in [
            "Porto_Setubal_Terminais_Cais.txt",
            "IT-015_Fundeadouros.txt",
        ]:
            (Path(self.store.knowledge_dir) / document_name).write_text(
                (repo_knowledge / document_name).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        self._write_knowledge_companion(
            "IT-015_Fundeadouros.txt",
            json.loads((repo_knowledge / "companions" / "IT-015_Fundeadouros.json").read_text(encoding="utf-8")),
        )
        self._write_knowledge_companion(
            "Porto_Setubal_Terminais_Cais.txt",
            json.loads(
                (repo_knowledge / "companions" / "Porto_Setubal_Terminais_Cais.json").read_text(
                    encoding="utf-8"
                )
            ),
        )
        self.store.list_documents()

        def synthesize_from_sources(**kwargs):
            question = kwargs["question"]
            if "terminais" in question.lower():
                answer = (
                    "Os principais terminais e cais operacionais de Setúbal são:\n\n"
                    "- Cais SECIL W/E\n"
                    "- Terminal Multiusos 1 (TMS1 / Cais das Fontainhas)\n"
                    "- Terminal Multiusos 2 (TMS2 / Terminal de Contentores)\n"
                    "- Terminal Autoeuropa / Ro-Ro\n"
                    "- SAPEC Sólidos e SAPEC Líquidos\n\n"
                    "Nota: estes nomes já agrupam aliases operacionais."
                )
            else:
                answer = (
                    "No catálogo operacional do portal existem 36 slots operacionais.\n\n"
                    "- Cais SECIL W/E\n"
                    "- Terminal Multiusos 1 (TMS1 / Cais das Fontainhas)\n"
                    "- Terminal Multiusos 2 (TMS2 / Terminal de Contentores)\n"
                    "- Terminal Autoeuropa / Ro-Ro\n"
                    "- LISNAVE / Mitrena, incluindo Hidrolift para as docas secas 31, 32 e 33\n\n"
                    "Nota: fundeadouros não contam como cais."
                )
            return {"answer": answer, "sources": kwargs.get("supplemental_sources", [])}

        with app.app.test_client() as client:
            self._login_client_as_admin(client)
            conversation = self.store.ensure_conversation(username="admin")
            with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                services.rag,
                "answer",
                side_effect=synthesize_from_sources,
            ) as answer_mock:
                terminals_response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Quais são os terminais que existem no porto de Setúbal?",
                    },
                )
                quays_response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation["id"],
                        "question": "Quantos cais existem em Setúbal?",
                    },
                )

        self.assertEqual(terminals_response.status_code, 200)
        terminals_payload = terminals_response.get_json()
        self.assertEqual(terminals_payload["answer_origin"], "llm")
        self.assertIn("Terminal Multiusos 1 (TMS1 / Cais das Fontainhas)", terminals_payload["answer"])
        self.assertIn("Terminal Multiusos 2 (TMS2 / Terminal de Contentores)", terminals_payload["answer"])
        self.assertIn("Terminal Autoeuropa / Ro-Ro", terminals_payload["answer"])
        self.assertNotIn("Terminal Multiusos Norte", terminals_payload["answer"])
        self.assertNotIn("Terminal Multiusos Sul", terminals_payload["answer"])
        self.assertNotIn("Existem quatro zonas de fundeio", terminals_payload["answer"])

        self.assertEqual(quays_response.status_code, 200)
        quays_payload = quays_response.get_json()
        self.assertEqual(quays_payload["answer_origin"], "llm")
        self.assertIn("36 slots operacionais", quays_payload["answer"])
        self.assertIn("fundeadouros não contam como cais", quays_payload["answer"].lower())
        self.assertNotIn("Terminal Multiusos Norte", quays_payload["answer"])
        self.assertNotIn("Terminal Multiusos Sul", quays_payload["answer"])
        self.assertNotIn("Berço Ro-Ro (na extremidade leste", quays_payload["answer"])

        self.assertEqual(answer_mock.call_count, 2)
        first_sources = answer_mock.call_args_list[0].kwargs["supplemental_sources"]
        source_snippets = "\n".join(source.get("snippet", "") for source in first_sources)
        self.assertIn("Factos operacionais validados para síntese", source_snippets)
        self.assertIn("nao usar terminal multiusos norte/sul", source_snippets.lower())

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

    def test_chat_initial_weather_tug_recommendation_uses_llm_with_it016_context(self) -> None:
        document_name = "IT-016_Rebocadores.txt"
        knowledge_path = Path(self.store.knowledge_dir) / document_name
        knowledge_path.write_text(
            "DOCUMENTO: IT-016 — REBOCADORES\n"
            "A tabela de rebocadores é orientativa na generalidade, mas define mínimos para cargas perigosas.\n"
            "Para atracar, ponderar DWT, estado carregado/vazio e bow/stern thruster.\n",
            encoding="utf-8",
        )
        self._write_knowledge_companion(
            document_name,
            {
                "document": document_name,
                "title": "IT-016 Rebocadores",
                "aliases": ["IT-016", "rebocadores", "reboques"],
                "summary": "Define o enquadramento de rebocadores nas manobras do Porto de Setúbal.",
                "key_points": [
                    "A recomendação depende de DWT, carga, estado carregado/vazio e bow/stern thruster.",
                    "A meteorologia live deve ser usada como condicionante operacional.",
                ],
                "faq": [
                    {
                        "question": "Quantos rebocadores devo recomendar?",
                        "answer": "Cruza a IT-016 com DWT, carga, estado carregado/vazio, thrusters e condições atuais.",
                        "keywords": ["quantos", "reboques", "rebocadores", "recomendacao", "atracar"],
                    }
                ],
            },
        )
        self._copy_tug_guidance()
        self._copy_safety_limits()
        self.store.list_documents()

        with patch.object(services, "weather_service", _StubWeatherService()):
            with app.app.test_client() as client:
                self._login_client_as_admin(client)
                conversation = self.store.ensure_conversation(username="admin")

                with patch.object(services.rag, "can_generate", return_value=True), patch.object(
                    services.rag,
                    "answer",
                    return_value={
                        "answer": "Com vento atual moderado, usaria a IT-016 e deixaria a decisão condicionada aos thrusters.",
                        "sources": [],
                    },
                ) as answer_mock:
                    response = client.post(
                        "/api/chat",
                        json={
                            "conversation_id": conversation["id"],
                            "question": (
                                "Com o estado de vento atual em porto, se fosse atracar um navio RORO "
                                "com 180m, quantos reboques recomendavas?"
                            ),
                        },
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer_origin"], "llm")
        answer_mock.assert_called_once()
        supplemental_sources = answer_mock.call_args.kwargs["supplemental_sources"]
        documents = {str(item.get("document") or "") for item in supplemental_sources}
        retrieval_modes = {str(item.get("retrieval_mode") or "") for item in supplemental_sources}
        execution_plan = answer_mock.call_args.kwargs["execution_plan"]
        conversation_state = answer_mock.call_args.kwargs["conversation_state"]
        self.assertEqual(execution_plan["primary_intent"], "live_reasoning")
        self.assertTrue(execution_plan["wants_documents"])
        self.assertTrue(execution_plan["needs_answer_critic"])
        self.assertIn("Meteorologia live", documents)
        self.assertIn(document_name, documents)
        self.assertIn("operational_safety_limits.json", documents)
        self.assertIn("tug_operational_guidance.json", documents)
        self.assertIn("live_planner", retrieval_modes)
        self.assertIn("operational_safety_limits", retrieval_modes)
        self.assertIn("operational_tug_guidance", retrieval_modes)
        self.assertIn("document_target", retrieval_modes)
        self.assertIn("document_companion", retrieval_modes)
        self.assertIn("Ro-Ro", conversation_state["summary"])
        self.assertIn("180 m", conversation_state["summary"])

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
                "subject": "Sondagens na barra e balização temporária",
                "location": "Barra",
                "description_text": "Sondagens hidrográficas com embarcação de apoio e área sinalizada com balização.",
                "excerpt": "Sondagens hidrográficas com embarcação de apoio e área sinalizada com balização.",
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
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        body = response.data.decode("utf-8-sig")
        self.assertIn("Anav nr 102", body)
        self.assertNotIn("Anav nr 101", body)
        self.assertIn("Croqui", body)
        self.assertIn("balização temporária", body)
        self.assertIn("hidrográficas", body)

    def test_local_warnings_report_pdf_returns_pdf_bytes(self) -> None:
        warnings = [
            {
                "id": 201,
                "display_code": "Anav nr 201",
                "subject": "Balização temporária",
                "location": "Canal Sul",
                "description_text": "Balização provisória em vigor com sinalização náutica.",
                "excerpt": "Balização provisória em vigor com sinalização náutica.",
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
        self.assertIn("Balização temporária".encode("cp1252"), response.data)
        self.assertIn("sinalização náutica".encode("cp1252"), response.data)
        self.assertIn(b"/WinAnsiEncoding", response.data)

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

    def test_local_warnings_page_is_available_to_all_authenticated_roles(self) -> None:
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
            for role in ("admin", "agente", "piloto"):
                with self.subTest(role=role):
                    with app.app.test_client() as client:
                        self._login_client_as_role(client, role)
                        response = client.get("/warnings/local")

                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    self.assertIn("Avisos locais", html)
                    self.assertIn("Anav nr 301", html)

    def test_local_warning_detail_is_available_to_all_authenticated_roles(self) -> None:
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
                "cancel_date_label": "06 abr 2026",
                "start_date_iso": "2026-04-03T08:00:00+00:00",
                "end_date_iso": "2026-04-06T17:00:00+00:00",
                "has_attachments": False,
                "attachments": [],
                "entity_name": "Capitania do Porto de Setúbal",
            },
        ]

        with patch.object(services, "local_warning_service", _StubLocalWarningService(warnings)):
            for role in ("admin", "agente", "piloto"):
                with self.subTest(role=role):
                    with app.app.test_client() as client:
                        self._login_client_as_role(client, role)
                        response = client.get("/warnings/local/301")

                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    self.assertIn("Anav nr 301", html)
                    self.assertIn("Corrente forte na barra", html)

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
        self.assertIn("Resposta do bot", whatsapp_service.sent_messages[0]["text"])
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

    def test_whatsapp_sos_request_asks_for_current_location(self) -> None:
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        payload = self._whatsapp_text_payload(message_id="wamid.SOS1", text="SOS")

        with patch.dict(os.environ, {"WHATSAPP_SOS_ENABLED": "1", "WHATSAPP_SOS_ALERT_NUMBERS": ""}):
            with patch.object(services, "whatsapp_service", whatsapp_service):
                with app.app.test_client() as client:
                    response = client.post("/webhooks/whatsapp", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 1)
        self.assertIn("🛟⚠️ SOS recebido", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("Partilha já a tua localização", whatsapp_service.sent_messages[0]["text"])
        self.assertTrue(self.store.get_runtime_state("whatsapp:sos:pending:351962063664"))
        channel_events = self.store._read_channel_events()
        self.assertEqual(channel_events[0]["event_type"], "incoming_sos_request")
        self.assertEqual(channel_events[1]["event_type"], "outgoing_sos_location_prompt")

    def test_whatsapp_sos_request_can_be_cancelled_before_location(self) -> None:
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        request_payload = self._whatsapp_text_payload(message_id="wamid.SOS_CANCEL1", text="SOS")
        cancel_payload = self._whatsapp_text_payload(
            message_id="wamid.SOS_CANCEL2",
            text="cancelar",
            timestamp="1712165460",
        )

        with patch.dict(os.environ, {"WHATSAPP_SOS_ENABLED": "1", "WHATSAPP_SOS_ALERT_NUMBERS": ""}):
            with patch.object(services, "whatsapp_service", whatsapp_service):
                with app.app.test_client() as client:
                    first = client.post("/webhooks/whatsapp", json=request_payload)
                    second = client.post("/webhooks/whatsapp", json=cancel_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 2)
        self.assertIn("SOS recebido", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("Pedido SOS cancelado", whatsapp_service.sent_messages[1]["text"])
        self.assertFalse(self.store.get_runtime_state("whatsapp:sos:pending:351962063664"))
        event_types = [event["event_type"] for event in self.store._read_channel_events()]
        self.assertIn("incoming_sos_cancel", event_types)
        self.assertIn("outgoing_sos_cancelled", event_types)

    def test_whatsapp_sos_location_alerts_admin_and_confirms_user(self) -> None:
        self.store.update_user_profile(
            "admin",
            full_name="Admin Porto",
            organization="APSS",
            email="admin@apss.pt",
            phone="+351 900 000 001",
            whatsapp_number="351900000001",
            whatsapp_opt_in=True,
        )
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664", "351900000001"})
        request_payload = self._whatsapp_text_payload(message_id="wamid.SOS2", text="Emergência")
        location_payload = self._whatsapp_location_payload(message_id="wamid.SOS3")

        with patch.dict(os.environ, {"WHATSAPP_SOS_ENABLED": "1", "WHATSAPP_SOS_ALERT_NUMBERS": ""}):
            with patch.object(services, "whatsapp_service", whatsapp_service):
                with app.app.test_client() as client:
                    first = client.post("/webhooks/whatsapp", json=request_payload)
                    second = client.post("/webhooks/whatsapp", json=location_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["delivered"], 2)
        self.assertEqual(len(whatsapp_service.sent_messages), 3)
        admin_alert = whatsapp_service.sent_messages[1]
        user_confirmation = whatsapp_service.sent_messages[2]
        self.assertEqual(admin_alert["to_number"], "351900000001")
        self.assertEqual(admin_alert["reply_to_message_id"], "")
        self.assertIn("🛟⚠️ ALERTA SOS", admin_alert["text"])
        self.assertIn("Telefone: +351962063664", admin_alert["text"])
        self.assertIn("Localização: 38.524400, -8.888200", admin_alert["text"])
        self.assertIn("https://maps.google.com/?q=38.524400,-8.888200", admin_alert["text"])
        self.assertEqual(user_confirmation["to_number"], "351962063664")
        self.assertIn("✅🛟 Localização recebida", user_confirmation["text"])
        self.assertFalse(self.store.get_runtime_state("whatsapp:sos:pending:351962063664"))
        channel_events = self.store._read_channel_events()
        event_types = [event["event_type"] for event in channel_events]
        self.assertIn("incoming_sos_request", event_types)
        self.assertIn("incoming_sos_location", event_types)
        self.assertIn("outgoing_sos_alert", event_types)
        self.assertIn("outgoing_sos_confirmation", event_types)

    def test_whatsapp_sos_from_admin_does_not_alert_same_number(self) -> None:
        self.store.update_user_profile(
            "admin",
            full_name="Admin Porto",
            organization="APSS",
            email="admin@apss.pt",
            phone="+351 962 063 664",
            whatsapp_number="351962063664",
            whatsapp_opt_in=True,
        )
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        request_payload = self._whatsapp_text_payload(message_id="wamid.SOS_ADMIN1", text="SOS")
        location_payload = self._whatsapp_location_payload(message_id="wamid.SOS_ADMIN2")

        with patch.dict(os.environ, {"WHATSAPP_SOS_ENABLED": "1", "WHATSAPP_SOS_ALERT_NUMBERS": ""}):
            with patch.object(services, "whatsapp_service", whatsapp_service):
                with app.app.test_client() as client:
                    first = client.post("/webhooks/whatsapp", json=request_payload)
                    second = client.post("/webhooks/whatsapp", json=location_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 2)
        self.assertIn("SOS recebido", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("não encontrei contacto de emergência externo", whatsapp_service.sent_messages[1]["text"])
        self.assertFalse(any("ALERTA SOS" in message["text"] for message in whatsapp_service.sent_messages))

    def test_whatsapp_sos_dispatched_alert_can_be_cancelled_and_admin_is_notified(self) -> None:
        self.store.update_user_profile(
            "admin",
            full_name="Admin Porto",
            organization="APSS",
            email="admin@apss.pt",
            phone="+351 900 000 001",
            whatsapp_number="351900000001",
            whatsapp_opt_in=True,
        )
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664", "351900000001"})
        request_payload = self._whatsapp_text_payload(message_id="wamid.SOS_SENT_CANCEL1", text="SOS")
        location_payload = self._whatsapp_location_payload(message_id="wamid.SOS_SENT_CANCEL2")
        cancel_payload = self._whatsapp_text_payload(
            message_id="wamid.SOS_SENT_CANCEL3",
            text="Cancelar",
            timestamp="1712165520",
        )

        with patch.dict(os.environ, {"WHATSAPP_SOS_ENABLED": "1", "WHATSAPP_SOS_ALERT_NUMBERS": ""}):
            with patch.object(services, "whatsapp_service", whatsapp_service):
                with app.app.test_client() as client:
                    first = client.post("/webhooks/whatsapp", json=request_payload)
                    second = client.post("/webhooks/whatsapp", json=location_payload)
                    third = client.post("/webhooks/whatsapp", json=cancel_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["delivered"], 2)
        self.assertEqual(third.get_json()["delivered"], 2)
        self.assertEqual(len(whatsapp_service.sent_messages), 5)
        self.assertIn("ALERTA SOS", whatsapp_service.sent_messages[1]["text"])
        self.assertIn("CANCELAMENTO SOS", whatsapp_service.sent_messages[3]["text"])
        self.assertEqual(whatsapp_service.sent_messages[3]["to_number"], "351900000001")
        self.assertIn("Avisei o contacto de emergência", whatsapp_service.sent_messages[4]["text"])
        event_types = [event["event_type"] for event in self.store._read_channel_events()]
        self.assertIn("outgoing_sos_cancel_alert", event_types)
        self.assertIn("outgoing_sos_dispatched_cancelled", event_types)

    def test_whatsapp_sos_location_after_expiry_does_not_alert_admin(self) -> None:
        self.store.update_user_profile(
            "admin",
            full_name="Admin Porto",
            organization="APSS",
            email="admin@apss.pt",
            phone="+351 900 000 001",
            whatsapp_number="351900000001",
            whatsapp_opt_in=True,
        )
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664", "351900000001"})
        request_payload = self._whatsapp_text_payload(message_id="wamid.SOS_EXPIRED1", text="SOS")
        location_payload = self._whatsapp_location_payload(message_id="wamid.SOS_EXPIRED2")

        with patch.dict(
            os.environ,
            {
                "WHATSAPP_SOS_ENABLED": "1",
                "WHATSAPP_SOS_ALERT_NUMBERS": "",
                "WHATSAPP_SOS_PENDING_TTL_MINUTES": "1",
            },
        ):
            with patch.object(services, "whatsapp_service", whatsapp_service):
                with app.app.test_client() as client:
                    first = client.post("/webhooks/whatsapp", json=request_payload)
                    pending = self.store.get_runtime_state("whatsapp:sos:pending:351962063664")
                    pending["requested_at"] = "2000-01-01T00:00:00+00:00"
                    self.store.set_runtime_state("whatsapp:sos:pending:351962063664", pending)
                    second = client.post("/webhooks/whatsapp", json=location_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 2)
        self.assertIn("SOS recebido", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("já expirou", whatsapp_service.sent_messages[1]["text"])
        self.assertFalse(self.store.get_runtime_state("whatsapp:sos:pending:351962063664"))
        self.assertFalse(
            any("ALERTA SOS" in message["text"] for message in whatsapp_service.sent_messages)
        )

    def test_whatsapp_location_without_pending_sos_does_not_alert_admin(self) -> None:
        self.store.update_user_profile(
            "admin",
            full_name="Admin Porto",
            organization="APSS",
            email="admin@apss.pt",
            phone="+351 900 000 001",
            whatsapp_number="351900000001",
            whatsapp_opt_in=True,
        )
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664", "351900000001"})
        location_payload = self._whatsapp_location_payload(message_id="wamid.LOC1")

        with patch.dict(os.environ, {"WHATSAPP_SOS_ENABLED": "1", "WHATSAPP_SOS_ALERT_NUMBERS": ""}):
            with patch.object(services, "whatsapp_service", whatsapp_service):
                with app.app.test_client() as client:
                    response = client.post("/webhooks/whatsapp", json=location_payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 1)
        self.assertEqual(whatsapp_service.sent_messages[0]["to_number"], "351962063664")
        self.assertIn("não há pedido SOS ativo", whatsapp_service.sent_messages[0]["text"])

    def test_reportar_evento_web_archives_without_photo(self) -> None:
        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"

            first = client.post(
                "/api/chat",
                json={
                    "question": "/reportar_evento AVARIA | cais Teporset | o guincho do cais nao esta a funcionar"
                },
            )
            second = client.post("/api/chat", json={"question": "não"})

        self.assertEqual(first.status_code, 200)
        self.assertIn("Queres anexar uma foto", first.get_json()["answer"])
        self.assertEqual(second.status_code, 200)
        answer = second.get_json()["answer"]
        self.assertIn("Reporte de evento registado", answer)
        self.assertIn("EVT-", answer)
        events_path = Path(os.environ["EVENT_REPORTS_DIR"]) / "eventos.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        self.assertEqual(events[0]["tag"], "AVARIA")
        self.assertEqual(events[0]["local"], "cais Teporset")
        self.assertFalse(events[0]["foto_path"])

    def test_reportar_evento_prefers_dot_template_but_keeps_pipe_compatibility(self) -> None:
        parsed = parse_event_report_command(
            "AVARIA. cais Teporset. o guincho do cais nao esta a funcionar. precisa de intervenção"
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["draft"]["tag"], "AVARIA")
        self.assertEqual(parsed["draft"]["local"], "cais Teporset")
        self.assertEqual(
            parsed["draft"]["description_original"],
            "o guincho do cais nao esta a funcionar. precisa de intervenção",
        )

        legacy = parse_event_report_command("DANO | navio ATLANTIC STAR | amassadela na amurada")
        self.assertTrue(legacy["ok"])
        self.assertEqual(legacy["draft"]["local"], "navio ATLANTIC STAR")
        self.assertIn("/reportar_evento TAG. LOCAL. DESCRIPTION", build_event_report_template(["LOCAL"]))

    def test_admin_event_reports_page_supports_review_edit_and_print(self) -> None:
        event = register_event_report(
            {
                "tag": "AVARIA",
                "local": "cais Teporset",
                "description_original": "o guincho do cais nao esta a funcionar",
            },
            username="admin",
            role="admin",
            user_label="Admin",
            description_processed="O guincho do cais não está a funcionar.",
        )

        with app.app.test_client() as client:
            with client.session_transaction() as flask_session:
                flask_session["username"] = "admin"
                flask_session["role"] = "admin"
                flask_session["_csrf_token"] = "event-token"

            response = client.get("/admin/event-reports?q=Teporset")
            self.assertEqual(response.status_code, 200)
            html = response.get_data(as_text=True)
            self.assertIn("Reportes de evento", html)
            self.assertIn(event["id"], html)

            detail = client.get(f"/admin/event-reports/{event['id']}?return_to=/admin/event-reports")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("Guardar revisão", detail.get_data(as_text=True))

            update = client.post(
                f"/admin/event-reports/{event['id']}/edit",
                data={
                    "csrf_token": "event-token",
                    "return_to": "/admin/event-reports",
                    "tag": "FALHA",
                    "estado": "resolvido",
                    "local": "cais Teporset 2",
                    "descricao_original": "o guincho do cais nao esta a funcionar",
                    "descricao_processada": "Guincho operacional após verificação.",
                    "nota_admin": "Validado pela coordenação.",
                },
            )
            self.assertEqual(update.status_code, 302)

            report = client.get(f"/admin/event-reports/print?ids={event['id']}")
            self.assertEqual(report.status_code, 200)
            self.assertIn("Relatório de eventos operacionais", report.get_data(as_text=True))

        events_path = Path(os.environ["EVENT_REPORTS_DIR"]) / "eventos.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        self.assertEqual(events[0]["tag"], "FALHA")
        self.assertEqual(events[0]["estado"], "resolvido")
        self.assertEqual(events[0]["nota_admin"], "Validado pela coordenação.")

    def test_whatsapp_reportar_evento_accepts_photo_and_archives_file(self) -> None:
        whatsapp_service = _StubWhatsAppService(allowed_numbers={"351962063664"})
        whatsapp_service.media_downloads["media-1"] = {
            "bytes": b"fake-jpeg",
            "mime_type": "image/jpeg",
            "filename": "guincho.jpg",
        }
        command_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.EVT1",
                                        "from": "351962063664",
                                        "timestamp": "1712165400",
                                        "type": "text",
                                        "text": {
                                            "body": "/reportar_evento DANO | navio ATLANTIC STAR | amassadela na amurada"
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        photo_payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351962063664", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.EVT2",
                                        "from": "351962063664",
                                        "timestamp": "1712165460",
                                        "type": "image",
                                        "image": {
                                            "id": "media-1",
                                            "mime_type": "image/jpeg",
                                            "caption": "",
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
                first = client.post("/webhooks/whatsapp", json=command_payload)
                second = client.post("/webhooks/whatsapp", json=photo_payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.get_json()["delivered"], 1)
        self.assertEqual(second.get_json()["delivered"], 1)
        self.assertIn("Queres anexar uma foto", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("Reporte de evento registado", whatsapp_service.sent_messages[1]["text"])
        self.assertIn("1 foto guardada", whatsapp_service.sent_messages[1]["text"])
        events_path = Path(os.environ["EVENT_REPORTS_DIR"]) / "eventos.json"
        events = json.loads(events_path.read_text(encoding="utf-8"))
        self.assertEqual(events[0]["tag"], "DANO")
        self.assertTrue(Path(events[0]["foto_path"]).exists())

    def test_whatsapp_webhook_sends_error_code_when_chat_runtime_fails(self) -> None:
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
                                        "id": "wamid.FAIL123",
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
            with patch("blueprints.whatsapp.handle_chat_turn", side_effect=RuntimeError("provider down")):
                with app.app.test_client() as client:
                    response = client.post("/webhooks/whatsapp", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 1)
        self.assertIn("#ERR-9001", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("Contacta o suporte", whatsapp_service.sent_messages[0]["text"])

    def test_whatsapp_webhook_does_not_send_error_when_only_outbound_metadata_fails(self) -> None:
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
                                        "id": "wamid.META123",
                                        "from": "351962063664",
                                        "timestamp": "1712165400",
                                        "type": "text",
                                        "text": {"body": "/help"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        with patch.object(services, "whatsapp_service", whatsapp_service):
            with patch.object(
                self.store,
                "update_message_channel_metadata",
                side_effect=RuntimeError("metadata down"),
            ):
                with app.app.test_client() as client:
                    response = client.post("/webhooks/whatsapp", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delivered"], 1)
        self.assertEqual(len(whatsapp_service.sent_messages), 1)
        self.assertIn("Comandos disponíveis", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("SOS", whatsapp_service.sent_messages[0]["text"])
        self.assertIn("CANCELAR SOS", whatsapp_service.sent_messages[0]["text"])
        self.assertNotIn("#ERR-9001", whatsapp_service.sent_messages[0]["text"])

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
        self.assertEqual(whatsapp_service.sent_messages[0]["text"], "👋 Bem-vindo ao PRAGtico\n\nEm que posso ajudar?")
        self.assertIn("Resposta do bot", whatsapp_service.sent_messages[1]["text"])
        self.assertIn("Resposta do bot", whatsapp_service.sent_messages[2]["text"])
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
        self.assertIn("Exportar bot", html)
        self.assertIn("Base do sistema", html)

    def test_admin_bot_casebooks_support_search_and_compact_limit(self) -> None:
        conversation = self.store.create_conversation("admin", "Teste LISNAVE")
        self.store.append_chat_message(
            "admin",
            conversation["id"],
            "user",
            "Quais sao as docas da Lisnave?",
        )
        self.store.append_chat_message(
            "admin",
            conversation["id"],
            "assistant",
            "A Lisnave tem docas secas 20, 21, 22 e o Hidrolift para 31, 32 e 33.",
            channel="whatsapp",
        )

        with app.app.test_client() as client:
            self._set_admin_session(client)
            response = client.get("/admin/bot?q=Lisnave&source_type=chat&chat_feedback=all&limit=8")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="q" value="Lisnave"', html)
        self.assertIn("A Lisnave tem docas secas", html)
        self.assertIn("Mostra 1 de 1", html)
        self.assertIn("Chat / WhatsApp", html)

    def test_admin_exports_bot_and_system_databases_as_json(self) -> None:
        self.store.upsert_feedback_eval_case(
            source_message_id="import-test",
            document="IT-014_Lisnave.txt",
            question="Quais sao as docas da Lisnave?",
            expected_answer="D31, D32 e D33 sao docas secas com acesso por Hidrolift.",
            expected_substrings=["Hidrolift"],
            updated_by="admin",
            source="web",
        )

        with app.app.test_client() as client:
            self._set_admin_session(client)
            bot_response = client.get("/admin/bot/export")
            system_response = client.get("/admin/system/export")

        self.assertEqual(bot_response.status_code, 200)
        bot_payload = json.loads(bot_response.get_data(as_text=True))
        self.assertEqual(bot_payload["kind"], "pragtico.bot_database_export")
        self.assertEqual(bot_payload["payload"]["feedback_eval_cases"][0]["document"], "IT-014_Lisnave.txt")

        self.assertEqual(system_response.status_code, 200)
        system_payload = json.loads(system_response.get_data(as_text=True))
        self.assertEqual(system_payload["kind"], "pragtico.system_database_export")
        self.assertIn("users", system_payload["payload"]["data_files"])
        self.assertIn("bot_database", system_payload["payload"])

    def test_admin_imports_bot_and_system_database_json(self) -> None:
        bot_payload = {
            "kind": "pragtico.bot_database_export",
            "version": 1,
            "payload": {
                "feedback_eval_cases": [
                    {
                        "source_message_id": "bot-import-1",
                        "document": "Porto_Setubal_Terminais_Cais.txt",
                        "question": "Quantos cais existem em Setubal?",
                        "expected_answer": "36 slots operacionais, sem duplicar aliases de terminais.",
                        "expected_substrings": ["36 slots"],
                        "source": "web",
                    }
                ]
            },
        }
        system_payload = {
            "kind": "pragtico.system_database_export",
            "version": 1,
            "payload": {
                "data_files": {
                    "runtime_state": {
                        "admin_import_test": {"ok": True}
                    }
                },
                "knowledge_files": [
                    {
                        "path": "admin_import_test.txt",
                        "content": "Conhecimento importado.",
                    }
                ],
            },
        }

        with app.app.test_client() as client:
            csrf_token = self._set_admin_session(client)
            bot_response = client.post(
                "/admin/bot/import",
                data={
                    "csrf_token": csrf_token,
                    "return_to": "/admin/bot#casebooks",
                    "bot_database_file": (
                        BytesIO(json.dumps(bot_payload).encode("utf-8")),
                        "bot.json",
                    ),
                },
                content_type="multipart/form-data",
            )
            system_response = client.post(
                "/admin/system/import",
                data={
                    "csrf_token": csrf_token,
                    "return_to": "/admin/bot#casebooks",
                    "import_mode": "merge",
                    "system_database_file": (
                        BytesIO(json.dumps(system_payload).encode("utf-8")),
                        "system.json",
                    ),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(bot_response.status_code, 302)
        self.assertEqual(system_response.status_code, 302)
        self.assertEqual(self.store.list_feedback_eval_cases()[0]["source_message_id"], "bot-import-1")
        self.assertEqual(self.store.get_runtime_state("admin_import_test"), {"ok": True})
        self.assertTrue((Path(self.store.knowledge_dir) / "admin_import_test.txt").exists())

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
        vessel_hash = sum((index + 1) * ord(char) for index, char in enumerate(vessel_name))
        return self.store.create_port_call(
            vessel_name=vessel_name,
            eta=eta,
            created_by=created_by,
            berth="TMS 2",
            last_port="Sines",
            next_port="Vigo",
            notes="Escala de teste.",
            vessel_imo=str(9000000 + (vessel_hash % 900000)),
            vessel_call_sign=f"CQ{vessel_hash % 10000:04d}",
            vessel_flag="Portugal",
            vessel_type="General Cargo",
            vessel_loa_m="142.50",
            vessel_beam_m="21.80",
            vessel_gt_t="8950",
            vessel_max_draft_m="7.20",
            vessel_dwt_t="12400",
        )

    def test_dashboard_is_shared_for_agents_but_scale_registry_remains_scoped(self) -> None:
        eta_base = datetime.now(timezone.utc) + timedelta(days=1)
        self._create_port_call(
            vessel_name="AGENCY STAR",
            created_by="agencia@example.com",
            eta=eta_base.isoformat(),
        )
        self._create_port_call(
            vessel_name="OTHER PLANNER",
            created_by="outra@example.com",
            eta=(eta_base + timedelta(hours=1)).isoformat(),
        )
        other_in_port = self._create_port_call(
            vessel_name="OTHER QUAY",
            created_by="outra@example.com",
            eta=(eta_base - timedelta(hours=1)).isoformat(),
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

    def test_agent_registry_uses_current_creator_agency_for_existing_scales(self) -> None:
        self.store.create_user(
            "late-agent@example.com",
            "secret123",
            "agente",
            full_name="Late Agent",
            organization="",
            email="late-agent@example.com",
            phone="+351 900 000 333",
        )
        self._create_port_call(
            vessel_name="LATE AGENCY",
            created_by="late-agent@example.com",
            eta=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        )
        self.store.update_user_profile(
            "late-agent@example.com",
            full_name="Late Agent",
            organization="Agencia X",
            email="late-agent@example.com",
            phone="+351 900 000 333",
        )

        with app.app.test_client() as client:
            self._set_session(client, username="agencia@example.com", role="agente")
            register_response = client.get("/port-calls/register")

        self.assertEqual(register_response.status_code, 200)
        self.assertIn("LATE AGENCY", register_response.get_data(as_text=True))


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

    def test_bot_recent_departures_uses_live_portal_departures(self) -> None:
        now = datetime.now(timezone.utc)
        departed_at = (now + timedelta(days=1)).isoformat()
        port_call = self.store.create_port_call(
            vessel_name="ELBTOWER",
            eta=(now - timedelta(days=1)).isoformat(),
            created_by="admin",
            berth="Fundeadouro Norte",
            last_port="Sines",
            next_port="Barcelona",
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
        self.store.mark_port_call_arrived(port_call["id"], arrived_at=(now - timedelta(hours=20)).isoformat(), updated_by="admin")
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

        with app.app.test_request_context("/"):
            session["role"] = "admin"
            answer = answer_direct_operational_query("Saiu algum navio recentemente?")

        self.assertIsNotNone(answer)
        self.assertEqual(answer["answer_origin"], "operational_live")
        self.assertIn("ELBTOWER", answer["answer"])
        self.assertIn("ATD", answer["answer"])
        self.assertIn("Fundeadouro Norte -> Barcelona", answer["answer"])

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

    def test_agent_can_see_and_post_scoped_scale_edit(self) -> None:
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
        self.assertIn("Editar escala e navio", html)
        self.assertNotIn("Contacto piloto", html)
        self.assertIn("+351 900 000 111", html)
        self.assertIn("351900000111", html)
        self.assertEqual(edit_response.status_code, 302)

    def test_import_port_call_json_from_textarea(self) -> None:
        future_eta = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        payload = """
        {
          "vessel_name": "MSC Lyria",
          "eta": "__FUTURE_ETA__",
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
        """.replace("__FUTURE_ETA__", future_eta)

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
        future_eta_local = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        payload = b"""
        {
          "scale": {
                "vessel_name": "Atlantic Trader",
                "eta_local": "__FUTURE_ETA_LOCAL__",
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
        """.replace(b"__FUTURE_ETA_LOCAL__", future_eta_local.encode("utf-8"))

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
        future_eta = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        payload = """
        {
          "vessel_name": "ARKLOW GLOBE",
          "eta": "__FUTURE_ETA__",
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
        """.replace("__FUTURE_ETA__", future_eta)

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
