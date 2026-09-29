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
