# Background loop — round 2: upcoming dated events, powered eval, Sol capability control

Synthetic data only (isolated temp profiles from `evals/fixtures.py`). Router backend, read-only tools in process.
Round 1 log: `../background_router_2026-09-28/ITERATIONS.md`.

## Changes (frozen loop: commit 01384fa)
1. **`upcoming_dated_events`** (worker.upcoming): dated events from the user's own records within 21 days, with
   `days_until`. Sources: explicit ISO dates, "N weeks … (started D)", and "next week"/"in N days"/"tomorrow"
   (relative to the record date). Analyses are excluded, so a revisit time the system set never counts as the user's
   decision. It says what the records state, not whether the event matters.
2. **quiet rule keyed to that list:** under `quiet`, a measurement gap surfaces only for an entry with
   `days_until ≤ 14` on the same outcome; otherwise silence. Direct answers and corrections of shown messages are
   unchanged.
3. **Policy:** a proposed measurement names its method and cadence ("home blood-pressure readings morning and
   evening for a week"), never just "take a reading".

## Eval changes
- `evals/cases_background2.jsonl` (generator `evals/make_cases_background2.py`): 58 synthetic cases from declarative
  fixtures. dev2 has measurement 12, revisit 8, silence 6. **holdout2 (fresh, never used to change the loop)** has
  measurement 12, revisit 10, silence 10. The round-1 holdout was not reused. Every case triggers exactly one
  investigation (checked offline).
- **Deterministic B17-class check:** measurement cases carry `methods`, and `next_step_names_a_method` requires
  `next_step` to name one. The round-1 measurement cases (B16/B17/B19/B20/B21/B24) got lists too.
- **Judge:** `cx/gpt-6-sol` for **both** the Luna and Sol runs (reasoning_effort=high, fresh context, no tools), and
  only on surface candidates. For the Sol runs the judge is again the same model as the one under test, so
  same-model correlation is broken only for the Luna numbers.
  The judge prompt now says the method wording is checked deterministically and exact phrases from expected_behavior
  are not required.

## Run 1: Luna medium on dev2 (loop 01384fa, eval lists at 01384fa)
| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 18/18 | 1.000 | 1.00 / 1.00 / 1.00 |
| revisit | 24/24 | 1.000 | 1.00 / 1.00 / 1.00 |
| measurement | 30/36 | 0.833 | 0.83 / 0.83 / 0.83 |

Expected silence 45/45; expected surface 27/33. Zero trust failures. 16.6 s, 15.2k prompt + 388 completion tokens,
and 3.1 turns per investigation.

Failures:
- GC09 r2 and GC11 r3 (**eval list defect**): "0–10 pain on the stairs for a few days" and "daily short log of
  sleep-onset trouble and night waking" are concrete methods that the lists did not contain; the Sol judge also said so.
  Lists widened (commit 77fe40a) and rescored from stored candidates (`summary_rescored.json`): measurement 32/36 (0.889).
- **GC07 r1 and r3 (model):** silent on "race registration closes in 8 days; 10 km or half marathon". It read the
  event as "not a measurement" and did not see the gap it creates. **GC07 r2**: it surfaced, but next_step was
  "use an existing race record if you have one", which names no method.
- **GC21 r1 (model):** silent under quiet with the cut ending in 3 days, despite an upcoming entry at days_until 3.
  It said the HRV data was irrelevant and stopped there.

Rescoring note: `rescore.py` recomputes the deterministic candidate checks from stored runs with the current checks,
identically for every campaign below. It then re-derives each run's status. A judge FAIL that cites only a check that
now passes (the judge sees `automatic_checks`) is re-read. Eval fixes found in round 2, all on the eval side, none on
the loop (commits 77fe40a, ecabbe1, 21afc78):
- Method lists were too narrow: rated 0–10 stair pain, a daily symptom log, arm girth by tape, a fixed-route run,
  and "home blood-pressure … morning and evening" were concrete methods the lists missed.
- The causal-wording check produced false positives on negated sentences ("would not, by itself, establish what
  caused the change", "neither would establish…"). It split at commas and ignored English negation, and "result"
  alone matched. It is now sentence-scoped, with negation hedges and a unit test. **Correction to round 1:** the
  iteration-0 causal flags (B09 r1, B12 r2/r3) were such false positives. Across all stored surface candidates from
  both rounds the checker now flags none. No true causal assertion appears in the corpus, so this check has no observed
  true positive; its unit test covers the positive direction.

## Capability control: Luna vs Sol, frozen loop 01384fa, medium effort, Sol judge for both, 3 runs per case

Rescored per-bucket pass counts (as-run numbers are in each campaign's `summary.json`, rescored ones in
`summary_rescored.json`; the combined view is `summary.json` in this directory):

| split | model | silence | revisit | measurement | expected surface | expected silence | trust | s | prompt tok | completion tok | turns |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dev (round 1) | Luna | 36/36 | 20/21 | 14/15 | 20/21 | 42/42 | 0 | 18.6 | 16.2k | 425 | 3.3 |
| dev (round 1) | Sol | 36/36 | 21/21 | 14/15 | 20/21 | 42/42 | 0 | 23.0 | 10.8k | 577 | 2.5 |
| holdout (round 1, seen) | Luna | 17/18 | 12/12 | 12/12 | 21/21 | 20/21 | 0 | 22.2 | 14.6k | 428 | 3.0 |
| holdout (round 1, seen) | Sol | 18/18 | 12/12 | 12/12 | 21/21 | 21/21 | 0 | 22.8 | 9.7k | 577 | 2.4 |
| dev2 | Luna | 18/18 | 24/24 | 32/36 | 29/33 | 45/45 | 0 | 16.6 | 15.2k | 388 | 3.1 |
| dev2 | Sol | 18/18 | 24/24 | 36/36 | 33/33 | 45/45 | 0 | 22.9 | 10.6k | 595 | 2.4 |
| **holdout2 (fresh)** | **Luna** | 30/30 | **23/30** | 34/36 | 33/36 | **54/60** | **2** | 15.5 | 15.0k | 381 | 3.1 |
| **holdout2 (fresh)** | **Sol** | 30/30 | **30/30** | 35/36 | 35/36 | **60/60** | **0** | 21.0 | 10.4k | 548 | 2.5 |

Milestone (silence ≥ 0.90, revisit ≥ 0.80, measurement ≥ 0.80, no critical trust failure):
- Luna meets it on dev, dev2 and the round-1 holdout. On the fresh holdout2 it misses: revisit 0.77 and 2 trust
  failures (GR14 r1 and r3 re-surfaced an already-delivered knee improvement).
- Sol meets it on every split.

The round-1 holdout was seen in round 1 (its B17 finding shaped the method check), so it no longer counts as held out;
it is kept only as a regression check. The measurement gain from round 1 (Luna holdout measurement 5/12 → 12/12)
comes from the upcoming-events list plus the deterministic method check replacing the judge's phrase-matching.

Where Luna and Sol differ (cases with any failing run; L/S = passes out of 3):
- dev: B14 L2 S3 (Luna listed six values for five readings), E046 L2 S2 (Luna silent once; Sol once stated a weight
  value the tools had not returned).
- round-1 holdout: B04 L2 S3 (Luna surfaced "ordered a blood-pressure cuff").
- dev2: GC07 L0 S3 (race registration deadline: Luna silent twice, and once named no method), GC21 L2 S3 (quiet plus
  a cut ending in 3 days: Luna silent once).
- holdout2: GR14 L1 S3 (Luna re-surfaced delivered content twice), GR18 L1 S3 (lifting straps: Luna surfaced "not
  comparable" twice), GR10 L2 S3 (treadmill vs outdoor), GR12 L2 S3 (other lab and unit), GC02 L1 S3 (appointment:
  Luna silent twice), GR15 L2 S3, GC04 L3 S2 (Sol stated an unreturned weight value).

Totals over the 4 splits (96 cases × 3 runs = 288 runs per model, zero NOT_RUN): **Luna failed 16 runs, Sol 2**.

Caveat on 3 of Luna's 16 (GR12 r3, GR18 r1, GR18 r2): Luna surfaced a message that said "not comparable, cannot judge"
without claiming a change. The case text allows "silence or say it is not comparable", and the Sol judge passed the
wording, but the deterministic `model_silent` check fails any surface on an expected-silence case. By the policy
(step 3: "not comparable" is rarely worth surfacing, and step 1: a data event alone is silence) the check is the
stricter, intended reading, so these stay as failures. Reading them as passes would give Luna 13 failures and
holdout2 revisit 26/30 (0.87), still with GR14's 2 trust failures.
