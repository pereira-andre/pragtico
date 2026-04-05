import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from integrations.local_warning_service import LocalWarningService
from integrations.wave_service import WaveService


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class ExternalServiceCacheTests(unittest.TestCase):
    def test_wave_service_reuses_persisted_snapshot_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "wave_cache.json"
            service = WaveService(
                endpoint="https://example.test/wave",
                station_name="Sines",
                cache_ttl_seconds=0,
                failure_backoff_seconds=3600,
                snapshot_path=str(snapshot_path),
            )
            with patch(
                "integrations.wave_service.requests.get",
                return_value=_FakeResponse(
                    {
                        "date": "2026-04-02 14:00:00",
                        "hm0": "1.2",
                        "hmax": "2.4",
                        "t02": "7.5",
                        "tmax": "11.0",
                        "temp": "16.1",
                        "thtp": "NW",
                    }
                ),
            ):
                fresh = service.get_current_conditions()

            restarted = WaveService(
                endpoint="https://example.test/wave",
                station_name="Sines",
                cache_ttl_seconds=0,
                failure_backoff_seconds=3600,
                snapshot_path=str(snapshot_path),
            )
            with patch(
                "integrations.wave_service.requests.get",
                side_effect=Exception("[Errno 111] Connection refused"),
            ):
                stale = restarted.get_current_conditions()

            self.assertFalse(fresh["cache_stale"])
            self.assertTrue(stale["cache_stale"])
            self.assertEqual(stale["last_reading_label"], fresh["last_reading_label"])
            self.assertIn("Instituto Hidrográfico", stale["source_error"])
            self.assertTrue(snapshot_path.exists())

    def test_wave_service_uses_backoff_after_failed_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "wave_cache.json"
            service = WaveService(
                endpoint="https://example.test/wave",
                station_name="Sines",
                cache_ttl_seconds=0,
                failure_backoff_seconds=3600,
                snapshot_path=str(snapshot_path),
            )
            with patch(
                "integrations.wave_service.requests.get",
                return_value=_FakeResponse(
                    {
                        "date": "2026-04-02 14:00:00",
                        "hm0": "1.2",
                        "hmax": "2.4",
                        "t02": "7.5",
                        "tmax": "11.0",
                        "temp": "16.1",
                        "thtp": "NW",
                    }
                ),
            ):
                service.get_current_conditions()

            with patch(
                "integrations.wave_service.requests.get",
                side_effect=Exception("[Errno 111] Connection refused"),
            ) as get_mock:
                first = service.get_current_conditions()
                second = service.get_current_conditions()

            self.assertEqual(get_mock.call_count, 1)
            self.assertTrue(first["cache_stale"])
            self.assertTrue(second["cache_stale"])

    def test_local_warning_service_reuses_persisted_snapshot_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "warnings_cache.json"
            service = LocalWarningService(
                endpoint="https://example.test/warnings?currentPage=1",
                cache_ttl_seconds=0,
                failure_backoff_seconds=3600,
                snapshot_path=str(snapshot_path),
            )
            with patch(
                "integrations.local_warning_service.requests.get",
                return_value=_FakeResponse(
                    {
                        "rows": [
                            {
                                "id": 1,
                                "code": "123",
                                "subject": "Trabalho subaquático",
                                "locationDescription": "Setúbal",
                                "description": "<p>Manter vigilância.</p>",
                                "attachments": [],
                                "entity": {"name": "Capitania do Porto de Setúbal"},
                                "state": {"code": "promulgado", "name": "Promulgado"},
                                "startDate": "2026-04-02T00:00:00Z",
                                "endDate": "2026-04-03T00:00:00Z",
                            }
                        ],
                        "totalPages": 1,
                    }
                ),
            ):
                fresh = service.list_warnings()

            restarted = LocalWarningService(
                endpoint="https://example.test/warnings?currentPage=1",
                cache_ttl_seconds=0,
                failure_backoff_seconds=3600,
                snapshot_path=str(snapshot_path),
            )
            with patch(
                "integrations.local_warning_service.requests.get",
                side_effect=Exception("[Errno 111] Connection refused"),
            ):
                stale = restarted.list_warnings()

            self.assertEqual(len(fresh), 1)
            self.assertEqual(len(stale), 1)
            self.assertEqual(stale[0]["display_code"], fresh[0]["display_code"])
            self.assertTrue(restarted.status()["stale"])
            self.assertIn("Instituto Hidrográfico", restarted.status()["error"])
            self.assertTrue(snapshot_path.exists())

    def test_local_warning_service_retries_without_ssl_verification_on_ssl_error(self) -> None:
        service = LocalWarningService(
            endpoint="https://example.test/warnings?currentPage=1",
            cache_ttl_seconds=0,
            failure_backoff_seconds=3600,
            allow_insecure_ssl_fallback=True,
        )
        verify_values: list[bool] = []

        def fake_get(_url, timeout, verify=True):
            verify_values.append(bool(verify))
            if verify:
                raise requests.exceptions.SSLError("certificate verify failed")
            return _FakeResponse(
                {
                    "rows": [
                        {
                            "id": 1,
                            "code": "123",
                            "subject": "Trabalho subaquático",
                            "locationDescription": "Setúbal",
                            "description": "<p>Manter vigilância.</p>",
                            "attachments": [],
                            "entity": {"name": "Capitania do Porto de Setúbal"},
                            "state": {"code": "promulgado", "name": "Promulgado"},
                            "startDate": "2026-04-02T00:00:00Z",
                            "endDate": "2026-04-03T00:00:00Z",
                        }
                    ],
                    "totalPages": 1,
                }
            )

        with patch("integrations.local_warning_service.requests.get", side_effect=fake_get):
            warnings = service.list_warnings()

        self.assertEqual(len(warnings), 1)
        self.assertEqual(verify_values, [True, False])
