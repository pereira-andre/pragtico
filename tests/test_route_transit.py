from __future__ import annotations

import unittest

from domain.route_transit import route_transit_answer


class RouteTransitAnswerTests(unittest.TestCase):
    def test_tms2_distance_from_pilar_2_is_deterministic(self) -> None:
        answer = route_transit_answer("Qual a distância do pilar 2 até ao cais TMS2?")

        self.assertIsNotNone(answer)
        self.assertIn("6,5 milhas náuticas", answer["answer"])
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


if __name__ == "__main__":
    unittest.main()
