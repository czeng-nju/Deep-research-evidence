"""Scope/completion guard regressions using local synthetic records only.

These are adversarial record checks, not a scientific benchmark or official
score estimate. No evaluation holdout data or external services are read.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill" / "scripts" / "research_audit.py"


def canonical(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ResearchAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        text = "Synthetic alpha condition has reading 4.\nSynthetic beta condition has reading 6.\n"
        (self.work / "source.txt").write_text(text, encoding="utf-8")
        self.state = {
            "schema_version": 1, "case_id": "SYNTHETIC-SCOPE-AUDIT",
            "input_contract": {"type": "object"},
            "requirements": [{"id": "R1", "text": "Report the alpha reading.", "answerability": "answerable", "status": "completed", "question_ids": ["Q1"]}],
            "questions": [{"id": "Q1", "text": "What is the alpha reading?", "requirement_ids": ["R1"], "dependencies": [], "acceptance_criteria": "Retain the original condition.", "status": "completed"}],
            "actions": [{"id": "A1", "question_ids": ["Q1"], "tool": "fixture_read", "parameters": {"path": "source.txt"}, "status": "completed", "stop_reason": "Synthetic closed fixture read completely.", "artifact_ids": ["ART1"]}],
            "sources": [{"id": "S1", "title": "Synthetic scope fixture", "artifact_id": "ART1", "access_level": "full_text", "identifiers": {}}],
            "artifacts": {"ART1": {"id": "ART1", "path": "source.txt", "source_id": "S1", "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}},
            "evidence": [{"id": "E1", "source_id": "S1", "question_ids": ["Q1"], "artifact_id": "ART1", "quote": text.splitlines()[0], "locator": {"line_start": 1, "line_end": 1}, "comparison_fields": {"condition": "alpha"}, "limitations": ["Synthetic fixture"], "context_ids": ["alpha"]},
                         {"id": "E2", "source_id": "S1", "question_ids": ["Q1"], "artifact_id": "ART1", "quote": text.splitlines()[1], "locator": {"line_start": 2, "line_end": 2}, "comparison_fields": {"condition": "beta"}, "limitations": ["Synthetic fixture"], "context_ids": ["beta"]}],
            "claims": [{"id": "C1", "question_ids": ["Q1"], "text": "Alpha has reading 4.", "kind": "fact", "supporting_evidence_ids": ["E1"], "opposing_evidence_ids": [], "reasoning": "", "scope_limits": ["Synthetic alpha only"], "review_status": "supported", "assertions": [{"id": "C1a", "text": "Alpha has reading 4.", "context_ids": ["alpha"], "evidence_ids": ["E1"], "kind": "fact", "reasoning": ""}]}],
            "conflicts": [], "reviews": [],
            "metadata": {"events": [], "contexts": [{"id": "alpha", "description": "Synthetic condition alpha."}, {"id": "beta", "description": "Synthetic condition beta."}], "delivery_checks": {
                "decomposition": {"status": "completed", "refs": ["requirements:R1", "questions:Q1"], "rationale": "One fixture question is mapped."},
                "retrieval_coverage": {"status": "completed", "refs": ["actions:A1"], "rationale": "The supplied closed fixture was read."},
                "evidence_links": {"status": "completed", "refs": ["evidence:E1", "claims:C1"], "rationale": "The alpha reading is located."},
                "conflict_assessment": {"status": "completed", "refs": ["evidence:E1", "evidence:E2"], "rationale": "Different fixture conditions are not contradictory same-condition readings."},
                "synthesis": {"status": "completed", "refs": ["claims:C1"], "rationale": "Only the requested alpha result is reported."},
                "gaps": {"status": "completed", "refs": ["questions:Q1", "actions:A1"], "rationale": "No additional requirement in the synthetic closed fixture; no field-wide completeness claim."},
            }},
        }
        self.bind_review()

    def tearDown(self):
        self.temp.cleanup()

    def bind_review(self):
        claim = self.state["claims"][0]
        pool = {e["id"]: e for e in self.state["evidence"]}
        self.state["reviews"] = [{
            "id": "V1", "claim_id": claim["id"], "verdict": "supported",
            "reviewer": "Synthetic test annotation, not human review",
            "evidence_ids": claim["supporting_evidence_ids"],
            "rationale": "Fixture-specific record annotation only.",
            "claim_sha256": canonical({k: v for k, v in claim.items() if k not in ("review_status", "updated_at")}),
            "evidence_sha256": {eid: canonical(pool[eid]) for eid in claim["supporting_evidence_ids"] if eid in pool},
        }]

    def run_audit(self, *extra):
        (self.work / "state.json").write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([sys.executable, str(SCRIPT), "--work", str(self.work), *map(str, extra)], text=True, capture_output=True, timeout=20)
        self.assertTrue(proc.stdout, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(proc.returncode, result["exit_code"])
        self.assertFalse(result["semantic_correctness_verified"])
        self.assertIsNone(result["official_score"])
        return result

    def codes(self, report):
        return {c["code"] for c in report["checks"]}

    def test_same_context_records_complete_without_certifying_science(self):
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 0, report["checks"])
        self.assertEqual(report["status"], "record_checks_completed_semantics_pending")
        self.assertEqual(report["metrics"]["answerable_requirements_with_scoped_located_claim_and_recorded_review"], ["R1"])
        self.assertTrue(report["prerequisite_validation"]["semantic_review_required"])

    def test_wrong_context_fact_rejected(self):
        self.state["claims"][0]["assertions"][0]["context_ids"] = ["beta"]
        self.bind_review()
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("scope.fact_mismatch", self.codes(report))
        self.assertEqual(report["metrics"]["answerable_requirements_with_scoped_located_claim_and_recorded_review"], [])

    def test_no_magic_semantics_from_context_id_names(self):
        self.state["metadata"]["contexts"][0]["id"] = "global"
        self.state["evidence"][0]["context_ids"] = ["global"]
        self.state["claims"][0]["assertions"][0]["context_ids"] = ["beta"]
        self.bind_review()
        self.assertIn("scope.fact_mismatch", self.codes(self.run_audit()))

    def test_missing_evidence_scope_is_unverified_not_global(self):
        del self.state["evidence"][0]["context_ids"]
        self.bind_review()
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 2)
        self.assertIn("scope.fact_unverified", self.codes(report))

    def test_legacy_records_are_compatible_but_unverified(self):
        del self.state["metadata"]["contexts"]
        del self.state["claims"][0]["assertions"]
        for evidence in self.state["evidence"]:
            del evidence["context_ids"]
        self.bind_review()
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 2)
        self.assertIn("scope.contexts_missing", self.codes(report))
        self.assertIn("scope.assertions_missing", self.codes(report))

    def cross_inference(self):
        claim = self.state["claims"][0]
        claim.update(kind="inference", text="The beta fixture reading exceeds alpha by 2.", reasoning="Subtract the two specified readings; no broader inference.", supporting_evidence_ids=["E1", "E2"])
        claim["assertions"] = [
            {"id": "premise-a", "text": "Alpha has reading 4.", "context_ids": ["alpha"], "evidence_ids": ["E1"], "kind": "fact", "reasoning": ""},
            {"id": "premise-b", "text": "Beta has reading 6.", "context_ids": ["beta"], "evidence_ids": ["E2"], "kind": "fact", "reasoning": ""},
            {"id": "difference", "text": claim["text"], "context_ids": ["alpha", "beta"], "evidence_ids": ["E1", "E2"], "kind": "inference", "reasoning": claim["reasoning"], "cross_context": True, "cross_context_note": "Comparison describes only these two synthetic conditions; no causal effect."},
        ]
        self.bind_review()

    def test_cross_context_comparison_has_separate_premises_and_stays_inference(self):
        self.cross_inference()
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 0, report["checks"])
        self.assertIn("scope.cross_context_inference", self.codes(report))
        self.assertEqual(report["metrics"]["annotated_assertions_by_kind"], {"fact": 2, "inference": 1, "unknown": 0})

    def test_cross_context_inference_requires_disclosure(self):
        self.cross_inference()
        del self.state["claims"][0]["assertions"][2]["cross_context_note"]
        self.bind_review()
        self.assertIn("scope.cross_context_undisclosed", self.codes(self.run_audit()))

    def test_extrapolation_may_be_disclosed_as_inference_but_never_fact(self):
        claim = self.state["claims"][0]
        claim.update(kind="inference", reasoning="Hypothesis only; beta is unmeasured for this property.")
        claim["assertions"][0].update(kind="inference", context_ids=["beta"], reasoning=claim["reasoning"], cross_context=True, cross_context_note="Alpha premise motivates a beta hypothesis without direct confirmation.")
        self.bind_review()
        report = self.run_audit()
        self.assertNotIn("scope.fact_mismatch", self.codes(report))
        self.assertEqual(report["metrics"]["annotated_assertions_by_kind"]["fact"], 0)

    def test_fact_claim_cannot_hide_inference(self):
        self.state["claims"][0]["assertions"][0].update(kind="inference", reasoning="An assumption.")
        self.bind_review()
        self.assertIn("scope.kind_mismatch", self.codes(self.run_audit()))

    def test_common_scope_needs_confirmation_and_bounded_ids(self):
        evidence = self.state["evidence"][0]
        evidence.update(context_scope="common", context_ids=["alpha", "beta"])
        self.bind_review()
        self.assertIn("scope.common_unconfirmed", self.codes(self.run_audit()))
        evidence["scope_confirmation"] = {"confirmed": True, "reviewer": "Fixture annotator", "rationale": "Synthetic annotation for testing the gate, not a real shared-method claim."}
        self.bind_review()
        report = self.run_audit()
        self.assertNotIn("scope.common_unconfirmed", self.codes(report))
        self.assertFalse(report["semantic_correctness_verified"])

    def test_review_of_old_text_only_does_not_certify_new_scope_fields(self):
        review = self.state["reviews"][0]
        del review["claim_sha256"]
        review["claim_text_sha256"] = hashlib.sha256(self.state["claims"][0]["text"].encode()).hexdigest()
        report = self.run_audit()
        self.assertIn("review.scope_unbound", self.codes(report))
        self.assertEqual(report["exit_code"], 2)

    def test_scope_mutation_invalidates_bound_review(self):
        self.state["evidence"][0]["context_ids"] = ["alpha", "beta"]
        report = self.run_audit()
        self.assertIn("structure.review.stale", self.codes(report))
        self.assertIn("delivery.claim_unreviewed", self.codes(report))

    def test_source_artifact_tampering_cannot_count_as_support(self):
        (self.work / "source.txt").write_text("Changed material", encoding="utf-8")
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("scope.premise_unlocated", self.codes(report))
        self.assertEqual(report["metrics"]["answerable_requirements_with_scoped_located_claim_and_recorded_review"], [])

    def test_six_area_gaps_are_located(self):
        self.state["metadata"]["delivery_checks"] = {}
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(len([c for c in report["gaps"] if c["code"] == "delivery.area_missing"]), 6)
        self.assertTrue(all(report["area_gaps"].values()))

    def test_completed_checkbox_without_refs_does_not_pass(self):
        self.state["metadata"]["delivery_checks"]["conflict_assessment"] = {"status": "completed"}
        report = self.run_audit()
        self.assertIn("delivery.refs_missing", self.codes(report))
        self.assertIn("delivery.rationale_missing", self.codes(report))

    def test_empty_stop_reason_and_unreviewed_claim_expose_gaps(self):
        self.state["actions"][0]["stop_reason"] = ""
        self.state["claims"][0]["review_status"] = "unreviewed"
        self.state["reviews"] = []
        report = self.run_audit()
        self.assertIn("delivery.stop_reason_missing", self.codes(report))
        self.assertIn("delivery.claim_unreviewed", self.codes(report))

    def test_missing_question_link_and_extra_requirement_keep_denominator(self):
        self.state["requirements"].append({"id": "R2", "text": "Another original requirement.", "answerability": "answerable", "status": "partial", "question_ids": []})
        report = self.run_audit()
        self.assertEqual(report["metrics"]["fixed_recorded_requirement_denominator"], 2)
        self.assertIn("delivery.requirement_question_missing", self.codes(report))
        self.assertIn("delivery.answerable_requirement_unanswered", self.codes(report))

    def unknown_claim(self):
        claim = self.state["claims"][0]
        claim.update(kind="unknown", text="The fixture cannot establish an external causal effect.", reasoning="The supplied two lines contain no causal design or external population data.", supporting_evidence_ids=[])
        claim["assertions"][0].update(kind="unknown", text=claim["text"], reasoning=claim["reasoning"], evidence_ids=[])
        self.bind_review()

    def test_justified_unknown_counts_separately_from_answered(self):
        self.unknown_claim()
        self.state["requirements"][0]["answerability"] = "unknown"
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 0, report["checks"])
        self.assertEqual(report["metrics"]["requirements_with_reviewed_unknown_explanation"], ["R1"])
        self.assertEqual(report["metrics"]["answerable_requirements_with_scoped_located_claim_and_recorded_review"], [])

    def test_unknown_cannot_satisfy_answerable_requirement(self):
        self.unknown_claim()
        report = self.run_audit()
        self.assertIn("delivery.answerable_requirement_unanswered", self.codes(report))
        self.assertEqual(report["metrics"]["fixed_recorded_requirement_denominator"], 1)

    def test_disagreeing_reviews_do_not_select_favorable_verdict(self):
        negative = copy.deepcopy(self.state["reviews"][0])
        negative.update(id="V2", verdict="unsupported", rationale="Synthetic adversarial review.")
        self.state["reviews"].append(negative)
        report = self.run_audit()
        self.assertIn("delivery.review_disagreement", self.codes(report))
        self.assertEqual(report["metrics"]["answerable_requirements_with_scoped_located_claim_and_recorded_review"], [])

    def test_typed_delivery_reference_cannot_point_at_missing_record(self):
        self.state["metadata"]["delivery_checks"]["gaps"]["refs"] = ["questions:NONEXISTENT"]
        self.assertIn("delivery.ref_invalid", self.codes(self.run_audit()))

    def test_json_output_file_and_rubric_mapping(self):
        output = self.work / "audit.json"
        report = self.run_audit("--output", output)
        self.assertEqual(json.loads(output.read_text()), report)
        self.assertEqual(set(report["rubric_mapping"]), {"task_completion", "result_correctness", "result_completeness", "verifiability"})
        self.assertTrue(all(c["rubric"] in report["rubric_mapping"] for c in report["checks"]))

    def test_bad_json_returns_machine_error_and_does_not_traceback(self):
        (self.work / "state.json").write_text('{"claims": NaN}', encoding="utf-8")
        proc = subprocess.run([sys.executable, str(SCRIPT), "--work", str(self.work)], text=True, capture_output=True, timeout=20)
        self.assertEqual(proc.returncode, 1)
        self.assertIsInstance(json.loads(proc.stdout)["errors"], list)
        self.assertNotIn("Traceback", proc.stderr)

    def test_output_cannot_overwrite_state(self):
        report = self.run_audit("--output", self.work / "state.json")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(json.loads((self.work / "state.json").read_text()), self.state)

    def annotation_file(self, name="research_annotations.json", register=True):
        path = self.work / name
        value = {k: copy.deepcopy(self.state["metadata"][k]) for k in ("contexts", "delivery_checks")}
        path.write_text(json.dumps(value), encoding="utf-8")
        if register:
            self.state["artifacts"]["ANNOTATIONS"] = {"id": "ANNOTATIONS", "path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return path, value

    def test_default_annotation_file_is_used_without_mutating_state(self):
        self.annotation_file()
        del self.state["metadata"]["contexts"]
        del self.state["metadata"]["delivery_checks"]
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 0, report["checks"])
        self.assertEqual(report["annotation_input"]["mode"], "default_file")
        self.assertEqual(report["annotation_input"]["verified_artifact_ids"], ["ANNOTATIONS"])
        self.assertEqual(json.loads((self.work / "state.json").read_text()), self.state)

    def test_explicit_annotations_override_metadata_and_default_file(self):
        self.annotation_file()
        path, value = self.annotation_file("chosen.json")
        value["delivery_checks"] = {}
        path.write_text(json.dumps(value), encoding="utf-8")
        self.state["artifacts"]["ANNOTATIONS"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        report = self.run_audit("--annotations", path)
        self.assertEqual(report["annotation_input"]["mode"], "explicit_file")
        self.assertEqual(len([r for r in report["gaps"] if r["code"] == "delivery.area_missing"]), 6)

    def test_unregistered_annotations_remain_unverified(self):
        path, _ = self.annotation_file(register=False)
        report = self.run_audit("--annotations", path)
        self.assertEqual(report["exit_code"], 2)
        self.assertIn("annotations.unretained", self.codes(report))

    def test_missing_explicit_annotation_file_is_error_not_silent_fallback(self):
        report = self.run_audit("--annotations", self.work / "missing.json")
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("annotations.json.read", self.codes(report))

    def test_output_cannot_overwrite_annotations(self):
        path, value = self.annotation_file()
        report = self.run_audit("--output", path)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(json.loads(path.read_text()), value)

    def test_malformed_assertion_kind_has_machine_diagnostic(self):
        self.state["claims"][0]["assertions"][0]["kind"] = {"not": "a string"}
        self.bind_review()
        report = self.run_audit()
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("scope.assertion_kind", self.codes(report))


if __name__ == "__main__":
    unittest.main()
