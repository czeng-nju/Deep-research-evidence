#!/usr/bin/env python3
"""Local research ledger. Python 3.9+, standard library, no network or model calls.

This records observations; it does not infer scientific truth or generate a case's
formal result. state.json is authoritative. JSONL logs are recoverable projections
of its immutable event/change history. Use one writer per work directory.
"""

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid


VERSION = "1.0"
KINDS = ("requirements", "questions", "actions", "sources", "evidence", "claims", "conflicts", "reviews")
TASK_STATUSES = {"planned", "ready", "running", "completed", "partial", "blocked"}
SUCCESS_STATUSES = {"success", "succeeded", "completed"}
SECRET_KEYS = {
    "authorization", "proxy_authorization", "api_key", "apikey", "x_api_key",
    "access_token", "refresh_token", "api_token", "bearer_token", "password",
    "passwd", "client_secret", "secret_key", "private_key", "cookie", "set_cookie",
    "secret", "credentials",
}


class LedgerError(ValueError):
    """An input, integrity or state error suitable for a JSON CLI response."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise LedgerError(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _obj_hash(obj):
    return _digest(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _read_json(path):
    def reject_constant(value):
        raise LedgerError("Non-finite JSON number: " + value)
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle, parse_constant=reject_constant)


def _atomic_write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, str(path))
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_write_json(path, obj):
    _atomic_write_bytes(path, _json_bytes(obj))


def load_state(work):
    state = _read_json(Path(work) / "state.json")
    if not isinstance(state, dict) or state.get("schema_version") != VERSION:
        raise LedgerError("Unsupported or missing ledger schema_version; expected " + VERSION)
    for kind in KINDS:
        if not isinstance(state.get(kind), list):
            raise LedgerError("State collection must be a list: " + kind)
    if not isinstance(state.get("artifacts"), dict) or not isinstance(state.get("metadata"), dict):
        raise LedgerError("State requires artifacts and metadata objects")
    _merge_budget_receipt(state, work)
    return state


def _explicit_request_lower_bound(state):
    budget = state["metadata"]["budget"]
    event_count = sum(event.get("model_request_count", 0)
                      for event in state["metadata"].get("events", []))
    observed = budget.get("observed_requests")
    return max(event_count, observed if observed is not None else 0,
               budget.get("durable_request_high_watermark", 0))


def _read_budget_receipt(work):
    try:
        receipt = _read_json(Path(work) / "budget_receipt.json")
        payload = receipt["payload"]
        if not isinstance(payload, dict):
            raise LedgerError("Budget receipt payload must be an object")
        if receipt.get("sha256") != _obj_hash(payload):
            raise LedgerError("Budget receipt integrity mismatch")
        if (payload.get("schema_version") != VERSION
                or not isinstance(payload.get("request_high_watermark"), int)
                or isinstance(payload["request_high_watermark"], bool)
                or payload["request_high_watermark"] < 0):
            raise LedgerError("Invalid budget receipt structure")
        if (not isinstance(payload.get("limit_requests"), int)
                or isinstance(payload["limit_requests"], bool) or payload["limit_requests"] <= 0
                or not isinstance(payload.get("limit_seconds"), (int, float))
                or isinstance(payload["limit_seconds"], bool)
                or not math.isfinite(payload["limit_seconds"]) or payload["limit_seconds"] <= 0):
            raise LedgerError("Invalid budget receipt limits")
        if not isinstance(payload.get("started_at"), str):
            raise LedgerError("Budget receipt start must be a timestamp string")
        parsed_start = datetime.fromisoformat(payload["started_at"].replace("Z", "+00:00"))
        if parsed_start.tzinfo is None:
            raise LedgerError("Budget receipt start must include its timezone")
        return payload, None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, str(exc)


def _merge_budget_receipt(state, work):
    """A checkpoint may restore content, but never roll the spend watermark back."""
    budget = state["metadata"]["budget"]
    receipt, error = _read_budget_receipt(work)
    if receipt is None or receipt.get("case_id") != state["case_id"]:
        budget["budget_uncertain"] = True
        budget["uncertainty_reason"] = error or "budget_receipt_case_mismatch"
        return False
    if receipt["started_at"] != budget["started_at"]:
        # Use the earliest known start; disagreement never grants extra time.
        starts = [receipt["started_at"], budget["started_at"]]
        budget["started_at"] = min(starts, key=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")))
        budget["budget_uncertain"] = True
        budget["uncertainty_reason"] = "budget_receipt_start_mismatch"
        return False
    watermark = max(receipt["request_high_watermark"], _explicit_request_lower_bound(state))
    budget["durable_request_high_watermark"] = watermark
    if budget.get("observed_requests") is not None or receipt.get("has_observation") or watermark:
        budget["observed_requests"] = max(budget.get("observed_requests") or 0, watermark)
        budget["counter_source"] = "durable_receipt_and_caller_observations_lower_bound"
    budget["limit_seconds"] = min(budget["limit_seconds"], receipt["limit_seconds"])
    budget["limit_requests"] = min(budget["limit_requests"], receipt["limit_requests"])
    # Uncertainty is sticky: a normal observation is not proof of a global count.
    if receipt.get("budget_uncertain"):
        budget["budget_uncertain"] = True
        budget["uncertainty_reason"] = receipt.get("uncertainty_reason", "receipt_marked_uncertain")
    return not budget.get("budget_uncertain", False)


def _persist_budget_receipt(state, work, initializing=False):
    budget = state["metadata"]["budget"]
    if not initializing and not _merge_budget_receipt(state, work):
        # Never replace a missing/damaged receipt with a checkpoint's old count.
        return
    watermark = _explicit_request_lower_bound(state)
    budget["durable_request_high_watermark"] = watermark
    payload = {"schema_version": VERSION, "case_id": state["case_id"],
               "started_at": budget["started_at"], "request_high_watermark": watermark,
               "has_observation": budget.get("observed_requests") is not None or watermark > 0,
               "limit_seconds": budget["limit_seconds"], "limit_requests": budget["limit_requests"],
               "budget_uncertain": bool(budget.get("budget_uncertain", False)),
               "updated_at": utc_now()}
    # Receipt commits before state: a crash can overcount conservatively, not refund calls.
    atomic_write_json(Path(work) / "budget_receipt.json", {"payload": payload, "sha256": _obj_hash(payload)})


def _safe_local_path(work, relative):
    if not isinstance(relative, str) or not relative:
        raise LedgerError("Missing retained artifact path")
    root = Path(work).resolve()
    path = Path(relative)
    if path.is_absolute():
        raise LedgerError("Retained path must be relative to work")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise LedgerError("Retained path escapes work directory")
    # Do not accept symlink substitutions, including those within the work tree.
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise LedgerError("Retained path contains a symlink")
    return resolved


def verify_artifacts(state, work):
    """Return hash checks without asserting scientific validity or mutating state."""
    result = {"checked_at": utc_now(), "ok": True, "valid": [], "invalid": []}
    for artifact_id, item in state.get("artifacts", {}).items():
        try:
            if not isinstance(item, dict):
                raise LedgerError("Artifact record is not an object")
            path = _safe_local_path(work, item.get("path"))
            if not path.is_file():
                raise LedgerError("Retained artifact is missing or not a regular file")
            observed = _digest(path.read_bytes())
            if observed != item.get("sha256"):
                result["invalid"].append({"id": artifact_id, "reason": "hash_mismatch",
                                          "expected_sha256": item.get("sha256"),
                                          "observed_sha256": observed})
            else:
                result["valid"].append(artifact_id)
        except (OSError, ValueError, TypeError) as exc:
            result["invalid"].append({"id": artifact_id, "reason": str(exc)})
    result["ok"] = not result["invalid"]
    return result


def _change(state, operation, record_id=None, before=None, after=None, details=None):
    history = state["metadata"].setdefault("changes", [])
    entry = {"id": "change:" + uuid.uuid4().hex, "recorded_at": utc_now(),
             "operation": operation, "record_id": record_id,
             "before_sha256": _obj_hash(before) if before is not None else None,
             "after_sha256": _obj_hash(after) if after is not None else None}
    if before is not None:
        entry["before"] = copy.deepcopy(before)
    if details is not None:
        entry["details"] = details
    history.append(entry)


def _save(state, work, initializing=False):
    work = Path(work)
    _persist_budget_receipt(state, work, initializing=initializing)
    state["metadata"]["updated_at"] = utc_now()
    atomic_write_json(work / "state.json", state)
    # Regenerating these views repairs a crash between state commit and log export.
    for filename, key in (("changes.jsonl", "changes"), ("events.jsonl", "events")):
        lines = [json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False)
                 for item in state["metadata"].get(key, [])]
        _atomic_write_bytes(work / filename, (("\n".join(lines) + "\n") if lines else "").encode("utf-8"))
    atomic_write_json(work / "progress.json", {
        "case_id": state["case_id"], "status": "partial",
        "updated_at": state["metadata"]["updated_at"],
        "counts": {kind: len(state[kind]) for kind in KINDS},
        "resume_status": state["metadata"].get("resume_status"),
        "limitation": "This is ledger progress, not the case's formal result or a scientific completion judgment.",
    })


def _identifier(value, label="id"):
    if not isinstance(value, str) or not value.strip():
        raise LedgerError(label + " must be a nonempty string")
    return value


def _record_list(value):
    items = value if isinstance(value, list) else [value]
    if not items or not all(isinstance(item, dict) for item in items):
        raise LedgerError("Expected one record object or a nonempty list of objects")
    ids = [_identifier(item.get("id")) for item in items]
    if len(ids) != len(set(ids)):
        raise LedgerError("Duplicate IDs in input batch")
    return items


def _secret_key(key):
    return str(key).strip().lower().replace("-", "_") in SECRET_KEYS


def _redact(value, path="", redactions=None):
    """Redact only explicit credential keys, never scientific 'token' fields."""
    redactions = redactions if redactions is not None else []
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child = path + "." + str(key)
            if _secret_key(key):
                result[key] = "[REDACTED]"
                redactions.append(child)
            else:
                result[key] = _redact(item, child, redactions)
        return result
    if isinstance(value, list):
        return [_redact(item, path + "[" + str(index) + "]", redactions)
                for index, item in enumerate(value)]
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        if any(_secret_key(key) for key, _ in pairs):
            clean = []
            for key, item in pairs:
                if _secret_key(key):
                    item = "[REDACTED]"
                    redactions.append(path + ".url_query." + key)
                clean.append((key, item))
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                               urlencode(clean), parsed.fragment))
    return value


def _task_record_check(item, kind):
    if kind in ("questions", "actions") and item.get("status", "planned") not in TASK_STATUSES:
        raise LedgerError("Invalid " + kind + " status for " + item["id"])
    if kind == "questions":
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise LedgerError("Question dependencies must be a list")
        for dep in dependencies:
            if not isinstance(dep, dict):
                raise LedgerError("Each dependency must be an object")
            _identifier(dep.get("question_id"), "dependency.question_id")
            if dep.get("type") not in ("hard", "soft"):
                raise LedgerError("Dependency type must be hard or soft")
            artifacts = dep.get("required_artifacts", [])
            if not isinstance(artifacts, list) or not all(isinstance(x, str) and x for x in artifacts):
                raise LedgerError("required_artifacts must be a list of artifact IDs")


def cmd_init(args):
    work = Path(args.work)
    if any((work / name).exists() for name in ("state.json", "manifest.json", "budget_receipt.json", "checkpoints")):
        raise LedgerError("Work directory already has a ledger; use resume or a new directory")
    if not math.isfinite(args.budget_seconds) or args.budget_seconds <= 0 or args.budget_requests <= 0:
        raise LedgerError("Budget limits must be positive")
    case_path = Path(args.case).resolve()
    case = _read_json(case_path)
    if not isinstance(case, dict):
        raise LedgerError("Case input must be a JSON object")
    case_id = _identifier(case.get("case_id"), "case_id in actual case input")
    # Read every supplied input before mutating the work directory.
    pending = [("case", case_path, case_path.read_bytes())]
    for label, provided in (("task", args.task), ("resources", args.resources)):
        if provided:
            original = Path(provided).resolve()
            pending.append((label, original, original.read_bytes()))
    started = utc_now()
    inputs = {}
    for label, original, data in pending:
        suffix = original.suffix if len(original.suffix) < 16 else ".bin"
        relative = "inputs/" + label + suffix
        _atomic_write_bytes(work / relative, data)
        inputs[label] = {"path": str(original), "sha256": _digest(data),
                         "snapshot": relative, "bytes": len(data)}
    state = {"schema_version": VERSION, "case_id": case_id,
             "input_contract": case.get("output_contract"),
             **{kind: [] for kind in KINDS}, "artifacts": {},
             "metadata": {"created_at": started, "updated_at": started, "inputs": inputs,
                          "output_contract_observed": "output_contract" in case,
                          "events": [], "changes": [], "cache": {}, "checkpoints": [],
                          "budget": {"started_at": started,
                                     "limit_seconds": args.budget_seconds,
                                     "limit_requests": args.budget_requests,
                                     "observed_requests": None,
                                     "counter_source": "unknown",
                                     "request_count_is_global_total": False}}}
    _change(state, "init", details={"input_labels": list(inputs)})
    _save(state, work, initializing=True)
    manifest = {"schema_version": VERSION, "case_id": case_id, "inputs": inputs,
                "input_contract": state["input_contract"],
                "output_contract_observed": state["metadata"]["output_contract_observed"],
                "started_at": started,
                "budget_limits": {"seconds": args.budget_seconds, "requests": args.budget_requests}}
    atomic_write_json(work / "manifest.json", manifest)
    return {"ok": True, "case_id": case_id, "state_path": str((work / "state.json").resolve()),
            "progress_status": "partial", "output_contract_observed": "output_contract" in case,
            "formal_output_written": False}


def cmd_put(args):
    state = load_state(args.work)
    items = _record_list(_read_json(args.file))
    for item in items:
        _task_record_check(item, args.kind)
    collection = state[args.kind]
    index = {item["id"]: pos for pos, item in enumerate(collection)}
    changed = []
    modified_existing = set()
    for item in items:
        # Plans can contain credential-bearing tool parameters as well as events.
        redactions = []
        item = copy.deepcopy(item)
        for key in ("parameters", "exact_parameters", "headers"):
            if key in item:
                item[key] = _redact(item[key], key, redactions)
        if redactions:
            item["parameter_redactions"] = redactions
        previous = collection[index[item["id"]]] if item["id"] in index else None
        if previous == item:
            continue
        _change(state, "put:" + args.kind, item["id"], previous, item)
        if previous is not None:
            # Updating a review bookkeeping flag is not a changed proposition.
            ignored = {"review_status", "reviewed_at", "validity", "invalid_reasons",
                       "status_before_invalidation", "status", "stop_reason", "updated_at",
                       "created_at", "started_at", "finished_at", "completed_at", "recorded_at",
                       "attempt", "attempts", "error", "retry_decision", "parameter_redactions"}
            if args.kind == "actions":
                # Produced outputs are execution bookkeeping; inputs remain semantic.
                ignored.update({"artifact_ids", "output_artifacts", "result", "result_id", "output"})
            before_content = {k: v for k, v in previous.items() if k not in ignored}
            after_content = {k: v for k, v in item.items() if k not in ignored}
            if before_content != after_content:
                modified_existing.add(item["id"])
            collection[index[item["id"]]] = item
        else:
            index[item["id"]] = len(collection)
            collection.append(item)
        changed.append(item["id"])
    invalidated = []
    if args.kind in ("requirements", "questions", "actions") and modified_existing:
        invalidated.extend(_invalidate_context_edit(state, args.kind, modified_existing))
    changed_evidence = set(modified_existing) if args.kind == "evidence" else set()
    if args.kind == "sources":
        for evidence in state["evidence"]:
            if evidence.get("source_id") in modified_existing:
                changed_evidence.add(evidence["id"])
                _invalidate(state, "evidence", evidence, "source_changed_requires_reextraction", invalidated)
        invalidated.extend(_propagate_invalidation(state, set(), False))
    affected_claims = set(modified_existing) if args.kind == "claims" else set()
    for claim in state["claims"]:
        if _evidence_references(claim) & changed_evidence:
            affected_claims.add(claim["id"])
    if affected_claims:
        _mark_reviews_stale(state, affected_claims,
                            args.kind + "_changed_after_review", invalidated)
    _save(state, args.work)
    return {"ok": True, "kind": args.kind, "changed_ids": changed, "count": len(collection),
            "claims_requiring_review": sorted(affected_claims), "invalidated_records": invalidated}


def cmd_artifact(args):
    state = load_state(args.work)
    artifact_id = _identifier(args.id)
    original = Path(args.path).resolve()
    if not original.is_file():
        raise LedgerError("Artifact input is not a regular file")
    data = original.read_bytes()
    sha = _digest(data)
    suffix = original.suffix if len(original.suffix) < 16 else ".bin"
    relative = "artifacts/" + _digest(artifact_id.encode("utf-8"))[:16] + "-" + sha[:16] + suffix
    old = state["artifacts"].get(artifact_id)
    if old and old.get("sha256") != sha:
        raise LedgerError("Artifact IDs are immutable; register changed bytes under a new ID")
    _atomic_write_bytes(Path(args.work) / relative, data)
    artifact = {"id": artifact_id, "path": relative, "sha256": sha,
                "bytes": len(data), "original_path": str(original),
                "registered_at": old.get("registered_at", utc_now()) if old else utc_now()}
    if args.source_id:
        artifact["source_id"] = args.source_id
    elif old and old.get("source_id"):
        artifact["source_id"] = old["source_id"]
    state["artifacts"][artifact_id] = artifact
    _change(state, "artifact", artifact_id, old, artifact)
    _save(state, args.work)
    return {"ok": True, "artifact": artifact}


def cmd_event(args):
    state = load_state(args.work)
    event = _read_json(args.file)
    if not isinstance(event, dict):
        raise LedgerError("Tool event must be an object")
    for key in ("id", "action_id", "tool", "status"):
        _identifier(event.get(key), "event." + key)
    if not isinstance(event.get("attempt"), int) or isinstance(event["attempt"], bool) or event["attempt"] < 1:
        raise LedgerError("event.attempt must be an integer >= 1")
    if "parameters" not in event and "exact_parameters" not in event:
        raise LedgerError("event requires actual parameters or exact_parameters")
    for key in ("input_artifacts", "output_artifacts"):
        if key not in event or not isinstance(event[key], list) or not all(isinstance(x, str) for x in event[key]):
            raise LedgerError("event." + key + " must explicitly list artifact IDs (or [])")
    if "model_request_count" in event and (
        not isinstance(event["model_request_count"], int)
        or isinstance(event["model_request_count"], bool) or event["model_request_count"] < 0
    ):
        raise LedgerError("model_request_count must be an observed nonnegative integer")
    redactions = []
    event = _redact(event, "event", redactions)
    if redactions:
        event["redactions"] = redactions
    events = state["metadata"].setdefault("events", [])
    for existing in events:
        if existing["id"] == event["id"]:
            comparable = {key: value for key, value in existing.items() if key != "recorded_at"}
            if comparable == event:
                return {"ok": True, "id": event["id"], "already_recorded": True}
            raise LedgerError("Event ID already exists with different content")
    event["recorded_at"] = utc_now()  # Receipt time, never a fabricated tool execution timestamp.
    events.append(event)
    _change(state, "event", event["id"], after=event)
    _save(state, args.work)
    return {"ok": True, "id": event["id"], "redactions": redactions,
            "note": "Recorded supplied observation; the helper did not execute this tool."}


def _budget_snapshot(state):
    budget = state["metadata"]["budget"]
    started = datetime.fromisoformat(budget["started_at"].replace("Z", "+00:00"))
    raw_elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    elapsed = max(0.0, raw_elapsed)
    # Only explicit counts attached to recorded real observations are counted.
    event_count = sum(event.get("model_request_count", 0)
                      for event in state["metadata"].get("events", []))
    observation = budget.get("observed_requests")
    lower_bound = _explicit_request_lower_bound(state)
    limit = budget["limit_requests"]
    reasons = []
    if elapsed >= budget["limit_seconds"]:
        reasons.append("time_limit_reached")
    if lower_bound >= limit:
        reasons.append("observed_request_lower_bound_reached_limit")
    uncertain = bool(budget.get("budget_uncertain", False))
    return {"started_at": budget["started_at"], "checked_at": utc_now(),
            "limit_seconds": budget["limit_seconds"], "limit_requests": limit,
            "elapsed_seconds": round(elapsed, 3),
            "remaining_seconds": max(0.0, round(budget["limit_seconds"] - elapsed, 3)),
            "clock_moved_backwards": raw_elapsed < 0,
            "observed_requests": observation, "explicit_event_requests": event_count,
            "requests_lower_bound": lower_bound,
            "request_count_status": "unknown" if observation is None and event_count == 0 else "observed_lower_bound",
            "request_count_is_global_total": False,
            "counter_source": budget.get("counter_source", "unknown"),
            "durable_request_high_watermark": budget.get("durable_request_high_watermark"),
            "budget_uncertain": uncertain, "uncertainty_reason": budget.get("uncertainty_reason"),
            "new_work_blocked": uncertain or bool(reasons) or raw_elapsed < 0,
            "remaining_requests_upper_bound": None if uncertain else max(0, limit - lower_bound),
            "exhausted": bool(reasons), "stop_reasons": reasons,
            "limitation": "Unobserved host/model calls may exist. An upper bound on remaining requests is not permission to spend it."}


def cmd_budget(args):
    state = load_state(args.work)
    if args.observed_requests is not None:
        if args.observed_requests < 0:
            raise LedgerError("observed-requests must be nonnegative")
        old = copy.deepcopy(state["metadata"]["budget"])
        previous = old.get("observed_requests")
        if previous is not None and args.observed_requests < previous:
            raise LedgerError("Observed request count cannot decrease across resumes")
        state["metadata"]["budget"].update({"observed_requests": args.observed_requests,
                                             "counter_source": "caller_observed_lower_bound",
                                             "observed_at": utc_now()})
        _change(state, "budget_observation", before=old, after=state["metadata"]["budget"])
        _save(state, args.work)
    return {"ok": True, **_budget_snapshot(state)}


def _question_graph(state):
    questions = state["questions"]
    mapping = {question["id"]: question for question in questions}
    if len(mapping) != len(questions):
        raise LedgerError("Duplicate question IDs")
    for question in questions:
        _task_record_check(question, "questions")
        seen = set()
        for dep in question.get("dependencies", []):
            target = dep["question_id"]
            if target not in mapping or target == question["id"]:
                raise LedgerError("Missing or self dependency: " + question["id"] + " -> " + target)
            if target in seen:
                raise LedgerError("Duplicate dependency in " + question["id"])
            seen.add(target)
    visiting, visited = set(), set()

    def visit(question_id):
        if question_id in visiting:
            raise LedgerError("Question dependency graph contains a cycle")
        if question_id in visited:
            return
        visiting.add(question_id)
        for dep in mapping[question_id].get("dependencies", []):
            if dep["type"] == "hard":
                visit(dep["question_id"])
        visiting.remove(question_id)
        visited.add(question_id)

    for question_id in mapping:
        visit(question_id)
    return mapping


def cmd_ready(args):
    state = load_state(args.work)
    mapping = _question_graph(state)
    verification = verify_artifacts(state, args.work)
    valid = set(verification["valid"])
    input_check = _input_verification(state, args.work)
    budget = _budget_snapshot(state)
    ready, waiting, inactive = [], [], []
    for question in state["questions"]:
        qid = question["id"]
        if question.get("status", "planned") not in ("planned", "ready"):
            inactive.append({"id": qid, "status": question.get("status")})
            continue
        reasons = []
        if budget["new_work_blocked"]:
            reasons.append("budget_exhausted_or_clock_uncertain")
        if budget["budget_uncertain"]:
            reasons.append("budget_receipt_untrusted_new_work_blocked")
        if question.get("validity") == "invalid":
            reasons.append("question_invalidated")
        if not input_check["ok"] or state["metadata"].get("resume_status") == "input_changed":
            reasons.append("original_input_changed")
        for dep in question.get("dependencies", []):
            if dep["type"] != "hard":
                continue
            predecessor = mapping[dep["question_id"]]
            if predecessor.get("status") != "completed" or predecessor.get("validity") == "invalid":
                reasons.append("dependency_not_validly_completed:" + dep["question_id"])
            for artifact_id in dep.get("required_artifacts", []):
                if artifact_id not in valid:
                    reasons.append("required_artifact_invalid_or_missing:" + artifact_id)
        if reasons:
            waiting.append({"id": qid, "reasons": reasons})
        else:
            ready.append(qid)
    return {"ok": True, "ready": ready, "waiting": waiting, "inactive": inactive,
            "artifact_verification": verification, "input_verification": input_check,
            "note": "Readiness is structural only; completed statuses are caller assertions, not scientific acceptance."}


def _cache_key(tool, parameters, scope):
    # JSON object keys are canonicalized; query words, filters and list order remain exact.
    return _obj_hash({"version": VERSION, "tool": tool, "parameters": parameters, "scope": scope})


def _key_from_args(args):
    if getattr(args, "key", None):
        if any(getattr(args, key, None) for key in ("tool", "parameters", "scope")):
            raise LedgerError("Use either --key or all of --tool/--parameters/--scope")
        return _identifier(args.key, "cache key")
    if not all(getattr(args, key, None) for key in ("tool", "parameters", "scope")):
        raise LedgerError("Cache key requires --tool, --parameters JSONFILE and --scope JSONFILE")
    return _cache_key(args.tool, _read_json(args.parameters), _read_json(args.scope))


def _artifact_ids_argument(value):
    if value is None:
        return None
    if value.startswith("["):
        parsed = json.loads(value)
    elif Path(value).is_file():
        parsed = _read_json(value)
    else:
        parsed = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise LedgerError("Artifact IDs must be a JSON list or comma-separated IDs")
    return parsed


def cmd_cache_put(args):
    state = load_state(args.work)
    if not _input_verification(state, args.work)["ok"]:
        raise LedgerError("Cannot cache results against changed or unavailable original inputs")
    key = _key_from_args(args)
    result = _read_json(args.file)
    if not isinstance(result, dict) or result.get("status") not in SUCCESS_STATUSES:
        raise LedgerError("Cache accepts only explicit success/succeeded/completed result objects")
    artifact_ids = _artifact_ids_argument(args.artifact_ids)
    if artifact_ids is None:
        artifact_ids = result.get("artifact_ids", result.get("output_artifacts"))
    if not isinstance(artifact_ids, list) or not artifact_ids or not all(isinstance(x, str) for x in artifact_ids):
        raise LedgerError("A reusable cache result must reference at least one retained artifact")
    verification = verify_artifacts(state, args.work)
    if not set(artifact_ids).issubset(set(verification["valid"])):
        raise LedgerError("Cannot cache missing or hash-invalid artifacts")
    redactions = []
    result = _redact(result, "result", redactions)
    entry = {"key": key, "status": "success", "stored_at": utc_now(), "result": result,
             "artifact_ids": list(dict.fromkeys(artifact_ids)),
             "artifact_hashes": {item: state["artifacts"][item]["sha256"] for item in artifact_ids},
             "input_fingerprint": _obj_hash(state["metadata"]["inputs"]), "redactions": redactions}
    old = state["metadata"].setdefault("cache", {}).get(key)
    state["metadata"]["cache"][key] = entry
    _change(state, "cache_put", key, old, entry)
    _save(state, args.work)
    return {"ok": True, "key": key, "artifact_ids": entry["artifact_ids"]}


def cmd_cache_get(args):
    state = load_state(args.work)
    key = _key_from_args(args)
    entry = state["metadata"].get("cache", {}).get(key)
    if not entry:
        return {"ok": True, "hit": False, "key": key, "reason": "not_found"}
    checks = verify_artifacts(state, args.work)
    valid = set(checks["valid"])
    reasons = []
    if not _input_verification(state, args.work)["ok"]:
        reasons.append("original_input_changed_or_unavailable")
    if entry.get("status") != "success":
        reasons.append("entry_not_successful")
    if state["metadata"].get("resume_status") == "input_changed":
        reasons.append("original_input_changed")
    if entry.get("input_fingerprint") != _obj_hash(state["metadata"]["inputs"]):
        reasons.append("input_fingerprint_changed")
    for artifact_id in entry.get("artifact_ids", []):
        if artifact_id not in valid:
            reasons.append("artifact_invalid_or_missing:" + artifact_id)
        elif state["artifacts"][artifact_id]["sha256"] != entry.get("artifact_hashes", {}).get(artifact_id):
            reasons.append("artifact_registration_changed:" + artifact_id)
    if not entry.get("artifact_ids"):
        reasons.append("no_retained_artifact")
    if reasons:
        return {"ok": True, "hit": False, "key": key, "reason": "invalidated", "details": reasons}
    return {"ok": True, "hit": True, "key": key, "entry": entry,
            "note": "Cache reuse is not a new external acquisition or independent evidence."}


def cmd_checkpoint(args):
    state = load_state(args.work)
    verification = verify_artifacts(state, args.work)
    inputs = _input_verification(state, args.work)
    checkpoint_id = "checkpoint:" + uuid.uuid4().hex
    relative = "checkpoints/" + checkpoint_id.replace(":", "-") + ".json"
    payload = _json_bytes(state)
    _atomic_write_bytes(Path(args.work) / relative, payload)
    entry = {"id": checkpoint_id, "path": relative, "sha256": _digest(payload),
             "created_at": utc_now(), "artifacts_ok": verification["ok"], "inputs_ok": inputs["ok"]}
    state["metadata"].setdefault("checkpoints", []).append(entry)
    _change(state, "checkpoint", checkpoint_id, after=entry)
    _save(state, args.work)
    atomic_write_json(Path(args.work) / "checkpoints" / "latest.json", entry)
    return {"ok": verification["ok"] and inputs["ok"], "checkpoint": entry,
            "artifact_verification": verification, "input_verification": inputs}


def _input_verification(state, work):
    errors = []
    for label, item in state["metadata"]["inputs"].items():
        for role in ("original", "snapshot"):
            try:
                path = (Path(item["path"]) if role == "original"
                        else _safe_local_path(work, item["snapshot"]))
                observed = _digest(path.read_bytes())
                if observed != item["sha256"]:
                    errors.append({"input": label, "role": role, "reason": "hash_mismatch"})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append({"input": label, "role": role, "reason": str(exc)})
    return {"ok": not errors, "errors": errors}


def _artifact_references(record):
    values = set()
    for key in ("artifact_id", "raw_artifact_id", "parsed_artifact_id"):
        if isinstance(record.get(key), str):
            values.add(record[key])
    for key in ("artifact_ids", "input_artifacts", "output_artifacts", "required_artifacts"):
        if isinstance(record.get(key), list):
            values.update(item for item in record[key] if isinstance(item, str))
    if isinstance(record.get("locator"), dict):
        values.update(_artifact_references(record["locator"]))
    return values


def _evidence_references(record):
    values = set()
    for key in ("evidence_ids", "supporting_evidence_ids", "opposing_evidence_ids"):
        if isinstance(record.get(key), list):
            values.update(value for value in record[key] if isinstance(value, str))
    for key in ("evidence_id_a", "evidence_id_b"):
        if isinstance(record.get(key), str):
            values.add(record[key])
    return values


def _mark_reviews_stale(state, claim_ids, reason, invalidated):
    for claim in state["claims"]:
        if claim["id"] in claim_ids and claim.get("review_status") != "unreviewed":
            before = copy.deepcopy(claim)
            claim["review_status"] = "unreviewed"
            _change(state, "review_required:claims", claim["id"], before, claim,
                    {"reason": reason})
    for review in state["reviews"]:
        if review.get("claim_id") in claim_ids:
            _invalidate(state, "reviews", review, reason, invalidated)


def _invalidate_context_edit(state, kind, modified_ids):
    """Conservative invalidation after a question/scope/tool-input revision."""
    invalidated = []
    question_ids = set(modified_ids) if kind == "questions" else set()
    action_ids = set(modified_ids) if kind == "actions" else set()
    if kind == "requirements":
        for question in state["questions"]:
            if set(question.get("requirement_ids", [])) & modified_ids:
                question_ids.add(question["id"])
        for requirement in state["requirements"]:
            if requirement["id"] in modified_ids:
                question_ids.update(requirement.get("question_ids", []))
    if kind == "actions":
        for action in state["actions"]:
            if action["id"] in modified_ids:
                question_ids.update(action.get("question_ids", []))
    # Hard downstream questions inherit the changed scientific context.
    while True:
        additions = {question["id"] for question in state["questions"]
                     if any(dep.get("type") == "hard" and dep.get("question_id") in question_ids
                            for dep in question.get("dependencies", []))}
        if additions.issubset(question_ids):
            break
        question_ids.update(additions)
    for action in state["actions"]:
        if set(action.get("question_ids", [])) & question_ids:
            action_ids.add(action["id"])
    affected_events = {event["id"] for event in state["metadata"].get("events", [])
                       if event.get("action_id") in action_ids}
    evidence_ids = set()
    for evidence in state["evidence"]:
        if (set(evidence.get("question_ids", [])) & question_ids
                or evidence.get("action_id") in action_ids
                or evidence.get("extraction_event_id") in affected_events):
            evidence_ids.add(evidence["id"])
    claim_ids = {claim["id"] for claim in state["claims"]
                 if set(claim.get("question_ids", [])) & question_ids
                 or _evidence_references(claim) & evidence_ids}
    targets = {"questions": question_ids, "actions": action_ids,
               "evidence": evidence_ids, "claims": claim_ids}
    reason = kind + "_semantic_context_changed"
    for collection, ids in targets.items():
        for record in state[collection]:
            if record["id"] in ids:
                _invalidate(state, collection, record, reason, invalidated)
    for conflict in state["conflicts"]:
        if (_evidence_references(conflict) & evidence_ids
                or conflict.get("claim_id") in claim_ids
                or conflict.get("question_id") in question_ids
                or set(conflict.get("question_ids", [])) & question_ids):
            _invalidate(state, "conflicts", conflict, reason, invalidated)
    _mark_reviews_stale(state, claim_ids, reason, invalidated)
    invalidated.extend(_propagate_invalidation(state, set(), False))
    # An opaque manually supplied cache key may omit the question context. Unless
    # that dependency can be proven, retire all entries after a semantic edit.
    state["metadata"]["context_revision"] = state["metadata"].get("context_revision", 0) + 1
    for key, entry in state["metadata"].get("cache", {}).items():
        if entry.get("status") == "success":
            before = copy.deepcopy(entry)
            entry["status"] = "invalidated"
            entry["invalid_reason"] = reason
            _change(state, "invalidate:cache", key, before, entry)
            invalidated.append({"kind": "cache", "id": key, "reason": reason})
    return invalidated


def _invalidate(state, kind, item, reason, invalidated):
    reasons = list(item.get("invalid_reasons", []))
    if item.get("validity") == "invalid" and reason in reasons:
        return False
    before = copy.deepcopy(item)
    item["validity"] = "invalid"
    if reason not in reasons:
        reasons.append(reason)
    item["invalid_reasons"] = reasons
    if kind in ("questions", "actions"):
        item.setdefault("status_before_invalidation", item.get("status", "planned"))
        item["status"] = "blocked"
    _change(state, "invalidate:" + kind, item["id"], before, item)
    invalidated.append({"kind": kind, "id": item["id"], "reason": reason})
    return True


def _propagate_invalidation(state, bad_artifacts, changed_inputs):
    invalidated = []
    # Preserve invalidity until a caller explicitly replaces/revalidates a record.
    invalid = {kind: {item["id"] for item in state[kind] if item.get("validity") == "invalid"}
               for kind in KINDS}
    bad_source_links = {item.get("source_id") for aid, item in state["artifacts"].items()
                        if aid in bad_artifacts and item.get("source_id")}
    while True:
        changed = False
        for kind in KINDS:
            for item in state[kind]:
                reasons = []
                if changed_inputs:
                    reasons.append("original_input_changed_or_unavailable")
                if _artifact_references(item) & bad_artifacts:
                    reasons.append("referenced_artifact_invalid")
                if kind == "sources" and item["id"] in bad_source_links:
                    reasons.append("registered_source_artifact_invalid")
                if item.get("source_id") in invalid["sources"]:
                    reasons.append("source_invalidated")
                if _evidence_references(item) & invalid["evidence"]:
                    reasons.append("evidence_invalidated")
                if item.get("claim_id") in invalid["claims"]:
                    reasons.append("claim_invalidated")
                if kind == "questions":
                    for dep in item.get("dependencies", []):
                        if dep.get("type") == "hard" and (
                            dep.get("question_id") in invalid["questions"]
                            or set(dep.get("required_artifacts", [])) & bad_artifacts
                        ):
                            reasons.append("hard_dependency_invalidated")
                    if any(item["id"] in evidence.get("question_ids", [])
                           for evidence in state["evidence"] if evidence["id"] in invalid["evidence"]):
                        reasons.append("supporting_evidence_requires_reassessment")
                if kind == "actions" and set(item.get("question_ids", [])) & invalid["questions"]:
                    reasons.append("question_invalidated")
                for reason in set(reasons):
                    changed |= _invalidate(state, kind, item, reason, invalidated)
                    invalid[kind].add(item["id"])
        if not changed:
            break
    _mark_reviews_stale(state, invalid["claims"], "claim_or_support_invalidated", invalidated)
    return invalidated


def _archive_before_recovery(work):
    """Preserve exact surviving bytes before checkpoint projections replace them."""
    work = Path(work)
    relative = "recovery/" + uuid.uuid4().hex
    archived = []
    for name in ("state.json", "events.jsonl", "changes.jsonl", "budget_receipt.json",
                 "manifest.json", "progress.json"):
        source = work / name
        if source.is_file():
            data = source.read_bytes()
            target = relative + "/" + name
            _atomic_write_bytes(work / target, data)
            archived.append({"original": name, "path": target,
                             "bytes": len(data), "sha256": _digest(data)})
    result = {"path": relative, "created_at": utc_now(), "files": archived,
              "warning": "Checkpoint recovery may omit later state changes. Archived logs are evidence for manual review, not an automatically reconstructed replay."}
    atomic_write_json(work / relative / "archive_manifest.json", result)
    return result


def cmd_resume(args):
    restored = None
    recovery_archive = None
    try:
        state = load_state(args.work)
    except (OSError, ValueError):
        recovery_archive = _archive_before_recovery(args.work)
        pointer = _read_json(Path(args.work) / "checkpoints" / "latest.json")
        path = _safe_local_path(args.work, pointer.get("path"))
        payload = path.read_bytes()
        if _digest(payload) != pointer.get("sha256"):
            raise LedgerError("State unavailable and latest checkpoint hash is invalid")
        state = json.loads(payload)
        if state.get("schema_version") != VERSION:
            raise LedgerError("Checkpoint schema version mismatch")
        restored = pointer["id"]
        _merge_budget_receipt(state, args.work)
        state["metadata"].setdefault("recoveries", []).append({
            "checkpoint_id": restored, "archive": recovery_archive,
            "post_checkpoint_state_may_be_missing": True,
        })
    inputs = _input_verification(state, args.work)
    artifacts = verify_artifacts(state, args.work)
    invalidated = _propagate_invalidation(state, {item["id"] for item in artifacts["invalid"]}, not inputs["ok"])
    state["metadata"]["resume_status"] = ("input_changed" if not inputs["ok"]
                                               else "artifacts_invalid" if not artifacts["ok"]
                                               else "budget_uncertain" if state["metadata"]["budget"].get("budget_uncertain")
                                               else "checkpoint_recovered_partial" if restored
                                               else "verified")
    state["metadata"]["last_resume_at"] = utc_now()
    _change(state, "resume", details={"input_check": inputs, "artifact_check": artifacts,
                                      "restored_from_checkpoint": restored,
                                      "recovery_archive": recovery_archive,
                                      "invalidated_records": invalidated})
    _save(state, args.work)
    budget = _budget_snapshot(state)
    return {"ok": inputs["ok"] and artifacts["ok"] and not budget["budget_uncertain"],
            "resume_status": state["metadata"]["resume_status"],
            "restored_from_checkpoint": restored, "input_verification": inputs,
            "artifact_verification": artifacts, "invalidated_records": invalidated,
            "budget": budget, "recovery_archive": recovery_archive,
            "post_checkpoint_state_may_be_missing": restored is not None,
            "recovery_warning": recovery_archive["warning"] if recovery_archive else None,
            "note": "Invalidated records remain invalid until explicitly replaced and reassessed; no scientific validity is inferred."}


def build_parser():
    parser = JsonArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Read actual input and initialize a separate partial ledger")
    init.add_argument("--case", required=True)
    init.add_argument("--work", required=True)
    init.add_argument("--task")
    init.add_argument("--resources")
    init.add_argument("--budget-seconds", type=float, default=900)
    init.add_argument("--budget-requests", type=int, default=64)
    init.set_defaults(handler=cmd_init)
    for name, help_text, handler in (
        ("show", "Show the current ledger", lambda args: {"ok": True, "state": load_state(args.work)}),
        ("ready", "List structurally ready question IDs; never execute them", cmd_ready),
        ("checkpoint", "Save an atomic ledger checkpoint", cmd_checkpoint),
        ("resume", "Verify original input and artifacts; propagate invalidation", cmd_resume),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--work", required=True)
        command.set_defaults(handler=handler)
    put = commands.add_parser("put", help="Upsert one or more records by stable id")
    put.add_argument("--work", required=True)
    put.add_argument("--kind", choices=KINDS, required=True)
    put.add_argument("--file", required=True)
    put.set_defaults(handler=cmd_put)
    artifact = commands.add_parser("artifact", help="Retain immutable bytes and SHA-256 under an artifact ID")
    artifact.add_argument("--work", required=True)
    artifact.add_argument("--id", required=True)
    artifact.add_argument("--path", required=True)
    artifact.add_argument("--source-id")
    artifact.set_defaults(handler=cmd_artifact)
    event = commands.add_parser("event", help="Append a supplied real tool observation, redacting explicit secrets")
    event.add_argument("--work", required=True)
    event.add_argument("--file", required=True)
    event.set_defaults(handler=cmd_event)
    for name, handler in (("cache-key", lambda args: {"ok": True, "key": _key_from_args(args)}),
                          ("cache-get", cmd_cache_get), ("cache-put", cmd_cache_put)):
        command = commands.add_parser(name, help="Canonical exact-parameter cache key or verified successful result")
        if name != "cache-key":
            command.add_argument("--work", required=True)
            command.add_argument("--key")
        command.add_argument("--tool")
        command.add_argument("--parameters", help="JSON file containing exact tool parameters")
        command.add_argument("--scope", help="JSON file containing scope/version/filter constraints")
        if name == "cache-put":
            command.add_argument("--file", required=True, help="JSON success result object")
            command.add_argument("--artifact-ids", help="JSON list/file or comma-separated retained artifact IDs")
        command.set_defaults(handler=handler)
    budget = commands.add_parser("budget", help="Observe wall time and an honest request-count lower bound")
    budget.add_argument("--work", required=True)
    budget.add_argument("--observed-requests", type=int)
    budget.set_defaults(handler=cmd_budget)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        result = args.handler(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0 if result.get("ok", True) else 2
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        # Do not echo parameters or source contents in error output.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": str(exc)},
                         ensure_ascii=False, allow_nan=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
