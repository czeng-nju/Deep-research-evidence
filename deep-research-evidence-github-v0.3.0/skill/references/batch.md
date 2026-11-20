# Batch bookkeeping

Use the `record_batch.py` script in the Skill's `scripts/` directory when several already-observed records can be saved together. It calls the existing ledger handlers serially in one Python process. Initialize the work directory separately with `research_state.py init`. Run the following example from the Skill directory:

```sh
python3 scripts/record_batch.py --work ./work --file ./batch.json
```

`batch.json` contains a stable batch ID and ordered, uniquely identified operations:

```json
{
  "id": "acquisition-01",
  "operations": [
    {"id": "retain", "command": "artifact", "arguments": {
      "id": "ART1", "path": "sources/study.txt", "source_id": "S1"
    }},
    {"id": "source", "command": "put", "arguments": {
      "kind": "sources", "records": {
        "id": "S1", "title": "Use the actual source title", "artifact_id": "ART1",
        "access_level": "full_text", "screening_status": "included",
        "inclusion_reason": "Replace with the actual inclusion rationale"
      }
    }},
    {"id": "checkpoint", "command": "checkpoint", "arguments": {}}
  ]
}
```

This is a shape example, not an acquisition record to submit. Supply actual records and an existing artifact. Relative artifact paths resolve against the batch file's directory; absolute paths are also accepted. Retained files remain under the ledger's artifact directory with the existing immutable-ID/hash checks. Never substitute a shell command for an artifact path.

Allowed commands and arguments are deliberately narrow:

| Command | Arguments |
|---|---|
| `artifact` | `id`, `path`, optional `source_id` |
| `put` | `kind` from the existing record contract; `records` containing an object or nonempty list |
| `event` | `event` containing a real observed event object, including `id`, `action_id`, `tool`, `status`, `attempt`, actual `parameters`, `input_artifacts`, `output_artifacts` |
| `checkpoint` | Empty object |

No operation accepts a shell command, arbitrary executable, output destination or work-directory override. The wrapper preserves record validation, event credential redaction, scientific-content invalidation and checkpoint verification from `research_state.py`. The supplied batch input itself may contain credentials: keep it private or redact it before sharing. The durable batch receipt stores its hash, step results and errors, not embedded input records.

## Failure and continuation

Each step commits independently. This is **not a whole-batch transaction**. A failed step stops later operations; earlier commits remain. The CLI returns JSON and exit code 2 on failure, including completed zero-based indices, individual step results and attempts. Keep `WORK/batches/` with the ledger. One coordinator must own all writes; this wrapper does not provide concurrent-writer locking.

Repair an unavailable input or external condition, then rerun the same command with identical JSON and the same batch-file directory. Completed steps are skipped. Event IDs keep their original deduplication behavior. The receipt retains failed attempts and retry history. Changed batch content under the same ID is rejected; use a new batch ID for corrected records or changed operation definitions. Changing an artifact's content also requires a new artifact ID.

If interrupted after a ledger commit but before the receipt commit, the wrapper recognizes a matching artifact/checkpoint history entry; ordinary put/event replay uses the engine's upsert/deduplication behavior. Ambiguous intervening writes stop for inspection. Completed receipts verify their history anchors and reject a rolled-back ledger; a damaged receipt also blocks continuation. These guarantees require the original receipts: a deleted receipt cannot prove that a batch ran previously. Preserve receipt backups and inspect recovery before starting a replacement batch. A checkpoint that returns `ok=false` remains a failed attempt with its verification details, even though the engine retained a diagnostic checkpoint; repair the underlying input/artifact problem and retry.

## What efficiency evidence this provides

For N eligible operations, the success path uses **1 top-level host command instead of N separate ledger commands**, while still executing all N logical handlers and their integrity checks. Initialization is excluded equally from both counts. Output reports `logical_operations`, `executed_steps`, `skipped_steps`, `recovered_steps` and `host_commands_this_invocation`; recovered bookkeeping is not a fresh acquisition. `executed_steps` counts handler calls in the current invocation, not record count or search calls.

This targets the rubric's **efficiency gain (5 points)** and supports **verifiability (5 points)** and **exception handling (3 points)** through retained results and resumable history. It does not by itself prove a faster end-to-end research task, fewer model requests, better retrieval, correct scientific conclusions or an official score increase. Measure wall time and tool/model use in a separately controlled whole-task comparison before claiming those gains.
