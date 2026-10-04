# Daily cloud analysis

A ChatGPT scheduled task runs once a day in the cloud and reasons over this Mac's data through the Personal Health
Context connector. The Mac stores, syncs and serves; it runs no model. The local background inference (Luna via the
router) is retired: `[model] enabled = false`.

## Layers

1. **Sources** (Mac, launchd): Oura and WHOOP every hour, iPhone push over LAN, the Apple Health export as backfill.
2. **Store + connector** (Mac): SQLite plus originals behind the MCP tools. `context_bootstrap` is the one entry point;
   it hands the model `last_digest`, `analysis_ledger` (one line per past analysis), `since_last_digest`, preferences,
   source status and the observation catalog.
3. **Reasoning** (ChatGPT cloud): the scheduled task below, and ordinary conversations. Both use the same tools.
4. **Memory**: each run saves one `kind=analysis` record with `payload.type = "digest"`. The next run, or any new
   conversation, starts from it. Deeper findings are separate analyses; the ledger keeps each to one line.

## Context budget

| Layer | What the model reads | Size |
|---|---|---|
| Connector instructions | `prompts/foreground.md`: procedure only | ~3 KB |
| Task prompt | `daily_task.md`: goal and output shape | ~1 KB |
| Bootstrap | digest, ledger, deltas, catalog, sources | measured by `ops/cloud_status.sh` |
| On demand | queries and record reads the model chooses | model-driven |

Guidance a frontier model already follows (how to compare, what a baseline is) stays out of the prompts. What stays in
is what it cannot know: this store's tools, receipts, time conventions, and the digest format.

## Set up (once, in ChatGPT on the web)

The task text reaches the cloud through the connector: `context_bootstrap` returns `daily_task` with the current
contents of `daily_task.md`, so an edit here applies from the next run. The scheduled task only needs to say: run the
daily task the connector returns.

1. Open a new chat with the Personal Health Context connector enabled. Use no other health connector in that chat:
   a session mixing a restricted vendor source cannot write.
2. Paste the text of `daily_task.md`, then say: `每天早上 8 点执行这个任务`.
3. Check Scheduled: the task shows daily at 08:00. Pick the model and reasoning effort you want when the task offers it.
4. Allow the connector's write actions for this task. A write that waits for approval pauses the task.

The Mac must be awake when the task runs; `pmset` shows sleep prevented, and the tunnel reconnects on its own.

## Check it ran

`ops/cloud_status.sh` lists the digests saved in the last 14 days and the connector calls for each day.
