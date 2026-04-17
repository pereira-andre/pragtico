import unittest
from unittest.mock import patch

from integrations.weather_service import WeatherService


class _FakeWeatherResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class WeatherServiceTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {
            "location": {
                "name": "Setúbal",
                "region": "Setúbal",
                "country": "Portugal",
                "localtime": "2026-04-17 10:06",
            },
            "current": {
                "temp_c": 14.1,
                "condition": {"text": "Sol"},
                "wind_kph": 11.5,
                "gust_kph": 14.8,
                "wind_dir": "NNE",
                "wind_degree": 22,
                "humidity": 77,
                "precip_mm": 0.0,
                "vis_km": 10,
                "is_day": 1,
            },
            "forecast": {
                "forecastday": [
                    {
                        "date": "2026-04-17",
                        "day": {
                            "condition": {"text": "Sol"},
                            "maxtemp_c": 23.8,
                            "mintemp_c": 9.9,
                            "maxwind_kph": 18.0,
                            "totalprecip_mm": 0.0,
                        },
                        "astro": {
                            "sunrise": "06:57 AM",
                            "sunset": "08:14 PM",
                            "moonrise": "06:28 AM",
                            "moonset": "09:03 PM",
                            "moon_phase": "New Moon",
                            "moon_illumination": "0",
                        },
                        "hour": [
                            {
                                "time": "2026-04-17 10:00",
                                "temp_c": 14.1,
                                "condition": {"text": "Sol", "icon": ""},
                                "wind_kph": 11.5,
                                "gust_kph": 14.8,
                                "wind_dir": "NNE",
                                "wind_degree": 22,
                                "precip_mm": 0.0,
                                "chance_of_rain": 0,
                                "humidity": 77,
                                "vis_km": 10,
                                "is_day": 1,
                            },
                            {
                                "time": "2026-04-17 11:00",
                                "temp_c": 18.3,
                                "condition": {"text": "Sol", "icon": ""},
                                "wind_kph": 10.2,
                                "gust_kph": 13.0,
                                "wind_dir": "NNE",
                                "wind_degree": 20,
                                "precip_mm": 0.0,
                                "chance_of_rain": 0,
                                "humidity": 60,
                                "vis_km": 10,
                                "is_day": 1,
                            },
                        ],
                    }
                ]
            },
            "alerts": {"alert": []},
        }

    def test_forecast_summary_uses_pt_moon_phase_and_topic_layout(self) -> None:
        service = WeatherService("key", "Setubal")

        with patch(
            "integrations.weather_service.requests.get",
            return_value=_FakeWeatherResponse(self._payload()),
        ):
            forecast = service.get_forecast(days=1)
            context = service.context_for_question("hoje")

        self.assertIn("Meteorologia para Setúbal", forecast["summary"])
        self.assertIn("Próximos dias:", forecast["summary"])
        self.assertIn("🌑 Lua nova", forecast["summary"])
        self.assertNotIn("New Moon", forecast["summary"])
        self.assertIn("- Próximas horas:", context["text"])
        self.assertIn("  - 10:00: Sol; 14.1 °C; vento 6.2 kts NNE; chuva 0%.", context["text"])
        self.assertNotIn(" | ", context["text"])


if __name__ == "__main__":
    unittest.main()
