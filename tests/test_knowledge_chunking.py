from __future__ import annotations

import unittest
from pathlib import Path

from domain.knowledge_chunking import structured_chunk_document
from domain.port_entities import (
    detect_port_entities,
    entity_names_from_matches,
    primary_entity,
    resolve_port_entity,
)
from integrations.rag_engine import SimpleRAGEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PortEntityTests(unittest.TestCase):
    def test_detects_specific_sapec_entities(self) -> None:
        names = entity_names_from_matches(detect_port_entities("Que restrições existem na SAPEC líquidos?"))
        self.assertIn("SAPEC liquidos", names)

        names = entity_names_from_matches(detect_port_entities("E na SAPEC sólidos?"))
        self.assertIn("SAPEC solidos", names)

    def test_detects_specific_secil_entities(self) -> None:
        names = entity_names_from_matches(detect_port_entities("O que diz o documento sobre a Secil W?"))
        self.assertIn("Secil W", names)

        names = entity_names_from_matches(detect_port_entities("E a Secil E?"))
        self.assertIn("Secil E", names)

    def test_generic_sapec_and_secil_return_disambiguation_options(self) -> None:
        sapec = resolve_port_entity("Fala-me da SAPEC")
        self.assertEqual(sapec["primary"]["name"], "SAPEC")
        self.assertTrue(sapec["is_ambiguous"])
        self.assertEqual(sapec["disambiguation"]["options"], ["SAPEC solidos", "SAPEC liquidos"])
        self.assertNotIn("SAPEC solidos", entity_names_from_matches(sapec["matches"]))
        self.assertNotIn("SAPEC liquidos", entity_names_from_matches(sapec["matches"]))

        secil = resolve_port_entity("Que regras existem para a Secil?")
        self.assertEqual(secil["primary"]["name"], "Secil")
        self.assertTrue(secil["is_ambiguous"])
        self.assertEqual(secil["disambiguation"]["options"], ["Secil W", "Secil E"])
        self.assertNotIn("Teporset", entity_names_from_matches(secil["matches"]))

    def test_specific_aliases_override_generic_groups(self) -> None:
        self.assertEqual(primary_entity("restrições na SAPEC líquidos")["name"], "SAPEC liquidos")
        self.assertFalse(resolve_port_entity("restrições na SAPEC líquidos")["is_ambiguous"])

        self.assertEqual(primary_entity("navio para o cais oeste da Secil")["name"], "Secil W")
        self.assertFalse(resolve_port_entity("navio para o cais oeste da Secil")["is_ambiguous"])

    def test_common_alias_variants_resolve_to_expected_terminals(self) -> None:
        cases = {
            "Atracar na EcoOil": "Eco-Oil",
            "ir para a ECOIL": "Eco-Oil",
            "Terminal ABB-ALSTOM": "Alstom",
            "Terminal Uralada": "Uralada",
            "Estaleiros Mitrena": "Lisnave",
            "Docas da Lisnave": "Lisnave",
            "cais das Praias do Sado": "Praias do Sado",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(primary_entity(query)["name"], expected)


class StructuredChunkingTests(unittest.TestCase):
    def test_secil_specific_sections_do_not_mix_west_and_east(self) -> None:
        text = (PROJECT_ROOT / "knowledge" / "IT-009_Secil.txt").read_text(encoding="utf-8")
        chunks = structured_chunk_document(text, "IT-009_Secil.txt")
        for chunk in chunks:
            section = chunk.get("section") or ""
            if not section.startswith(("CAIS DE OESTE", "CAIS DE ESTE")):
                continue
            names = set(chunk.get("entity_names") or [])
            self.assertFalse({"Secil W", "Secil E"} <= names, section)

    def test_sapec_sections_keep_specific_entities(self) -> None:
        text = (PROJECT_ROOT / "knowledge" / "IT-029_SAPEC.txt").read_text(encoding="utf-8")
        chunks = structured_chunk_document(text, "IT-029_SAPEC.txt")
        solids = [chunk for chunk in chunks if chunk.get("section", "").startswith("TPS")]
        liquids = [chunk for chunk in chunks if chunk.get("section", "").startswith("TGL")]

        self.assertTrue(any("SAPEC solidos" in (chunk.get("entity_names") or []) for chunk in solids))
        self.assertTrue(any("SAPEC liquidos" in (chunk.get("entity_names") or []) for chunk in liquids))


class RetrievalRerankTests(unittest.TestCase):
    def test_rerank_prefers_specific_entity_over_conflicting_entity(self) -> None:
        engine = SimpleRAGEngine.__new__(SimpleRAGEngine)
        query = "Que restrições existem na SAPEC líquidos?"
        query_entities = engine._query_entities(query)
        candidates = [
            {
                "id": "sapec-solidos",
                "document": "IT-029_SAPEC.txt",
                "chunk_id": 1,
                "text": "SAPEC TPS solidos calado restricoes",
                "entity_names": ["SAPEC solidos", "SAPEC"],
                "score": 0.9,
                "retrieval_mode": "semantic",
            },
            {
                "id": "sapec-liquidos",
                "document": "IT-029_SAPEC.txt",
                "chunk_id": 2,
                "text": "SAPEC TGL liquidos calado restricoes",
                "entity_names": ["SAPEC liquidos", "SAPEC"],
                "score": 0.4,
                "retrieval_mode": "lexical",
            },
        ]

        results = engine._rerank_candidates(query, candidates, query_entities, top_k=1)

        self.assertEqual(results[0]["id"], "sapec-liquidos")


if __name__ == "__main__":
    unittest.main()
