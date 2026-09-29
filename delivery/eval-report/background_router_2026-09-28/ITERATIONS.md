# Background loop — router campaign (cx/gpt-6-luna)

Synthetic data only: every run builds an isolated temp profile from `evals/fixtures.py`; no production root is opened.
Model under test: `cx/gpt-6-luna` through the local router (`backend=router`), read-only tools called in process.
Judge: also `cx/gpt-6-luna` (router, reasoning_effort=high, fresh context, no tools). This is a **same-model judge**:
its errors correlate with the model's. It runs only on surface candidates; silence outcomes are scored
deterministically. Hard checks (silence vs surface, the gate outcome, evidence ids returned by tools, question_id,
causal-wording markers, measurement-gap naming its outcome and decision) are deterministic and override the judge.

Dev split: 24 background cases (12 silence, 7 revisit, 5 measurement), each run 3 times, so 72 runs.
Holdout split: 14 cases, untouched until the milestone is met on dev.
Milestone: silence ≥ 0.90, revisit ≥ 0.80, measurement ≥ 0.80, zero critical trust failures.

Bucket = the case's category in the corpus. E037/E038 (budget exhausted, model failure) sit in `silence` and never
reach the model. "per run" = the pass rate of run 1 / run 2 / run 3.

## Iteration 0: baseline (old context, same router loop)
Change: none to the context. The old 13-line `background.md` plus the old thin `task_text` was run through the new
router loop (`scratchpad` shim that monkeypatches `worker.packet`; not committed).

| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 19/36 | 0.528 | 0.50 / 0.58 / 0.50 |
| revisit | 5/21 | 0.238 | 0.29 / 0.29 / 0.14 |
| measurement | 6/15 | 0.400 | 0.40 / 0.40 / 0.40 |

Cost: 17.9k prompt + 963 completion tokens, 64.6 s, and 4.6 model turns per investigation on average (max 282 s).

Failures (one-line cause):
- 30 runs ended in an invalid candidate even after the repair turn (`candidate_schema_invalid` or `candidate_not_json`).
  The old prompt names the schema file but never shows its shape. **Contract gap (loop).**
- B05 r3: re-surfaced a 5 km result the user had already been shown. The old task gives no outbox history. **Context gap.**
- B11 r1: surfaced one short night as worth tracking. **Judgment, no range context.**
- B09 r1, B12 r2, B12 r3: causal wording ("见效", "because"). **Prompt gap.**
- E046 r1–r3: the review job stayed silent on a fat-loss goal with a checkpoint next week. The review task only gives a
  seq window, with no goals and no trigger content. **Context gap.**
- B14 r3, B21 r3: the gate rejected the candidate as `stale_evidence`.

## Iteration 1: assembled context packet + rewritten policy (commit cffe4a4)
Change: `worker.packet()` assembles the fixed context: the question verbatim, prior analyses (evidence current or
stale), everything already surfaced or shadow-recorded and whether it was shown, the preference with the attention
budget, source sync status, the observation catalog, and the triggering changes with deterministic before/after numbers.
Every truncation is marked. `background.md` is rewritten: silence is the expected outcome, a six-step ordered decision
procedure, measurement-gap conditions, a wording rule, and the output shape shown inline.

| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 33/36 | 0.917 | 1.00 / 0.92 / 0.83 |
| revisit | 18/21 | 0.857 | 0.86 / 0.86 / 0.86 |
| measurement | 10/15 | 0.667 | 0.80 / 0.60 / 0.60 |

Invalid candidates went from 30 to 0. Cost: 16.6k prompt + 412 completion tokens, 38.2 s, and 3.7 turns per
investigation (max 93 s).

Triage of the 11 failing runs:
- **B14 r1 and r3 (loop defect: packet arithmetic).** The judge flagged "all five new readings above the earlier range"
  as false. The claim was actually true for the data. The packet's "previous 28 days" window was `start_at <= lo`,
  so it included the first new reading (63). The baseline max became 63 and the claim looked wrong. Fix: disjoint
  windows and a deterministic `new_values_vs_previous_range` (above / inside / below counts).
- **B01 r3 (judgment).** It surfaced a sleep summary on an in-range night, reasoning that the question was "now
  answerable". That was just as true before this night. Fix: step 4 now states that a question answerable before
  the change was not made answerable by it.
- **B22 r3 (loop defect: quiet was prose only).** Under `quiet` it surfaced a measurement gap with no decision
  point in the records. The packet said only "a higher bar". Fix: the packet spells out the quiet rule (a direct answer
  the new evidence itself provides, a correction of something shown, or a gap tied to a decision the records date within
  about two weeks). A hard gate cannot tell "decision near" from prose, so this stays model judgment; see the verdict.
- **E035 r2 (loop defect: budget not visible).** It surfaced while the 168 h quiet budget was spent. The gate dropped
  it; no harm, but the investigation was wasted. Fix: the packet carries `budget_available_now`, and the policy says a
  surface that the gate will drop should be silence unless it corrects something the user was shown.
- **E042 r2 (loop defect: correction rule).** It surfaced the 52→62 correction because the old insight cited the typo.
  That insight had been withdrawn (`stale`) and never shown, so there was nothing to correct. Fix: a correction is news
  only when the message it makes wrong was `delivered`.
- **E046 r1–r3 (loop defect: review framing).** Silent every time. It reasoned that no new body-fat data exists, but
  here the missing measurement is the finding. The packet did not say what a review job is for. Fix: a job-type preamble
  (a review looks for measurement gaps with decision value), and step 4(c) says "no new direct data" is not a reason for
  silence in a gap case.
- **B16 r2 (loop defect: candidate contract).** It put two `rr_…` read receipts into `evidence_ids`, so the gate
  refused it with `missing_evidence`. Fix: `parse_candidate` rejects any evidence id that is not `rec_/obs_/obj:`, and
  the repair turn names the bad ids.
- **B16 r3 (judgment, judge-only).** It named measurements but not how a result would change the next-block choice.
  Fix: the output shape's `next_step` now asks for "how its result would change that choice".
- Also seen: in 20 of 66 investigations (iter1) a `context_query` used a nonexistent `observed_at` column or queried a
  packet field (`all_sources_for_metric`) as a table. This wasted a turn and sometimes the data. Fix: a compact schema
  of the queryable tables at the top of the packet.
