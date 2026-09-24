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
  unchanged. Values are NOT converted between compatible units: an export written in a unit other than the one the
  helper requests for that type does not match its live copy (both stay; the live-sync activation test must cover the
  requested units per type). Category samples use unit `""`.
- `value`:
  - Quantity samples: `round(value_num, 4)`.
  - Category samples (metric starts with `HKCategoryTypeIdentifier`): the **XML identifier string** of the category
    value, e.g. `HKCategoryValueSleepAnalysisAsleepCore`, `HKCategoryValueSleepAnalysisInBed`. The iPhone helper
    sends this exact string in `value_text` (and the raw int in `value_num`); the XML importer takes it from the
    Record `value` attribute. `value_num` is ignored for the key.
  - Workouts (metric `HKWorkoutTypeIdentifier`): `[activity_type_string, round(duration_seconds)]`, where
    activity_type_string is the XML form, e.g. `HKWorkoutActivityTypeRunning`. The helper sends it in `value_text`
    and duration seconds in `value_num`; the importer takes `workoutActivityType` and converts `duration` +
    `durationUnit` to seconds. A raw activity value the helper's table does not know is sent as
    `HKWorkoutActivityTypeUnknown(<raw>)`, never as the known `...Other`, so it matches no export row rather than a
    wrong one.
- The key is an equivalence heuristic, not an identity proof: two distinct samples with the same metric, whole-second
  window, rounded value, unit and source share it. Export rows that share it are all kept (distinct native_id);
  `canonical_observations` hides only exact repeats of one source record (every measured field and the device equal).

## Formula versioning
The stored key is only meaningful under one formula. Any change to this section bumps the store migration that recomputes
`origin_key` for every stored row and rebuilds `supersessions` (v6 for this version).

## Supersession (which copy is canonical)
- A relation, once made, is history: migrations that re-key rows carry each relation to the export row's new key.
- A live row supersedes any export row with the same `origin_key`, in **either arrival order**, and a live
  **tombstone** keeps that supersession (the user deleted the sample on the phone; the backfill copy must not
  reappear). Implemented as the table `supersessions(origin_key PRIMARY KEY, live_observation_id)`; export rows are
  canonical iff their origin_key has no supersession row.

## Oura
`source_id` = `oura` (API v2, `phctx.oura`): one observation per Oura document; `native_id` = `<collection>:<document id>`
(time series: `<collection>:<timestamp>`), `metric` = `oura.<collection>`, `value_num` = the collection's headline number
(daily scores, sleep `total_sleep_duration` s, workout `calories` kcal, heart rate `bpm`), the whole document in `raw`.
Daily documents span the local day. Oura entries mirrored into Apple Health keep their Apple provenance
(`source_name` Oura) and are stored like any other sample (owner decision 2026-09-24).

## WHOOP
`source_id` = `whoop` (API v2, `phctx.whoop`): one observation per record; `native_id` = `<collection>:<id>` (recovery:
its `sleep_id`), `metric` = `whoop.<collection>`, `value_num` = cycle/workout `strain`, recovery `recovery_score` %, sleep
`sleep_performance_percentage` %; `value_text` = workout `sport_name`, `nap`, else `score_state`; the whole record in `raw`
(resting HR, HRV, SpO2, skin temperature, stage durations, zone durations live there). An open cycle has no end:
`end_at` = `start_at` until WHOOP closes it.

## Life Dashboard Companion (`src/phctx/companion.py`)
A third producer of Apple HealthKit samples, alongside export.xml and `ios/HealthSyncCore`'s live helper: the
free, open-source iPhone app [Life Dashboard Companion](https://github.com/owen282000/life-dashboard-companion-app-ios)
(MIT, not developed in this repo — see `ios/COMPANION_SETUP.md`), which POSTs its own JSON shape to a
webhook the owner configures in the app, HMAC-signed (`X-Signature: sha256=<hex>`) and received by a plain-HTTP,
LAN-only server on port 47823 (`phctx companion-server`). It never sends deletions.

`source_id` = `apple_health:companion`, `native_id` = the HealthKit `uuid` string the app attaches to each record
(same identity as `ios/HealthSyncCore`'s `HKObject.uuid.uuidString`). Every record the app can express as one real
HealthKit sample is translated to that sample's real HK identifier, using `origin_key()` exactly as export.xml and
the live helper do, so the three producers can share one canonical observation. Two structural exceptions split a
single wire record into two observations with one synthesized `native_id` (documented at the call site in
`companion.py`, not silent): `blood_pressure` (systolic/diastolic) and `nutrition` (energy/protein/carbs/fat) — the
app attaches only one uuid to the combined record; the identifier, window, and value of each half are still exact,
only the second half's `native_id` is not a device UUID.

Units for the app's own quantity types beyond the ones `ios/HealthSyncCore/Sources/HealthSyncCore/Normalization.swift`
already verifies (steps, active calories, the heart-rate family, oxygen saturation, body mass/fat/lean mass, sleep)
are the app's own HealthKit unit for that field (read from its `HealthKitManager.swift`), not independently confirmed
against export.xml's unit spelling for that identifier — per the tolerance above, a mismatch there means the two
copies simply aren't merged into one canonical row, not a wrong value. Two record types have no faithful HK
identifier at all and are stored as `companion.<type>` (raw fields, no invented identifier): `total_calories` (the
app merges active + basal energy into one array with no field saying which type a given record is) and
`menstruation_period` (client-derived from consecutive flow days; HealthKit has no period sample type).
