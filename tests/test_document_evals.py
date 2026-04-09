import unittest
import tempfile
from pathlib import Path

from domain.knowledge_evals import (
    evaluate_companion_cases,
    load_eval_cases_from_dir,
    register_feedback_correction_eval,
    remove_feedback_correction_eval,
)


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
    def test_register_and_remove_feedback_correction_eval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_dir = Path(temp_dir) / "knowledge"
            knowledge_dir.mkdir(parents=True, exist_ok=True)

            case = register_feedback_correction_eval(
                knowledge_dir,
                document="IT-036_RegulacaoAgulhas.txt",
                question="Qual é a regra para compensação de agulhas dentro do Porto à noite?",
                corrected_answer="À noite a RA não se efetua com navios de LOA igual ou superior a 225 metros.",
                feedback_note="Faltava o limite de LOA.",
                updated_by="admin",
                source="web",
                source_message_id="msg-123",
            )

            self.assertEqual(case["document"], "IT-036_RegulacaoAgulhas.txt")
            self.assertTrue((knowledge_dir / "evals" / "operator_feedback_correction_evals.json").exists())

            removed = remove_feedback_correction_eval(
                knowledge_dir,
                source_message_id="msg-123",
            )
            self.assertEqual(removed, 1)
