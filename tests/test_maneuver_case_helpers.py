"""Unit tests for maneuver case ranking and environment signatures."""

import unittest

from storage.maneuver_case_helpers import build_case_environment_signature, rank_similar_maneuver_cases


def _make_case(
    *,
    maneuver_id: str,
    origin: str,
    destination: str,
    feedback_status: str = "",
    wind_kts: float = 14.0,
    gust_kts: float = 18.0,
    wind_dir: str = "NW",
    wave_height_m: float = 1.5,
    wave_period_s: float = 7.5,
    wave_dir: str = "W",
    latest_event_at: str = "2026-03-24T06:10:00+00:00",
) -> dict:
    return {
        "maneuver_id": maneuver_id,
        "port_call_id": f"pc-{maneuver_id}",
        "reference_code": f"REF-{maneuver_id}",
        "vessel_name": f"Vessel {maneuver_id}",
        "maneuver_type": "entry",
        "current_state": "completed",
        "current_state_label": "Realizada",
        "origin_label": origin,
        "destination_label": destination,
        "latest_event_at": latest_event_at,
        "feedback_status": feedback_status,
        "feature_snapshot": {
            "maneuver_type": "entry",
            "origin": origin,
            "destination": destination,
            "origin_key": "leixoes" if origin == "Leixoes" else "sines",
            "destination_key": "tms 2" if destination == "TMS 2" else "cais 10 autoeuropa",
            "origin_is_known_berth": False,
            "destination_is_known_berth": destination == "TMS 2",
            "vessel_type": "Porta-contentores",
            "vessel_type_key": "porta contentores",
            "vessel_loa_m": 179.2,
            "bow_thruster": "yes",
            "stern_thruster": "no",
            "tug_count": "2",
            "wave_sensitive": True,
        },
        "environment_snapshot": {
            "latest": {
                "status": "captured",
                "phase": "execution",
                "weather": {
                    "closest_hour": {
                        "wind_kts": wind_kts,
                        "gust_kts": gust_kts,
                        "wind_dir": wind_dir,
                    }
                },
                "wave": {
                    "current": {
                        "significant_height_m": wave_height_m,
                        "mean_period_s": wave_period_s,
                        "direction": wave_dir,
                    }
                },
            }
        },
    }


class ManeuverCaseHelperTests(unittest.TestCase):
    def test_build_case_environment_signature_extracts_operational_bands(self) -> None:
        signature = build_case_environment_signature(
            _make_case(
                maneuver_id="sig",
                origin="Leixoes",
                destination="TMS 2",
                wind_kts=18.0,
                gust_kts=24.0,
                wind_dir="NW",
                wave_height_m=1.8,
                wave_period_s=7.2,
                wave_dir="W",
            )
        )

        self.assertTrue(signature["captured"])
        self.assertEqual(signature["wind_band"], "strong")
        self.assertEqual(signature["wind_quadrant"], "NW")
        self.assertEqual(signature["wave_height_band"], "1_2m")
        self.assertEqual(signature["wave_period_band"], "6_10s")
        self.assertEqual(signature["wave_direction_quadrant"], "W")

    def test_rank_similar_maneuver_cases_enforces_primary_operational_berth(self) -> None:
        target_case = _make_case(maneuver_id="target", origin="Leixoes", destination="TMS 2")
        same_berth = _make_case(maneuver_id="same", origin="Leixoes", destination="TMS 2")
        other_berth = _make_case(
            maneuver_id="other",
            origin="Leixoes",
            destination="Cais 10 / Autoeuropa",
        )

        matches = rank_similar_maneuver_cases(
            [same_berth, other_berth],
            maneuver_type="entry",
            origin="Leixoes",
            destination="TMS 2",
            vessel_type="Porta-contentores",
            vessel_loa_m="179.2",
            bow_thruster="yes",
            stern_thruster="no",
            tug_count="2",
            environment_signature=build_case_environment_signature(target_case),
            limit=5,
        )

        self.assertEqual([item["maneuver_id"] for item in matches], ["same"])

    def test_rank_similar_maneuver_cases_prefers_validated_environment_match(self) -> None:
        target_case = _make_case(
            maneuver_id="target",
            origin="Leixoes",
            destination="TMS 2",
            wind_kts=18.0,
            gust_kts=24.0,
            wind_dir="NW",
            wave_height_m=1.8,
            wave_period_s=7.2,
            wave_dir="W",
        )
        approved_match = _make_case(
            maneuver_id="approved",
            origin="Leixoes",
            destination="TMS 2",
            feedback_status="approved",
            wind_kts=17.0,
            gust_kts=22.0,
            wind_dir="NW",
            wave_height_m=1.7,
            wave_period_s=7.8,
            wave_dir="W",
        )
        rougher_match = _make_case(
            maneuver_id="rough",
            origin="Leixoes",
            destination="TMS 2",
            wind_kts=31.0,
            gust_kts=35.0,
            wind_dir="SE",
            wave_height_m=3.4,
            wave_period_s=12.0,
            wave_dir="S",
            latest_event_at="2026-03-25T06:10:00+00:00",
        )

        matches = rank_similar_maneuver_cases(
            [rougher_match, approved_match],
            maneuver_type="entry",
            origin="Leixoes",
            destination="TMS 2",
            vessel_type="Porta-contentores",
            vessel_loa_m="179.2",
            bow_thruster="yes",
            stern_thruster="no",
            tug_count="2",
            environment_signature=build_case_environment_signature(target_case),
            limit=2,
        )

        self.assertEqual(matches[0]["maneuver_id"], "approved")
        self.assertEqual(matches[0]["experience_label"], "Experiência validada")
        self.assertIn("mesma faixa de vento", matches[0]["similarity_reasons"])
        self.assertIn("mesma faixa de ondulação", matches[0]["similarity_reasons"])


if __name__ == "__main__":
    unittest.main()
