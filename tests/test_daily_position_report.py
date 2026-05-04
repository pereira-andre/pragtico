from __future__ import annotations

import unittest
from datetime import date, datetime

from blueprints.dashboard import _build_daily_position_report_context


class DailyPositionReportTests(unittest.TestCase):
    def test_report_separates_today_arrivals_shifts_and_positions(self) -> None:
        activity = {
            "arrivals": [
                {
                    "id": "pc-entry",
                    "reference_code": "PTSET26ENTRY",
                    "vessel_name": "ENTRY STAR",
                    "eta": "2026-05-04T14:00:00+01:00",
                    "eta_label": "14:00",
                    "last_port": "Sines",
                    "berth_label": "TMS 2 - Posição A",
                    "agent_label": "Agente",
                    "agent_profile": {"organization": "Agência A"},
                },
                {
                    "id": "pc-direct",
                    "reference_code": "PTSET26DIRECT",
                    "vessel_name": "DIRECT CALL",
                    "eta": "2026-05-04T09:30:00+01:00",
                    "eta_label": "09:30",
                    "last_port": "Lisboa",
                    "berth_label": "SAPEC Líquidos",
                    "agent_profile": {"organization": "Agência B"},
                },
                {
                    "id": "pc-tomorrow",
                    "vessel_name": "TOMORROW",
                    "eta": "2026-05-05T08:00:00+01:00",
                    "eta_label": "08:00",
                },
            ],
            "planned_maneuvers": [
                {
                    "port_call_id": "pc-entry",
                    "reference_code": "PTSET26ENTRY",
                    "vessel_name": "ENTRY STAR",
                    "maneuver_type": "entry",
                    "planned_value": "2026-05-04T14:15:00+01:00",
                    "planned_label": "14:15",
                    "local_origin": "Sines",
                    "local_destination": "TMS 2 - Posição A",
                    "agent_profile": {"organization": "Agência Planeamento"},
                },
                {
                    "port_call_id": "pc-shift",
                    "reference_code": "PTSET26SHIFT",
                    "vessel_name": "SHIFTING BAY",
                    "maneuver_type": "shift",
                    "planned_value": "2026-05-04T16:00:00+01:00",
                    "planned_label": "16:00",
                    "local_origin": "Eco-Oil",
                    "local_destination": "Lisnave",
                    "agent_profile": {"organization": "Agência C"},
                },
            ],
            "berthed": [
                {
                    "berth": "Tanquisado",
                    "vessels": [
                        {
                            "id": "pc-berthed",
                            "reference_code": "PTSET26BERTH",
                            "vessel_name": "ALONGSIDE",
                            "ship_type_label": "Tanque",
                            "planned_departure_label": "18:00",
                            "agent_profile": {"organization": "Agência D"},
                        }
                    ],
                }
            ],
            "anchorages": [
                {
                    "berth": "Fundeadouro Norte",
                    "vessels": [
                        {
                            "id": "pc-anchor",
                            "reference_code": "PTSET26ANCHOR",
                            "vessel_name": "WAITING",
                            "ship_type_label": "Carga geral",
                            "agent_profile": {"organization": "Agência E"},
                        }
                    ],
                }
            ],
        }

        report = _build_daily_position_report_context(
            activity,
            target_date=date(2026, 5, 4),
            generated_at=datetime(2026, 5, 4, 12, 0),
            tide_day={
                "events": [{"time": "10:42", "height_m": 3.1, "type": "preia-mar"}],
                "luminosity": {
                    "sunrise": "06:35",
                    "sunset": "20:30",
                    "daylight_duration": "13h 55m",
                    "night_duration": "10h 05m",
                },
            },
        )

        self.assertEqual(report["summary"]["arrivals"], 2)
        self.assertEqual([item["id"] for item in report["arrivals"]], ["pc-direct", "pc-entry"])
        self.assertEqual(report["arrivals"][1]["agency_label"], "Agência Planeamento")
        self.assertEqual(report["summary"]["shifts"], 1)
        self.assertEqual(report["shifts"][0]["origin"], "Eco-Oil")
        self.assertEqual(report["shifts"][0]["destination"], "Lisnave")
        self.assertEqual(report["summary"]["positions"], 2)
        self.assertEqual(report["summary"]["departures_planned"], 1)
        self.assertEqual(report["berthed"][0]["vessels"][0]["note"], "Saída planeada: 18:00")
        self.assertEqual(report["anchorages"][0]["vessels"][0]["agency_label"], "Agência E")
        self.assertEqual(report["tide_day"]["events"][0]["time"], "10:42")
        self.assertEqual(report["tide_day"]["luminosity"]["daylight_duration"], "13h 55m")


if __name__ == "__main__":
    unittest.main()
