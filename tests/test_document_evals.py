import unittest
import tempfile
from pathlib import Path

from domain.knowledge_evals import (
    evaluate_companion_cases,
    load_eval_cases_from_dir,
    load_eval_cases_from_store,
)
from storage import LocalStore


REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
CASES_PATH = KNOWLEDGE_DIR / "evals"


class CriticalDocumentEvalTests(unittest.TestCase):
    def test_critical_document_companion_evals_pass(self) -> None:
        cases = load_eval_cases_from_dir(CASES_PATH)
        results = evaluate_companion_cases(cases, KNOWLEDGE_DIR)
        failures = [item for item in results if not item["passed"]]
        if failures:
            summary = "\n".join(
                f"{item['document']} :: missing {', '.join(item['missing_substrings'])} :: {item['answer']}"
                for item in failures
            )
            self.fail(summary)


class FeedbackCorrectionEvalRegistryTests(unittest.TestCase):
    def test_load_eval_cases_from_store_includes_feedback_eval_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir) / "knowledge"
            data_dir = Path(temp_dir) / "data"
            store = LocalStore(data_dir=str(data_dir), knowledge_dir=str(knowledge_dir))
            store.upsert_feedback_eval_case(
                source_message_id="msg-123",
                document="IT-036_RegulacaoAgulhas.txt",
                question="Qual é a regra para compensação de agulhas dentro do Porto à noite?",
                expected_answer="À noite a RA não se efetua com navios de LOA igual ou superior a 225 metros.",
                expected_substrings=["225 metros"],
                feedback_note="Faltava o limite de LOA.",
                updated_by="admin",
                source="web",
            )

            cases = load_eval_cases_from_store(store)
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["document"], "IT-036_RegulacaoAgulhas.txt")
            self.assertEqual(cases[0]["source_message_id"], "msg-123")
