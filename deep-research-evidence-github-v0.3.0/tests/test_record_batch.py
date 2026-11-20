"""Public CLI behavior for batch bookkeeping; all research data is synthetic."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill" / "scripts" / "record_batch.py"
LEDGER = SCRIPT.with_name("research_state.py")


class RecordBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.work = self.base / "work"
        self.case = self.base / "case.json"
        self.case.write_text(json.dumps({"case_id": "SYNTHETIC-BATCH"}), encoding="utf-8")
        self.run_cli(LEDGER, "init", "--case", self.case, "--work", self.work)
        self.source = self.base / "source.txt"
        self.source.write_text("Synthetic research fixture only.\nResult: 12 units.\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, script, *args, success=True):
        process = subprocess.run([sys.executable, str(script), *map(str, args)],
                                 capture_output=True, text=True, timeout=20)
        self.assertTrue(process.stdout, process.stderr)
        output = json.loads(process.stdout)
        self.assertEqual(process.returncode == 0, success, output)
        return output

    def state(self):
        return json.loads((self.work / "state.json").read_text(encoding="utf-8"))

    def batch(self, operations, batch_id="B1", success=True, filename="batch.json"):
        path = self.base / filename
        path.write_text(json.dumps({"id": batch_id, "operations": operations}, ensure_ascii=False), encoding="utf-8")
        return self.run_cli(SCRIPT, "--work", self.work, "--file", path, success=success)

    def operation(self, identifier, command, **arguments):
        return {"id": identifier, "command": command, "arguments": arguments}

    def event(self):
        return {"id": "T1", "action_id": "A1", "tool": "synthetic-fixture", "status": "success",
                "attempt": 1, "parameters": {"query": "synthetic", "api_key": "FAKE_SECRET"},
                "input_artifacts": [], "output_artifacts": ["ART1"]}

    def operations(self):
        return [self.operation("keep", "artifact", id="ART1", path="source.txt", source_id="S1"),
                self.operation("question", "put", kind="questions", records={"id": "Q1", "text": "Synthetic?", "status": "planned"}),
                self.operation("source", "put", kind="sources", records={"id": "S1", "artifact_id": "ART1", "title": "Synthetic"}),
                self.operation("observation", "event", event=self.event()),
                self.operation("save", "checkpoint")]

    def receipt(self, result):
        return json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))["payload"]

    def rewrite_receipt(self, result, journal):
        # Simulate interruption after state commit but before receipt replacement.
        canonical = json.dumps(journal, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        envelope = {"payload": journal, "sha256": hashlib.sha256(canonical).hexdigest()}
        Path(result["receipt"]).write_text(json.dumps(envelope), encoding="utf-8")

    def test_complete_batch_preserves_all_operations_with_one_host_command(self):
        result = self.batch(self.operations())
        self.assertEqual(result["executed_steps"], 5)
        self.assertEqual(result["logical_operations"], 5)
        self.assertEqual(result["host_commands_this_invocation"], 1)
        self.assertEqual(result["completed_indices"], [0, 1, 2, 3, 4])
        self.assertFalse(result["model_requests_measured"])
        state = self.state()
        self.assertEqual(state["sources"][0]["artifact_id"], "ART1")
        self.assertEqual(len(state["metadata"]["events"]), 1)
        self.assertEqual(len(state["metadata"]["checkpoints"]), 1)
        self.assertEqual([c["operation"] for c in state["metadata"]["changes"]],
                         ["init", "artifact", "put:questions", "put:sources", "event", "checkpoint"])
        self.assertIsNone(state["metadata"]["budget"]["observed_requests"])
        self.assertNotIn("FAKE_SECRET", (self.work / "state.json").read_text())
        self.assertNotIn("FAKE_SECRET", Path(result["receipt"]).read_text())

    def test_batch_and_individual_commands_have_same_observable_records(self):
        operations = self.operations()
        self.batch(operations)
        batch_state = self.state()
        separate = self.base / "separate"
        self.run_cli(LEDGER, "init", "--case", self.case, "--work", separate)
        calls = 0
        for operation in operations:
            command, arguments = operation["command"], operation["arguments"]
            args = [command, "--work", separate]
            if command == "artifact":
                args.extend(["--id", "ART1", "--path", self.source, "--source-id", "S1"])
            elif command in ("put", "event"):
                path = self.base / "individual.json"
                path.write_text(json.dumps(arguments.get("records", arguments.get("event"))), encoding="utf-8")
                args.extend(["--file", path])
                if command == "put":
                    args.extend(["--kind", arguments["kind"]])
            self.run_cli(LEDGER, *args)
            calls += 1
        state = json.loads((separate / "state.json").read_text())
        self.assertEqual(calls, 5)
        for kind in ("questions", "sources"):
            self.assertEqual(batch_state[kind], state[kind])
        self.assertEqual(batch_state["artifacts"]["ART1"]["sha256"], state["artifacts"]["ART1"]["sha256"])
        self.assertEqual([c["operation"] for c in batch_state["metadata"]["changes"]],
                         [c["operation"] for c in state["metadata"]["changes"]])
        clean = lambda event: {key: value for key, value in event.items() if key != "recorded_at"}
        self.assertEqual(clean(batch_state["metadata"]["events"][0]), clean(state["metadata"]["events"][0]))

    def test_retry_after_missing_file_preserves_failure_and_does_not_duplicate_event(self):
        operations = [self.operation("observed", "event", event=self.event()),
                      self.operation("missing", "artifact", id="ART1", path="later.txt"),
                      self.operation("finish", "put", kind="claims", records={"id": "C1", "kind": "unknown"})]
        failed = self.batch(operations, success=False)
        self.assertEqual(failed["completed_indices"], [0])
        self.assertEqual(self.state()["claims"], [])
        (self.base / "later.txt").write_text("Synthetic repaired input", encoding="utf-8")
        resumed = self.batch(operations)
        self.assertEqual(resumed["skipped_steps"], 1)
        self.assertEqual(resumed["executed_steps"], 2)
        attempts = self.receipt(resumed)["steps"][1]["attempts"]
        self.assertEqual([a["status"] for a in attempts], ["failed", "completed"])
        self.assertIn("error", attempts[0])
        self.assertEqual(len(self.state()["metadata"]["events"]), 1)

    def test_repeating_completed_batch_skips_all_mutations(self):
        operations = self.operations()
        self.batch(operations)
        before = (self.work / "state.json").read_bytes()
        result = self.batch(operations)
        self.assertEqual(result["skipped_steps"], 5)
        self.assertEqual(result["executed_steps"], 0)
        self.assertEqual(before, (self.work / "state.json").read_bytes())

    def test_same_id_changed_content_rejected_before_mutation(self):
        operations = self.operations()
        self.batch(operations)
        before = (self.work / "state.json").read_bytes()
        operations[1]["arguments"]["records"]["text"] = "Changed?"
        result = self.batch(operations, success=False)
        self.assertIn("different content", result["error"])
        self.assertEqual(before, (self.work / "state.json").read_bytes())

    def test_rejects_shell_commands_and_unsupported_argument_destinations(self):
        marker = self.base / "injection-marker"
        for index, operation in enumerate([
            self.operation("bad", "shell", script="touch " + str(marker)),
            self.operation("bad", "put", kind="questions", records={"id": "Q1"}, work=str(self.base / "escape")),
            self.operation("bad", "checkpoint", shell=True),
            self.operation("bad", "event", file="/etc/passwd"),
        ]):
            with self.subTest(index=index):
                self.batch([operation], batch_id="unsafe" + str(index), success=False)
        self.assertFalse(marker.exists())
        self.assertFalse((self.base / "escape").exists())
        self.assertEqual(len(self.state()["metadata"]["changes"]), 1)

    def test_shell_metacharacters_in_artifact_path_are_literal(self):
        marker = self.base / "SHOULD_NOT_EXIST"
        self.batch([self.operation("literal", "artifact", id="A", path="$(touch " + str(marker) + ")")], success=False)
        self.assertFalse(marker.exists())

    def test_batch_id_cannot_escape_receipt_directory_and_symlink_is_rejected(self):
        result = self.batch([self.operation("save", "checkpoint")], batch_id="../../escaped")
        self.assertEqual(Path(result["receipt"]).parent, (self.work / "batches").resolve())
        other = self.base / "outside"
        other.mkdir()
        other_work = self.base / "symlink-work"
        self.run_cli(LEDGER, "init", "--case", self.case, "--work", other_work)
        (other_work / "batches").symlink_to(other, target_is_directory=True)
        path = self.base / "batch.json"
        self.run_cli(SCRIPT, "--work", other_work, "--file", path, success=False)
        self.assertEqual(list(other.iterdir()), [])

    def test_interrupted_commit_window_does_not_duplicate_artifact_or_checkpoint(self):
        for index, operation in enumerate([self.operations()[0], self.operations()[-1]]):
            with self.subTest(command=operation["command"]):
                identifier = "interrupted" + str(index)
                result = self.batch([operation], batch_id=identifier)
                journal = self.receipt(result)
                step = journal["steps"][0]
                step["status"] = step["attempts"][-1]["status"] = "running"
                step["attempts"][-1].pop("result")
                step.pop("after")
                self.rewrite_receipt(result, journal)
                before = len(self.state()["metadata"]["changes"])
                resumed = self.batch([operation], batch_id=identifier)
                self.assertEqual(resumed["recovered_steps"], 1)
                self.assertEqual(resumed["executed_steps"], 0)
                self.assertEqual(len(self.state()["metadata"]["changes"]), before)

    def test_completed_receipts_do_not_silently_skip_rolled_back_ledger(self):
        original = (self.work / "state.json").read_bytes()
        operations = self.operations()
        self.batch(operations)
        (self.work / "state.json").write_bytes(original)
        result = self.batch(operations, success=False)
        self.assertIn("no longer contains", result["error"])

    def test_artifact_immutability_and_checkpoint_failure_preserve_engine_checks(self):
        operations = self.operations()
        self.batch(operations)
        self.source.write_text("Changed bytes", encoding="utf-8")
        failed = self.batch([operations[0]], batch_id="changed-artifact", success=False)
        self.assertIn("immutable", failed["steps"][0]["attempts"][-1]["error"]["message"])
        self.case.write_text("{}", encoding="utf-8")
        failed = self.batch([operations[-1]], batch_id="invalid-checkpoint", success=False)
        attempt = failed["steps"][0]["attempts"][-1]
        self.assertFalse(attempt["result"]["ok"])
        self.assertFalse(attempt["result"]["input_verification"]["ok"])


if __name__ == "__main__":
    unittest.main()
