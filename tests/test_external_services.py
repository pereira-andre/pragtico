import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from integrations.local_warning_service import LocalWarningService
from integrations.whatsapp_cloud import WhatsAppCloudService
from integrations.wave_service import WaveService


class _FakeResponse:
    def __init__(self, payload, *, status_code: int = 200, text: str = "", content: bytes = b"", headers: dict | None = None):
        self._payload = payload
        self.status_code = status_code
        self.text = text or ""
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)
        return None

    def json(self):
        return self._payload


class ExternalServiceCacheTests(unittest.TestCase):
    def test_whatsapp_parse_webhook_events_maps_button_reply_to_text_event(self) -> None:
        service = WhatsAppCloudService(enabled=True)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351965756128", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.BUTTON123",
                                        "from": "351965756128",
                                        "timestamp": "1712165400",
                                        "type": "button",
                                        "button": {"text": "Iniciar", "payload": "start"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        events = service.parse_webhook_events(payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "message_text")
        self.assertEqual(events[0]["text"], "Iniciar")

    def test_whatsapp_parse_webhook_events_maps_image_media(self) -> None:
        service = WhatsAppCloudService(enabled=True)
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"wa_id": "351965756128", "profile": {"name": "Andre"}}],
                                "messages": [
                                    {
                                        "id": "wamid.IMAGE123",
                                        "from": "351965756128",
                                        "timestamp": "1712165400",
                                        "type": "image",
                                        "image": {
                                            "id": "media-123",
                                            "mime_type": "image/jpeg",
                                            "sha256": "abc",
                                            "caption": "guincho",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }

        events = service.parse_webhook_events(payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "message_media")
        self.assertEqual(events[0]["media_kind"], "image")
        self.assertEqual(events[0]["media_id"], "media-123")
        self.assertEqual(events[0]["mime_type"], "image/jpeg")

    def test_whatsapp_download_media_fetches_metadata_and_bytes(self) -> None:
        service = WhatsAppCloudService(
            enabled=True,
            access_token="token-123",
            phone_number_id="phone-123",
            graph_api_version="v25.0",
        )
        get_calls: list[dict] = []

        def fake_get(url, **kwargs):
            get_calls.append({"url": url, **kwargs})
            if url.endswith("/media-123"):
                return _FakeResponse({"url": "https://cdn.test/photo", "mime_type": "image/jpeg"})
            if url == "https://cdn.test/photo":
                return _FakeResponse({}, content=b"photo-bytes", headers={"Content-Type": "image/jpeg"})
            raise AssertionError(f"URL inesperada: {url}")

        with patch("integrations.whatsapp_cloud.requests.get", side_effect=fake_get):
            payload = service.download_media("media-123")

        self.assertEqual(payload["bytes"], b"photo-bytes")
        self.assertEqual(payload["mime_type"], "image/jpeg")
        self.assertEqual(len(get_calls), 2)
        self.assertEqual(get_calls[0]["headers"]["Authorization"], "Bearer token-123")

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

    def test_whatsapp_profile_picture_upload_uses_resumable_upload_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            picture_path = Path(temp_dir) / "icon.png"
            picture_path.write_bytes(b"\x89PNG\r\n\x1a\npragtico")

            service = WhatsAppCloudService(
                enabled=True,
                access_token="token-123",
                phone_number_id="phone-123",
            )
            post_calls: list[dict] = []

            def fake_post(url, **kwargs):
                post_calls.append({"url": url, **kwargs})
                if url.endswith("/app/uploads"):
                    return _FakeResponse({"id": "upload:session-1"})
                if url.endswith("/upload:session-1"):
                    return _FakeResponse({"h": "handle-1"})
                raise AssertionError(f"URL inesperada: {url}")

            with patch("integrations.whatsapp_cloud.requests.post", side_effect=fake_post):
                handle = service.upload_profile_picture(picture_path)

        self.assertEqual(handle, "handle-1")
        self.assertEqual(len(post_calls), 2)
        self.assertEqual(post_calls[0]["params"]["file_name"], "icon.png")
        self.assertEqual(post_calls[0]["params"]["file_type"], "image/png")
        self.assertEqual(post_calls[0]["params"]["file_length"], len(b"\x89PNG\r\n\x1a\npragtico"))
        self.assertEqual(post_calls[0]["headers"]["Authorization"], "OAuth token-123")
        self.assertEqual(post_calls[1]["headers"]["Content-Type"], "image/png")
        self.assertEqual(post_calls[1]["headers"]["file_offset"], "0")
        self.assertEqual(post_calls[1]["data"], b"\x89PNG\r\n\x1a\npragtico")

    def test_whatsapp_update_business_profile_posts_picture_handle(self) -> None:
        service = WhatsAppCloudService(
            enabled=True,
            access_token="token-123",
            phone_number_id="phone-123",
            graph_api_version="v25.0",
        )
        post_calls: list[dict] = []

        def fake_post(url, **kwargs):
            post_calls.append({"url": url, **kwargs})
            return _FakeResponse({"data": [{"id": "phone-123"}]})

        with patch("integrations.whatsapp_cloud.requests.post", side_effect=fake_post):
            response = service.update_business_profile(
                about="PRAGtico",
                description="Coordenação portuária",
                profile_picture_handle="handle-1",
                websites=["https://pragtico.test"],
            )

        self.assertEqual(response, {"data": [{"id": "phone-123"}]})
        self.assertEqual(
            post_calls[0]["url"],
            "https://graph.facebook.com/v25.0/phone-123/whatsapp_business_profile",
        )
        self.assertEqual(post_calls[0]["headers"]["Authorization"], "Bearer token-123")
        self.assertEqual(
            post_calls[0]["json"],
            {
                "messaging_product": "whatsapp",
                "about": "PRAGtico",
                "description": "Coordenação portuária",
                "profile_picture_handle": "handle-1",
                "websites": ["https://pragtico.test"],
            },
        )

    def test_whatsapp_attempt_template_message_reports_recipient_not_allowed(self) -> None:
        service = WhatsAppCloudService(
            enabled=True,
            access_token="token-123",
            phone_number_id="phone-123",
            allowed_numbers="351968576736",
            welcome_template_name="pragtico_welcome_v2",
        )

        with patch(
            "integrations.whatsapp_cloud.requests.post",
            return_value=_FakeResponse(
                {
                    "error": {
                        "message": "(#131030) Recipient phone number not in allowed list",
                        "type": "OAuthException",
                        "code": 131030,
                        "error_data": {
                            "details": "O número de telemóvel do destinatário não está na lista de permissões.",
                        },
                    }
                },
                status_code=400,
            ),
        ):
            result = service.attempt_template_message(
                "351968576736",
                template_name="pragtico_welcome_v2",
                source="admin_verify",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "recipient_not_allowed")
        self.assertIn("lista de destinatários", result["summary"])

    def test_whatsapp_attempt_template_message_blocks_local_denied_without_api_call(self) -> None:
        service = WhatsAppCloudService(
            enabled=True,
            access_token="token-123",
            phone_number_id="phone-123",
            allowed_numbers="351962063664",
        )

        with patch("integrations.whatsapp_cloud.requests.post") as post_mock:
            result = service.attempt_template_message(
                "351968576736",
                template_name="pragtico_welcome_v2",
                source="admin_verify",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["category"], "local_denied")
        post_mock.assert_not_called()
