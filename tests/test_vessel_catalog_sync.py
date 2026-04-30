from __future__ import annotations

import unittest

from core import services
from blueprints.port_calls import (
    VESSEL_CATALOG_STATE_KEY,
    _coerce_port_call_payload_with_catalog,
    _delete_all_vessel_catalog_records,
    _filter_vessel_catalog_options,
    _sync_vessel_catalog_record_to_active_port_calls,
    _vessel_catalog_txt,
    _validate_vessel_catalog_record,
)


class FakeStore:
    def __init__(self) -> None:
        self.updated: list[dict] = []
        self.runtime_state: dict = {}
        self.port_calls = [
            {
                "id": "way-forward",
                "status": "scheduled",
                "vessel_name": "WAY FORWARD",
                "vessel_imo": "9876543",
                "vessel_call_sign": "WAYF",
                "vessel_flag": "PT",
                "vessel_type": "Restantes",
                "vessel_loa_m": "150",
                "vessel_beam_m": "24",
                "vessel_gt_t": "12000",
                "vessel_dwt_t": "6000",
                "vessel_max_draft_m": "7.0",
                "vessel_bow_thruster": "yes",
                "vessel_stern_thruster": "unknown",
                "eta": "2026-05-01T12:00:00+01:00",
                "berth": "Cais 10 / Autoeuropa",
                "last_port": "Southampton",
                "next_port": "Vigo",
                "notes": "",
            },
            {
                "id": "old-way-forward",
                "status": "departed",
                "vessel_name": "WAY FORWARD",
                "vessel_imo": "9876543",
            },
        ]

    def list_port_calls(self) -> list[dict]:
        return self.port_calls

    def edit_port_call(self, **kwargs) -> dict:
        self.updated.append(kwargs)
        return kwargs

    def get_runtime_state(self, key: str):
        return self.runtime_state.get(key)

    def set_runtime_state(self, key: str, value):
        self.runtime_state[key] = value

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        return {
            "arrivals": [self.port_calls[0]],
            "in_port": [],
            "departed": [self.port_calls[1]],
            "aborted": [],
        }


class VesselCatalogSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = services.store
        services.store = FakeStore()

    def tearDown(self) -> None:
        services.store = self.previous_store

    def test_roro_alias_normalizes_to_roll_on_roll_off(self) -> None:
        record = _validate_vessel_catalog_record(
            {
                "vessel_name": "WAY FORWARD",
                "vessel_imo": "9876543",
                "vessel_call_sign": "WAYF",
                "vessel_flag": "PT",
                "vessel_type": "roro",
                "vessel_loa_m": "150",
                "vessel_beam_m": "24",
                "vessel_gt_t": "12000",
                "vessel_dwt_t": "6000",
                "vessel_max_draft_m": "7.0",
                "vessel_bow_thruster": "yes",
                "vessel_stern_thruster": "unknown",
            }
        )

        self.assertEqual(record["vessel_type"], "Roll-on/Roll-off")

    def test_catalog_edit_syncs_matching_active_port_calls_only(self) -> None:
        record = _validate_vessel_catalog_record(
            {
                "vessel_name": "WAY FORWARD",
                "vessel_imo": "9876543",
                "vessel_call_sign": "WAYF",
                "vessel_flag": "PT",
                "vessel_type": "roro",
                "vessel_loa_m": "150",
                "vessel_beam_m": "24",
                "vessel_gt_t": "12000",
                "vessel_dwt_t": "6000",
                "vessel_max_draft_m": "7.0",
                "vessel_bow_thruster": "yes",
                "vessel_stern_thruster": "unknown",
            }
        )

        synced_count = _sync_vessel_catalog_record_to_active_port_calls(
            record,
            updated_by="admin",
        )

        self.assertEqual(synced_count, 1)
        self.assertEqual(services.store.updated[0]["port_call_id"], "way-forward")
        self.assertEqual(services.store.updated[0]["vessel_type"], "Roll-on/Roll-off")

    def test_vessel_catalog_filter_searches_imo_and_type_label(self) -> None:
        vessels = [
            {
                "vessel_name": "WAY FORWARD",
                "vessel_imo": "9876543",
                "vessel_type": "Roll-on/Roll-off",
                "vessel_type_label": "Roll-on/Roll-off",
            },
            {
                "vessel_name": "OCEAN MERCURY",
                "vessel_imo": "9182736",
                "vessel_type": "Graneis sólidos",
                "vessel_type_label": "Graneis sólidos",
            },
        ]

        filtered = _filter_vessel_catalog_options(vessels, q="9876543", vessel_type="Roll-on/Roll-off")

        self.assertEqual([item["vessel_name"] for item in filtered], ["WAY FORWARD"])

    def test_vessel_catalog_txt_contains_exportable_profile(self) -> None:
        body = _vessel_catalog_txt(
            {
                "vessel_name": "WAY FORWARD",
                "vessel_imo": "9876543",
                "vessel_type_label": "Roll-on/Roll-off",
            }
        )

        self.assertIn("PRAGtico - Ficha do Navio", body)
        self.assertIn("Navio: WAY FORWARD", body)
        self.assertIn("Tipo: Roll-on/Roll-off", body)

    def test_port_call_import_fills_missing_vessel_fields_from_catalog(self) -> None:
        services.store.runtime_state[VESSEL_CATALOG_STATE_KEY] = {
            "items": [
                {
                    "key": "imo:9876543",
                    "vessel_name": "WAY FORWARD",
                    "vessel_imo": "9876543",
                    "vessel_call_sign": "WAYF",
                    "vessel_flag": "PT",
                    "vessel_type": "Roll-on/Roll-off",
                    "vessel_loa_m": "199.9",
                    "vessel_beam_m": "32.2",
                    "vessel_gt_t": "52000",
                    "vessel_dwt_t": "18000",
                    "vessel_max_draft_m": "9.8",
                    "vessel_bow_thruster": "yes",
                    "vessel_stern_thruster": "unknown",
                }
            ],
            "deleted_keys": [],
        }

        form_data = _coerce_port_call_payload_with_catalog(
            {
                "vessel_imo": "9876543",
                "eta": "2026-05-02T15:00:00+01:00",
                "berth": "Cais 10 / Autoeuropa",
                "last_port": "Southampton",
                "next_port": "Vigo",
                "draft_m": "8.7",
                "tug_count": 2,
                "constraints": [],
                "notes": "Entrada prevista.",
            }
        )

        self.assertEqual(form_data["vessel_name"], "WAY FORWARD")
        self.assertEqual(form_data["vessel_type"], "Roll-on/Roll-off")
        self.assertEqual(form_data["vessel_loa_m"], "199.9")
        self.assertEqual(form_data["vessel_bow_thruster"], "yes")

    def test_delete_all_vessel_catalog_records_hides_stored_and_derived_vessels(self) -> None:
        services.store.runtime_state[VESSEL_CATALOG_STATE_KEY] = {
            "items": [
                {
                    "key": "imo:1234567",
                    "vessel_name": "CATALOG ONLY",
                    "vessel_imo": "1234567",
                    "vessel_call_sign": "CATO",
                    "vessel_flag": "PT",
                    "vessel_type": "Carga geral",
                    "vessel_loa_m": "100",
                    "vessel_beam_m": "20",
                    "vessel_gt_t": "5000",
                    "vessel_dwt_t": "2500",
                    "vessel_max_draft_m": "5.5",
                    "vessel_bow_thruster": "unknown",
                    "vessel_stern_thruster": "unknown",
                }
            ],
            "deleted_keys": [],
        }

        removed_count = _delete_all_vessel_catalog_records()

        state = services.store.runtime_state[VESSEL_CATALOG_STATE_KEY]
        self.assertEqual(removed_count, 2)
        self.assertEqual(state["items"], [])
        self.assertIn("imo:1234567", state["deleted_keys"])
        self.assertIn("imo:9876543", state["deleted_keys"])


if __name__ == "__main__":
    unittest.main()
