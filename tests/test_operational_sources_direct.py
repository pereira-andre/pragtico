from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import unittest
from zoneinfo import ZoneInfo

from flask import Flask

from core import services
from core.chat_planner import build_chat_execution_plan
from core.operational_sources import answer_direct_operational_query, build_operational_chat_sources


class FakeStore:
    def __init__(self, activity: dict) -> None:
        self.activity = activity
        self.runtime_state: dict = {}

    def get_port_activity_snapshot(self, window_days: int = 5) -> dict:
        return self.activity

    def get_runtime_state(self, key: str):
        return self.runtime_state.get(key)


class FakeWeatherService:
    enabled = True

    forecast = {
        "location": {"name": "Setúbal", "localtime": "2026-04-30 10:40"},
        "current": {
            "condition": "Parcialmente nublado",
            "temp_c": 18,
            "wind_kts": 7.4,
            "gust_kts": 9.3,
            "wind_dir": "S",
            "humidity": 73,
            "vis_km": 10,
            "precip_mm": 0.02,
        },
        "forecast_days": [
            {
                "date": "2026-04-30",
                "date_label": "30/04/2026",
                "condition": "Parcialmente nublado",
                "min_temp_c": 14,
                "max_temp_c": 21,
                "rain_mm": 0.4,
                "sunrise": "06:40",
                "sunset": "20:23",
                "daylight_duration_label": "13h 43m",
                "night_duration_label": "10h 17m",
                "moonrise": "18:12",
                "moonset": "05:28",
                "moon_phase": "Full Moon",
                "moon_phase_icon": "🌕",
                "moon_phase_label": "Lua cheia",
                "moon_illumination": "98",
                "max_wind_kts": 14,
                "max_gust_kts": 20,
            },
            {
                "date": "2026-05-01",
                "date_label": "01/05/2026",
                "condition": "Céu limpo",
                "min_temp_c": 13,
                "max_temp_c": 22,
                "rain_mm": 0,
                "sunrise": "06:39",
                "sunset": "20:24",
                "max_wind_kts": 12,
                "max_gust_kts": 18,
            },
        ],
        "hourly_groups": [
            {
                "date": "2026-04-30",
                "date_label": "30/04/2026",
                "hours": [
                    {"timestamp": "2026-04-30 11:00", "time": "11:00", "condition": "Nublado", "temp_c": 19, "wind_kts": 8, "gust_kts": 12, "wind_dir": "S", "chance_of_rain": 10},
                    {"timestamp": "2026-04-30 12:00", "time": "12:00", "condition": "Abertas", "temp_c": 20, "wind_kts": 10, "gust_kts": 15, "wind_dir": "SW", "chance_of_rain": 5},
                ],
            },
            {
                "date": "2026-05-01",
                "date_label": "01/05/2026",
                "hours": [
                    {"timestamp": "2026-05-01 09:00", "time": "09:00", "condition": "Céu limpo", "temp_c": 16, "wind_kts": 7, "gust_kts": 11, "wind_dir": "NW", "chance_of_rain": 0},
                ],
            },
        ],
    }

    def __init__(self) -> None:
        self.forecast = deepcopy(self.forecast)

    def get_forecast(self, days: int = 3) -> dict:
        return self.forecast

    def context_for_question(self, question: str) -> dict:
        return {"document": "WeatherAPI Setúbal", "retrieval_mode": "live_api", "snippet": "forecast", "text": "forecast"}

    def context_source(self) -> dict:
        return self.context_for_question("")

    def _resolve_query_dates(self, question, reference_date):
        clean = (question or "").lower()
        if "amanh" in clean:
            return ["2026-05-01"]
        if "hoje" in clean:
            return ["2026-04-30"]
        return []

    def _resolve_query_times(self, question):
        return []


class FakeTideEvent:
    def __init__(self, year: int, month: int, day: int, hour: int, minute: int, height: float) -> None:
        self.timestamp = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("Europe/Lisbon"))
        self.height = height

    @property
    def date_value(self) -> date:
        return self.timestamp.date()

    @property
    def tide_type(self) -> str:
        return "preia-mar" if self.height >= 2.0 else "baixa-mar"


class FakeTideService:
    def __init__(self) -> None:
        self.events = [
            FakeTideEvent(2026, 5, 8, 1, 29, 1.2),
            FakeTideEvent(2026, 5, 8, 7, 42, 2.5),
            FakeTideEvent(2026, 5, 8, 13, 30, 1.4),
            FakeTideEvent(2026, 5, 8, 20, 3, 2.7),
        ]

    def resolve_query_dates(self, question: str) -> list[date]:
        return [date(2026, 5, 8)]

    def events_for_date(self, target_date: date) -> list[FakeTideEvent]:
        return [item for item in self.events if item.date_value == target_date]


class FakeSchedulingTideService:
    def __init__(self) -> None:
        self.events = [
            FakeTideEvent(2026, 5, 16, 3, 4, 3.4),
            FakeTideEvent(2026, 5, 16, 9, 11, 0.5),
            FakeTideEvent(2026, 5, 16, 15, 28, 3.5),
            FakeTideEvent(2026, 5, 16, 21, 37, 0.4),
            FakeTideEvent(2026, 5, 17, 3, 42, 3.4),
        ]

    def resolve_query_dates(self, question: str, reference_date: date | None = None) -> list[date]:
        clean = (question or "").lower()
        ref = reference_date or date(2026, 5, 16)
        if "amanh" in clean:
            return [ref.replace(day=17)]
        if "hoje" in clean:
            return [ref]
        return [ref]

    def events_for_date(self, target_date: date) -> list[FakeTideEvent]:
        return [item for item in self.events if item.date_value == target_date]


class FakeSchedulingWeatherService(FakeWeatherService):
    forecast = deepcopy(FakeWeatherService.forecast)
    forecast["location"] = {"name": "Setúbal", "localtime": "2026-05-16 10:40"}
    forecast["current"] = {
        "condition": "Sol",
        "temp_c": 17,
        "wind_kts": 8,
        "gust_kts": 12,
        "wind_dir": "NW",
        "humidity": 70,
        "vis_km": 10,
        "precip_mm": 0,
    }
    forecast["hourly_groups"] = [
        {
            "date": "2026-05-16",
            "date_label": "16/05/2026",
            "hours": [
                {"timestamp": "2026-05-16 15:00", "time": "15:00", "condition": "Sol", "temp_c": 19, "wind_kts": 9, "gust_kts": 13, "wind_dir": "NW", "chance_of_rain": 0},
                {"timestamp": "2026-05-16 21:00", "time": "21:00", "condition": "Limpo", "temp_c": 16, "wind_kts": 8, "gust_kts": 11, "wind_dir": "N", "chance_of_rain": 0},
            ],
        }
    ]


class FakeBadThenSafeWeatherService(FakeSchedulingWeatherService):
    forecast = deepcopy(FakeSchedulingWeatherService.forecast)
    forecast["hourly_groups"] = [
        {
            "date": "2026-05-16",
            "date_label": "16/05/2026",
            "hours": [
                {"timestamp": "2026-05-16 15:00", "time": "15:00", "condition": "Vento forte", "temp_c": 19, "wind_kts": 27, "gust_kts": 31, "wind_dir": "NW", "chance_of_rain": 0},
                {"timestamp": "2026-05-16 21:00", "time": "21:00", "condition": "Abertas", "temp_c": 16, "wind_kts": 12, "gust_kts": 16, "wind_dir": "N", "chance_of_rain": 0},
            ],
        }
    ]


class OperationalSourcesDirectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = services.store
        self.previous_weather_service = services.weather_service
        self.previous_tide_service = services.tide_service
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.activity = {
            "stats": {"occupied_slot_count": 1, "slot_capacity_count": 36},
            "arrivals": [],
            "in_port": [
                {
                    "id": "pc1",
                    "reference_code": "PTSET26ELBT81C3A1",
                    "vessel_name": "ELBTOWER",
                    "vessel_imo": "9876543",
                    "vessel_call_sign": "ELBT9",
                    "vessel_flag": "PT",
                    "ship_type_label": "Carga geral",
                    "ship_loa_label": "120",
                    "ship_beam_label": "18",
                    "ship_gt_label": "8000",
                    "ship_dwt_label": "12000",
                    "ship_max_draft_label": "7.5",
                    "ship_bow_thruster_label": "Sim",
                    "ship_stern_thruster_label": "Não",
                    "status": "in_port",
                    "berth_label": "TMS 2 - Posição A",
                    "last_port": "Hamburgo",
                    "next_port": "Lisboa",
                    "agent_label": "Duarte Gomes",
                    "agent_profile": {"organization": "Navex Setúbal"},
                }
            ],
            "departed": [],
            "aborted": [],
            "departure_candidates": [],
            "planned_maneuvers": [],
            "archived_maneuvers": [
                {
                    "port_call_id": "pc1",
                    "reference_code": "PTSET26ELBT81C3A1",
                    "vessel_name": "ELBTOWER",
                    "maneuver_id": "dep-12345678",
                    "maneuver_type": "departure",
                    "maneuver_label": "Sair",
                    "situation_label": "Concluída",
                    "situation_class": "completed",
                    "date_label": "22 abril 2026",
                    "date_value": "2026-04-22T02:00:00+01:00",
                    "actual_value": "2026-04-22T02:00:00+01:00",
                    "actual_label": "02:00",
                    "local_origin": "TMS 2 - Posição A",
                    "local_destination": "Barcelona",
                    "agent_label": "Duarte Gomes",
                    "agent_profile": {"organization": "Navex Setúbal"},
                    "validated_by_label": "Piloto Validador",
                    "validated_by_profile": {"full_name": "Piloto Validador"},
                    "executed_by_label": "Piloto Executor",
                    "executed_by_profile": {"full_name": "Piloto Executor"},
                    "tug_count_label": "2",
                    "constraint_badges": [],
                }
            ],
            "archived_scales": [],
            "maneuvers": [],
            "planned_groups": [],
        }
        services.store = FakeStore(self.activity)
        services.weather_service = FakeWeatherService()
        services.tide_service = FakeTideService()

    def tearDown(self) -> None:
        services.store = self.previous_store
        services.weather_service = self.previous_weather_service
        services.tide_service = self.previous_tide_service

    def _answer(self, question: str) -> str:
        with self.app.test_request_context("/"):
            payload = answer_direct_operational_query(question)
        self.assertIsNotNone(payload)
        return payload["answer"]

    def test_maneuver_id_uses_maneuver_id_not_scale_reference(self) -> None:
        answer = self._answer("Qual o id da manobra de saída do ELBTOWER dia 22 de abril?")

        self.assertIn("dep-12345678", answer)
        self.assertIn("PTSET26ELBT81C3A1", answer)

    def test_maneuver_approver_answer_uses_validated_by(self) -> None:
        answer = self._answer("Quem aprovou a manobra do ELBTOWER para sair dia 22 de abril?")

        self.assertIn("Piloto Validador", answer)
        self.assertIn("dep-12345678", answer)

    def test_agent_agency_answer_uses_profile_organization(self) -> None:
        answer = self._answer("O Duarte Gomes trabalha para que agência?")

        self.assertIn("Navex Setúbal", answer)

    def test_agent_lookup_mentions_agency(self) -> None:
        answer = self._answer("Qual era o agente do ELBTOWER na saída dia 22 de abril?")

        self.assertIn("Duarte Gomes", answer)
        self.assertIn("Navex Setúbal", answer)

    def test_vessel_detail_answer_includes_profile_location_and_maneuver(self) -> None:
        answer = self._answer("Podes dar dados do navio ELBTOWER?")

        self.assertIn("GT 8000", answer)
        self.assertIn("DWT 12000", answer)
        self.assertIn("TMS 2 - Posição A", answer)
        self.assertIn("dep-12345678", answer)
        self.assertIn("Navex Setúbal", answer)

    def test_secil_north_anchorage_next_tide_calculates_marking_window(self) -> None:
        services.tide_service = FakeSchedulingTideService()
        services.weather_service = FakeSchedulingWeatherService()

        answer = self._answer("Tenho um navio para mudar do fundeadouro Norte para a Secil Este. A que horas devo marcar manobra a partir de agora?")

        self.assertIn("14:43-14:58", answer)
        self.assertIn("preia-mar às 15:28", answer)
        self.assertIn("Fundeadouro Norte", answer)
        self.assertIn("Meteorologia", answer)

    def test_referenced_tide_time_uses_previous_secil_context_assumption(self) -> None:
        services.weather_service = FakeSchedulingWeatherService()
        answer = self._answer("Então se a maré é às 15:28, marco a que horas?")

        self.assertIn("Assumindo a situação anterior", answer)
        self.assertIn("14:43-14:58", answer)
        self.assertIn("SECIL E", answer)
        self.assertIn("Meteorologia", answer)

    def test_unfavorable_weather_skips_first_tide_window(self) -> None:
        services.tide_service = FakeSchedulingTideService()
        services.weather_service = FakeBadThenSafeWeatherService()

        answer = self._answer("Tenho um navio para mudar do fundeadouro Norte para a Secil Este. A que horas devo marcar manobra a partir de agora?")

        self.assertIn("20:52-21:07", answer)
        self.assertIn("Não escolhi a primeira janela", answer)
        self.assertIn("acima de 25 kt", answer)

    def test_tms1_large_vessel_capacity_answer_uses_rule_not_live_berthed_list(self) -> None:
        answer = self._answer("Quantos navios grandes podem estar atracados no TMS 1 ao mesmo tempo?")

        self.assertIn("máximo 3 navios grandes", answer)
        self.assertIn("2 navios grandes na frente principal", answer)
        self.assertIn("Cais 8: 215 m", answer)
        self.assertIn("Cais 8 faz parte do TMS 1", answer)
        self.assertIn("Cais 4: 175 m", answer)
        self.assertIn("regra de capacidade", answer)
        self.assertNotIn("ELBTOWER", answer)
        self.assertNotIn("Navios atracados em cais", answer)

    def test_spring_tide_definition_is_not_routed_to_live_tides(self) -> None:
        payload = answer_direct_operational_query("Quando se considera maré viva no Porto de Setúbal?")

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_rule", payload["answer_origin"])
        self.assertIn("baixa-mar é inferior a 1,0 m", payload["answer"])
        self.assertIn("preia-mar é superior a 3,0 m", payload["answer"])
        self.assertNotIn("Marés para", payload["answer"])

    def test_same_berth_priority_uses_p13_before_general_priority(self) -> None:
        payload = answer_direct_operational_query("Quando dois navios pretendem o mesmo cais, qual tem prioridade?")

        self.assertIsNotNone(payload)
        self.assertEqual("operational_priority", payload["answer_origin"])
        self.assertIn("8 milhas náuticas", payload["answer"])
        self.assertIn("Baliza número 2", payload["answer"])
        self.assertIn("planeamento geral", payload["answer"])
        self.assertNotIn("1. Manobras com reponto", payload["answer"])

    def test_lisnave_barra_entry_antecedence_uses_reponto_scheduling(self) -> None:
        payload = answer_direct_operational_query(
            "Com quanto tempo de antecedência se marca uma entrada de fora da barra para a LISNAVE?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("2 horas antes do reponto de maré pretendido", payload["answer"])
        self.assertNotIn("10,5 milhas náuticas", payload["answer"])

    def test_repeated_reponto_question_handles_lisnave_exit_and_tanquisado_entry(self) -> None:
        payload = answer_direct_operational_query(
            "Quando marco uma saida da Doca 22 da Lisnave e uma entrada para Tanquisado?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("Saída Doca 22", payload["answer"])
        self.assertIn("2 horas antes da preia-mar", payload["answer"])
        self.assertIn("Entrada Tanquisado", payload["answer"])
        self.assertIn("Fundeadouro Norte", payload["answer"])
        self.assertNotIn("120 min antes", payload["answer"])

    def test_troia_to_ecooil_today_uses_next_future_reponto_and_shift_context(self) -> None:
        services.tide_service = FakeSchedulingTideService()
        services.weather_service = FakeSchedulingWeatherService()

        payload = answer_direct_operational_query(
            "Tenho outro navio para mudar do fundeadouro Tróia para a Eco-Oil. A que horas devo marcar manobra? e se fosse para marcar hoje a manobra?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("mudança para Eco-Oil vinda de Tróia/Fundeadouro Sul", payload["answer"])
        self.assertIn("14:28", payload["answer"])
        self.assertIn("preia-mar às 15:28", payload["answer"])
        self.assertIn("1 hora antes", payload["answer"])
        self.assertNotIn("08:11", payload["answer"])
        self.assertNotIn("07:11", payload["answer"])

    def test_ecooil_low_tide_rule_does_not_assume_barra_schedule(self) -> None:
        services.tide_service = FakeSchedulingTideService()
        services.weather_service = FakeSchedulingWeatherService()

        payload = answer_direct_operational_query(
            "Podia atracar no baixa-mar das 09:11 na Eco-Oil, ou isso é contra as regras?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("Eco-Oil em baixa-mar", payload["answer"])
        self.assertIn("máximo 5,5 m", payload["answer"])
        self.assertIn("até 250 m", payload["answer"])
        self.assertIn("09:11 já passou", payload["answer"])
        self.assertNotIn("fora da Barra", payload["answer"])
        self.assertNotIn("07:11", payload["answer"])

    def test_tanquisado_ecooil_to_lisnave_shift_uses_specific_section(self) -> None:
        payload = answer_direct_operational_query(
            "Quando marco mudança de Tanquisado/Eco-Oil para Lisnave, incluindo Doca 21?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("SECÇÃO 3 — MUDANÇA TANQUISADO OU ECO-OIL PARA LISNAVE", payload["answer"])
        self.assertIn("Antecedência de marcação normal: 1 hora antes do reponto", payload["answer"])
        self.assertIn("Doca 21 ou Doca 22", payload["answer"])
        self.assertIn("2 horas antes da preia-mar", payload["answer"])

    def test_tms_high_draft_plural_calados_uses_tide_scheduling(self) -> None:
        payload = answer_direct_operational_query("Como marco TMS 1/TMS 2 com calados 9, 10,5 e 12 m?")

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("calado entre 9 m e 12 m", payload["answer"])
        self.assertIn("1h a 1h30 antes da preia-mar", payload["answer"])
        self.assertIn("calado praticável", payload["answer"])

    def test_it016_dwt_loaded_no_bowthruster_returns_ggp_count(self) -> None:
        payload = answer_direct_operational_query(
            "Quantos rebocadores são necessários para atracar carregado um navio entre 15.001 e 25.000 DWT sem bow thruster?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tug_guidance", payload["answer_origin"])
        self.assertIn("2 rebocadores grandes", payload["answer"])
        self.assertIn("1 pequeno", payload["answer"])
        self.assertIn("1 rebocador pequeno", payload["answer"])
        self.assertIn("GGp", payload["answer"])

    def test_tug_line_establishment_speed_does_not_route_to_parted_line_emergency(self) -> None:
        payload = answer_direct_operational_query(
            "Qual é a velocidade máxima do navio durante o estabelecimento do cabo de reboque?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tug_guidance", payload["answer_origin"])
        self.assertIn("6 nós sobre a água", payload["answer"])
        self.assertIn("5 nós à proa", payload["answer"])
        self.assertNotIn("Cabo do reboque partido", payload["answer"])

    def test_secil_entry_from_barra_uses_full_reponto_wording(self) -> None:
        payload = answer_direct_operational_query(
            "Com que antecedência se marca uma entrada para a Secil vinda de fora da Barra?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("secil_reponto_rule", payload["answer_origin"])
        self.assertIn("30 a 45 minutos antes do reponto de maré", payload["answer"])
        self.assertIn("preia-mar", payload["answer"])
        self.assertIn("baixa-mar", payload["answer"])
        self.assertIn("Piloto Coordenador", payload["answer"])

    def test_secil_same_berth_exit_and_fundeadouro_entry_calculates_times(self) -> None:
        payload = answer_direct_operational_query(
            "Se um navio sai da Secil no reponto das 13:51 e outro vem do Fundeadouro Norte para o mesmo cais, como se marcam as manobras?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("secil_reponto_rule", payload["answer_origin"])
        self.assertIn("13:36", payload["answer"])
        self.assertIn("45 minutos", payload["answer"])
        self.assertIn("1 hora", payload["answer"])
        self.assertIn("12:51", payload["answer"])
        self.assertIn("13:06", payload["answer"])
        self.assertIn("10 a 15 minutos", payload["answer"])

    def test_secil_west_reponto_is_not_optional(self) -> None:
        payload = answer_direct_operational_query("Para manobrar no Secil W, tenho de acertar com o reponto de mare?")

        self.assertIsNotNone(payload)
        self.assertEqual("secil_reponto_rule", payload["answer_origin"])
        self.assertIn("SECIL W/Oeste", payload["answer"])
        self.assertIn("todos os navios devem atracar próximo do reponto", payload["answer"])
        self.assertIn("LOA > 170 m", payload["answer"])
        self.assertNotIn("não existe imposição genérica", payload["answer"])

    def test_loss_of_engine_emergency_prioritizes_anchor_and_vts_channel(self) -> None:
        answer = self._answer(
            "O navio ficou sem máquina e ainda não tem rebocadores perto. O que aconselhas de imediato?"
        )

        self.assertIn("largar ferro imediatamente", answer)
        self.assertIn("VHF 73", answer)
        self.assertIn("canal 14", answer)
        self.assertIn("canal 71", answer)
        self.assertNotIn("Regra prática de posicionamento", answer)

    def test_loss_of_bow_thruster_emergency_sets_one_shackle_anchor(self) -> None:
        answer = self._answer("Se o navio perder bow na manobra, o que faz?")

        self.assertIn("1 manilha na agua", answer)
        self.assertIn("VHF 73", answer)
        self.assertIn("canal 14", answer)

    def test_fog_underway_colreg_procedure_is_not_plain_suspension(self) -> None:
        question = "Se um navio for apanhado no meio do nevoeiro a navegar, que procedimentos deve adoptar segundo a COLREG?"
        with self.app.test_request_context("/"):
            payload = answer_direct_operational_query(question)
            sources = build_operational_chat_sources(question)

        self.assertIsNotNone(payload)
        self.assertEqual("fog_underway_procedure", payload["answer_origin"])
        self.assertIn("Regra 19", payload["answer"])
        self.assertIn("Regra 35", payload["answer"])
        self.assertIn("1 som prolongado", payload["answer"])
        self.assertIn("Avaliar posição", payload["answer"])
        self.assertIn("cais de destino", payload["answer"])
        self.assertIn("fundeadouro", payload["answer"])
        self.assertIn("abortar antes de entrar", payload["answer"])
        self.assertIn("VHF 73", payload["answer"])
        self.assertNotIn("Não. Com nevoeiro em porto", payload["answer"])
        self.assertEqual(["fog_underway_procedure"], [source.get("retrieval_mode") for source in sources])

    def test_generic_fog_question_uses_port_suspension_priority_and_requests(self) -> None:
        payload = answer_direct_operational_query("O que fazer quando há nevoeiro?")

        self.assertIsNotNone(payload)
        self.assertEqual("operational_safety_limit", payload["answer_origin"])
        self.assertIn("pilotagem é suspensa", payload["answer"])
        self.assertIn("fila de prioridade", payload["answer"])
        self.assertIn("requisições continuam", payload["answer"])
        self.assertNotEqual("colreg_interpretation", payload["answer_origin"])

    def test_wind_maximum_admissible_question_uses_safety_rule_not_weather(self) -> None:
        payload = answer_direct_operational_query("Qual o vento máximo admissível para se manobrar?")

        self.assertIsNotNone(payload)
        self.assertEqual("operational_safety_limit", payload["answer_origin"])
        self.assertIn("25 kt", payload["answer"])
        self.assertIn("Acima de 20 kt", payload["answer"])
        self.assertIn("superior a 25 kt", payload["answer"])
        self.assertIn("manobras ficam suspensas", payload["answer"])
        self.assertNotIn("Meteorologia para Setúbal", payload["answer"])

    def test_wind_conditioning_threshold_question_uses_safety_rule(self) -> None:
        payload = answer_direct_operational_query(
            "A partir de que velocidade de vento as manobras ficam condicionadas?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_safety_limit", payload["answer_origin"])
        self.assertIn("Acima de 20 kt", payload["answer"])
        self.assertIn("25 kt", payload["answer"])

    def test_tms_high_draft_entry_gets_tide_scheduling_answer(self) -> None:
        payload = answer_direct_operational_query(
            "Um navio com 10,5 m de calado quer entrar no TMS 1 à preia-mar das 14:00. Quando marco?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("TMS 1/TMS 2", payload["answer"])
        self.assertIn("1h a 1h30 antes da preia-mar", payload["answer"])
        self.assertIn("12:30-13:00", payload["answer"])
        self.assertIn("calado praticável", payload["answer"])

    def test_sapec_high_draft_departure_gets_tide_scheduling_answer(self) -> None:
        payload = answer_direct_operational_query(
            "Um navio de grande calado quer sair da SAPEC ao reponto das 14:00. Quando marco?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("SAPEC", payload["answer"])
        self.assertIn("30 min antes do reponto", payload["answer"])
        self.assertIn("13:30", payload["answer"])

    def test_sapec_liquidos_imo_high_draft_mentions_limits_and_formula(self) -> None:
        payload = answer_direct_operational_query(
            "SAPEC Líquidos com 9,4 m de calado e carga IMO deve entrar à preia-mar das 14:00, quando marco?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("TGL/SAPEC Líquidos", payload["answer"])
        self.assertIn("12:30", payload["answer"])
        self.assertIn("9,5 m para IMO", payload["answer"])
        self.assertIn("10,0 m para não-IMO", payload["answer"])
        self.assertIn("fórmula de calado praticável", payload["answer"])

    def test_sapec_tps_tgl_high_draft_summary_covers_entry_departure_and_imo_limits(self) -> None:
        payload = answer_direct_operational_query(
            "Como marco SAPEC Sólidos e Líquidos com calado alto, IMO e não-IMO?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("Entrada TPS: marcar 1 hora e 30 minutos antes da preia-mar", payload["answer"])
        self.assertIn("Saída TPS com grande calado: marcar 30 minutos antes do reponto", payload["answer"])
        self.assertIn("Carga IMO: calado máximo de referência 9,5 m", payload["answer"])
        self.assertIn("Carga não-IMO: calado máximo de referência 10,0 m", payload["answer"])

    def test_reponto_vs_preia_mar_question_does_not_fall_to_live_tides(self) -> None:
        payload = answer_direct_operational_query(
            "Qual é a diferença entre marcar para o reponto e marcar para a preia-mar?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("corrente nula", payload["answer"])
        self.assertIn("profundidade suficiente", payload["answer"])
        self.assertIn("LISNAVE", payload["answer"])
        self.assertIn("Tanquisado", payload["answer"])
        self.assertNotIn("Marés para", payload["answer"])

    def test_teporset_departure_uses_local_reponto_margin(self) -> None:
        payload = answer_direct_operational_query(
            "Saída da Teporset para o reponto das 20:03, a que horas marco?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tide_scheduling", payload["answer_origin"])
        self.assertIn("15 min antes do reponto", payload["answer"])
        self.assertIn("19:48", payload["answer"])
        self.assertIn("reponto local acontece cerca de 15 min depois", payload["answer"])

    def test_numeric_wind_question_with_manobrar_uses_suspension_limit(self) -> None:
        payload = answer_direct_operational_query("Posso manobrar com 26 kt de vento se tiver reboques?")

        self.assertIsNotNone(payload)
        self.assertEqual("operational_safety_limit", payload["answer_origin"])
        self.assertIn("26 kt", payload["answer"])
        self.assertIn("superior a 25 kt", payload["answer"])
        self.assertIn("manobras ficam suspensas", payload["answer"])

    def test_colreg_source_coverage_question_is_not_misrouted_to_fog_procedure(self) -> None:
        payload = answer_direct_operational_query("A fonte RIEAM/COLREG cobre nevoeiro e ultrapassagem?")

        self.assertIsNotNone(payload)
        self.assertEqual("colreg_interpretation", payload["answer_origin"])
        self.assertIn("Regra 19", payload["answer"])
        self.assertIn("Regra 34", payload["answer"])
        self.assertIn("Ultrapassagem em canal estreito", payload["answer"])
        self.assertNotIn("Nevoeiro súbito com o navio já a navegar", payload["answer"])

    def test_colreg_source_with_visibility_words_is_not_misrouted_to_safety_threshold(self) -> None:
        payload = answer_direct_operational_query(
            "A base cobre RIEAM/COLREG para anti-colisão e visibilidade reduzida?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("colreg_interpretation", payload["answer_origin"])
        self.assertIn("Regra 5", payload["answer"])
        self.assertIn("Regra 19", payload["answer"])
        self.assertIn("anti-colisão", payload["answer"])
        self.assertNotIn("fog_visibility_km_reference", payload["answer"])

    def test_parted_mooring_lines_emergency_prepares_lines_and_tugs(self) -> None:
        answer = self._answer("Se partirem cabos na manobra, qual e a resposta imediata?")

        self.assertIn("preparar novos cabos", answer)
        self.assertIn("lancha de cabos", answer)
        self.assertIn("rebocadores de atencao", answer)

    def test_tug_line_parted_uses_ship_mooring_line_free_of_winch(self) -> None:
        answer = self._answer("Se o cabo do reboque partir, o que fazemos?")

        self.assertIn("cabo de amarracao do navio", answer)
        self.assertIn("sem estar no guincho", answer)
        self.assertIn("fazer firme ao reboque", answer)

    def test_parted_tug_line_de_wording_uses_ship_mooring_line(self) -> None:
        answer = self._answer("Cabo de reboque partido: que cabo devo preparar para o rebocador?")

        self.assertIn("cabo de amarracao do navio", answer)
        self.assertIn("sem estar no guincho", answer)
        self.assertIn("fazer firme ao reboque", answer)

    def test_blackout_with_tug_reference_does_not_load_tug_guidance_source(self) -> None:
        question = (
            "Um navio teve um problema. Blackout, não tem o rebocadores pedidos "
            "nem há nenhum por perto para o ajudar. O que aconselhas de imediato?"
        )
        with self.app.test_request_context("/"):
            payload = answer_direct_operational_query(question)
            sources = build_operational_chat_sources(question)

        self.assertIsNotNone(payload)
        self.assertEqual("operational_emergency_response", payload["answer_origin"])
        self.assertIn("Blackout/sem maquina", payload["answer"])
        self.assertIn("VHF 73", payload["answer"])
        retrieval_modes = [source.get("retrieval_mode") for source in sources]
        self.assertIn("operational_emergency_response", retrieval_modes)
        self.assertNotIn("operational_tug_guidance", retrieval_modes)

    def test_terse_tug_anchor_fragment_asks_for_reformulation(self) -> None:
        payload = answer_direct_operational_query("Navio reboques fundear")

        self.assertIsNotNone(payload)
        self.assertEqual("operational_clarification", payload["answer_origin"])
        self.assertIn("Reformula", payload["answer"])
        self.assertIn("fundear", payload["answer"])
        self.assertNotIn("GGp", payload["answer"])

    def test_navigation_light_characteristic_direct_answer(self) -> None:
        payload = answer_direct_operational_query("Qual e a caracteristica da Boia 1CN?")

        self.assertIsNotNone(payload)
        self.assertEqual("navigation_lights", payload["answer_origin"])
        self.assertIn("Boia N.º 1CN", payload["answer"])
        self.assertIn("Fl G 3s", payload["answer"])
        self.assertIn("38º30,33'N", payload["answer"])
        self.assertIn("IALA A", payload["answer"])

    def test_navigation_light_iala_direct_answer(self) -> None:
        payload = answer_direct_operational_query("Em Setubal usamos que sistema IALA?")

        self.assertIsNotNone(payload)
        self.assertEqual("navigation_lights", payload["answer_origin"])
        self.assertIn("IALA A", payload["answer"])
        self.assertIn("bombordo", payload["answer"])
        self.assertIn("estibordo", payload["answer"])

    def test_hidrolift_beam_limit_triggers_without_lisnave_word(self) -> None:
        payload = answer_direct_operational_query(
            "Tenho um navio para entrar no hidrolift no preia-mar das 20:03. "
            "O navio tem 45 m de boca, pode manobrar para entrar a essa hora?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_rule", payload["answer_origin"])
        self.assertIn("Não.", payload["answer"])
        self.assertIn("boca máxima de 32 m", payload["answer"])
        self.assertIn("Boca: 45 m", payload["answer"])
        self.assertIn("45 m de boca", payload["answer"])

    def test_secil_e_entry_timing_compares_marked_hour_with_reponto(self) -> None:
        payload = answer_direct_operational_query(
            "Marquei manobra de entrada para a Secil E as 1925. Está correta a hora?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("secil_entry_timing", payload["answer_origin"])
        self.assertIn("19:25", payload["answer"])
        self.assertIn("38 min antes do reponto", payload["answer"])
        self.assertIn("20:03", payload["answer"])
        self.assertIn("30-45 min", payload["answer"])
        self.assertIn("Atenção: o critério principal aqui não é apenas ser dia/noite", payload["answer"])
        self.assertNotIn("não há proibição", payload["answer"].lower())

    def test_secil_e_confirmation_question_is_treated_as_qa_not_action(self) -> None:
        payload = answer_direct_operational_query(
            "Tenho uma entrada para Secil Este às 19:25. O que precisas confirmar?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("secil_entry_timing", payload["answer_origin"])
        self.assertIn("reponto de maré", payload["answer"])
        self.assertIn("proveniência", payload["answer"])
        self.assertIn("30-45 min", payload["answer"])
        self.assertIn("45 min a 1 h", payload["answer"])

    def test_secil_e_draft_above_reference_is_not_emergency(self) -> None:
        payload = answer_direct_operational_query(
            "O navio vai para a Secil Este e tem 8,3 m de calado, há problema?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("secil_draft_rule", payload["answer_origin"])
        self.assertIn("há problema", payload["answer"])
        self.assertIn("8,3 m", payload["answer"])
        self.assertIn("8,0 m", payload["answer"])
        self.assertIn("não fechar a manobra", payload["answer"])
        self.assertNotIn("Emergencia operacional", payload["answer"])

    def test_contextualized_secil_e_draft_followup_gets_draft_rule(self) -> None:
        payload = answer_direct_operational_query(
            "O navio tem 8,3 m de calado, há problema? para Secil E"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("secil_draft_rule", payload["answer_origin"])
        self.assertIn("SECIL E/Este", payload["answer"])
        self.assertIn("excede", payload["answer"])

    def test_secil_w_reponto_question_gets_direct_rule_not_llm_fallback(self) -> None:
        payload = answer_direct_operational_query(
            "Entrada para SECIL W marcada para 13:30, tenho de ir ao reponto?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("secil_reponto_rule", payload["answer_origin"])
        self.assertIn("SECIL W/Oeste", payload["answer"])
        self.assertIn("todos os navios devem atracar próximo do reponto", payload["answer"])
        self.assertIn("30-45 min antes do reponto", payload["answer"])
        self.assertNotIn("não está previsto o recurso ao reponto", payload["answer"].lower())

    def test_roro_autoeuropa_can_exit_wording_uses_live_weather_and_tugs(self) -> None:
        services.weather_service.forecast["current"].update({"wind_kts": 13, "gust_kts": 20, "wind_dir": "S"})

        payload = answer_direct_operational_query(
            "Um roro vai sair agora da Autoeuropa. Tem 200 m e já pus 2 reboques. Pode sair com a meteorologia atual?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tug_guidance", payload["answer_origin"])
        self.assertIn("Recomendo 2 rebocadores", payload["answer"])
        self.assertIn("Ro-Ro com vento Sul a sair: 2 rebocadores", payload["answer"])
        self.assertIn("Autoeuropa", payload["answer"])
        self.assertIn("Meteorologia considerada", payload["answer"])
        self.assertIn("rajadas 20", payload["answer"])
        self.assertIn("ponderar atrasar", payload["answer"])

    def test_vessel_detail_answer_falls_back_to_catalog_by_call_sign(self) -> None:
        services.store.runtime_state["port_call_vessel_catalog"] = {
            "items": [
                {
                    "key": "imo:9329981",
                    "vessel_name": "GALBOT",
                    "vessel_imo": "9329981",
                    "vessel_call_sign": "9HA2522",
                    "vessel_flag": "Malta",
                    "vessel_type": "Graneis líquidos",
                    "vessel_loa_m": "128.6",
                    "vessel_beam_m": "20.4",
                    "vessel_gt_t": "7123",
                    "vessel_dwt_t": "10450",
                    "vessel_max_draft_m": "7.2",
                    "vessel_bow_thruster": "yes",
                    "vessel_stern_thruster": "unknown",
                }
            ],
            "deleted_keys": [],
        }

        answer = self._answer("Dados navio GALBOT call sign 9HA2522")

        self.assertIn("GALBOT", answer)
        self.assertIn("9HA2522", answer)
        self.assertIn("GT 7123", answer)
        self.assertIn("Ficha de catálogo", answer)

    def test_daylight_answer_uses_weather_astro_data(self) -> None:
        answer = self._answer("Qual o período luminoso para hoje?")

        self.assertIn("06:40", answer)
        self.assertIn("20:23", answer)
        self.assertIn("13h 43m", answer)

    def test_moon_answer_uses_weather_astro_data(self) -> None:
        answer = self._answer("Qual a fase da lua hoje?")

        self.assertIn("Lua cheia", answer)
        self.assertIn("98%", answer)

    def test_today_forecast_includes_next_hours_summary(self) -> None:
        answer = self._answer("Quais as previsões meteorológicas para hoje?")

        self.assertIn("Resumo das próximas horas", answer)
        self.assertIn("vento", answer)
        self.assertIn("rajadas", answer)

    def test_weather_typo_forecast_next_hours_uses_live_weather(self) -> None:
        answer = self._answer("previsao metrologica proximas horas")

        self.assertIn("Previsão meteorológica", answer)
        self.assertIn("Resumo das próximas horas", answer)
        self.assertIn("vento", answer)

    def test_next_days_forecast_includes_wind_and_gusts(self) -> None:
        answer = self._answer("Meteo próximos dias")

        self.assertIn("Previsão geral", answer)
        self.assertIn("vento médio", answer)
        self.assertIn("rajadas", answer)

    def test_expected_arrivals_uses_port_call_eta(self) -> None:
        self.activity["arrivals"] = [
            {
                "id": "pc-arrival",
                "reference_code": "PTSET26ARKLAF927D",
                "vessel_name": "ARKLOW GLOBE",
                "eta": "2026-04-30T15:00:00+01:00",
                "eta_label": "30 Apr 15:00",
                "last_port": "Lisboa",
                "berth_label": "SAPEC Sólidos",
                "agent_label": "Administrador",
                "agent_profile": {"organization": "APSS"},
                "maneuver_history": [
                    {
                        "id": "entry-12345678",
                        "type": "entry",
                        "planned_at": "2026-04-30T15:00:00+01:00",
                    }
                ],
            }
        ]

        answer = self._answer("Algum navio nas previsões de chegada?")

        self.assertIn("ARKLOW GLOBE", answer)
        self.assertIn("ETA 30 Apr 15:00", answer)
        self.assertIn("entrada ENTRY-12", answer)
        self.assertNotIn("ETA Sem hora", answer)

    def test_vessels_in_planning_question_returns_planned_maneuvers(self) -> None:
        self.activity["planned_maneuvers"] = [
            {
                "port_call_id": "pc-galbot",
                "reference_code": "PTSET26GALB123456",
                "vessel_name": "GALBOT",
                "maneuver_id": "391513b3-f521-4b90-9cdf-aafcc183b756",
                "maneuver_type": "entry",
                "maneuver_label": "Entrar",
                "situation_label": "Pendente",
                "situation_class": "pending",
                "planned_label": "19:12",
                "date_value": "2026-04-30T19:12:00+01:00",
                "local_origin": "Sines",
                "local_destination": "Tanquisado (lado jusante)",
                "agent_label": "Administrador",
                "agent_profile": {"organization": "APSS"},
                "pilot_label": "",
            }
        ]

        answer = self._answer("Que navios estão no planeamento?")

        self.assertIn("Manobras planeadas registadas", answer)
        self.assertIn("GALBOT", answer)
        self.assertIn("Tanquisado (lado jusante)", answer)
        self.assertIn("manobra 391513B3", answer)

    def test_planned_maneuvers_question_returns_planning(self) -> None:
        self.activity["planned_maneuvers"] = [
            {
                "port_call_id": "pc-way",
                "reference_code": "PTSET26WAYFC171CA",
                "vessel_name": "WAY FORWARD",
                "maneuver_id": "768ab23c-d9dc-4d5d-b722-49915d90a739",
                "maneuver_type": "entry",
                "maneuver_label": "Entrar",
                "situation_label": "Pendente",
                "planned_label": "15:00",
                "local_origin": "Southampton",
                "local_destination": "Cais 10 / Autoeuropa",
                "agent_label": "Administrador",
                "agent_profile": {"organization": "APSS"},
            }
        ]

        answer = self._answer("Que manobras estão previstas no planeamento?")

        self.assertIn("WAY FORWARD", answer)
        self.assertIn("Entrar 15:00", answer)
        self.assertIn("manobra 768AB23C", answer)

    def test_tug_question_with_current_weather_uses_wind_before_recommending(self) -> None:
        services.weather_service.forecast["current"]["wind_kts"] = 13
        services.weather_service.forecast["current"]["gust_kts"] = 20
        services.weather_service.forecast["current"]["wind_dir"] = "S"

        with self.app.test_request_context("/"):
            payload = answer_direct_operational_query(
                "Tenho um navio para sair da Autoeuropa. É um roro com 200 m e tem bowthruster. "
                "Quantos reboques devo pedir face às condições meteorológicas atuais?"
            )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tug_guidance", payload["answer_origin"])
        self.assertIn("Recomendo 2 rebocadores", payload["answer"])
        self.assertIn("Ro-Ro com vento Sul a sair: 2 rebocadores", payload["answer"])
        self.assertIn("Autoeuropa", payload["answer"])
        self.assertIn("Meteorologia considerada", payload["answer"])
        self.assertIn("rajadas 20 kts", payload["answer"])
        self.assertIn("ponderar atrasar", payload["answer"])

    def test_bulk_strong_north_departure_rule_without_word_vento_uses_four_tugs(self) -> None:
        payload = answer_direct_operational_query(
            "Graneleiro grande a sair com Norte forte: qual é a regra prática dos rebocadores?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tug_guidance", payload["answer_origin"])
        self.assertIn("Recomendo 4 rebocadores grandes", payload["answer"])
        self.assertIn("a sair com vento Norte forte: considerar 4 rebocadores grandes", payload["answer"])

    def test_tanquisado_east_wind_costado_question_without_tug_word_is_direct(self) -> None:
        payload = answer_direct_operational_query(
            "Com vento Leste forte a largar Tanquisado, que cuidado especial há no costado?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tug_guidance", payload["answer_origin"])
        self.assertIn("3 rebocadores", payload["answer"])
        self.assertIn("1 rebocador estabelecido à proa", payload["answer"])
        self.assertIn("terceiro", payload["answer"])
        self.assertIn("equivalência E=N", payload["answer"])

    def test_small_deep_no_bowthruster_rejects_small_tug(self) -> None:
        payload = answer_direct_operational_query(
            "Navio de 118 m, sem bowthruster e calado 8,4 m: pequeno reboque chega?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("operational_tug_guidance", payload["answer_origin"])
        self.assertIn("Recomendo 1 rebocador grande", payload["answer"])
        self.assertIn("nao rebocador pequeno de 25 t", payload["answer"])

    def test_alstom_wind_limit_blocks_at_15_knots_with_practical_rules(self) -> None:
        answer = self._answer("Entrada para a Alstom desde a Barra com vento 15 kts pode avançar?")

        self.assertIn("Local: ALSTOM", answer)
        self.assertIn("atracam apenas por estibordo", answer)
        self.assertIn("reponto de preia-mar", answer)
        self.assertIn("1h30", answer)
        self.assertIn("inferior a 15 kt", answer)
        self.assertIn("atinge/excede o limite prático", answer)

    def test_lisnave_cais_3a_length_uses_dolphin_operational_total(self) -> None:
        answer = self._answer("Navio de 360 m cabe no Cais 3 A da Lisnave?")

        self.assertIn("Cais 3 A", answer)
        self.assertIn("240 m", answer)
        self.assertIn("115 m", answer)
        self.assertIn("Duque d'Alba", answer)
        self.assertIn("366 metros de comprimento operacional", answer)
        self.assertIn("360 m fica dentro", answer)

    def test_lisnave_cais_3a_over_length_rejects_against_operational_total(self) -> None:
        answer = self._answer("E se o navio tiver 390 m para o Cais 3 A da Lisnave, cabe em comprimento?")

        self.assertIn("Cais 3 A", answer)
        self.assertIn("390 m excede", answer)
        self.assertIn("366 metros de comprimento operacional", answer)

    def test_tanquisado_length_uses_operational_total_not_physical_slot(self) -> None:
        answer = self._answer("Qual é o comprimento operacional do Tanquisado com duques d'alba?")

        self.assertIn("IT-010_Tanquisado.txt", answer)
        self.assertIn("Comprimento operacional total: 463 m", answer)
        self.assertIn("cais físico de 75 m", answer)
        self.assertIn("dois duques d'alba", answer)
        self.assertIn("não deve ser avaliado só pelo slot", answer)

    def test_doca21_depth_answer_includes_open_and_closed_gate_values(self) -> None:
        answer = self._answer("Qual é a profundidade disponível na entrada da Doca 21 com a comporta aberta?")

        self.assertIn("6,10 metros", answer)
        self.assertIn("5,49 metros", answer)
        self.assertIn("comporta aberta", answer)
        self.assertIn("comporta fechada", answer)

    def test_tanquisado_two_tugs_is_explicitly_insufficient(self) -> None:
        answer = self._answer("Entrada para Tanquisado com 2 rebocadores pode avancar?")

        self.assertIn("Recomendo 3 rebocadores", answer)
        self.assertIn("Rebocadores insuficientes", answer)
        self.assertIn("foram indicados 2", answer)

    def test_doca21_large_vessel_tug_question_inferrs_lisnave_from_doca(self) -> None:
        answer = self._answer("Mas o navio tem 300 m quantos rebocadores tem de usar para entrar na doca 21?")

        self.assertIn("Recomendo 6 rebocadores", answer)
        self.assertIn("Lisnave acima de 250 m", answer)
        self.assertIn("LOA indicado: 300 m", answer)

    def test_direct_answer_uses_current_followup_question_when_plan_is_contextualized(self) -> None:
        previous_plan = build_chat_execution_plan("Qual é o comprimento operacional do Cais 3 A na Lisnave?")

        tug_payload = answer_direct_operational_query(
            "Mas o navio tem 300 m quantos rebocadores tem de usar para entrar na doca 21?",
            plan=previous_plan,
        )
        length_payload = answer_direct_operational_query(
            "E se o navio tiver 390 m para o Cais 3 A da Lisnave, cabe em comprimento?",
            plan=previous_plan,
        )

        self.assertIsNotNone(tug_payload)
        self.assertIn("Recomendo 6 rebocadores", tug_payload["answer"])
        self.assertIn("Lisnave acima de 250 m", tug_payload["answer"])
        self.assertIsNotNone(length_payload)
        self.assertIn("390 m excede", length_payload["answer"])

    def test_barra_draft_tup_and_visibility_threshold_have_direct_answers(self) -> None:
        barra = self._answer("Qual é o calado máximo na barra do Porto de Setúbal?")
        tup = self._answer("Qual é a fórmula da TUP para um navio de contentores?")
        visibility = self._answer("Se o live feed indicar 1,0 km de visibilidade, o bot trata como visibilidade reduzida?")

        self.assertIn("10,30 m + altura da maré", barra)
        self.assertIn("12,0 m", barra)
        self.assertIn("ondulação inferior a 1 m", barra)
        self.assertIn("TUP = GT x UP", tup)
        self.assertIn("contentores", tup)
        self.assertIn("fog_visibility_km_reference", visibility)
        self.assertIn("1.0 km", visibility)
        self.assertIn("visibilidade operacional reduzida", visibility)

    def test_visibility_threshold_accepts_limiar_and_decimal_value(self) -> None:
        answer = self._answer("Se a visibilidade live aparecer a 0,8 km, isso é abaixo do teu limiar técnico?")

        self.assertIn("fog_visibility_km_reference", answer)
        self.assertIn("0,8 km está abaixo", answer)
        self.assertIn("1,0 km", answer)

    def test_tms2_capacity_and_positions_are_answered_directly(self) -> None:
        adjacent = self._answer("No TMS 2, a lógica de ocupar cais adjacentes é semelhante ao TMS 1?")
        positions = self._answer("O TMS 2 tem quantas posições no modelo atual?")

        self.assertIn("pode ocupar posições adjacentes", adjacent)
        self.assertIn("4 posições", adjacent)
        self.assertIn("não continua para o Cais 8", adjacent)
        self.assertIn("4 posições", positions)
        self.assertIn("Posição A", positions)
        self.assertIn("Posição D", positions)

    def test_operational_priority_questions_are_answered_directly(self) -> None:
        conflict = self._answer(
            "Tenho no mesmo reponto um navio de passageiros e uma entrada normal de carga geral. Quem priorizo?"
        )
        order = self._answer("Qual é a ordem geral: passageiros, gado vivo, reefers, roro, contentores e outros?")

        self.assertIn("reponto de maré", conflict)
        self.assertIn("priorizo o navio de passageiros", conflict)
        self.assertIn("saídas > mudanças > entradas", conflict)
        self.assertIn("Passageiros, animais vivos/gado vivo, reefers", order)
        self.assertIn("Ro-Ro", order)
        self.assertIn("Contentores", order)

    def test_local_culture_outao_answer_does_not_use_operational_route(self) -> None:
        answer = self._answer("Conta-me uma curiosidade curta sobre o Forte do Outão, sem misturar com regras de manobra.")

        self.assertIn("Outão", answer)
        self.assertIn("1390", answer)
        self.assertIn("Hospital Ortopédico", answer)
        self.assertNotIn("milhas náuticas", answer)

    def test_checklist_answers_pull_terminal_specific_sources(self) -> None:
        eco = self._answer("A checklist puxa regras Eco-Oil para uma entrada?")
        tanq = self._answer("A checklist puxa regras Tanquisado para uma saida?")
        lisnave = self._answer("A checklist da manobra avisa se uma doca Lisnave estiver com 3 rebocadores?")

        self.assertIn("IT-008_EcoOil.txt", eco)
        self.assertIn("Atracacao noturna proibida", eco)
        self.assertIn("IT-010_Tanquisado.txt", tanq)
        self.assertIn("calado maximo absoluto", tanq)
        self.assertIn("Saida fora de reponto", tanq)
        self.assertIn("preia-mar precedente", tanq)
        self.assertIn("Lisnave - doca", lisnave)
        self.assertIn("3 rebocadores", lisnave)
        self.assertIn("4 rebocadores", lisnave)
        self.assertIn("proa a norte", lisnave)

    def test_navigation_lights_source_coverage_does_not_route_to_colreg_fishing(self) -> None:
        payload = answer_direct_operational_query(
            "A fonte de luzes tem registos indexáveis da Boia 1CN, Boia 2CS e Doca Pesca?"
        )

        self.assertIsNotNone(payload)
        self.assertEqual("navigation_lights", payload["answer_origin"])
        self.assertIn("Doca Pesca", payload["answer"])
        self.assertIn("Boia N.º 1CN", payload["answer"])
        self.assertIn("Boia N.º 2CS", payload["answer"])
        self.assertNotIn("Regra 26 - Pesca", payload["answer"])


if __name__ == "__main__":
    unittest.main()
