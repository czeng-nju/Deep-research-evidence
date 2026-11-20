#!/usr/bin/env python3
"""Read-only structural checks; a valid record is not a scientifically valid answer.

Python 3.9+, standard library only. Exit 0: checked structural constraints hold;
1: violations or unreadable input; 2: result contract cannot be fully machine-checked.
State --strict requires complete record fields, not successful research or positive
semantic verdicts. Honest partial/unknown states are not rejected for being partial.
"""
import argparse
import decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


COLLECTIONS = ("requirements", "questions", "actions", "sources", "evidence",
               "claims", "conflicts", "reviews")
HEX = re.compile(r"^[0-9a-fA-F]{64}$")


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


class Report:
    def __init__(self, mode):
        self.data = {"mode": mode, "checks": [], "errors": [], "warnings": [],
                     "semantic_review_required": [], "metrics": {},
                     "scope": "Structural validation only; no scientific correctness or official score is certified."}

    def add(self, level, code, path, message):
        row = {"code": code, "path": path, "message": message}
        self.data["checks"].append(dict(row, status=level))
        if level == "error":
            self.data["errors"].append(row)
        elif level == "warning":
            self.data["warnings"].append(row)
        elif level == "semantic_review_required":
            self.data["semantic_review_required"].append(row)

    def error(self, code, path, message):
        self.add("error", code, path, message)

    def warn(self, code, path, message):
        self.add("warning", code, path, message)

    def manual(self, code, path, message):
        self.add("semantic_review_required", code, path, message)

    def passed(self, code, path, message):
        self.add("pass", code, path, message)

    def finish(self, incomplete=False):
        invalid = bool(self.data["errors"])
        self.data["structurally_valid"] = not invalid
        self.data["validation_complete"] = not (invalid or incomplete)
        self.data["status"] = ("invalid" if invalid else "incomplete" if incomplete
                               else "structure_checked_semantics_pending"
                               if self.data["semantic_review_required"] else "structure_checked")
        self.data["exit_code"] = 1 if invalid else 2 if incomplete else 0
        return self.data["exit_code"]


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_hash(value):
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8"))


def read_json(path, report, label):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    def floating(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("number exceeds finite parser range: " + value)
        return number
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                          parse_float=floating,
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError("nonfinite JSON number: " + x)))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        report.error("json.read", label, str(exc))
        return None


def confined(root, value, report, label):
    """Permit absolute paths only inside root, and never follow a symlink."""
    if not nonempty(value):
        report.error("path.missing", label, "Expected a nonempty retained artifact path.")
        return None
    root = Path(root).resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.relative_to(root)
        if ".." in relative.parts:
            raise ValueError("parent traversal is not allowed")
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError("symlink in retained path")
        candidate.resolve().relative_to(root)
        return candidate
    except (ValueError, OSError, RuntimeError) as exc:
        report.error("path.escape", label, "Path must stay inside its declared root without symlinks: " + str(exc))
        return None


def validate_state(work, report, strict=False):
    root = Path(work).resolve()
    state_path = confined(root, "state.json", report, "state.json")
    if state_path is None:
        return
    state = read_json(state_path, report, "state.json")
    if not isinstance(state, dict):
        report.error("state.object", "$", "State must be a nonempty JSON object.")
        return
    version = state.get("schema_version")
    if version not in (1, "1", "v1", "1.0") or isinstance(version, bool):
        report.error("state.version", "schema_version", "Expected schema_version 1.")
    if not nonempty(state.get("case_id")):
        report.error("state.case_id", "case_id", "A real nonempty case_id is required.")
    if "input_contract" not in state:
        report.error("state.contract_missing", "input_contract", "Preserve the observed contract, including null when none was supplied.")
    if not isinstance(state.get("input_contract"), dict) or not state.get("input_contract"):
        report.warn("state.contract_unverified", "input_contract", "Absent, empty or natural-language contract has not been machine-verified.")
        report.manual("state.contract_review", "input_contract", "Review the actual task and output requirements; do not invent a replacement contract.")
    if not isinstance(state.get("metadata"), dict):
        report.error("state.metadata", "metadata", "Expected a metadata object.")
    indices, seen = {}, {}
    for name in COLLECTIONS:
        rows = state.get(name)
        indices[name] = {}
        if not isinstance(rows, list):
            report.error("state.collection", name, "Required collection must be an array.")
            continue
        for n, row in enumerate(rows):
            loc = "%s[%d]" % (name, n)
            if not isinstance(row, dict) or not nonempty(row.get("id")):
                report.error("record.id", loc, "Every record needs a nonempty string id.")
                continue
            ident = row["id"]
            if ident in indices[name]:
                report.error("record.duplicate_id", loc, "ID already used in this collection.")
                continue
            seen[ident] = loc
            indices[name][ident] = row
    if not indices["requirements"] or not indices["questions"]:
        report.error("state.no_plan", "$", "At least one original requirement and one linked question are needed; an empty ledger is not a research record.")

    artifacts = state.get("artifacts")
    if not isinstance(artifacts, dict):
        report.error("state.artifacts", "artifacts", "Expected an artifact metadata object indexed by ID.")
        artifacts = {}
    raw, texts, valid_artifacts = {}, {}, set()
    for aid, meta in artifacts.items():
        loc = "artifacts." + aid
        if not nonempty(aid) or not isinstance(meta, dict):
            report.error("artifact.metadata", loc, "Artifact ID and metadata are required.")
            continue
        seen[aid] = loc
        if "id" in meta and meta["id"] != aid:
            report.error("artifact.id", loc, "Artifact metadata ID differs from its dictionary key.")
        path = confined(root, meta.get("path"), report, loc + ".path")
        expected = meta.get("sha256")
        if not isinstance(expected, str) or not HEX.fullmatch(expected):
            report.error("artifact.sha256", loc, "A 64-hex SHA-256 is required.")
        if path is None:
            continue
        try:
            if not path.is_file():
                raise ValueError("artifact is not a regular file")
            payload = path.read_bytes()
            actual = digest(payload)
            raw[aid] = payload
            if isinstance(expected, str) and actual == expected.lower() and meta.get("validity") != "invalid":
                valid_artifacts.add(aid)
                report.passed("artifact.hash", loc, "Retained file matches its SHA-256.")
            elif meta.get("validity") == "invalid" and isinstance(expected, str) and actual == expected.lower():
                report.warn("artifact.invalidated", loc, "Artifact is explicitly invalidated and cannot provide current support.")
            else:
                report.error("artifact.hash_mismatch", loc, "Retained file does not match its recorded SHA-256.")
            # ASCII-only PDFs are still PDFs, never quote-verifiable UTF-8 text.
            if b"%PDF-" not in payload[:1024] and b"\x00" not in payload:
                try:
                    texts[aid] = payload.decode("utf-8")
                except UnicodeError:
                    pass
        except (OSError, ValueError) as exc:
            report.error("artifact.read", loc, str(exc))

    def ref(value, target, loc, optional=False):
        if optional and value is None:
            return False
        pool = artifacts if target == "artifacts" else indices[target]
        if not nonempty(value) or value not in pool:
            report.error("reference.missing", loc, "Unknown %s ID: %r" % (target, value))
            return False
        return True

    def refs(row, field, target, loc, required=False):
        value = row.get(field, [])
        if not isinstance(value, list):
            report.error("reference.array", loc + "." + field, "Expected an array of IDs.")
            return []
        if required and not value:
            report.error("reference.empty", loc + "." + field, "At least one linked ID is required.")
        valid, local = [], set()
        for n, ident in enumerate(value):
            if ref(ident, target, "%s.%s[%d]" % (loc, field, n)):
                valid.append(ident)
                if ident in local:
                    report.warn("reference.duplicate", loc + "." + field, "Repeated ID does not count as independent evidence: " + ident)
                local.add(ident)
        return valid

    def fields(row, names, loc):
        for name in names:
            if name not in row:
                (report.error if strict else report.warn)("record.field_missing", loc + "." + name, "Field is missing; use explicit unknown/null where justified.")

    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    inputs = metadata.get("inputs", {})
    if isinstance(inputs, dict):
        for label, entry in inputs.items():
            if not isinstance(entry, dict):
                report.error("input.metadata", "metadata.inputs." + label, "Expected input identity metadata.")
                continue
            path = confined(root, entry.get("snapshot"), report, "metadata.inputs." + label)
            if path is None:
                continue
            try:
                payload = path.read_bytes()
                if digest(payload) != entry.get("sha256"):
                    report.error("input.snapshot_changed", "metadata.inputs." + label, "Retained original input no longer matches its recorded hash.")
                if label == "case":
                    original = read_json(path, report, "metadata.inputs.case.snapshot")
                    if isinstance(original, dict):
                        if state.get("case_id") != original.get("case_id"):
                            report.error("input.case_id_changed", "case_id", "Ledger identity differs from retained actual case.")
                        if state.get("input_contract") != original.get("output_contract"):
                            report.error("input.contract_changed", "input_contract", "Ledger output contract differs from retained actual case.")
            except OSError as exc:
                report.error("input.snapshot_unreadable", "metadata.inputs." + label, str(exc))
    else:
        report.error("input.metadata", "metadata.inputs", "Expected an object when input identities are supplied.")

    events = metadata.get("events", [])
    event_ids = set()
    if not isinstance(events, list):
        report.error("events.array", "metadata.events", "Events must be an array.")
        events = []
    for n, event in enumerate(events):
        loc = "metadata.events[%d]" % n
        if not isinstance(event, dict) or not nonempty(event.get("id")):
            report.error("event.id", loc, "Every actual attempt needs an event ID.")
            continue
        if event["id"] in event_ids:
            report.error("event.duplicate_id", loc, "Repeated event ID.")
        event_ids.add(event["id"])
        ref(event.get("action_id"), "actions", loc + ".action_id")
        refs(event, "input_artifacts", "artifacts", loc)
        refs(event, "output_artifacts", "artifacts", loc)
        if not integer(event.get("attempt")) or event["attempt"] < 1:
            report.error("event.attempt", loc, "Attempt number must be an integer >= 1.")
        if not nonempty(event.get("tool")) or not nonempty(event.get("status")):
            report.error("event.identity", loc, "Tool and actual status are required.")
        if not isinstance(event.get("parameters", event.get("exact_parameters")), dict):
            report.error("event.parameters", loc, "Record exact observed parameters as an object.")
        if event.get("status") in ("failed", "error") and not event.get("error"):
            report.warn("event.failure_details", loc, "Failed attempt lacks recorded error details.")
    invalid = {name: {ident for ident, row in rows.items() if row.get("validity") == "invalid"}
               for name, rows in indices.items()}
    for name, ids in invalid.items():
        for ident in sorted(ids):
            report.warn("record.invalidated", name + "." + ident, "Invalidated historical record is retained but cannot count as current support.")
    invalid["sources"].update(sid for sid, row in indices["sources"].items()
                              if not isinstance(row.get("artifact_id"), str) or row["artifact_id"] not in valid_artifacts)
    invalid["evidence"].update(eid for eid, row in indices["evidence"].items()
                               if not isinstance(row.get("source_id"), str) or row["source_id"] in invalid["sources"]
                               or not isinstance(row.get("artifact_id"), str) or row["artifact_id"] not in valid_artifacts)

    for rid, row in indices["requirements"].items():
        loc = "requirements." + rid
        if not nonempty(row.get("text")):
            report.error("requirement.text", loc, "Original requirement text is required.")
        refs(row, "question_ids", "questions", loc)
        fields(row, ("answerability", "status"), loc)
    graph = {}
    for qid, row in indices["questions"].items():
        loc = "questions." + qid
        if not nonempty(row.get("text")):
            report.error("question.text", loc, "A searchable question is required.")
        refs(row, "requirement_ids", "requirements", loc, required=True)
        fields(row, ("acceptance_criteria", "dependencies", "status"), loc)
        if not row.get("acceptance_criteria"):
            report.warn("question.acceptance", loc, "No acceptance criterion recorded; completion requires semantic review.")
        deps = row.get("dependencies", [])
        graph[qid] = []
        if not isinstance(deps, list):
            report.error("question.dependencies", loc, "Dependencies must be an array.")
            continue
        for n, dep in enumerate(deps):
            dl = loc + ".dependencies[%d]" % n
            if not isinstance(dep, dict):
                report.error("dependency.object", dl, "Expected a dependency object.")
                continue
            if ref(dep.get("question_id"), "questions", dl):
                graph[qid].append(dep["question_id"])
            if dep.get("type") not in ("hard", "soft"):
                report.error("dependency.type", dl, "Dependency type must be hard or soft.")
            refs(dep, "required_artifacts", "artifacts", dl)
    # Iterative Kahn elimination avoids recursion errors on adversarial graphs.
    pending = {key: set(value) for key, value in graph.items()}
    while pending:
        ready = {key for key, value in pending.items() if not value}
        if not ready:
            report.error("questions.cycle", "questions", "Dependency cycle involving: " + ", ".join(sorted(pending)))
            break
        pending = {key: value - ready for key, value in pending.items() if key not in ready}
    else:
        report.passed("questions.dag", "questions", "Question dependencies are acyclic; scientific necessity is not established.")
    for aid, row in indices["actions"].items():
        loc = "actions." + aid
        refs(row, "question_ids", "questions", loc, required=True)
        refs(row, "artifact_ids", "artifacts", loc)
        if not nonempty(row.get("tool")) or not isinstance(row.get("parameters"), dict):
            report.error("action.inputs", loc, "Action needs a tool name and exact parameter object.")
        fields(row, ("status", "stop_reason"), loc)
    for sid, row in indices["sources"].items():
        loc = "sources." + sid
        fields(row, ("access_level", "identifiers"), loc)
        if not any(nonempty(row.get(k)) for k in ("url", "title", "local_identity")):
            report.error("source.identity", loc, "Source needs a URL, title or explicit local identity.")
        ref(row.get("artifact_id"), "artifacts", loc + ".artifact_id")
    for aid, meta in artifacts.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("source_id") is not None:
            ref(meta["source_id"], "sources", "artifacts." + aid + ".source_id")
        for key in ("original_pdf_artifact_id", "page_map_artifact_id"):
            if meta.get(key) is not None:
                ref(meta[key], "artifacts", "artifacts." + aid + "." + key)

    located = set()
    for eid, row in indices["evidence"].items():
        loc = "evidence." + eid
        ref(row.get("source_id"), "sources", loc + ".source_id")
        refs(row, "question_ids", "questions", loc, required=True)
        ref(row.get("artifact_id"), "artifacts", loc + ".artifact_id")
        fields(row, ("comparison_fields", "limitations", "quote", "locator"), loc)
        aid, quote, locator = row.get("artifact_id"), row.get("quote"), row.get("locator")
        if row.get("extraction_event_id") is not None and row["extraction_event_id"] not in event_ids:
            report.error("evidence.event_missing", loc, "extraction_event_id does not identify a recorded actual tool event.")
        if eid in invalid["evidence"] and row.get("validity") != "invalid":
            report.error("evidence.invalid_support", loc, "Active evidence depends on an invalid source/artifact and must be revalidated or marked invalid.")
        meta = artifacts.get(aid, {}) if isinstance(aid, str) else {}
        if isinstance(meta, dict) and meta.get("source_id") not in (None, row.get("source_id")):
            report.error("evidence.source_mismatch", loc, "Evidence source differs from its artifact's source.")
        if not isinstance(locator, dict):
            report.error("evidence.locator", loc, "A locator object is required; unknown positions must remain null.")
            continue
        start, end = locator.get("line_start"), locator.get("line_end")
        if end is None and start is not None:
            end = start
        if start is not None or end is not None:
            if not integer(start) or not integer(end) or start < 1 or end < start:
                report.error("locator.lines", loc, "Line positions must be positive 1-based inclusive integers in order.")
            elif aid not in texts:
                report.warn("quote.nontext", loc, "Artifact is binary/PDF or unavailable; bytes are not a text-location check.")
            else:
                lines = texts[aid].splitlines()
                if end > len(lines):
                    report.error("locator.out_of_range", loc, "Line locator exceeds the retained text.")
                elif not nonempty(quote):
                    report.warn("quote.missing", loc, "No quote supplied; text support has not been checked.")
                else:
                    normalize = lambda s: re.sub(r"\s+", " ", s).strip()
                    excerpt = "\n".join(lines[start - 1:end])
                    if normalize(quote) not in normalize(excerpt):
                        report.error("quote.mismatch", loc, "Quote was not found in the specified lines; only whitespace normalization is allowed.")
                    elif aid in valid_artifacts and eid not in invalid["evidence"]:
                        located.add(eid)
                        report.passed("quote.located", loc, "Quote occurs in the hashed text artifact at the recorded lines; this does not prove claim support.")
        else:
            report.warn("locator.unverified", loc, "No exact text-line location; section/table/page labels alone were not machine-verified.")
        page = locator.get("physical_pdf_page")
        if page is not None:
            if not integer(page) or page < 1:
                report.error("locator.pdf_page", loc, "Physical PDF page must be a positive 1-based integer or null.")
            else:
                validate_page_map(aid, page, start, end, meta, artifacts, raw, valid_artifacts, report, loc)
        if locator.get("printed_page_label") is not None:
            report.manual("locator.printed_page", loc, "Printed page label requires checking the actual page image; it is not inferred from physical page number.")
        report.manual("evidence.meaning", loc, "Check original-source fidelity, access limitations, study conditions and relevance to the linked questions.")

    reviews_by_claim = {}
    stale_reviews = set(invalid["reviews"])
    for rid, row in indices["reviews"].items():
        loc = "reviews." + rid
        ok = ref(row.get("claim_id"), "claims", loc + ".claim_id")
        refs(row, "evidence_ids", "evidence", loc)
        if row.get("verdict") not in ("supported", "partial", "unsupported", "contradicted", "unreviewed"):
            report.error("review.verdict", loc, "Unknown review verdict.")
        if not nonempty(row.get("reviewer")):
            report.error("review.identity", loc, "Record reviewer identity/type; automation is not independent human review.")
        if row.get("verdict") != "unreviewed" and not nonempty(row.get("rationale")):
            report.error("review.rationale", loc, "A semantic verdict needs its reasoning.")
        if not ok:
            continue
        cid = row["claim_id"]
        claim = indices["claims"][cid]
        claim_links = claim.get("supporting_evidence_ids", []) + claim.get("opposing_evidence_ids", [])
        if cid in invalid["claims"] or any(eid in invalid["evidence"] for eid in claim_links):
            stale_reviews.add(rid)
            report.warn("review.invalid_support", loc, "Review depends on an invalidated claim/evidence and cannot count as current review.")
        expected_text_hash = digest(str(claim.get("text", "")).encode("utf-8"))
        hash_present = False
        for key in ("claim_text_sha256", "claim_text_hash"):
            if key in row:
                hash_present = True
                if row[key] != expected_text_hash:
                    stale_reviews.add(rid)
                    report.error("review.stale", loc + "." + key, "Claim text changed or its hash is invalid; review must be renewed.")
        if "claim_sha256" in row:
            hash_present = True
            versioned = {key: value for key, value in claim.items() if key not in ("review_status", "updated_at")}
            if row["claim_sha256"] != canonical_hash(versioned):
                stale_reviews.add(rid)
                report.error("review.stale", loc + ".claim_sha256", "Versioned claim content changed; canonical hash excludes review_status and updated_at.")
        for key, pool in (("evidence_sha256", indices["evidence"]), ("artifact_sha256", artifacts)):
            if key in row:
                hashes = row[key]
                if not isinstance(hashes, dict):
                    report.error("review.hashes", loc + "." + key, "Expected ID-to-SHA256 object.")
                    continue
                for target, value in hashes.items():
                    current = pool.get(target)
                    actual = (current.get("sha256") if key == "artifact_sha256" and isinstance(current, dict)
                              else canonical_hash(current) if current is not None else None)
                    if actual != value:
                        stale_reviews.add(rid)
                        report.error("review.stale", loc + "." + key, "Missing or changed reviewed object: " + target)
        if not hash_present:
            report.warn("review.unbound", loc, "Review is not bound to a claim version; later edits cannot be detected reliably.")
        reviews_by_claim.setdefault(cid, []).append((rid, row))

    unknown = 0
    eligible = set()
    for cid, row in indices["claims"].items():
        loc = "claims." + cid
        refs(row, "question_ids", "questions", loc, required=True)
        kind = row.get("kind")
        if kind not in ("fact", "inference", "unknown"):
            report.error("claim.kind", loc, "Claim kind must be fact, inference or unknown.")
        if not nonempty(row.get("text")):
            report.error("claim.text", loc, "An atomic claim needs nonempty text.")
        supporting = refs(row, "supporting_evidence_ids", "evidence", loc, required=kind in ("fact", "inference"))
        opposing = refs(row, "opposing_evidence_ids", "evidence", loc)
        if (set(supporting) | set(opposing)) & invalid["evidence"]:
            invalid["claims"].add(cid)
            if row.get("validity") != "invalid":
                report.error("claim.invalid_support", loc, "Active claim references invalid evidence; renew its support or mark it invalid.")
        if set(supporting) & set(opposing):
            report.warn("claim.evidence_polarity", loc, "Same evidence appears as supporting and opposing; explain mixed findings explicitly.")
        if kind == "inference" and not nonempty(row.get("reasoning")):
            report.error("claim.reasoning", loc, "Inference requires explicit reasoning and assumptions.")
        fields(row, ("scope_limits", "review_status"), loc)
        if kind == "unknown":
            unknown += 1
            report.manual("claim.unknown", loc, "Unknown is not counted as an answered research requirement; assess whether uncertainty itself was requested.")
        candidates = [(rid, r) for rid, r in reviews_by_claim.get(cid, []) if rid not in stale_reviews]
        verdicts = {r.get("verdict") for _, r in candidates}
        if len(verdicts - {"unreviewed"}) > 1:
            report.warn("review.disagreement", loc, "Review verdicts disagree; no favorable verdict is automatically selected.")
        if cid not in invalid["claims"] and kind in ("fact", "inference") and supporting and set(supporting) <= located and verdicts == {"supported"}:
            eligible.add(cid)
        if not candidates or verdicts == {"unreviewed"}:
            report.warn("claim.unreviewed", loc, "No current substantive semantic review is recorded.")
        if cid not in invalid["claims"] and row.get("review_status") == "supported" and "supported" not in verdicts:
            report.error("claim.review_status", loc, "Claim says supported without a corresponding current review.")
        report.manual("claim.support", loc, "Recorded review verdicts are assertions, not validator-certified entailment; check support, counterevidence, units and scope.")
    for fid, row in indices["conflicts"].items():
        loc = "conflicts." + fid
        linked = refs(row, "evidence_ids", "evidence", loc, required=True)
        if len(set(linked)) < 2:
            report.error("conflict.sides", loc, "A conflict needs at least two distinct evidence records.")
        fields(row, ("comparability_fields", "status", "explanation"), loc)
        report.manual("conflict.interpretation", loc, "Assess both sides and comparability; source counts cannot resolve disagreement.")

    covered = set()
    for cid in eligible:
        for qid in indices["claims"][cid].get("question_ids", []):
            if qid not in invalid["questions"]:
                covered.update(indices["questions"].get(qid, {}).get("requirement_ids", []))
    all_requirements = set(indices["requirements"])
    report.data["metrics"].update({
        "record_counts": {key: len(value) for key, value in indices.items()},
        "artifact_count": len(artifacts), "hash_verified_artifacts": len(valid_artifacts),
        "text_located_evidence": len(located), "unknown_claims": unknown,
        "claims_with_located_support_and_recorded_supported_review": len(eligible),
        "original_requirement_denominator": len(all_requirements), "recorded_tool_events": len(events),
        "requirements_linked_to_located_claims_with_recorded_supported_review": len(covered & all_requirements),
        "uncovered_requirement_ids": sorted(all_requirements - covered),
        "semantic_success_count": None,
        "metric_limitation": "Coverage is structural and review-record based, never a correctness score; unknown/absent claims do not shrink the original denominator."
    })
    report.manual("requirements.completion", "requirements", "Independently assess each original requirement against acceptance criteria; linkage counts do not establish completion.")


def validate_page_map(aid, page, start, end, meta, artifacts, raw, verified, report, loc):
    map_id = meta.get("page_map_artifact_id") if isinstance(meta, dict) else None
    if map_id not in verified:
        report.warn("pdf.mapping_missing", loc, "Physical page is unverified: no retained, hash-verified page-map artifact.")
        return
    try:
        mapping = json.loads(raw[map_id].decode("utf-8"))
        if not isinstance(mapping, dict):
            raise ValueError("mapping must be an object")
        pdf_id = mapping.get("source_artifact_id")
        if pdf_id not in verified or b"%PDF-" not in raw[pdf_id][:1024]:
            raise ValueError("mapping must reference a hash-verified original PDF")
        if mapping.get("source_sha256") != digest(raw[pdf_id]):
            raise ValueError("original PDF hash does not match mapping")
        if mapping.get("text_artifact_id") != aid or mapping.get("text_sha256") != digest(raw.get(aid, b"")):
            raise ValueError("parsed text identity/hash does not match mapping")
        pages = mapping.get("pages")
        if not isinstance(pages, list):
            raise ValueError("pages must be an array")
        matching = [p for p in pages if isinstance(p, dict) and p.get("physical_pdf_page") == page
                    and integer(p.get("line_start")) and integer(p.get("line_end"))
                    and integer(start) and integer(end)
                    and 1 <= p["line_start"] <= start <= end <= p["line_end"]]
        if not matching:
            raise ValueError("no mapping entry covers the evidence lines at this physical page")
        report.passed("pdf.mapping_link", loc, "Page-map identity and line interval match retained PDF/text hashes.")
        report.manual("pdf.mapping_fidelity", loc, "Mapping provenance and actual PDF page fidelity still require inspecting parser output or page image; PDF bytes were not semantically parsed.")
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        report.error("pdf.mapping_invalid", loc, str(exc))


SCHEMA_ANNOTATIONS = {"$schema", "$id", "$anchor", "$comment", "$defs", "definitions", "title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly"}
SCHEMA_RULES = {"$ref", "type", "required", "properties", "additionalProperties", "items", "enum", "const", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minProperties", "maxProperties", "allOf", "anyOf", "oneOf", "not"}


def schema_check(value, schema, root=None, path="$", depth=0, active_refs=()):
    """Return violations and unsupported constraints, never silently ignore a rule."""
    errors, unsupported = [], []
    def fail(message): errors.append({"path": path, "message": message})
    def unknown(message): unsupported.append({"path": path, "message": message})
    def sub(v, s, p, refs=active_refs):
        e, u = schema_check(v, s, root, p, depth + 1, refs)
        errors.extend(e); unsupported.extend(u)
    if depth > 80:
        unknown("Schema recursion/depth exceeds supported bound."); return errors, unsupported
    if root is None: root = schema
    if schema is True: return errors, unsupported
    if schema is False: fail("Boolean false schema rejects the value."); return errors, unsupported
    if not isinstance(schema, dict):
        fail("Schema must be an object or boolean."); return errors, unsupported
    for key in schema:
        if key not in SCHEMA_RULES | SCHEMA_ANNOTATIONS:
            unknown("Unsupported schema keyword: " + key)
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not (reference == "#" or reference.startswith("#/")):
            unknown("Only local JSON Pointer $ref is supported.")
        elif (reference, path) in active_refs:
            unknown("Recursive reference at the same instance location.")
        else:
            try:
                target = root
                for part in reference[2:].split("/") if reference != "#" else []:
                    part = unquote(part).replace("~1", "/").replace("~0", "~")
                    target = target[int(part)] if isinstance(target, list) else target[part]
                sub(value, target, path, active_refs + ((reference, path),))
            except (KeyError, IndexError, TypeError, ValueError):
                fail("Unresolvable local $ref: " + reference)
    types = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
             "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
             "null": lambda v: v is None, "integer": lambda v: not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) and int(v) == v,
             "number": lambda v: not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)}
    if "type" in schema:
        wanted = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not wanted or any(not isinstance(t, str) or t not in types for t in wanted):
            fail("Invalid/unsupported type declaration.")
        elif not any(types[t](value) for t in wanted):
            fail("Value does not match declared type: " + repr(wanted))
    # JSON equality must distinguish booleans from numbers, unlike Python ==.
    def equal(a, b):
        if isinstance(a, bool) != isinstance(b, bool): return False
        if isinstance(a, dict) and isinstance(b, dict): return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
        if isinstance(a, list) and isinstance(b, list): return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
        return a == b
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]: fail("enum must be a nonempty array.")
        elif not any(equal(value, item) for item in schema["enum"]): fail("Value is not in enum.")
    if "const" in schema and not equal(value, schema["const"]): fail("Value differs from const.")
    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(k, str) for k in required): fail("required must be an array of field names.")
        else:
            for key in required:
                if key not in value: fail("Missing required field: " + key)
        props = schema.get("properties", {})
        if not isinstance(props, dict): fail("properties must be an object."); props = {}
        for key, child in props.items():
            if key in value: sub(value[key], child, path + "." + key)
        additional = schema.get("additionalProperties", True)
        for key in set(value) - set(props):
            if additional is False: fail("Unexpected property: " + key)
            elif isinstance(additional, dict): sub(value[key], additional, path + "." + key)
            elif additional is not True: fail("additionalProperties must be a schema or boolean.")
        for key, op in (("minProperties", lambda n, b: n < b), ("maxProperties", lambda n, b: n > b)):
            if key in schema:
                if not integer(schema[key]) or schema[key] < 0: fail(key + " must be a nonnegative integer.")
                elif op(len(value), schema[key]): fail(key + " violated.")
    if isinstance(value, list):
        if "items" in schema:
            if isinstance(schema["items"], (dict, bool)):
                for n, item in enumerate(value): sub(item, schema["items"], path + "[%d]" % n)
            else: unknown("Tuple/list-valued items is unsupported; use a schema for items.")
        for key, op in (("minItems", lambda n, b: n < b), ("maxItems", lambda n, b: n > b)):
            if key in schema:
                if not integer(schema[key]) or schema[key] < 0: fail(key + " must be a nonnegative integer.")
                elif op(len(value), schema[key]): fail(key + " violated.")
        if "uniqueItems" in schema:
            if not isinstance(schema["uniqueItems"], bool): fail("uniqueItems must be boolean.")
            elif schema["uniqueItems"] and any(equal(v, w) for n, v in enumerate(value) for w in value[:n]): fail("uniqueItems violated.")
    if isinstance(value, str):
        for key, op in (("minLength", lambda n, b: n < b), ("maxLength", lambda n, b: n > b)):
            if key in schema:
                if not integer(schema[key]) or schema[key] < 0: fail(key + " must be a nonnegative integer.")
                elif op(len(value), schema[key]): fail(key + " violated.")
        if "pattern" in schema:
            pattern = schema["pattern"]
            # Deliberately narrow syntax; Python and ECMA regular expressions differ.
            if not isinstance(pattern, str): fail("pattern must be a string.")
            elif len(pattern) > 200 or len(value) > 10000 or any(token in pattern for token in ("(", ")", "|", "\\p", "\\P", "\\u", "\\k")) or sum(pattern.count(c) for c in "*+{") > 1:
                unknown("Pattern uses an unsupported regular-expression feature or length.")
            else:
                try:
                    if not re.search(pattern, value): fail("pattern violated (supported Python-compatible subset).")
                except re.error as exc: unknown("Pattern cannot be evaluated: " + str(exc))
    if types["number"](value):
        for key, op in (("minimum", lambda v, b: v < b), ("maximum", lambda v, b: v > b),
                        ("exclusiveMinimum", lambda v, b: v <= b), ("exclusiveMaximum", lambda v, b: v >= b)):
            if key in schema:
                if not types["number"](schema[key]): unknown(key + " must use numeric bounds; older boolean form is unsupported.")
                elif op(value, schema[key]): fail(key + " violated.")
        if "multipleOf" in schema:
            unit = schema["multipleOf"]
            if not types["number"](unit) or unit <= 0: fail("multipleOf must be positive.")
            else:
                try:
                    if decimal.Decimal(str(value)) % decimal.Decimal(str(unit)) != 0: fail("multipleOf violated.")
                except decimal.DecimalException: unknown("multipleOf precision could not be checked.")
    for key in ("allOf", "anyOf", "oneOf"):
        if key not in schema: continue
        branches = schema[key]
        if not isinstance(branches, list) or not branches: fail(key + " requires a nonempty schema array."); continue
        outcomes = [schema_check(value, s, root, path, depth + 1, active_refs) for s in branches]
        if key == "allOf":
            for e, u in outcomes: errors.extend(e); unsupported.extend(u)
        else:
            certain = sum(not e and not u for e, u in outcomes)
            uncertain = sum(not e and bool(u) for e, u in outcomes)
            if key == "anyOf" and certain: continue
            if key == "oneOf" and certain > 1: fail("oneOf matched more than one branch."); continue
            if key == "oneOf" and certain == 1 and not uncertain: continue
            if uncertain: unknown(key + " cannot be decided because a potentially matching branch has unsupported rules.")
            else: fail(key + " matched no branch.")
    if "not" in schema:
        e, u = schema_check(value, schema["not"], root, path, depth + 1, active_refs)
        if not e and u: unknown("not cannot be decided because its child has unsupported rules.")
        elif not e: fail("not schema matched the value.")
    return errors, unsupported


def validate_result(case_path, result_path, work, report):
    case = read_json(case_path, report, "case")
    result = read_json(result_path, report, "result")
    if not isinstance(case, dict) or not case:
        report.error("case.object", "case", "Case must be a nonempty JSON object."); return False
    if not isinstance(result, dict) or not result:
        report.error("result.object", "result", "Result must be a nonempty JSON object."); return False
    cid = case.get("case_id")
    if not nonempty(cid): report.error("case.case_id", "case.case_id", "Case must supply its real case_id.")
    if not nonempty(result.get("case_id")) or result.get("case_id") != cid:
        report.error("result.case_id", "result.case_id", "Result case_id must equal the original case_id.")
    else: report.passed("result.case_id", "result.case_id", "Case identifier matches.")
    contract = case.get("output_contract")
    incomplete = False
    if isinstance(contract, dict) and ("json_schema" in contract or "schema" in contract):
        key = "json_schema" if "json_schema" in contract else "schema"
        if set(contract) - {key, "description", "title"}:
            report.manual("contract.wrapper", "output_contract", "Additional wrapper constraints need manual review."); incomplete = True
        contract = contract[key]
    recognizable = isinstance(contract, bool) or isinstance(contract, dict) and bool(set(contract) & SCHEMA_RULES)
    if not recognizable:
        report.warn("contract.unsupported", "output_contract", "Absent, empty, natural-language or nonstandard contract cannot be certified as JSON Schema.")
        report.manual("contract.manual", "output_contract", "Check every original output requirement manually; no schema-valid claim is made.")
        incomplete = True
    else:
        errors, unsupported = schema_check(result, contract)
        for item in errors: report.error("schema.violation", item["path"], item["message"])
        for item in unsupported: report.warn("schema.unsupported", item["path"], item["message"])
        incomplete = incomplete or bool(unsupported)
        if not errors and not unsupported: report.passed("schema.supported_subset", "result", "All applicable constraints in the supported JSON Schema subset passed.")
        if unsupported: report.manual("schema.manual", "output_contract", "Unsupported rules must be checked separately before claiming contract compliance.")
    if work:
        validate_state(work, report)
        state = read_json(Path(work) / "state.json", report, "state.json")
        if isinstance(state, dict) and state.get("case_id") != cid:
            report.error("result.state_case", "state.case_id", "Work ledger belongs to another case.")
    report.manual("result.scientific_review", "result", "Schema validity does not verify the answer, citations, uncertainty or fulfilment of research requirements.")
    report.data["metrics"]["contract_machine_check_complete"] = not incomplete
    return incomplete


def frontmatter(text, report):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        report.error("package.frontmatter", "SKILL.md", "SKILL.md must begin with YAML frontmatter."); return
    end = next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), None)
    if end is None:
        report.error("package.frontmatter", "SKILL.md", "Unclosed YAML frontmatter."); return
    values, i = {}, 1
    while i < end:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"): i += 1; continue
        match = re.match(r"^([A-Za-z_][\w-]*):(?:\s*(.*))?$", line)
        if not match:
            report.error("package.yaml_subset", "SKILL.md:%d" % (i + 1), "Unsupported YAML structure; use plain scalar name/description fields."); i += 1; continue
        key, value = match.group(1), match.group(2) or ""
        if key in values: report.error("package.yaml_duplicate", "SKILL.md", "Duplicate frontmatter key: " + key)
        if value in (">", ">-", ">+", "|", "|-", "|+"):
            pieces = []; i += 1
            while i < end and (not lines[i].strip() or lines[i].startswith(" ")):
                pieces.append(lines[i].strip()); i += 1
            values[key] = ("\n" if value.startswith("|") else " ").join(pieces).strip(); continue
        if value.startswith('"'):
            try: value = json.loads(value)
            except ValueError: report.error("package.yaml_quote", "SKILL.md", "Double-quoted field must use JSON-compatible escapes."); value = ""
        elif value.startswith("'"):
            if not value.endswith("'"): report.error("package.yaml_quote", "SKILL.md", "Unclosed single-quoted scalar."); value = ""
            else: value = value[1:-1].replace("''", "'")
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
            if value.startswith(("[", "{", "&", "*", "!")):
                report.error("package.yaml_subset", "SKILL.md", "Collection/tag/anchor syntax is not supported for frontmatter scalars.")
            if value.lower() in ("null", "~", "true", "false") or ": " in value:
                report.error("package.yaml_scalar", "SKILL.md", "Use a quoted string for reserved YAML values or a colon followed by a space.")
        values[key] = value; i += 1
    name, description = values.get("name"), values.get("description")
    if not nonempty(name) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
        report.error("package.name", "SKILL.md", "Skill name must be 1–64 lowercase alphanumeric/hyphen characters without edge/doubled hyphens.")
    if not nonempty(description): report.error("package.description", "SKILL.md", "A nonempty scalar description is required.")


def validate_package(path, report):
    supplied = Path(path)
    if supplied.is_symlink(): report.error("package.symlink", str(path), "Package root cannot be a symlink."); return
    root = supplied.resolve()
    if not root.is_dir(): report.error("package.directory", str(path), "Package path must be a directory."); return
    files, text_files, total = [], {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in list(dirs):
            p = Path(directory) / name
            if p.is_symlink(): report.error("package.symlink", str(p.relative_to(root)), "Symlink directories are forbidden."); dirs.remove(name)
        for name in names:
            p = Path(directory) / name; rel = str(p.relative_to(root)); files.append(rel)
            if p.is_symlink(): report.error("package.symlink", rel, "Symlink files are forbidden."); continue
            try:
                if not p.is_file(): raise ValueError("not a regular file")
                raw = p.read_bytes(); total += len(raw)
                if len(raw) > 500000: report.error("package.file_size", rel, "File exceeds 500000 bytes.")
                if b"\x00" in raw: raise ValueError("NUL byte indicates non-text content")
                text_files[rel] = raw.decode("utf-8")
            except (OSError, UnicodeError, ValueError) as exc: report.error("package.text", rel, "Every package file must be UTF-8 text: " + str(exc))
    if len(files) > 50: report.error("package.file_count", "$", "Package exceeds 50 files.")
    if total > 2000000: report.error("package.total_size", "$", "Package exceeds 2000000 bytes.")
    if "SKILL.md" not in text_files: report.error("package.skill", "SKILL.md", "Root SKILL.md is missing or unreadable.")
    else: frontmatter(text_files["SKILL.md"], report)
    checked = set()
    for rel, content in text_files.items():
        if not rel.lower().endswith(".md"): continue
        links = re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", content)
        links.extend(re.findall(r"`((?:scripts|references|templates)/[^`\s]+\.[A-Za-z0-9]+)`", content))
        for target in links:
            target = target.strip().strip("<>")
            if " \"" in target: target = target.split(" \"", 1)[0]
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path: continue
            decoded = unquote(parsed.path)
            key = (rel, decoded)
            if key in checked: continue
            checked.add(key)
            # Link resolution is relative to the Markdown file, not process cwd.
            candidate = confined(root, str(Path(rel).parent / decoded), report, rel + " -> " + decoded)
            if candidate is not None and not candidate.exists(): report.error("package.reference", rel, "Missing local resource: " + decoded)
    report.data["metrics"].update({"file_count": len(files), "total_bytes": total, "local_references_checked": len(checked), "limits": {"files": 50, "per_file_bytes": 500000, "total_bytes": 2000000}})
    report.manual("package.runtime", "$", "Static package checks do not prove runtime tool availability, task performance or correct activation.")


def main(argv=None):
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    state = commands.add_parser("state", help="Check WORK/state.json and retained artifacts")
    state.add_argument("--work", required=True); state.add_argument("--strict", action="store_true")
    result = commands.add_parser("result", help="Check case identity and original output contract")
    result.add_argument("--case", required=True); result.add_argument("--result", required=True); result.add_argument("--work")
    package = commands.add_parser("package", help="Check an uploadable Skill directory")
    package.add_argument("--path", required=True)
    try:
        args = parser.parse_args(argv)
    except ValueError as exc:
        report = Report("cli")
        report.error("cli.arguments", "$", str(exc))
        code = report.finish()
        print(json.dumps(report.data, ensure_ascii=False, indent=2))
        return code
    report = Report(args.mode); incomplete = False
    try:
        if args.mode == "state": validate_state(args.work, report, args.strict)
        elif args.mode == "result": incomplete = validate_result(args.case, args.result, args.work, report)
        else: validate_package(args.path, report)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError) as exc:
        report.error("validation.exception", "$", "Validation could not finish: %s: %s" % (type(exc).__name__, exc))
    code = report.finish(incomplete)
    print(json.dumps(report.data, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
