#!/usr/bin/env python3
"""Audit annotated claim scope and final-delivery gaps; Python 3.9+, stdlib.

This checks records, not scientific entailment or official competition scores.
Exit 1 means a detected contradiction/invalid record; 2 means missing auditable
information or a delivery gap; 0 means these limited record checks completed.
Scientific support and completeness remain subject to source-based review.
"""
import argparse
import json
from pathlib import Path
import sys

import validate


AREAS = ("decomposition", "retrieval_coverage", "evidence_links",
         "conflict_assessment", "synthesis", "gaps")
RUBRIC = {
    "task_completion": "实际效果／任务完成度（15分）",
    "result_correctness": "实际效果／结果正确性（15分）",
    "result_completeness": "实际效果／结果完整性（5分）",
    "verifiability": "实际效果／结果可核验性（5分）",
}
AREA_COLLECTIONS = {
    "decomposition": {"requirements", "questions"},
    "retrieval_coverage": {"actions", "artifacts"},
    "evidence_links": {"claims", "evidence"},
    "conflict_assessment": {"conflicts", "evidence"},
    "synthesis": {"claims"},
    "gaps": {"requirements", "questions", "actions", "claims"},
}


def active(row):
    return isinstance(row, dict) and row.get("validity") != "invalid"


def ids(value):
    return (value if isinstance(value, list) and
            all(validate.nonempty(x) for x in value) else [])


class Audit:
    def __init__(self):
        self.data = {
            "audit_version": "0.3", "checks": [], "errors": [], "gaps": [],
            "unverified": [], "metrics": {}, "rubric_mapping": RUBRIC,
            "semantic_correctness_verified": False, "semantic_verification": "not_verified", "official_score": None,
            "scope": "Annotation consistency and delivery-record checks only. Context tags, review verdicts and delivery statuses are fallible Agent/user assertions, not proof of source meaning, scientific completeness or competition points.",
        }

    def add(self, status, code, path, message, rubric="result_correctness", area="evidence_links"):
        row = {"status": status, "code": code, "path": path, "message": message,
               "rubric": rubric, "area": area}
        self.data["checks"].append(row)
        bucket = {"error": "errors", "gap": "gaps", "unverified": "unverified"}.get(status)
        if bucket:
            self.data[bucket].append(row)

    def finish(self):
        code = 1 if self.data["errors"] else 2 if self.data["gaps"] or self.data["unverified"] else 0
        self.data["exit_code"] = code
        self.data["status"] = ("record_violations" if code == 1 else
                               "record_checks_incomplete" if code == 2 else
                               "record_checks_completed_semantics_pending")
        self.data["area_gaps"] = {
            area: [r for r in self.data["checks"] if r["area"] == area and r["status"] != "checked"]
            for area in AREAS
        }
        return self.data


def audit_work(work, annotations=None):
    report = Audit()
    base = validate.Report("state")
    state_path = validate.confined(Path(work).resolve(), "state.json", base, "state.json")
    state = validate.read_json(state_path, base, "state.json") if state_path else None
    if not isinstance(state, dict):
        for row in base.data["errors"]:
            report.add("error", row["code"], row["path"], row["message"], "verifiability")
        report.add("error", "state.object", "$", "Expected a JSON state object.", "verifiability")
        return report.finish()

    indices = {}
    for name in validate.COLLECTIONS:
        rows = state.get(name)
        indices[name] = {}
        if not isinstance(rows, list):
            report.add("error", "record.collection", name, "Expected an array.", "verifiability")
            continue
        for pos, row in enumerate(rows):
            if not isinstance(row, dict) or not validate.nonempty(row.get("id")):
                report.add("error", "record.id", "%s[%d]" % (name, pos), "Record needs a nonempty id.", "verifiability")
            elif row["id"] in indices[name]:
                report.add("error", "record.duplicate_id", name + "." + row["id"], "Repeated ID.", "verifiability")
            else:
                indices[name][row["id"]] = row
    # The existing checker owns artifact hashes, exact text locators and stale
    # review detection. Preserve failures instead of pretending to re-prove it.
    try:
        validate.validate_state(work, base)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError) as exc:
        report.add("error", "structure.uncheckable", "$", "Malformed record prevents prerequisite validation: " + str(exc), "verifiability")
    for row in base.data["errors"]:
        report.add("error", "structure." + row["code"], row["path"], row["message"], "verifiability")
    report.data["prerequisite_validation"] = {
        "error_count": len(base.data["errors"]), "warnings": base.data["warnings"],
        "semantic_review_required": base.data["semantic_review_required"],
        "limitation": "Exact quotes and hashes do not prove scientific support. See validate.py for the full structural report.",
    }
    located = {row["path"][len("evidence."):] for row in base.data["checks"]
               if row["code"] == "quote.located" and row["status"] == "pass"}
    stale_reviews = {rid for rid in indices["reviews"] for row in base.data["checks"]
                     if row["code"] in ("review.stale", "review.invalid_support")
                     and (row["path"] == "reviews." + rid or row["path"].startswith("reviews." + rid + "."))}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    default_annotations = Path(work) / "research_annotations.json"
    annotation_path = Path(annotations) if annotations is not None else default_annotations if default_annotations.exists() else None
    if annotation_path is not None:
        annotation_report = validate.Report("annotations")
        annotation_data = validate.read_json(annotation_path, annotation_report, str(annotation_path))
        for row in annotation_report.data["errors"]:
            report.add("error", "annotations." + row["code"], row["path"], row["message"], "verifiability")
        # A selected file is authoritative for both sections. Do not silently
        # combine it with possibly stale metadata if one section is absent.
        metadata = dict(metadata)
        metadata.pop("contexts", None)
        metadata.pop("delivery_checks", None)
        if isinstance(annotation_data, dict):
            for key in ("contexts", "delivery_checks"):
                if key in annotation_data:
                    metadata[key] = annotation_data[key]
        else:
            report.add("error", "annotations.object", str(annotation_path), "Annotation file must be a JSON object with contexts and delivery_checks.", "verifiability")
        try:
            annotation_hash = validate.digest(annotation_path.read_bytes())
        except OSError:
            annotation_hash = None
        registered = []
        verified_artifacts = {row["path"][len("artifacts."):] for row in base.data["checks"]
                              if row["code"] == "artifact.hash" and row["status"] == "pass"}
        artifact_pool = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
        for aid, artifact in artifact_pool.items():
            if isinstance(artifact, dict) and aid in verified_artifacts and artifact.get("sha256") == annotation_hash:
                registered.append(aid)
        if annotation_hash and not registered:
            report.add("unverified", "annotations.unretained", str(annotation_path), "Register the annotation file as a retained artifact; a currently readable sidecar alone is not a versioned research record.", "verifiability")
        report.data["annotation_input"] = {
            "mode": "explicit_file" if annotations is not None else "default_file",
            "path": str(annotation_path.resolve()), "sha256": annotation_hash,
            "verified_artifact_ids": sorted(registered),
        }
    else:
        report.data["annotation_input"] = {
            "mode": "metadata_fallback", "path": None,
            "limitation": "No external annotation file supplied/found. Existing metadata is read without modifying state history; absent sections remain unverified or missing.",
        }
    contexts = metadata.get("contexts")
    context_index = {}
    if contexts is None:
        report.add("unverified", "scope.contexts_missing", "metadata.contexts", "Legacy record has no explicit contexts; missing scope is never universal.")
    elif not isinstance(contexts, list):
        report.add("error", "scope.contexts_type", "metadata.contexts", "Expected an array of {id, description}.")
    else:
        for n, context in enumerate(contexts):
            path = "metadata.contexts[%d]" % n
            if not isinstance(context, dict) or not validate.nonempty(context.get("id")) or not validate.nonempty(context.get("description")):
                report.add("error", "scope.context_definition", path, "Define a nonempty context id and description from actual study conditions.")
                continue
            if context["id"] in context_index:
                report.add("error", "scope.context_duplicate", path, "Context ID is repeated.")
            context_index[context["id"]] = context

    def read_contexts(row, path):
        value = row.get("context_ids")
        if value is None or value == []:
            report.add("unverified", "scope.unspecified", path + ".context_ids", "No source scope supplied; it cannot support arbitrary contexts.")
            return None
        if not isinstance(value, list) or any(not validate.nonempty(x) for x in value):
            report.add("error", "scope.context_ids_type", path + ".context_ids", "Expected nonempty string IDs in an array.")
            return None
        unknown = set(value) - set(context_index)
        if unknown:
            report.add("error", "scope.context_reference", path + ".context_ids", "Undefined contexts: " + ", ".join(sorted(unknown)))
            return None
        return set(value)

    evidence_scope = {}
    for eid, evidence in indices["evidence"].items():
        if not active(evidence):
            continue
        path = "evidence." + eid
        scope = read_contexts(evidence, path)
        label = evidence.get("context_scope", "specific")
        if label not in ("specific", "common", "global"):
            report.add("error", "scope.scope_type", path + ".context_scope", "Use specific, common or global; no implicit wildcard scope.")
            scope = None
        if label in ("common", "global"):
            confirmation = evidence.get("scope_confirmation")
            confirmed = (isinstance(confirmation, dict) and confirmation.get("confirmed") is True
                         and validate.nonempty(confirmation.get("reviewer"))
                         and validate.nonempty(confirmation.get("rationale")))
            if not confirmed:
                report.add("error", "scope.common_unconfirmed", path + ".scope_confirmation", "Common/global scope needs explicit source-based confirmation, reviewer and rationale; context_ids must still enumerate applicability.")
                scope = None
            else:
                report.add("checked", "scope.common_annotation", path + ".scope_confirmation", "Explicit confirmation is recorded; its source interpretation is not certified.")
        evidence_scope[eid] = scope

    scope_eligible = set()
    reviewed_claims = set()
    assertion_counts = {"fact": 0, "inference": 0, "unknown": 0}
    for cid, claim in indices["claims"].items():
        if not active(claim):
            continue
        path = "claims." + cid
        assertions = claim.get("assertions")
        supported = ids(claim.get("supporting_evidence_ids"))
        assertion_ok = True
        if not isinstance(assertions, list) or not assertions:
            report.add("unverified", "scope.assertions_missing", path + ".assertions", "Legacy/unannotated claim needs atomic assertions before scope consistency can be checked.")
            assertion_ok = False
            assertions = []
        seen = set()
        for pos, assertion in enumerate(assertions):
            loc = path + ".assertions[%d]" % pos
            before = len(report.data["checks"])
            if not isinstance(assertion, dict) or not validate.nonempty(assertion.get("id")) or not validate.nonempty(assertion.get("text")):
                report.add("error", "scope.assertion_identity", loc, "Each atomic assertion needs a nonempty id and text.")
                assertion_ok = False
                continue
            if assertion["id"] in seen:
                report.add("error", "scope.assertion_duplicate", loc, "Assertion IDs must be unique within a claim.")
            seen.add(assertion["id"])
            kind = assertion.get("kind")
            if not isinstance(kind, str) or kind not in assertion_counts:
                report.add("error", "scope.assertion_kind", loc + ".kind", "Use fact, inference or unknown.")
                assertion_ok = False
                continue
            assertion_counts[kind] += 1
            if claim.get("kind") == "fact" and kind != "fact" or claim.get("kind") == "unknown" and kind != "unknown":
                report.add("error", "scope.kind_mismatch", loc + ".kind", "A fact claim cannot hide an inference/unknown; an unknown claim cannot hide factual assertions.")
            target = read_contexts(assertion, loc)
            linked = assertion.get("evidence_ids")
            if not isinstance(linked, list) or any(not validate.nonempty(x) for x in linked):
                report.add("error", "scope.evidence_ids_type", loc + ".evidence_ids", "Expected an array of evidence IDs.")
                linked = []
            if kind in ("fact", "inference") and not linked:
                report.add("gap", "scope.premises_missing", loc + ".evidence_ids", "An asserted fact/inference needs its own supporting premises.")
            for eid in linked:
                if eid not in indices["evidence"] or not active(indices["evidence"].get(eid)):
                    report.add("error", "scope.evidence_reference", loc + ".evidence_ids", "Missing/invalid evidence: " + eid)
                if kind != "unknown" and eid not in supported:
                    report.add("error", "scope.evidence_not_linked", loc + ".evidence_ids", "Assertion premise is absent from claim.supporting_evidence_ids: " + eid)
                if eid not in located:
                    report.add("unverified", "scope.premise_unlocated", loc + ".evidence_ids", "Premise has no verified hash/text location: " + eid, "verifiability")
            known = set().union(*(evidence_scope.get(eid) or set() for eid in linked))
            unknown_scope = any(evidence_scope.get(eid) is None for eid in linked)
            if kind == "fact" and target and linked:
                missing = target - known
                if missing:
                    report.add("unverified" if unknown_scope else "error",
                               "scope.fact_unverified" if unknown_scope else "scope.fact_mismatch",
                               loc + ".context_ids", "Fact lacks source-scope support for: " + ", ".join(sorted(missing)))
                elif unknown_scope:
                    report.add("unverified", "scope.premise_scope_unknown", loc, "At least one cited premise has unverified context scope.")
                else:
                    report.add("checked", "scope.fact_annotation_consistent", loc, "Annotated source contexts cover this fact's contexts; entailment and annotation truth remain unverified.")
            if kind in ("inference", "unknown") and not validate.nonempty(assertion.get("reasoning")):
                report.add("error", "scope.reasoning_missing", loc + ".reasoning", "Inference/unknown needs reasoning, assumptions or an evidence-based uncertainty explanation.")
            if kind == "inference" and target and linked:
                if unknown_scope:
                    report.add("unverified", "scope.inference_scope_unknown", loc, "Inference premises include unverified scope.")
                cross = len(known | target) > 1 or known != target
                if cross and not (assertion.get("cross_context") is True and validate.nonempty(assertion.get("cross_context_note"))):
                    report.add("error", "scope.cross_context_undisclosed", loc, "Cross-context inference must set cross_context=true and explain interpretation/limits in cross_context_note.")
                elif cross:
                    report.add("checked", "scope.cross_context_inference", loc, "Cross-context inference is disclosed and remains an inference, never a verified fact.")
            if any(row["status"] != "checked" for row in report.data["checks"][before:]):
                assertion_ok = False

        reviews = [r for rid, r in indices["reviews"].items() if r.get("claim_id") == cid
                   and active(r) and rid not in stale_reviews]
        verdicts = {r.get("verdict") for r in reviews if isinstance(r.get("verdict"), str)}
        substantive = [r for r in reviews if r.get("verdict") in ("supported", "partial", "unsupported", "contradicted")
                       and validate.nonempty(r.get("rationale")) and validate.nonempty(r.get("reviewer"))]
        if not substantive:
            report.add("gap", "delivery.claim_unreviewed", path, "No current substantive source-based review is recorded.", "result_correctness", "synthesis")
        elif len(verdicts - {"unreviewed"}) > 1:
            report.add("gap", "delivery.review_disagreement", path, "Recorded reviewers disagree; reconcile or disclose the disagreement, do not pick the favorable verdict.", "result_correctness", "synthesis")
        elif verdicts == {"supported"}:
            reviewed_claims.add(cid)
        if assertions and substantive:
            versioned = {k: v for k, v in claim.items() if k not in ("review_status", "updated_at")}
            binding = any(r.get("claim_sha256") == validate.canonical_hash(versioned)
                          and isinstance(r.get("evidence_sha256"), dict)
                          and all(r["evidence_sha256"].get(eid) == validate.canonical_hash(indices["evidence"].get(eid))
                                  for eid in supported) for r in substantive)
            if not binding:
                report.add("unverified", "review.scope_unbound", path, "Review is not bound to current assertion/context fields and supporting evidence. Record claim_sha256 and evidence_sha256 after actual review.", "verifiability", "synthesis")
                assertion_ok = False
        if assertion_ok and assertions and (claim.get("kind") == "unknown" or supported and set(supported) <= located):
            scope_eligible.add(cid)

    # Six delivery areas are evidence-bearing statements, never checkbox scores.
    checks = metadata.get("delivery_checks")
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    pools = dict(indices, artifacts=artifacts)
    if checks is not None and not isinstance(checks, dict):
        report.add("error", "delivery.checks_type", "metadata.delivery_checks", "Expected a six-key object.", "result_completeness")
    checks = checks if isinstance(checks, dict) else {}
    for area in AREAS:
        entry = checks.get(area)
        loc = "metadata.delivery_checks." + area
        if not isinstance(entry, dict):
            report.add("gap", "delivery.area_missing", loc, "Final delivery has no recorded assessment for this required area.", "result_completeness", area)
            continue
        status = entry.get("status")
        if status not in ("completed", "partial", "blocked", "not_applicable"):
            report.add("error", "delivery.status", loc + ".status", "Use completed, partial, blocked or not_applicable.", "result_completeness", area)
        elif status in ("partial", "blocked"):
            report.add("gap", "delivery.area_incomplete", loc, "This area is explicitly " + status + ".", "task_completion", area)
        elif status == "not_applicable":
            report.add("unverified", "delivery.applicability", loc, "Applicability requires original-task review; marking N/A does not waive a required area.", "result_completeness", area)
        if not validate.nonempty(entry.get("rationale")):
            report.add("gap", "delivery.rationale_missing", loc + ".rationale", "Explain the assessment, including no-conflict/no-gap findings within the actual searched scope.", "result_completeness", area)
        refs = entry.get("refs")
        if not isinstance(refs, list) or not refs:
            report.add("gap", "delivery.refs_missing", loc + ".refs", "Link actual records as collection:id; a bare status is not evidence.", "verifiability", area)
            refs = []
        for ref in refs:
            pieces = ref.split(":", 1) if isinstance(ref, str) else []
            if len(pieces) != 2 or pieces[0] not in AREA_COLLECTIONS[area] or pieces[1] not in pools.get(pieces[0], {}):
                report.add("error", "delivery.ref_invalid", loc + ".refs", "Missing or inappropriate typed reference: " + repr(ref), "verifiability", area)
            elif not active(pools[pieces[0]][pieces[1]]):
                report.add("error", "delivery.ref_invalidated", loc + ".refs", "Referenced record is invalidated: " + ref, "verifiability", area)

    for aid, action in indices["actions"].items():
        if active(action) and not validate.nonempty(action.get("stop_reason")):
            report.add("gap", "delivery.stop_reason_missing", "actions." + aid + ".stop_reason", "Final delivery needs a reason for stopping each retrieval/tool branch, including blocked or budget-limited work.", "task_completion", "retrieval_coverage")
    if not any(active(row) for row in indices["actions"].values()):
        report.add("gap", "delivery.actions_missing", "actions", "No acquisition actions are retained; source presence alone does not show retrieval coverage.", "task_completion", "retrieval_coverage")

    answered, justified_unknown = [], []
    for rid, requirement in indices["requirements"].items():
        path = "requirements." + rid
        qids = ids(requirement.get("question_ids"))
        linked_questions = {qid for qid in qids if active(indices["questions"].get(qid))}
        if not linked_questions:
            report.add("gap", "delivery.requirement_question_missing", path + ".question_ids", "Original requirement has no active linked subquestion; keep the fixed denominator.", "task_completion", "decomposition")
        for qid in linked_questions:
            if rid not in ids(indices["questions"][qid].get("requirement_ids")):
                report.add("error", "delivery.requirement_link_asymmetric", path + ".question_ids", "Question does not link back to requirement: " + qid, "verifiability", "decomposition")
        linked_claims = [(cid, c) for cid, c in indices["claims"].items()
                         if active(c) and set(ids(c.get("question_ids"))) & linked_questions]
        eligible = [(cid, c) for cid, c in linked_claims if cid in scope_eligible and cid in reviewed_claims]
        answerability = requirement.get("answerability")
        if answerability == "answerable":
            if any(c.get("kind") in ("fact", "inference") for _, c in eligible):
                answered.append(rid)
            else:
                report.add("gap", "delivery.answerable_requirement_unanswered", path, "Answerable requirement lacks a located, scope-annotated claim with a current recorded supported review; an unknown does not satisfy it.", "task_completion", "synthesis")
        elif answerability == "unknown":
            if any(c.get("kind") == "unknown" and validate.nonempty(c.get("reasoning")) for _, c in eligible):
                justified_unknown.append(rid)
                report.add("checked", "delivery.justified_unknown_recorded", path, "Reasoned uncertainty has a current recorded review; whether uncertainty is genuinely warranted requires original-task/source review.", "result_completeness", "gaps")
            else:
                report.add("gap", "delivery.unknown_unexplained", path, "Unanswerable/uncertainty requirement needs a reviewed unknown claim explaining the missing evidence and limits.", "result_completeness", "gaps")
        elif answerability == "not_applicable":
            report.add("unverified", "delivery.requirement_applicability", path, "Original requirements cannot be waived by annotation; retain and justify applicability in final output.", "task_completion", "gaps")
        else:
            report.add("gap", "delivery.answerability_missing", path + ".answerability", "Keep original answerability explicit; it must not be changed just to make an unanswered requirement disappear.", "task_completion", "decomposition")
    if not indices["requirements"]:
        report.add("gap", "delivery.requirements_missing", "requirements", "No original requirements retained.", "task_completion", "decomposition")
    report.data["metrics"] = {
        "fixed_recorded_requirement_denominator": len(indices["requirements"]),
        "answerable_requirements_with_scoped_located_claim_and_recorded_review": answered,
        "requirements_with_reviewed_unknown_explanation": justified_unknown,
        "annotated_assertions_by_kind": assertion_counts,
        "scope_consistent_claim_ids": sorted(scope_eligible),
        "semantic_success_count": None,
        "limitation": "Counts are record-based, not scores. Original requirement omissions, incorrect context labels, hidden counterevidence and mistaken reviews need independent source/task review. No field-presence check proves completion.",
    }
    return report.finish()


def main(argv=None):
    parser = validate.JsonArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, help="Directory containing state.json")
    parser.add_argument("--annotations", help="Authoritative {contexts, delivery_checks} JSON; defaults to WORK/research_annotations.json when present")
    parser.add_argument("--output", help="Optionally write the same machine JSON report")
    try:
        args = parser.parse_args(argv)
        result = audit_work(args.work, args.annotations)
        if args.output:
            output = Path(args.output)
            protected = {(Path(args.work) / "state.json").resolve()}
            annotation_path = result.get("annotation_input", {}).get("path")
            if annotation_path:
                protected.add(Path(annotation_path).resolve())
            if output.resolve() in protected:
                raise ValueError("--output must not overwrite state.json or its selected annotations")
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, OSError, UnicodeError, RecursionError, TypeError, KeyError, AttributeError) as exc:
        report = Audit()
        report.add("error", "audit.input_or_output", "$", str(exc), "verifiability")
        result = report.finish()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
