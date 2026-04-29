from __future__ import annotations

import unittest
from pathlib import Path

from domain.knowledge_chunking import structured_chunk_document
from domain.port_entities import detect_port_entities, entity_names_from_matches
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
