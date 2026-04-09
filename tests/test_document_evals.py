import unittest
from pathlib import Path

from domain.knowledge_evals import evaluate_companion_cases, load_eval_cases


REPO_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
CASES_PATH = KNOWLEDGE_DIR / "evals" / "critical_document_companion_evals.json"


class CriticalDocumentEvalTests(unittest.TestCase):
    def test_critical_document_companion_evals_pass(self) -> None:
        cases = load_eval_cases(CASES_PATH)
        results = evaluate_companion_cases(cases, KNOWLEDGE_DIR)
        failures = [item for item in results if not item["passed"]]
        if failures:
            summary = "\n".join(
                f"{item['document']} :: missing {', '.join(item['missing_substrings'])} :: {item['answer']}"
                for item in failures
            )
            self.fail(summary)
