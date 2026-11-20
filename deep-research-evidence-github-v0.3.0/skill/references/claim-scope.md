# Claim scope and final-delivery audit

Use `scripts/research_audit.py --work WORK_DIR [--annotations FILE] [--output REPORT.json]` after actual extraction, synthesis and source-based review. It reads `state.json`, runs the existing structural checks, then reports annotated scope inconsistencies and delivery gaps as machine JSON. It does not retrieve, interpret papers, repair claims or assign competition points. Python 3.9+ and the standard library suffice; keep `validate.py` beside it.

Exit codes: `1` = contradictory/invalid records; `2` = missing auditable information or an unfinished delivery area; `0` = these limited record checks completed, **scientific semantics still pending**. The output always states `semantic_correctness_verified: false`, `official_score: null`, and `semantic_success_count: null`. Read located findings, not only the exit code. Old ledgers remain readable; missing new annotations are unverified, never an automatic pass.

## Keep annotations in a retained sidecar

The recommended interface is `WORK_DIR/research_annotations.json` with `{ "contexts": [...], "delivery_checks": {...} }`. Register its bytes using `research_state.py artifact --work WORK_DIR --id ANNOTATIONS_V1 --path WORK_DIR/research_annotations.json` (use the actual absolute paths). For later changes, retain the old file and use a new file/artifact ID, passing the new file with `--annotations`. Do not overwrite an already registered file and keep its old hash. Use existing `put` or `record_batch.py` commands to add the new claim/evidence fields; do not directly edit a live ledger or its history.

Selection order is: explicit `--annotations FILE`; otherwise the default `WORK_DIR/research_annotations.json` when present; otherwise existing `state.metadata.contexts` and `state.metadata.delivery_checks`. A selected file is authoritative for **both** sections; absent sections do not silently inherit possibly stale metadata. The metadata form is a compatibility fallback, not an instruction to mutate `state.json` manually. The report records the selected path, SHA-256 and matching verified artifact IDs. An unregistered sidecar is readable but reported as unverified. A missing or invalid explicitly selected file is an error, never silent fallback. Audit/output generation does not mutate the ledger or annotation file.

## Define contexts from the actual source

Set the annotation file's `contexts` (or existing `state.metadata.contexts`) to an array of `{id, description}`. A context describes an actual combination of relevant population/material, intervention, method, comparator, outcome, conditions and time; use as much detail as the claim needs. IDs have no scientific semantics. Calling an ID `global`, `30C` or `experiment2` proves nothing.

For each active evidence record add:

| Field | Meaning |
|---|---|
| `context_ids` | Nonempty array of defined context IDs whose applicability you extracted from the source itself. Missing/empty scope is unknown, never universal. |
| `context_scope` | Optional `specific` (default), `common`, or `global`. None of these enables a wildcard. |
| `scope_confirmation` | Required for `common`/`global`: `{confirmed: true, reviewer: "Agent/name/type", rationale: "How the actual retained passage establishes shared applicability"}`. Still enumerate all applicable `context_ids`. |

Use the existing quote, locator, artifact, source and contribution fields to retain the passage that establishes the scope. A table header or methods passage may establish shared conditions; retrieve and link it if needed. Do not infer shared scope from the fact that two citations share a paper ID. Do not broaden a source's tags to silence an audit error. Confirmation is the reviewer's fallible assertion, **not** script-verified source meaning. The checker deliberately cannot certify its truth.

## Bind every atomic assertion to its own premises

Add `claims[].assertions` as an array with the following fields. IDs are unique within each claim, not necessarily across claims.

| Field | Rule |
|---|---|
| `id`, `text` | Stable ID and one atomic proposition. The checker cannot prove the text is atomic or that it covers every sentence. |
| `kind` | `fact`, `inference`, or `unknown`. A parent fact claim may contain only fact assertions; a parent unknown claim only unknown assertions. An inference claim may include its factual premises. |
| `context_ids` | Explicit contexts the assertion applies to. |
| `evidence_ids` | Evidence for this assertion, rather than all references in a paragraph. Fact/inference evidence must also occur in the parent `supporting_evidence_ids`. Keep opposing evidence in the existing separate field. |
| `reasoning` | Required for inference and unknown: derivation/assumptions, or why the actual evidence cannot answer. |
| `cross_context`, `cross_context_note` | For a comparison or extrapolation across different contexts, set `cross_context: true` and explain the interpretation and its limits in a nonempty note. |

A fact for context B supported only by context A produces `scope.fact_mismatch`. If a premise's scope is missing, the result is `scope.fact_unverified`, not proof that it applies to B. A disclosed cross-context inference is permitted; it is counted separately from facts. This permits legitimate comparisons without treating a hypothesis or calculation as a directly observed fact.

For example, retain premise A's value with A's evidence, premise B's value with B's evidence, and a separate inference for `B − A` with both evidence IDs and its calculation/limitations. Merely putting both IDs on an unstructured paragraph does not establish either premise. Union coverage of context labels cannot detect every wrong premise, unit, direction or causal claim; review the actual passages.

After the review, bind its current content using the existing review fields:

- `claim_sha256`: SHA-256 of canonical JSON for the entire claim, excluding only `review_status` and `updated_at`. Canonical JSON means UTF-8, `ensure_ascii=False`, `sort_keys=True`, separators `(',', ':')`, no nonfinite numbers.
- `evidence_sha256`: object mapping every supporting evidence ID to its canonical JSON SHA-256, including context annotations.
- Retain `reviewer`, `verdict`, `rationale`, and the reviewed `evidence_ids`. Existing artifact hashes and locators must remain valid.

A claim-text-only hash cannot bind newly added assertion or scope fields. Changing a context annotation requires a real renewed review; do not update a hash merely to remove a warning. Conflicting review verdicts are reported rather than choosing the favorable one.

## Record all six delivery assessments

Use the annotation file's `delivery_checks` (or existing `state.metadata.delivery_checks`) as an object with these six required keys. Each value has `status`, `refs`, and `rationale`. Status is `completed`, `partial`, `blocked`, or `not_applicable`. Partial/blocked is an explicit gap. N/A remains unverified and does not waive a required area. Diagnostic paths use the canonical `metadata.contexts`/`metadata.delivery_checks` names even when they were supplied by the sidecar; `annotation_input.path` identifies that file.

| Key | Assessment and allowed `refs` collections |
|---|---|
| `decomposition` | Original requirements and meaningful questions; `requirements`, `questions`. |
| `retrieval_coverage` | Actual source/query coverage and stop decisions; `actions`, `artifacts`. |
| `evidence_links` | Individual citation contributions and claim support; `claims`, `evidence`. |
| `conflict_assessment` | Located opposing evidence, comparability, or a scoped no-conflict finding; `conflicts`, `evidence`. |
| `synthesis` | Useful bounded conclusions and actual reviews; `claims`. |
| `gaps` | Remaining missing evidence, or a justified no-additional-gap disposition within the searched scope; `requirements`, `questions`, `actions`, `claims`. |

`refs` is a nonempty array of typed strings such as `"questions:Q1"` or `"evidence:E2"`. IDs are resolved in the named collection, because IDs need not be globally unique. `rationale` explains what was checked and what remains uncertain. A no-conflict or no-gap finding needs actual evidence/scope references and reasoning, not an empty list or a generic tick.

The auditor additionally flags requirements without linked questions or suitable reviewed claims, missing final action `stop_reason`, absent reviews, invalid/stale support, and missing six-area assessments. Requirements marked `answerable` remain in the denominator when the response says unknown. An original uncertainty requirement can instead link to a reviewed `unknown` claim with reasons and remain separately counted as justified uncertainty. The Agent must preserve original answerability; relabeling a missing answer as unknown is not a repair.

The denominator is the recorded original requirement list. The script cannot know whether the Agent omitted an input requirement or deleted it before recording; independently compare the final answer and ledger with the actual user task. It also cannot prove `completed` is true, that all retrieval was performed, that all counterevidence was included, that contexts were labeled honestly, or that a reviewer was correct. A completed six-item checklist is documentation, not a scientific completion score.

## Rubric mapping

Every finding names one scoring subcriterion through its `rubric` field, and one of the six areas through `area`.

| `rubric` code | User's scoring item | Limited intended improvement |
|---|---|---|
| `result_correctness` | 实际效果／结果正确性，15 分 | Catch recorded scope overreach, undisclosed cross-context inference, unreviewed claims and disagreement. |
| `task_completion` | 实际效果／任务完成度，15 分 | Expose original requirement omissions in the ledger, unfinished retrieval branches and answerable questions left unanswered. |
| `result_completeness` | 实际效果／结果完整性，5 分 | Require explicit assessment of all six requested areas; distinguish justified uncertainty from missing answers. |
| `verifiability` | 实际效果／结果可核验性，5 分 | Locate failures, bind review content, and retain exact source/hash/record references. |

These are optimization targets, not awarded points. Synthetic guard tests verify only the implemented invariants. Quality gain, efficiency gain, innovation relative to existing systems, and official scores require separate fair end-to-end evaluation.

## Minimal executable synthetic example

Run from this Skill's directory (the directory containing `scripts/`). This creates a deliberately incomplete toy ledger with a wrong-context fact. It is not a prepared research answer or benchmark case. The audit should report `scope.fact_mismatch` and missing delivery/review information, exit `1`, and preserve the ledger unchanged.

```sh
demo_dir=$(mktemp -d)
python3 - "$demo_dir" <<'PY'
import hashlib, json, pathlib, sys
work = pathlib.Path(sys.argv[1])
quote = "The blue tray holds 4 synthetic counters."
(work / "source.txt").write_text(quote + "\n", encoding="utf-8")
state = {
    "schema_version": 1, "case_id": "SYNTHETIC-SCOPE-DEMO",
    "input_contract": {"type": "object"},
    "requirements": [{"id": "R1", "text": "Report the blue tray count.",
        "answerability": "answerable", "status": "partial", "question_ids": ["Q1"]}],
    "questions": [{"id": "Q1", "text": "How many counters are in the blue tray?",
        "requirement_ids": ["R1"], "dependencies": [], "status": "partial",
        "acceptance_criteria": "Preserve the stated tray."}],
    "actions": [{"id": "A1", "question_ids": ["Q1"], "tool": "synthetic_fixture",
        "parameters": {}, "status": "completed", "stop_reason": "Toy fixture only.",
        "artifact_ids": ["T1"]}],
    "sources": [{"id": "S1", "title": "Toy source", "artifact_id": "T1",
        "access_level": "full_text", "identifiers": {}}],
    "artifacts": {"T1": {"id": "T1", "path": "source.txt", "source_id": "S1",
        "sha256": hashlib.sha256((quote + "\n").encode()).hexdigest()}},
    "evidence": [{"id": "E1", "source_id": "S1", "artifact_id": "T1",
        "question_ids": ["Q1"], "quote": quote,
        "locator": {"line_start": 1, "line_end": 1}, "comparison_fields": {},
        "limitations": ["Toy fixture"], "context_ids": ["blue"]}],
    "claims": [{"id": "C1", "text": "The amber tray holds 4 counters.",
        "question_ids": ["Q1"], "kind": "fact", "supporting_evidence_ids": ["E1"],
        "opposing_evidence_ids": [], "reasoning": "", "scope_limits": [],
        "review_status": "unreviewed", "assertions": [{"id": "a1",
            "text": "The amber tray holds 4 counters.", "kind": "fact",
            "context_ids": ["amber"], "evidence_ids": ["E1"], "reasoning": ""}]}],
    "conflicts": [], "reviews": [],
    "metadata": {"events": []}
}
annotations = {"contexts": [
        {"id": "blue", "description": "The blue tray in the toy source."},
        {"id": "amber", "description": "A different tray, with no count supplied."}],
        "delivery_checks": {}}
note_path = work / "research_annotations.json"
note_path.write_text(json.dumps(annotations), encoding="utf-8")
# A synthetic fixture is created from scratch here. For a live ledger, register
# this file with research_state.py artifact instead of manually editing state.
state["artifacts"]["NOTES1"] = {"id": "NOTES1", "path": note_path.name,
    "sha256": hashlib.sha256(note_path.read_bytes()).hexdigest()}
(work / "state.json").write_text(json.dumps(state), encoding="utf-8")
PY
python3 scripts/research_audit.py --work "$demo_dir" --output "$demo_dir/audit.json"
```

The correct next action is to inspect the original task and source, correct the actual assertion if warranted, perform and record source-based review, and complete the six delivery assessments. Changing the evidence's `context_ids` from blue to amber would falsify the toy source annotation; the script cannot prevent dishonest annotations or replace scientific judgment.
