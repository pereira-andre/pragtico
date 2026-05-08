from __future__ import annotations

import unittest

from core.operational_sources import answer_direct_operational_query
from domain.tug_guidance import build_tug_operational_guidance_source


class TugOperationalGuidanceTests(unittest.TestCase):
    def _snippet(self, question: str) -> str:
        source = build_tug_operational_guidance_source(question, "knowledge")
        self.assertIsNotNone(source)
        return str(source["snippet"])

    def test_lisnave_gap_between_100_and_150_uses_three_tugs(self) -> None:
        snippet = self._snippet("Um navio de 130 m para a Lisnave precisa de quantos rebocadores?")

        self.assertIn("Lisnave acima de 100 m ate 150 m: 3 rebocadores", snippet)

    def test_lisnave_over_250_direct_answer_uses_six_tugs(self) -> None:
        payload = answer_direct_operational_query(
            "Mas o navio tem 300 m quantos rebocadores tem de usar para entrar na doca 21?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("Recomendo 6 rebocadores", payload["answer"])
        self.assertIn("Lisnave acima de 250 m: 6 rebocadores", payload["answer"])
        self.assertNotIn("Recomendo 3 rebocadores", payload["answer"])

    def test_lisnave_over_250_guidance_orders_specific_rule_before_generic_minimum(self) -> None:
        snippet = self._snippet("Um navio na LISNAVE de 300 m manobra com quantos rebocadores normalmente?")

        specific = snippet.index("Lisnave acima de 250 m: 6 rebocadores")
        generic = snippet.index("Lisnave: usar sempre no minimo 3 rebocadores")
        self.assertLess(specific, generic)

    def test_critical_south_channel_berths_use_three_tugs_minimum(self) -> None:
        snippet = self._snippet("Navio de 101 m com bowthruster para Tanquisado precisa de quantos rebocadores?")

        self.assertIn("Tanquisado: usar sempre no minimo 3 rebocadores", snippet)

        ecooil_snippet = self._snippet("Navio pequeno com bowthruster para Eco-Oil leva quantos rebocadores?")
        self.assertIn("Eco-Oil: usar sempre no minimo 3 rebocadores", ecooil_snippet)

    def test_direct_answer_uses_tanquisado_three_tugs_minimum(self) -> None:
        payload = answer_direct_operational_query(
            "Navio de 101 m com bowthruster para Tanquisado precisa de quantos rebocadores?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("Recomendo 3 rebocadores", payload["answer"])
        self.assertIn("Tanquisado: usar sempre no minimo 3 rebocadores", payload["answer"])

    def test_roro_over_220_with_strong_north_wind_uses_four_tugs(self) -> None:
        snippet = self._snippet("Quantos reboques para RORO de 230 m a entrar com vento norte forte?")

        self.assertIn("Ro-Ro com mais de 220 m e vento Norte forte: considerar 4 rebocadores grandes", snippet)
        self.assertIn("Ro-Ro com vento Norte a entrar: 3 rebocadores", snippet)

    def test_bulk_like_north_strong_departure_uses_four_tugs_outside_south_channel_exceptions(self) -> None:
        snippet = self._snippet("Um graneleiro de 190 m a sair do TMS2 com vento norte forte leva quantos rebocadores?")

        self.assertIn("a sair com vento Norte forte: considerar 4 rebocadores grandes", snippet)
        self.assertIn("com vento Norte a sair: 3 rebocadores", snippet)

    def test_bulk_like_north_strong_departure_excludes_tanquisado(self) -> None:
        snippet = self._snippet("Um graneleiro de 190 m a sair de Tanquisado com vento norte forte leva quantos rebocadores?")

        self.assertNotIn("a sair com vento Norte forte: considerar 4 rebocadores grandes", snippet)
        self.assertIn("com vento Norte a sair: 3 rebocadores", snippet)

    def test_bulk_like_north_strong_departure_excludes_shipyard_alias(self) -> None:
        snippet = self._snippet("Um graneleiro de 190 m a sair do estaleiro com vento norte forte leva quantos rebocadores?")

        self.assertNotIn("a sair com vento Norte forte: considerar 4 rebocadores grandes", snippet)
        self.assertIn("terminal/cais inferido: Lisnave", snippet)

    def test_west_east_equivalence_is_not_applied_to_tanquisado(self) -> None:
        snippet = self._snippet("Um graneleiro de 190 m a sair de Tanquisado com vento E leva quantos rebocadores?")

        self.assertIn("E mencionado: equivalencia E=N fraco nao aplicada sem contexto TMS2/Autoeuropa", snippet)
        self.assertNotIn("com vento Norte a sair: 3 rebocadores", snippet)

    def test_tanquisado_departure_strong_east_wind_adds_side_push_guidance(self) -> None:
        snippet = self._snippet("A sair de Tanquisado com vento E forte, onde meto o reboque?")

        self.assertIn("Tanquisado com 3 rebocadores", snippet)
        self.assertIn("1 a proa e 1 a popa", snippet)
        self.assertIn("Tanquisado a sair com vento E forte", snippet)
        self.assertIn("1 rebocador a empurrar ao costado", snippet)
        self.assertIn("direcao de vento inferida: E", snippet)

    def test_tanquisado_strong_west_wind_does_not_apply_east_side_push_guidance(self) -> None:
        snippet = self._snippet("A sair de Tanquisado com vento W forte, onde meto o reboque?")

        self.assertNotIn("Tanquisado a sair com vento E forte", snippet)

    def test_ecooil_departure_strong_west_wind_adds_side_push_guidance(self) -> None:
        snippet = self._snippet("A sair da Eco-Oil com vento W forte, onde meto o reboque?")

        self.assertIn("Eco-Oil com 3 rebocadores", snippet)
        self.assertIn("1 a proa e 1 a popa", snippet)
        self.assertIn("Eco-Oil a sair com vento W forte", snippet)
        self.assertIn("1 rebocador a empurrar ao costado", snippet)

    def test_west_east_equivalence_applies_to_tms2_autoeuropa_context(self) -> None:
        snippet = self._snippet("Um porta contentores grande de 190 m a sair do TMS2 com vento E leva quantos rebocadores?")

        self.assertIn("E tratado como N fraco nos terminais TMS2/Autoeuropa", snippet)
        self.assertIn("com vento Norte a sair: 3 rebocadores", snippet)

    def test_small_deep_draft_no_bowthruster_uses_large_tug(self) -> None:
        snippet = self._snippet("Navio sem bowthruster de 110 m e calado 8 m precisa de quantos rebocadores?")

        self.assertIn("pelo menos 1 rebocador grande de cerca de 35 t", snippet)

    def test_direct_answer_handles_singular_rebocador_rule(self) -> None:
        payload = answer_direct_operational_query(
            "Navio sem bowthruster de 110 m e calado 8 m precisa de quantos rebocadores?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("Recomendo 1 rebocador grande", payload["answer"])

    def test_emergency_with_tug_reference_does_not_return_positioning_rules(self) -> None:
        source = build_tug_operational_guidance_source(
            "O navio ficou sem máquina e ainda não tem rebocadores perto. O que aconselhas de imediato?",
            "knowledge",
        )

        self.assertIsNone(source)

    def test_direct_tanquisado_positioning_keeps_bow_and_stern_established(self) -> None:
        payload = answer_direct_operational_query(
            "A sair de Tanquisado com vento E forte, onde meto o reboque?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("1 a proa e 1 a popa", payload["answer"])
        self.assertIn("terceiro", payload["answer"])
        self.assertIn("empurrar ao costado", payload["answer"])

    def test_fourth_tug_pushes_alongside_or_standby_when_no_room(self) -> None:
        payload = answer_direct_operational_query(
            "Tenho 4 rebocadores na Tanquisado, onde meto o quarto reboque?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("4.º rebocador", payload["answer"])
        self.assertIn("empurrar ao costado", payload["answer"])
        self.assertIn("ma vizinhanca", payload["answer"])
        self.assertIn("standby", payload["answer"])

    def test_five_tugs_lisnave_bow_south_sets_two_forward_one_aft(self) -> None:
        payload = answer_direct_operational_query(
            "Na Lisnave com 5 rebocadores e navio com proa a sul, onde meto os rebocadores?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("2 rebocadores a proa", payload["answer"])
        self.assertIn("1 rebocador a popa", payload["answer"])

    def test_five_tugs_lisnave_stern_south_sets_two_aft_one_forward(self) -> None:
        payload = answer_direct_operational_query(
            "Na Lisnave com 5 rebocadores e navio com popa a sul, onde meto os rebocadores?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("2 rebocadores a popa", payload["answer"])
        self.assertIn("1 rebocador a proa", payload["answer"])

    def test_six_tugs_sets_two_forward_two_aft_and_two_pushing(self) -> None:
        payload = answer_direct_operational_query(
            "Com 6 rebocadores, onde meto os rebocadores?"
        )

        self.assertIsNotNone(payload)
        self.assertIn("2 rebocadores a proa", payload["answer"])
        self.assertIn("2 rebocadores a popa", payload["answer"])
        self.assertIn("os outros 2 empurram ao costado", payload["answer"])


if __name__ == "__main__":
    unittest.main()
