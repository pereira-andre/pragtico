from __future__ import annotations

import unittest

from domain.route_transit import route_transit_answer


class RouteTransitAnswerTests(unittest.TestCase):
    def test_tms2_distance_from_pilar_2_is_deterministic(self) -> None:
        answer = route_transit_answer("Qual a distância do pilar 2 até ao cais TMS2?")

        self.assertIsNotNone(answer)
        self.assertIn("6,5 milhas náuticas", answer["answer"])
        self.assertIn("TMS2", answer["answer"])
        self.assertIn("TMS 2", answer["answer"])

    def test_reverse_lisnave_to_pilar_2_uses_reverse_wording(self) -> None:
        answer = route_transit_answer("Quanto tempo levo a sair da LISNAVE até chegar ao pilar 2?")

        self.assertIsNotNone(answer)
        self.assertIn("Da LISNAVE/Mitrena até ao Pilar 2", answer["answer"])
        self.assertIn("1 hora e 30 minutos a 2 horas", answer["answer"])

    def test_alstom_barra_uses_high_water_lead_time(self) -> None:
        answer = route_transit_answer(
            "Quanto tempo da Barra para o Cais Alstom para apanhar o reponto de preia-mar?"
        )

        self.assertIsNotNone(answer)
        self.assertEqual("operational_route_transit", answer["answer_origin"])
        self.assertIn("Cais ALSTOM", answer["answer"])
        self.assertIn("1 hora e 30 minutos antes da preia-mar", answer["answer"])
        self.assertIn("reponto de preia-mar", answer["answer"])

    def test_tms1_reference_distances_are_deterministic_and_reversible(self) -> None:
        cases = [
            ("Qual a distância do TMS 1 até à Alstom?", "3,5 milhas náuticas", "Cais ALSTOM"),
            ("Qual a distância do TMS 1 até à SAPEC?", "2,2 milhas náuticas", "SAPEC"),
            ("Qual a distância do TMS 1 até às Praias do Sado?", "1,6 milhas náuticas", "Praias do Sado"),
            ("Qual a distância do TMS 1 até à Autoeuropa em NM?", "1,0 milha náutica", "Autoeuropa"),
            ("Qual a distância do TMS 1 até à bóia João Farto?", "1,6 milhas náuticas", "Bóia João Farto"),
            ("Qual a distância do TMS 1 até ao Outão?", "3,0 milhas náuticas", "Outão"),
            ("Qual a distância do TMS 1 até fora da Barra?", "6,0 milhas náuticas", "fora da Barra"),
            ("Qual a distância da Autoeuropa ao TMS 1?", "1,0 milha náutica", "Autoeuropa"),
            ("Qual a distância do Pilar 2 até ao TMS 1?", "6,0 milhas náuticas", "TMS 1"),
        ]

        for question, distance, token in cases:
            with self.subTest(question=question):
                answer = route_transit_answer(question)

                self.assertIsNotNone(answer)
                self.assertEqual("operational_route_transit", answer["answer_origin"])
                self.assertIn(distance, answer["answer"])
                self.assertIn(token, answer["answer"])
                self.assertIn("pode ser somada", answer["answer"])

    def test_setubal_route_graph_calculates_remaining_north_channel(self) -> None:
        answer = route_transit_answer("Estou na Bóia João Farto para a Alstom, quanto falta?")

        self.assertIsNotNone(answer)
        self.assertEqual("operational_route_transit", answer["answer_origin"])
        self.assertIn("Canal Norte", answer["answer"])
        self.assertIn("5,0 milhas náuticas", answer["answer"])
        self.assertIn("Bóia João Farto -> Bóia 1CC: rumo 040°", answer["answer"])
        self.assertIn("Bóia 3CC -> TMS 1: rumo 105°", answer["answer"])
        self.assertIn("Bóia 5CC -> TMS 2: rumo 120°", answer["answer"])
        self.assertIn("SAPEC -> Cais ALSTOM: rumo 120°", answer["answer"])
        self.assertNotIn("rumo inverso", answer["answer"])

    def test_tms_references_are_on_north_channel_before_autoeuropa(self) -> None:
        answer = route_transit_answer("Da entrada da barra ao TMS2 pelo canal norte, quanto falta?")

        self.assertIsNotNone(answer)
        self.assertEqual("operational_route_transit", answer["answer_origin"])
        self.assertIn("Canal Norte", answer["answer"])
        self.assertIn("6,5 milhas náuticas", answer["answer"])
        self.assertIn("TMS 1 -> Bóia 5CC", answer["answer"])
        self.assertIn("Bóia 5CC -> TMS 2", answer["answer"])

    def test_setubal_route_graph_calculates_eta_from_speed_and_start_time(self) -> None:
        answer = route_transit_answer(
            "Da Bóia 12 CS para a Lisnave a 6 nós, saída às 10:00, qual ETA?"
        )

        self.assertIsNotNone(answer)
        self.assertIn("Canal Sul para LISNAVE", answer["answer"])
        self.assertIn("1,0 milha náutica", answer["answer"])
        self.assertIn("Bóia 12CS -> Bóia 14CS", answer["answer"])
        self.assertIn("A 6,0 kt, duração estimada: 10 min.", answer["answer"])
        self.assertIn("ETA ao destino: 10:10", answer["answer"])

    def test_setubal_route_graph_reverses_headings_for_departure(self) -> None:
        answer = route_transit_answer("Da Lisnave para o Pilar 2, quais os rumos de saída?")

        self.assertIsNotNone(answer)
        self.assertIn("10,5 milhas náuticas", answer["answer"])
        self.assertIn("LISNAVE / docas / Hidrolift -> Bóia 14CS / fim do Canal Sul: rumo 210°", answer["answer"])
        self.assertIn("Outão -> Pilar 2 / entrada da Barra: rumo 220°", answer["answer"])
        self.assertNotIn("rumo inverso", answer["answer"])

    def test_cross_channel_route_uses_joao_farto_between_lisnave_and_tms1(self) -> None:
        answer = route_transit_answer(
            "Vou mudar um navio da LISNAVE para o TMS1. Se for a 5 kts quanto tempo levo de um cais ao outro?"
        )

        self.assertIsNotNone(answer)
        self.assertEqual("operational_route_transit", answer["answer_origin"])
        self.assertIn("Canal Sul / Canal Norte via Bóia João Farto", answer["answer"])
        self.assertIn("7,9 milhas náuticas", answer["answer"])
        self.assertIn("LISNAVE / docas / Hidrolift -> Bóia 14CS", answer["answer"])
        self.assertIn("Bóia 4CS -> Bóia João Farto", answer["answer"])
        self.assertIn("Bóia João Farto -> Bóia 1CC", answer["answer"])
        self.assertIn("Bóia 3CC -> TMS 1", answer["answer"])
        self.assertIn("A 5,0 kt, duração estimada: 1 h 35 min.", answer["answer"])
        self.assertNotIn("rumo inverso", answer["answer"])

    def test_setubal_route_graph_covers_full_north_and_south_channel_totals(self) -> None:
        north = route_transit_answer("Da posição de embarque até ao fim do canal norte, qual a distância?")
        south = route_transit_answer("Da entrada da barra até ao fim do canal sul, quais as milhas?")

        self.assertIsNotNone(north)
        self.assertIn("Canal Norte", north["answer"])
        self.assertIn("10,3 milhas náuticas", north["answer"])
        self.assertIn("Pilot station / posição de embarque -> Pilar 2", north["answer"])

        self.assertIsNotNone(south)
        self.assertIn("Canal Sul", south["answer"])
        self.assertIn("10,0 milhas náuticas", south["answer"])
        self.assertIn("Bóia 12CS -> Bóia 14CS", south["answer"])

    def test_common_multi_destination_time_summaries_do_not_drop_destinations(self) -> None:
        cases = [
            (
                "Quanto tempo leva do Fundeadouro Norte para os cais a norte, cais a sul e a SECIL?",
                ("15 a 25 minutos", "SECIL", "20 minutos", "cais a sul", "1 hora e 30 minutos"),
            ),
            (
                "Quanto tempo leva do Canal Sul para cais do sul, cais a norte e SECIL?",
                ("cais do sul", "30 minutos a 1 hora", "cais a norte", "1 hora a 1 hora e 20 minutos", "SECIL", "40 minutos"),
            ),
            (
                "Quanto tempo leva desde a entrada da Barra até à SECIL, Praias do Sado, SAPEC e fundeadouros?",
                ("SECIL", "30 minutos", "Praias do Sado", "SAPEC", "1 hora e 20 minutos", "Fundeadouro Norte", "45 minutos", "Fundeadouro Sul"),
            ),
        ]

        for question, tokens in cases:
            with self.subTest(question=question):
                answer = route_transit_answer(question)

                self.assertIsNotNone(answer)
                self.assertEqual("operational_route_transit", answer["answer_origin"])
                for token in tokens:
                    self.assertIn(token, answer["answer"])

    def test_route_order_question_is_not_confused_with_canal_norte_tug_rules(self) -> None:
        answer = route_transit_answer("Qual a ordem dos cais de entrada pelo Canal Norte e pelo Canal Sul?")

        self.assertIsNotNone(answer)
        self.assertEqual("operational_route_transit", answer["answer_origin"])
        self.assertIn("Entrada pelo Canal Norte", answer["answer"])
        self.assertIn("TMS 1 -> TMS 2 -> Autoeuropa", answer["answer"])
        self.assertIn("Entrada pelo Canal Sul", answer["answer"])
        self.assertIn("Tanquisado/Eco-Oil", answer["answer"])
        self.assertNotIn("dois rebocadores à popa", answer["answer"])

    def test_pilot_station_distance_uses_pilot_wording_not_barra_draft(self) -> None:
        answer = route_transit_answer("A que distância está a posição de embarque dos pilotos da entrada da barra?")

        self.assertIsNotNone(answer)
        self.assertEqual("operational_route_transit", answer["answer_origin"])
        self.assertIn("pilotos", answer["answer"])
        self.assertIn("1 milha náutica", answer["answer"])
        self.assertIn("entrada da Barra", answer["answer"])
        self.assertNotIn("Calado máximo", answer["answer"])

    def test_fundeadouro_norte_to_lisnave_reponto_lead_time_is_one_hour_and_half(self) -> None:
        answer = route_transit_answer(
            "Navio do Fundeadouro Norte para a Lisnave deve sair quando para chegar ao reponto das 20:03?"
        )

        self.assertIsNotNone(answer)
        self.assertEqual("operational_route_transit", answer["answer_origin"])
        self.assertIn("Percurso/duracao", answer["answer"])
        self.assertIn("cerca de 1 hora e 30 minutos", answer["answer"])
        self.assertIn("20:03", answer["answer"])
        self.assertIn("18:33", answer["answer"])
        self.assertIn("fase critica", answer["answer"])

    def test_fundeadouro_norte_to_south_quays_reponto_lead_time_is_one_hour_and_half(self) -> None:
        for destination in ("Tanquisado", "Eco-Oil", "Teporset"):
            with self.subTest(destination=destination):
                answer = route_transit_answer(
                    f"Navio do Fundeadouro Norte para {destination} deve sair quando para chegar ao reponto das 20:03?"
                )

                self.assertIsNotNone(answer)
                self.assertEqual("operational_route_transit", answer["answer_origin"])
                self.assertIn("cerca de 1 hora e 30 minutos", answer["answer"])
                self.assertIn("20:03", answer["answer"])
                self.assertIn("18:33", answer["answer"])
                self.assertIn("cais a sul", answer["answer"])


if __name__ == "__main__":
    unittest.main()
