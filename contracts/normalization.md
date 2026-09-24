# Apple sample normalization v1 (shared by XML backfill and the iPhone helper)

Two producers describe the same HealthKit sample: `src/phctx/apple_export.py` (export.xml) and
`ios/HealthSyncHelper/AnchoredSyncCoordinator.swift` (live HKAnchoredObjectQuery). They must produce the same
**equivalence key** (`origin_key`) for the same underlying sample, while each keeps a **source identity**
(`native_id`) that never collapses distinct source records.

## Source identity (`native_id`)
- Live: `HKObject.uuid.uuidString` (unchanged).
- Export: `x2:` + sha256 over the canonical JSON of **every** attribute of the `<Record>`/`<Workout>` element plus its
  child elements (MetadataEntry key/values, WorkoutStatistics, WorkoutEvent, FileReference, InstantaneousBeatsPerMinute,
  HeartRateVariabilityMetadataList), first 40 hex chars. Nothing is rounded or dropped. Two export rows share a
  native_id only if every attribute and child is identical.

## Equivalence key (`origin_key`) — `origin_key(metric, start, end, value_num, value_text, unit, source_name)`
sha256 of canonical JSON `[metric, start_utc, end_utc, value, unit, source]` where:
- `start_utc`/`end_utc`: ISO-8601 UTC instants truncated to whole seconds (`YYYY-MM-DDTHH:MM:SS`): export.xml has 1-second
  precision while HealthKit dates carry fractions, so both producers must meet at the second.
- `source`: source name, whitespace-trimmed.
- `unit`: normalized — `Cal`→`kcal`, `count/min`→`count/min`, `min`→`min`, `%`→`%`; unknown units pass through
  unchanged. Category samples use unit `""`.
- `value`:
  - Quantity samples: `round(value_num, 4)`.
  - Category samples (metric starts with `HKCategoryTypeIdentifier`): the **XML identifier string** of the category
    value, e.g. `HKCategoryValueSleepAnalysisAsleepCore`, `HKCategoryValueSleepAnalysisInBed`. The iPhone helper
    sends this exact string in `value_text` (and the raw int in `value_num`); the XML importer takes it from the
    Record `value` attribute. `value_num` is ignored for the key.
  - Workouts (metric `HKWorkoutTypeIdentifier`): `[activity_type_string, round(duration_seconds)]`, where
    activity_type_string is the XML form, e.g. `HKWorkoutActivityTypeRunning`. The helper sends it in `value_text`
    and duration seconds in `value_num`; the importer takes `workoutActivityType` and converts `duration` +
    `durationUnit` to seconds.

## Formula versioning
The stored key is only meaningful under one formula. Any change to this section bumps the store migration that recomputes
`origin_key` for every stored row and rebuilds `supersessions` (v6 for this version).

## Supersession (which copy is canonical)
- A live row supersedes any export row with the same `origin_key`, in **either arrival order**, and a live
  **tombstone** keeps that supersession (the user deleted the sample on the phone; the backfill copy must not
  reappear). Implemented as the table `supersessions(origin_key PRIMARY KEY, live_observation_id)`; export rows are
  canonical iff their origin_key has no supersession row.

## Restricted provenance (both producers, before any persistence)
A sample is restricted (Oura) if **any** of these contain `oura` (case-insensitive): source name, bundle id, device
name/manufacturer/model, any metadata key or value. The phone drops it before writing the outbox; the Mac drops it
again at ingest. Workout routes / ECG files are persisted only when their parent record is permitted.
