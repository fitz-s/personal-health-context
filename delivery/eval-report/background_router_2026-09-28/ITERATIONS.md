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

## Iteration 2: packet arithmetic, schema, quiet/budget/correction rules, evidence-id check (commit df8b195)
Change: the iteration-1 fixes listed above.

| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 34/36 | 0.944 | 1.00 / 1.00 / 0.83 |
| revisit | 20/21 | 0.952 | 1.00 / 1.00 / 0.86 |
| measurement | 11/15 | 0.733 | 0.60 / 0.80 / 0.80 |

Cost: 13.9k prompt + 408 completion tokens, 3.1 turns, and **101 s** per investigation (max 490 s). Latency regressed
against iteration 1 (38 s) with fewer turns and fewer tokens: 33 s per model turn against 10.7 s. The slowdown is
therefore on the router side, not the loop. The slowest runs are 3-turn investigations of ordinary size, and the loop
did not change what it sends per turn. From iteration 3 on, each model turn records its own wait (`seconds`), so
router time is measured directly rather than inferred.

Triage of the 7 failing runs:
- **E046 r1–r3 (loop defect: the scenario clock).** Silent every time again, for a reason the traces make plain: r3
  says "the 12-week checkpoint has already passed". The packet's `Now:` was the wall clock (2026-09-29), while the case
  is dated 2026-09-23 and the note says "checkpoint next week". The packet told the model it was a week later than the
  scenario, so the decision point looked past. This is a harness/packet mismatch, not model judgment. Fix: `run_once`
  and `execute` take `now` (harness passes the case's `now`); production passes nothing and uses the wall clock.
  The same mismatch affected every case with a "next week" or "in two weeks" note (B16, B19, B21, B22).
- **B21 r1 (loop defect: no rule for a met revisit condition).** Silent with "week 12 and the watch still cannot
  answer muscle gain". That is exactly scenario 9's finding, but step 4 had no branch for "a prior analysis's
  revisit condition is now met". Fix: step 4(c) now covers it.
- **E043 r3 (judgment, policy gap).** A like-for-like 3RM that falls from 110 to 102.5 kg, against an analysis saying
  "trending up; likely continuing", was dismissed as "one result, not enough for a trend". Fix: step 4(b) says a
  like-for-like result against the stated direction contradicts the analysis even if it cannot establish a new trend.
- **B11 r3 (policy gap).** One 330-minute night below a 410–439 range was surfaced as "worth noting". The rule only
  covered a single point inside the range. Fix: one new sample is not a trend even outside the range; several
  consecutive values beyond it are a candidate change.
- **B22 r3 (judgment, rule ambiguity).** Under quiet, the analysis's own "check after 12 weeks" was treated as the
  decision point. Fix: the quiet rule says a decision is a choice the user says they are making, and a revisit time an
  analysis set is not one.

## Iteration 3: scenario clock, revisit-condition rule, contradiction and one-sample rules, quiet wording (ran uncommitted; its code is included in commit 04078d8)
Change: the iteration-2 fixes above, plus per-turn router wait recorded in the trace.

| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 35/36 | 0.972 | 1.00 / 1.00 / 0.92 |
| revisit | 17/21 | 0.810 | 0.86 / 0.71 / 0.86 |
| measurement | 13/15 | 0.867 | 1.00 / 0.60 / 1.00 |

All three buckets met their thresholds, but one critical trust failure (B14 r1) means the milestone is **not** met.
Expected-surface recall: 16/21. Cost: 14.1k prompt + 389 completion tokens, 3.1 turns, 27.3 s per investigation
(max 73 s). Router wait per model turn: mean 8.8 s, median 4.5 s, max 39 s (186 turns). This confirms that
iteration 2's 101 s was router latency: the loop sent the same number of turns and tokens.

Triage of the 7 failing runs:
- **B14 r1 (critical trust failure; loop defect in the candidate contract).** It listed 22 observation ids, and the
  last one was truncated or retyped (`obs_008f8fe8…f5e5c221…`), so it was never returned by a tool. The gate refused
  it (`missing_evidence`), so nothing reached the user, but a fabricated id in a candidate is a trust failure.
  Fix: `parse_candidate` now requires every evidence id to appear in this run's tool results; otherwise the repair
  turn names it. `evidence_versions` entries not seen in any read are dropped (the gate checks current versions itself).
- **E043 r2 (same defect).** The candidate's `evidence_versions` key `rec_ad45…1771a0c872` was a mistyped copy of
  `rec_ad45…def177d4a4cfc`. The gate refused the whole candidate as `stale_evidence`. The content was right: a clear
  correction of the old "trending up" analysis. Same fix.
- **B09 r2 and r3 (judgment on question reading).** Under quiet, the same-course 5 km retest was dismissed because
  "one trial cannot show the plan caused the improvement". The question asks whether the time improved during the
  plan, not whether the plan caused it. Fix: step 2 says a question phrased "improving with X" asks whether the outcome
  changed during X, and a like-for-like measurement answers it; causation is a separate question.
- **E046 r2 (judgment).** Silent again ("no specific decision needs changing"). With the clock fixed it passed 2 of 3,
  so this is the remaining model variance on "a checkpoint is a decision point".
- **B22 r3 (judgment).** Still surfaced under quiet on the analysis's own 12-week revisit time, although the packet now
  says explicitly that such a time is not a user choice. It passed 2 of 3.
- **B16 r2 (judge-only).** `next_step` again did not say how a result would change the next-block choice.

## Iteration 4: tool-returned evidence ids only, question-reading rule (commit 04078d8)
Change: the iteration-3 fixes above.

| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 35/36 | 0.972 | 1.00 / 0.92 / 1.00 |
| revisit | 19/21 | 0.905 | 0.71 / 1.00 / 1.00 |
| measurement | 12/15 | 0.800 | 0.80 / 0.80 / 0.80 |

All buckets met their thresholds; there was one critical trust failure (B07 r2), so the milestone is **not** met.
Expected-surface recall: 17/21. No fabricated or retyped evidence id reached the gate. Cost: 14.9k prompt + 414
completion tokens, 3.2 turns, 26.4 s per investigation (max 83 s). Router wait per turn: mean 8.2 s, median 4.4 s.

Triage of the 6 failing runs:
- **B07 r2 (critical trust failure; loop defect: no device-switch signal).** After a watch→ring switch it surfaced
  "sleep can no longer be compared across devices". It never called the switch a health change, but it spent the
  user's attention on a data event. The packet showed only the new source's window (`previous_28_days_same_source: n=0`)
  and a catalog line for the watch; the model had to infer the switch. Fix: the packet computes `source_switch`
  (from, to, the previous source's stats, "not comparable; a data event") when a metric's new window has no history on
  its own source but another source reported it before. Step 1 now says a "values are not comparable" note is itself
  a data event and not a health message.
- **E046 r1 and r3, B21 r2 (judgment on "matters now").** They were silent with "the checkpoint doesn't say what choice it
  affects" and "no new information changes the earlier conclusion". A goal checkpoint was not recognised as a decision
  point. Fix: the measurement-gap section defines "matters now" concretely: a checkpoint, program end, appointment or
  choice within about two weeks (a checkpoint of the user's goal *is* a decision point), a goal change, or a prior
  revisit time with nothing added since.
- **B09 r1 (judgment under quiet).** It was silent on a same-course 5 km retest, citing "no decision within two weeks".
  This applied the quiet rule for measurement gaps to a direct answer, which the rule already allows. This is model
  variance: 2 of 3 passed.
- **B14 r1 (judge-only).** The judge said the listed chronological order (62, 63, 64, 63, 64) was unsupported: the packet
  listed values without dates (true order 63, 64, 62, 64, 63). Fix: the packet now gives `new_values_in_time_order`
  as [date, value] pairs.

## Iteration 5: device-switch signal, dated values, "matters now" definition (ran uncommitted; code in the iteration-6 commit)
Change: the iteration-4 fixes above.

| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 36/36 | 1.000 | 1.00 / 1.00 / 1.00 |
| revisit | 18/21 | 0.857 | 0.86 / 0.86 / 0.86 |
| measurement | 13/15 | 0.867 | 1.00 / 0.80 / 0.80 |

Recorded result: one critical trust failure (E039 r3, `no_causal_assertion`), so the milestone is **not** met. That flag
is a checker false positive; see below. Expected-surface recall: 16/21. Cost: 15.5k prompt + 448 completion tokens,
3.2 turns, 24.7 s per investigation (max 366 s: one router turn took 359 s). Router wait per turn: mean 7.8 s,
median 3.9 s.

Triage of the 5 failing runs:
- **E039 r3 (harness defect: causal-marker false positive).** It flagged "可据此判断矫正是否仍在奏效" ("use this to judge
  *whether* the correction is still working"). That is a question, not an assertion; the judge passed the candidate.
  Fix: `是否`/`whether` in the same clause counts as a hedge; a unit test covers assertions against questions and
  hedges. Re-scoring iterations 1–5 with the new checker changes only this one run. Earlier summaries are left as
  recorded.
- **E039 r2 (judgment: detail inflation).** "Same examiner" became "same therapist". The judge counted this as an
  unsupported personal fact. Fix: a wording rule to restate facts in the source's terms, without adding or
  strengthening who measured, which device or protocol, or a date.
- **E046 r2 and r3 (judgment).** Silent with "the checkpoint hasn't arrived yet". The concrete "matters now" definition
  did not land for this case: the model reads "checkpoint next week" as "not yet a decision". 1 of 3 passed.
- **B09 r1 (judgment under quiet).** The same misreading as iteration 4 (a direct answer treated as needing a near
  decision). 2 of 3 passed.

## Iteration 6: fidelity rule, causal-check fix, router retry (commit bf98499; this is the frozen loop)
Change: the iteration-5 fixes above; router calls retry 4× with 10/20/40 s backoff and record the HTTP status.

The first pass hit a router outage: 24 of 72 runs ended `model_call_failed` with no model turn (8 cases × 3 runs),
and 3 more lost the judge. A router health probe right after returned OK in 1.4 s. The 8 affected cases were rerun
on the same code (`iter6_rerun/`, 24 runs, zero NOT_RUN) and replaced the NOT_RUN rows in `iter6/results_all_runs.jsonl`.
The original rows are kept in `iter6/results_all_runs_not_run_original.jsonl`, and the rerun traces are in
`iter6/rerun_traces/`.

| bucket | pass | rate | per run |
|---|---|---|---|
| silence | 36/36 | 1.000 | 1.00 / 1.00 / 1.00 |
| revisit | 21/21 | 1.000 | 1.00 / 1.00 / 1.00 |
| measurement | 14/15 | 0.933 | 1.00 / 1.00 / 0.80 |

**Dev milestone met:** silence ≥ 0.90, revisit ≥ 0.80, measurement ≥ 0.80, zero critical trust failures.
Expected-surface recall: 20/21; expected silence: 42/42. Cost: 15.7k prompt + 429 completion tokens, 3.2 turns,
27.3 s per investigation (max 103 s). Router wait per turn: mean 8.3 s, median 4.0 s.

Remaining failure: **E046 r3** was silent on the fat-loss goal with "checkpoint next week" (it passed 2 of 3). Across
iterations 3–6, E046 was the one case whose failure survived every context change: it passed 1/3, 1/3, 1/3 and
2/3 in iterations 3–6. Treat it as the model's residual uncertainty on "is a goal checkpoint a decision point" rather
than a loop gap.
