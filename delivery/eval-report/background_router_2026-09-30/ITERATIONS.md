# Background loop — round 3: context quality first (Luna, medium, Sol judge)

Direction for this round: no effort comparisons; reasoning_effort is fixed at medium. Improve what the packet gives
the model. For each failure, ask first what context was missing or misleading, and only then whether a rule is needed.
Sol is used only as the judge (to break same-model judging); a Sol-as-candidate control is secondary and runs only
after context stops producing gains.

Synthetic data only. Router backend, model `cx/gpt-6-luna`, judge `cx/gpt-6-sol` (effort high, surface candidates
only). 3 runs per case. Tuning uses dev (24 cases) and dev2 (26 cases) only.

**holdout3** (`evals/cases_holdout3.jsonl`, generator `evals/make_cases_holdout3.py`, 32 cases: measurement 12,
revisit 10, silence 10) was written and committed (c46b120) **before** any round-3 loop change or result, and is run
once at the end. holdout2 is no longer a holdout, because its failing traces were read in round 2; it is kept as an
extra regression check.

Scoring: each campaign's as-run `summary.json` plus `summary_rescored.json` from `rescore.py`, which recomputes the
deterministic candidate checks with the current code and applies them identically to every campaign.

## Baseline (round 2, loop 01384fa, same judge): Luna on dev and dev2
| split | silence | revisit | measurement | trust |
|---|---|---|---|---|
| dev | 36/36 | 20/21 | 14/15 | 0 |
| dev2 | 18/18 | 24/24 | 32/36 | 0 |

## Iteration 1: dated event tied to the trigger and the question (commit e5ea104)
Context triage of the baseline failures:
- **GC07, GC21, E046 (silent at a near dated event).** Every packet listed the event in "Upcoming dated events". The
  triggering record *was* that event, but nothing said so: the trigger looked like just another note, the job
  preamble said the trigger was "new data matching one open question", and the model reasoned "the new record is not
  a measurement, so nothing changed". **Missing context:** the link between trigger, event and question.
  Fix: a trigger record that is a dated event carries `dated_event {date, days_until, "a dated event the user
  recorded, not a measurement"}`. Each upcoming event lists `mentions_questions` (open questions whose watch terms
  it contains) and states "N days left: anything measured for this must be done before then". The revisit preamble
  says a trigger may be a dated event.
- **Prior analyses showed receipt-binding internals** (`bound: false, current: false, note: freshness unverified…`),
  which read as "this analysis is not current". **Misleading context.** Fix: a plain `evidence_status`: current /
  stale (with what changed) / not verifiable but not known to be stale.
- **B14 r1 (six values listed for five readings).** The packet listed the five dated values correctly. This is a
  copying error, not context; no change.

| split | silence | revisit | measurement | expected surface | expected silence | trust | s | prompt tok |
|---|---|---|---|---|---|---|---|---|
| dev | 36/36 | 21/21 | 13/15 | 19/21 | 42/42 | 0 | 27.5 | 16.0k |
| dev2 | 18/18 | 22/24 | 35/36 | 31/33 | 44/45 | 0 | 19.6 | 15.9k |

GC21 went to 3/3, GC07 to 2/3 (the third run surfaced but named no method), and E046 stayed at 2/3. New or remaining:
- **B21 r3 (silent at "week 12 done"; the analysis said "check after 12 weeks").** It said "no checkpoint in the
  records". The analysis's condition and the qualifying note were both in the packet, but in different sections, with
  no age on the analysis and nothing showing what had accumulated since. **Missing context:** a side-by-side view.
- **GR08 r1 (quiet; the analysis said "repeat the same stairs rating after 8 weeks"; the new note is that rating at
  week 9).** It said "just one new rating; not enough for a trend". Same gap: the model did not connect the note to the
  analysis's named repeat.
- **E046 r3.** Silent: "no verifiable new measurement sufficient to change a decision". The review packet has the
  goal, the checkpoint (days_until 6) and the catalog. See iteration 2.
- **GC07 r3 (named "fixed route, fixed duration easy run" without distance or pace).** It passed the judge's
  substance but not the method list. A borderline eval call; the list keeps "固定路线" (fixed route) but this
  wording used "同一条平坦路线" (same flat route) with a separator. Left as a failure.
- **GR04 r3 (surfaced "fingerstick kit is not comparable").** The case allows "say it is not comparable", but the policy
  prefers silence. Kept as a failure (same reading as round 2).

## Iteration 2: what arrived since the newest analysis (commit f1a2811)
Fix for B21 and GR08: each prior analysis carries `days_ago`. A section lists the records written and the passive
samples added since the newest analysis, and asks whether they meet a condition the analysis set (a named repeat, a
date, a number of weeks), or show that the data accumulating since cannot answer the question.

| split | silence | revisit | measurement | expected surface | expected silence | trust | s | prompt tok |
|---|---|---|---|---|---|---|---|---|
| dev | 35/36 | 21/21 | 15/15 | 21/21 | 41/42 | 0 | 20.9 | 16.8k |
| dev2 | 17/18 | 23/24 | 32/36 | 29/33 | 43/45 | 0 | 18.6 | 16.4k |

B21, E046 and GR08 passed where they had failed before (E046 3/3 for the first time since round 1; GR08 would have too,
except r1 ended in an evidence error). But the new section **over-steered**: it ended with a question ("does anything
here meet a condition the analysis set…?") that invited the model to find one.
- **GC15 r3 and GS01 r3 (new false surfaces).** GC15's analysis set no condition, and 23 accumulating weight samples
  were read as "the weight trend is now answerable". GS01 surfaced one high reading. **Leading context.**
- **B22 r2 (quiet, no dated event).** It surfaced the 12-week revisit under quiet. The rule was prose over a list the
  model had to evaluate. **Ambiguous context.**
- **GC21 r2 (gate refused: missing_evidence).** The candidate cited an observation id missing its last hex digit. The
  `e in returned` test is a substring test, so the truncated id passed parsing and the gate refused the (correct)
  surface. **Loop defect.**
- **GR08 r1 (candidate_evidence_invalid after repair)** and **GC07 r3 (method)**: GC07 proposed a before/after on a
  baseline the records say does not exist. The judge caught it.
- **GC01 r1 (judge):** a before-and-after circumference plan that cannot inform the Sept 30 choice in time. Judgment.

## Iteration 3: whole-id evidence, neutral since-analysis section, quiet permission stated (commit 1a0d880)
- Evidence ids must equal a whole id that a tool returned; a truncated copy no longer passes (unit test).
- The since-analysis section states the analysis's revisit condition (or that it set none) and asks nothing.
- Under quiet, the packet states `measurement_gap_under_quiet: allowed | not allowed: no dated event within 14 days`,
  computed from the dated events rather than left for the model to evaluate.
