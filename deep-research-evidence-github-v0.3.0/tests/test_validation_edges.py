"""Public-CLI regressions for evidence integrity; synthetic fixtures only.

Prepared before the coordinated test run. These fixtures are not competition
cases, scientific findings, or evidence of end-to-end research performance.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VALIDATE = ROOT / "skill" / "scripts" / "validate.py"
SENTENCE = "The effect was -2.5e-3 units."


class ValidationEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.work = self.base / "work"
        self.work.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def invoke(self, *args):
        proc = subprocess.run(
            [sys.executable, str(VALIDATE), *map(str, args)],
            capture_output=True, text=True, timeout=20,
        )
        self.assertTrue(proc.stdout.strip(), proc.stderr)
        return proc.returncode, json.loads(proc.stdout)

    def fixture(self, contract):
        """A small internally linked ledger, with a retained actual input snapshot."""
        source = self.work / "artifacts" / "source.txt"
        source.parent.mkdir(exist_ok=True)
        source.write_text(SENTENCE + "\n", encoding="utf-8")
        case = {"case_id": "SYNTHETIC-VALIDATION-EDGE", "output_contract": contract}
        snapshot = self.write_json(self.work / "inputs" / "case.json", case)
        state = {
            "schema_version": "1.0", "case_id": case["case_id"],
            "input_contract": contract,
            "requirements": [{"id": "R1", "text": "Report the synthetic effect.",
                              "answerability": "answerable", "status": "completed",
                              "question_ids": ["Q1"]}],
            "questions": [{"id": "Q1", "text": "What effect does the fixture state?",
                           "requirement_ids": ["R1"], "dependencies": [],
                           "acceptance_criteria": "Retain sign and exponent.",
                           "status": "completed"}],
            "actions": [],
            "sources": [{"id": "S1", "title": "Synthetic source fixture",
                         "url": "https://example.invalid/synthetic-only",
                         "access_level": "full_text", "identifiers": {},
                         "artifact_id": "ART1"}],
            "evidence": [{"id": "E1", "source_id": "S1", "question_ids": ["Q1"],
                          "artifact_id": "ART1", "quote": SENTENCE,
                          "locator": {"line_start": 1, "line_end": 1},
                          "comparison_fields": {}, "limitations": ["Synthetic fixture"]}],
            "claims": [{"id": "C1", "question_ids": ["Q1"], "text": SENTENCE,
                        "kind": "fact", "supporting_evidence_ids": ["E1"],
                        "opposing_evidence_ids": [], "reasoning": "",
                        "scope_limits": ["Synthetic fixture only"],
                        "review_status": "supported"}],
            "conflicts": [],
            "reviews": [{"id": "V1", "claim_id": "C1", "verdict": "supported",
                         "reviewer": "Synthetic fixture annotation",
                         "evidence_ids": ["E1"], "rationale": "Fixture repeats its source text.",
                         "claim_text_sha256": hashlib.sha256(SENTENCE.encode("utf-8")).hexdigest()}],
            "artifacts": {"ART1": {"id": "ART1", "path": "artifacts/source.txt",
                                   "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                                   "source_id": "S1"}},
            "metadata": {"events": [], "inputs": {"case": {
                "path": str(snapshot), "snapshot": "inputs/case.json",
                "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            }}},
        }
        self.save(state)
        return state

    def save(self, state):
        self.write_json(self.work / "state.json", state)

    @staticmethod
    def schema():
        return {"type": "object", "required": ["case_id"]}

    @staticmethod
    def codes(report, category):
        return {item["code"] for item in report[category]}

    def assert_no_supported_count(self, report):
        metrics = report["metrics"]
        self.assertEqual(metrics["claims_with_located_support_and_recorded_supported_review"], 0)
        self.assertEqual(metrics["requirements_linked_to_located_claims_with_recorded_supported_review"], 0)
        self.assertEqual(metrics["original_requirement_denominator"], 1)
        self.assertIsNone(metrics["semantic_success_count"])

    def test_natural_language_input_contract_remains_honest_partial_validation(self):
        self.fixture("Explain the finding in Chinese and identify unresolved limitations.")
        code, report = self.invoke("state", "--work", self.work)
        self.assertEqual(code, 0, report["errors"])
        self.assertIn("state.contract_unverified", self.codes(report, "warnings"))
        self.assertIn("state.contract_review", self.codes(report, "semantic_review_required"))
        self.assertIsNone(report["metrics"]["semantic_success_count"])

    def test_null_input_contract_is_not_fabricated_or_rejected_as_tampering(self):
        self.fixture(None)
        code, report = self.invoke("state", "--work", self.work)
        self.assertEqual(code, 0, report["errors"])
        self.assertIn("state.contract_unverified", self.codes(report, "warnings"))
        self.assertNotIn("input.contract_changed", self.codes(report, "errors"))
        self.assertIn("state.contract_review", self.codes(report, "semantic_review_required"))

    def test_unknown_schema_constraint_returns_incomplete_exit_two(self):
        contract = self.schema()
        contract["unevaluatedProperties"] = False
        case = self.write_json(self.base / "case.json", {
            "case_id": "SYNTHETIC-UNSUPPORTED-CONTRACT", "output_contract": contract,
        })
        result = self.write_json(self.base / "result.json", {
            "case_id": "SYNTHETIC-UNSUPPORTED-CONTRACT",
        })
        code, report = self.invoke("result", "--case", case, "--result", result)
        self.assertEqual(code, 2)
        self.assertFalse(report["validation_complete"])
        self.assertFalse(report["metrics"]["contract_machine_check_complete"])
        self.assertIn("schema.unsupported", self.codes(report, "warnings"))
        self.assertIn("schema.manual", self.codes(report, "semantic_review_required"))

    def test_invalid_source_cannot_leave_its_claim_counted_as_supported(self):
        state = self.fixture(self.schema())
        state["sources"][0].update(validity="invalid", invalid_reasons=["source_changed"])
        self.save(state)
        code, report = self.invoke("state", "--work", self.work)
        self.assertNotEqual(code, 0)
        self.assertIn("evidence.invalid_support", self.codes(report, "errors"))
        self.assertIn("review.invalid_support", self.codes(report, "warnings"))
        self.assert_no_supported_count(report)

    def test_invalid_evidence_invalidates_existing_supported_review(self):
        state = self.fixture(self.schema())
        state["evidence"][0].update(validity="invalid", invalid_reasons=["source_reextraction_needed"])
        self.save(state)
        code, report = self.invoke("state", "--work", self.work)
        self.assertNotEqual(code, 0)
        self.assertIn("claim.invalid_support", self.codes(report, "errors"))
        self.assertIn("review.invalid_support", self.codes(report, "warnings"))
        self.assert_no_supported_count(report)

    def test_quote_normalization_does_not_erase_sign_or_exponent(self):
        state = self.fixture(self.schema())
        mutations = ("The effect was 2.5e-3 units.", "The effect was -2.5e3 units.")
        for quote in mutations:
            with self.subTest(quote=quote):
                state["evidence"][0]["quote"] = quote
                self.save(state)
                code, report = self.invoke("state", "--work", self.work)
                self.assertNotEqual(code, 0)
                self.assertIn("quote.mismatch", self.codes(report, "errors"))
                self.assertEqual(report["metrics"]["text_located_evidence"], 0)
                self.assert_no_supported_count(report)

    def test_pdf_page_without_mapping_is_explicitly_unverified(self):
        state = self.fixture(self.schema())
        state["evidence"][0]["locator"].update(physical_pdf_page=7, printed_page_label="iv")
        self.save(state)
        code, report = self.invoke("state", "--work", self.work)
        self.assertEqual(code, 0, report["errors"])
        self.assertIn("pdf.mapping_missing", self.codes(report, "warnings"))
        self.assertIn("locator.printed_page", self.codes(report, "semantic_review_required"))
        self.assertFalse(any(check["code"] == "pdf.mapping_link" and check["status"] == "pass"
                             for check in report["checks"]))
        self.assertIsNone(report["metrics"]["semantic_success_count"])

    def test_claim_edit_cannot_reuse_hash_bound_supported_review(self):
        state = self.fixture(self.schema())
        state["claims"][0]["text"] = "The effect was +2.5e-3 units."
        self.save(state)
        code, report = self.invoke("state", "--work", self.work)
        self.assertNotEqual(code, 0)
        self.assertIn("review.stale", self.codes(report, "errors"))
        self.assert_no_supported_count(report)


if __name__ == "__main__":
    unittest.main()
