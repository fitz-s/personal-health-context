# Offline executable foundation

Python 3.11+, standard library only. This is **not** an installed production MCP server or a HealthKit/Oura connector.

```sh
PYTHONPATH=reference_core python3 -m phctx demo
python3 scripts/verify.py
PYTHONPATH=reference_core python3 -m phctx --root /new/local/data init
PYTHONPATH=reference_core python3 -m phctx --root /new/local/data status
PYTHONPATH=reference_core python3 -m phctx --root /new/local/data backup /new/snapshot
```

From Python, `Store.restore(snapshot, new_destination)` verifies the snapshot and restores only to a new directory. `put_attachment_bytes` accepts bytes from a **trusted** transport; it is not an SSRF-safe network downloader or a MIME/document validator. `ingest_batch` accepts normalized samples from a **trusted** importer; it is not HealthKit authorization, a signed HTTP endpoint, or a vendor provenance classifier. Never expose these functions raw on the network.

`query_readonly` is broad but bounded read-only SQL. `search` is Unicode substring with keyset pagination and explicitly non-snapshot-stable results; production must add the required time filters and snapshot semantics. Routine resolution, extraction, observation-level evidence invalidation, worker/model reasoning, current SDK transport/auth, iPhone construction, file transport, encryption, durable export/deletion, and real deployment remain mandatory local implementation work specified in 03–07.

Source policy denies persisted real Oura sources by default. Operator-only source registration and source-name checks do not prove arbitrary text has no restricted origin; production provenance/session isolation is essential. `synthetic:` exists only for this isolated test harness, never as a production policy override.

The executable foundation solves local database/object invariants so the local implementation can start from measured behavior, not an empty skeleton. Test names, execution timestamps and actual Python/SQLite versions are in evidence; no claims of model accuracy or real accounts follow from these tests.
