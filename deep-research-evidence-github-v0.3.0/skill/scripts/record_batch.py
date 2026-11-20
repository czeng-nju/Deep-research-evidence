#!/usr/bin/env python3
"""Run a serial batch of ledger commands, not research/model/tool calls.

Python 3.9+, standard library. One writer per work directory. Completed steps
commit individually; the batch is not a transaction. Keep its receipts when
moving or recovering a ledger.
"""

import argparse
import copy
import json
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
import research_state as ledger


ALLOWED = {
    "put": ({"kind", "records"}, {"kind", "records"}),
    "artifact": ({"id", "path", "source_id"}, {"id", "path"}),
    "event": ({"event"}, {"event"}),
    "checkpoint": (set(), set()),
}


def _keys(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ledger.LedgerError(label + " must be an object")
    if set(value) - allowed or required - set(value):
        raise ledger.LedgerError(label + " has unsupported or missing keys")


def _validate_batch(batch):
    _keys(batch, {"id", "operations"}, {"id", "operations"}, "batch")
    ledger._identifier(batch["id"], "batch.id")
    operations = batch["operations"]
    if not isinstance(operations, list) or not operations:
        raise ledger.LedgerError("batch.operations must be a nonempty list")
    ids = set()
    for operation in operations:
        _keys(operation, {"id", "command", "arguments"},
              {"id", "command", "arguments"}, "operation")
        identifier = ledger._identifier(operation["id"], "operation.id")
        if identifier in ids:
            raise ledger.LedgerError("Operation IDs must be unique within a batch")
        ids.add(identifier)
        command = operation["command"]
        if not isinstance(command, str) or command not in ALLOWED:
            raise ledger.LedgerError("Only artifact, put, event and checkpoint are permitted")
        arguments = operation["arguments"]
        _keys(arguments, *ALLOWED[command], "operation.arguments")
        if command == "put":
            if arguments["kind"] not in ledger.KINDS:
                raise ledger.LedgerError("Unsupported record collection")
            ledger._record_list(arguments["records"])
        elif command == "event":
            if not isinstance(arguments["event"], dict):
                raise ledger.LedgerError("Embedded event must be an object")
        elif command == "artifact":
            for name in ("id", "path"):
                ledger._identifier(arguments[name], "artifact." + name)
            if "source_id" in arguments:
                ledger._identifier(arguments["source_id"], "artifact.source_id")
    return batch


def _receipt_path(work, batch_id):
    # Neither batch nor operation IDs are interpolated into a filesystem path.
    name = "batches/" + ledger._digest(batch_id.encode("utf-8")) + ".json"
    return ledger._safe_local_path(work, name)


def _save_receipt(path, journal):
    journal["updated_at"] = ledger.utc_now()
    ledger.atomic_write_json(path, {"payload": journal, "sha256": ledger._obj_hash(journal)})


def _changes(state):
    return state["metadata"].get("changes", [])


def _anchor(state):
    changes = _changes(state)
    return {"change_count": len(changes), "last_change_id": changes[-1]["id"] if changes else None}


def _check_anchor(state, anchor):
    changes = _changes(state)
    count = anchor["change_count"]
    if len(changes) < count or (count and changes[count - 1]["id"] != anchor["last_change_id"]):
        raise ledger.LedgerError("Ledger history no longer contains a batch receipt; inspect recovery before replay")


def _artifact_path(operation, base):
    path = Path(operation["arguments"]["path"])
    return path if path.is_absolute() else base / path


def _dispatch(operation, work, base):
    command, arguments = operation["command"], operation["arguments"]
    if command == "artifact":
        return ledger.cmd_artifact(argparse.Namespace(
            work=str(work), id=arguments["id"], path=str(_artifact_path(operation, base)),
            source_id=arguments.get("source_id")))
    if command == "checkpoint":
        return ledger.cmd_checkpoint(argparse.Namespace(work=str(work)))
    # Named temporary input is private, removed on exit, and never shell-expanded.
    payload = arguments["records"] if command == "put" else arguments["event"]
    with tempfile.TemporaryDirectory(prefix="research-batch-") as temporary:
        path = Path(temporary) / "input.json"
        ledger.atomic_write_json(path, payload)
        args = argparse.Namespace(work=str(work), file=str(path))
        if command == "put":
            args.kind = arguments["kind"]
            return ledger.cmd_put(args)
        return ledger.cmd_event(args)


def _recover_committed(operation, attempt, state, work):
    """Recognize the non-idempotent wrappers after a receipt-write interruption.

    put is an upsert and event deduplicates by ID in the original engine. Artifact
    registration/checkpoint also append changes, so recognize their exact single
    committed change instead of creating a duplicate. Ambiguity stops safely.
    """
    _check_anchor(state, attempt["before"])
    tail = _changes(state)[attempt["before"]["change_count"]:]
    command = operation["command"]
    if command == "put" and tail:
        # Upsert replay must not overwrite a later edit after an interruption.
        arguments = operation["arguments"]
        current = {item["id"]: item for item in state[arguments["kind"]]}
        for record in ledger._record_list(arguments["records"]):
            expected, redactions = copy.deepcopy(record), []
            for key in ("parameters", "exact_parameters", "headers"):
                if key in expected:
                    expected[key] = ledger._redact(expected[key], key, redactions)
            if redactions:
                expected["parameter_redactions"] = redactions
            if current.get(expected["id"]) != expected:
                raise ledger.LedgerError("Interrupted put has intervening record changes; inspect before replay")
    if command not in ("artifact", "checkpoint") or not tail:
        return None
    if len(tail) != 1 or tail[0]["operation"] != command:
        raise ledger.LedgerError("Interrupted batch step has ambiguous intervening writes; inspect receipt history")
    change = tail[0]
    if command == "artifact":
        identifier = operation["arguments"]["id"]
        artifact = state["artifacts"].get(identifier)
        if (change.get("record_id") != identifier or not artifact
                or artifact.get("sha256") != attempt.get("input_sha256")
                or ledger._obj_hash(artifact) != change.get("after_sha256")):
            raise ledger.LedgerError("Interrupted artifact operation cannot be verified")
        verification = ledger.verify_artifacts(state, work)
        if identifier not in verification["valid"]:
            raise ledger.LedgerError("Interrupted artifact operation has missing or damaged retained bytes")
        return {"ok": True, "artifact": artifact, "recovered_committed_step": True}
    entries = [entry for entry in state["metadata"].get("checkpoints", [])
               if entry["id"] == change.get("record_id")]
    if len(entries) != 1 or ledger._obj_hash(entries[0]) != change.get("after_sha256"):
        raise ledger.LedgerError("Interrupted checkpoint operation cannot be verified")
    checkpoint = entries[0]
    retained = ledger._safe_local_path(work, checkpoint["path"])
    if ledger._digest(retained.read_bytes()) != checkpoint["sha256"]:
        raise ledger.LedgerError("Interrupted checkpoint bytes are missing or damaged")
    artifacts = ledger.verify_artifacts(state, work)
    inputs = ledger._input_verification(state, work)
    return {"ok": artifacts["ok"] and inputs["ok"], "checkpoint": checkpoint,
            "artifact_verification": artifacts, "input_verification": inputs,
            "recovered_committed_step": True}


def run_batch(work, file):
    work, file = Path(work).resolve(), Path(file).resolve()
    batch = _validate_batch(ledger._read_json(file))
    state = ledger.load_state(work)  # init is deliberately a separate operation.
    fingerprint = ledger._obj_hash(batch)
    path = _receipt_path(work, batch["id"])
    if path.exists():
        envelope = ledger._read_json(path)
        journal = envelope["payload"]
        if envelope.get("sha256") != ledger._obj_hash(journal):
            raise ledger.LedgerError("Batch receipt integrity mismatch")
        if journal.get("batch_sha256") != fingerprint or journal.get("batch_id") != batch["id"]:
            raise ledger.LedgerError("Batch ID already exists with different content; use a new batch ID")
        if journal.get("source_base") != str(file.parent):
            raise ledger.LedgerError("Batch source directory changed; preserve relative artifact path meaning")
        if journal.get("ledger_created_at") != state["metadata"]["created_at"]:
            raise ledger.LedgerError("Batch receipt belongs to a different ledger")
        for step in journal["steps"]:
            if step["status"] == "completed":
                _check_anchor(state, step["after"])
    else:
        journal = {"schema_version": "1.0", "batch_id": batch["id"], "batch_sha256": fingerprint,
                   "source_base": str(file.parent), "ledger_created_at": state["metadata"]["created_at"],
                   "created_at": ledger.utc_now(), "status": "pending", "invocations": 0,
                   "steps": [{"index": index, "id": operation["id"], "command": operation["command"],
                              "status": "pending", "attempts": []}
                             for index, operation in enumerate(batch["operations"])]}
    journal["invocations"] += 1
    journal["status"] = "running"
    _save_receipt(path, journal)
    executed, skipped, recovered = 0, 0, 0
    for operation, step in zip(batch["operations"], journal["steps"]):
        if step["status"] == "completed":
            skipped += 1
            continue
        state = ledger.load_state(work)
        # A prior attempt may have committed state before its receipt was saved.
        result = None
        if step["attempts"] and (step["attempts"][-1]["status"] == "running"
                                 or step["attempts"][-1].get("needs_commit_check")):
            result = _recover_committed(operation, step["attempts"][-1], state, work)
            step["attempts"][-1]["retry_resolution"] = "recovered_commit" if result else "safe_replay"
            if step["attempts"][-1]["status"] == "running":
                step["attempts"][-1]["status"] = "interrupted"
            if result is not None and result.get("ok", True):
                step["status"] = "completed"
                step["after"] = _anchor(state)
                step["attempts"][-1].update(status="completed", result=result, recovered_at=ledger.utc_now())
                _save_receipt(path, journal)
                recovered += 1
                continue
        attempt = {"number": len(step["attempts"]) + 1, "started_at": ledger.utc_now(),
                   "status": "running", "before": _anchor(state)}
        step["attempts"].append(attempt)
        step["status"] = "running"
        _save_receipt(path, journal)
        try:
            if result is None:
                if operation["command"] == "artifact":
                    attempt["input_sha256"] = ledger._digest(_artifact_path(operation, file.parent).read_bytes())
                    _save_receipt(path, journal)
                executed += 1
                result = _dispatch(operation, work, file.parent)
            else:
                recovered += 1
            attempt["result"] = result
            if not result.get("ok", True):
                raise ledger.LedgerError("Ledger command returned ok=false; inspect the retained step result")
            step["status"] = attempt["status"] = "completed"
            step["after"] = _anchor(ledger.load_state(work))
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            step["status"] = attempt["status"] = "failed"
            attempt["error"] = {"type": type(exc).__name__, "message": str(exc)}
            attempt["needs_commit_check"] = result is None
            journal["status"] = "failed"
        attempt["finished_at"] = ledger.utc_now()
        _save_receipt(path, journal)
        if step["status"] == "failed":
            break
    if all(step["status"] == "completed" for step in journal["steps"]):
        journal["status"] = "completed"
        _save_receipt(path, journal)
    return {"ok": journal["status"] == "completed", "batch_id": batch["id"], "status": journal["status"],
            "receipt": str(path), "completed_indices": [s["index"] for s in journal["steps"] if s["status"] == "completed"],
            "executed_steps": executed, "skipped_steps": skipped, "recovered_steps": recovered,
            "logical_operations": len(batch["operations"]), "host_commands_this_invocation": 1,
            "model_requests_measured": False, "steps": journal["steps"],
            "limitation": "Serial bookkeeping only. Each step commits independently. No search/model calls or scientific validation."}


def main(argv=None):
    try:
        parser = ledger.JsonArgumentParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--work", required=True)
        parser.add_argument("--file", required=True)
        args = parser.parse_args(argv)
        result = run_batch(args.work, args.file)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0 if result["ok"] else 2
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
