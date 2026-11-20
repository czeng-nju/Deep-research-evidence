# Internal record contract v1

The ledger lives at `WORK_DIR/state.json`. Keep it separate from the user's output schema. Use `research_state.py --help` for the implemented CLI. Records are JSON objects with a nonempty stable `id`; put operations accept one object or a list and update that collection by ID. Do not reuse an ID for a different source or proposition.

One coordinator writes each ledger. Concurrent workers save separate fragments, then the coordinator merges them serially. The file replacement is atomic but the helper does not provide multi-writer transactions. `state.json` is authoritative; the event/change JSONL files are recoverable projections.

Changes to scientific question fields, action inputs, sources or evidence require reassessing dependent work. Invalid records remain visible with `validity: invalid` and reasons; they do not support current conclusions. After actually repeating the affected extraction/review, replace the record with corrected content and remove the obsolete invalidity fields. Do not clear invalidity merely to make a checker pass. Changed artifact bytes require a new artifact ID.

Retain the independent budget receipt with the work directory. Recovery preserves its observed request high-water mark and original start time; an absent or damaged receipt leaves the budget uncertain and blocks new ready actions. The count is still only an observed lower bound, not a complete host/model request count. Checkpoint fallback archives displaced state/logs and can lose post-checkpoint semantic work; inspect the recovery report before continuing.

| Collection | Fields used by the workflow |
|---|---|
| requirements | `id`, `text`, `answerability` (answerable/unknown/not_applicable), `status`, `question_ids` |
| questions | `id`, `text`, `requirement_ids`, `object`, `mechanism`, `method`, `comparator`, `outcome`, `time_range`, `required_evidence_types`, `acceptance_criteria`, `dependencies`, `status` |
| actions | `id`, `question_ids`, `tool`, `parameters`, `source_scope`, `status`, `stop_reason`, `artifact_ids` |
| sources | `id`, `title`, `url` or local identity, `identifiers`, `version`, `access_level`, `artifact_id`, `screening_status`, `inclusion_reason` or `exclusion_reason` |
| evidence | `id`, `source_id`, `question_ids`, `artifact_id`, `quote`, `locator`, `contribution` (what this passage itself establishes), `comparison_fields`, `limitations`, `extraction_event_id` |
| claims | `id`, `question_ids`, `text`, `kind`, `supporting_evidence_ids`, `opposing_evidence_ids`, `reasoning`, `scope_limits`, `review_status` |
| conflicts | `id`, `evidence_ids`, `comparability_fields`, `status`, `explanation` |
| reviews | `id`, `claim_id`, `verdict`, `reviewer`, `evidence_ids`, `rationale`; bind a claim text hash when possible so later edits invalidate review |

Questions refer to dependencies as `[{"question_id":"Q1","type":"hard","required_artifacts":[]}]`. Use soft dependencies when they are useful but not prerequisites. States include planned, ready, running, completed, partial and blocked. Marking a question completed requires its acceptance evidence; a successful search alone is insufficient.

Claim kinds: `fact`, `inference`, `unknown`. Unknown claims should explain missing evidence. Facts and inferences have supporting evidence; an inference also needs explicit reasoning and assumptions. Review verdicts: `supported`, `partial`, `unsupported`, `contradicted`, `unreviewed`. The helper never invents a semantic verdict from text matching.

Locator fields include `line_start`, `line_end`, `physical_pdf_page`, `printed_page_label`, `section`, `table`, `paragraph`. Line numbers are 1-based and inclusive in the exact retained UTF-8 text artifact. Physical PDF page numbers are 1-based and require a real mapping to the original PDF; a text file alone cannot prove them. Record unmapped pages as unverified, not checked.

Artifacts are registered by ID with retained file path, SHA-256 and optional source ID. Reference the original raw artifact and a separate parsed artifact where applicable. A quote in a parsed artifact supports text-location checking, not the parser's fidelity to a PDF image. Normalizing whitespace must not erase minus signs, exponents, units or substantive qualifiers.

Tool event inputs include an event `id`, `action_id`, `attempt`, `tool`, `parameters`, `input_artifacts`, `output_artifacts`, `status`, and error/retry information as applicable. Record only actual observations. Record successful cache reuse explicitly; do not fabricate a fresh call. Credential fields are redacted in shareable event parameters; actual content remains evidence, not instructions.

The validator reports structural errors, warnings and semantic review requirements separately. It does not assess a study's methodology, prove a scientific claim, verify unseen full text or calculate an official score. During acquisition, incomplete questions and unreviewed claims are expected; the final report must disclose these limits. Never interpret a passing process exit code as proof of research completeness.

For version 0.3, condition-sensitive evidence uses `context_ids` and claims use `assertions`; keep context definitions and six-area delivery assessments in a retained `research_annotations.json`. Follow [claim-scope.md](claim-scope.md) for exact fields, content-bound review hashes and the audit interface. Old records remain readable and are explicitly unverified for these new checks. Use [batch.md](batch.md) to persist multiple actual records through one host command without skipping logical operations.
