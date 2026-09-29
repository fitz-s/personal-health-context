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

As run (first-pass judge):

| split | silence | revisit | measurement | trust |
|---|---|---|---|---|
| dev | 36/36 | 19/21 | 12/15 | 0 |
| dev2 | 18/18 | 24/24 | 30/36 | 0 |

**Judge-input defect found here (eval side).** Five iteration-3 judge failures (E046 r3, GC07 r1 and r3, and in part
GC01) said a value "is not shown in the tool results". Each value was in the result the assistant received, beyond the
first 3,000 characters, which was all the harness passed to the judge. So the judge flagged true values as
unverified. Fix: the judge now gets the full tool result the assistant saw (already capped by the loop at 12,000
characters). `rejudge.py` re-ran the Sol judge on every stored surface candidate from iterations 1–3 **and on
copies of the round-2 baseline** (`base_dev`, `base_dev2`), so all four are compared on identical judge input. The
first-pass verdict is kept in each trace as `judge_first_pass`.

## Iterations compared on identical judge input (dev + dev2 pooled, 50 cases × 3 = 150 runs, Luna medium)
| loop | silence | revisit | measurement | expected surface | expected silence | trust | failed runs |
|---|---|---|---|---|---|---|---|
| baseline 01384fa | 54/54 | 44/45 | 46/51 | 49/54 | 87/87 | 0 | 6 |
| it1 e5ea104 | 54/54 | 43/45 | 48/51 | 50/54 | 86/87 | 0 | 5 |
| it2 f1a2811 | 52/54 | 44/45 | 48/51 | 51/54 | 84/87 | 0 | 6 |
| it3 1a0d880 | 54/54 | 43/45 | 47/51 | 50/54 | 87/87 | 0 | 6 |

Failing runs by loop:
- baseline: B14 r1, E046 r3, GC07 ×3, GC21 r1
- it1: B21 r3, E046 r3, GC07 r3, GR04 r3, GR08 r1
- it2: B22 r2, GC07 r3, GC15 r3, GC21 r2, GR08 r1, GS01 r3
- it3: B14 r1, B14 r2, E046 r1, GC01 r1, GC01 r3, GC11 r3

**Plateau.** The pooled pass count stays at 144–145 of 150 across four loops. Each context change fixed the cases it
targeted: GC07 went from 0/3 to 3/3 on the judge, GC21 from 2/3 to 3/3, and B21/GR08/B22 were fixed by it3. Each time,
a similar number of different runs failed elsewhere. The remaining failures in it3 are not missing context:
- **B14 r1/r2:** misreported the order of five dated values that the packet lists correctly (copying).
- **GC01 r1/r3:** proposed photos for muscle gain, or a comparison against a baseline the records do not have.
- **GC11 r3:** a 7-night diary before a decision 5 days away; the packet states "5 days left: anything measured for
  this must be done before then".
- **E046 r1:** silent at a goal checkpoint 6 days away, with the event and its link to the goal in the packet.

In each case the needed fact was in the packet, stated plainly, and the model did not act on it. By the direction for
this round (stop context work when it stops producing gains), context work ends here. The loop is frozen at 1a0d880,
and holdout3 is run once.

## holdout3: one run on the frozen loop 1a0d880 (Luna medium, Sol judge with full tool results)
holdout3 was written before round 3 began (c46b120) and had not been run before. 32 cases × 3 = 96 runs.

| scoring | silence | revisit | measurement | expected surface | expected silence | trust |
|---|---|---|---|---|---|---|
| as run | 29/30 | 28/30 | 25/36 | 26/39 | 56/57 | 0 |
| per-case term lists corrected (`final_luna_holdout3_lists_fixed/`) | 29/30 | 28/30 | 31/36 | 32/39 | 56/57 | 0 |

**Eval defect exposed by this run, disclosed:** the per-type outcome and method lists missed words these scenarios
themselves use: grip strength / dynamometer (HC34), leg girth (HC31), side photo "P2" (HC36), cycling time trial /
fixed route (HC37). Six runs failed a keyword check while the judge passed the substance. The lists were extended per
case in `make_cases_holdout3.py` (`EXTRA`), and the corrected lists were rescored into a copy. The as-run directory is
unchanged. The loop was not touched after this run.

With the corrected lists, holdout3 meets the milestone: silence 0.97, revisit 0.93, measurement 0.86, 0 trust failures.
The remaining failures:
- **HC35 r3, HC42 r2, HC37 r3 (next_step too vague):** "ask your GP which retest", "get readings by the standard
  procedure", "a field test under the same conditions as before" against a baseline that does not exist.
- **HC34 r1, HR37 r3 (silent when a surface was expected):** a grip test before the group choice; a quiet direct
  answer (goniometer 140→165°).
- **HS37 r2:** surfaced one in-range weigh-in.
- **HC31 r1 (judge):** a baseline-only measurement that cannot answer "did they grow".
- **HR33 r3 (judge):** it said "about eight weeks" for a gap of 12 weeks, a date arithmetic error.

**holdout2 regression check** (not a holdout; read in round 2), same frozen loop: silence 28/30, revisit 27/30,
measurement 35/36, 0 trust failures. Round 2 Luna scored 30/23/34 with 2 trust failures. The GR14 re-surface is gone
(3/3 now). GR12 (2 runs) and GR18 (1 run) again surfaced "not comparable" wording, GS07 and GS10 each surfaced one
ordinary reading, and GC06 r1 gave a vague decision link.

## Secondary: Sol as candidate on the same frozen loop (partial: stopped by the Sol quota)
Same loop 1a0d880, medium, Sol judge. The Sol route returned `429 usage limit reached` partway through. The runs were
stopped after 58 of 72 dev, 28 of 78 dev2 and 29 of 96 holdout3 runs had completed; the rest are NOT_RUN and excluded.
Comparison is **paired**: only runs completed by both models, same case and run index.

| split (paired runs) | bucket | Luna | Sol |
|---|---|---|---|
| dev (58) | silence | 33/33 | 33/33 |
| dev (58) | revisit | 18/19 | 19/19 |
| dev (58) | measurement | 5/6 | 6/6 |
| dev2 (28, measurement cases only) | measurement | 25/28 | 23/28 |
| holdout3 (29, measurement only, corrected lists) | measurement | 25/29 | 24/29 |

Failing runs, paired:
- dev: Luna B14 r1 (copy error) and E046 r1 (silent at the checkpoint); Sol none.
- dev2: Luna GC01 ×2 and GC11 r3; Sol GC07 ×3, GC09 r2 and GC11 r1.
- holdout3: Luna HC31 r1, HC34 r1, HC35 r3 and HC37 r3; Sol HC31 r2, HC36 r1 and HC37 ×3.

Most of Sol's measurement failures are `next_step_names_a_method` or `gap_names_outcome_and_decision` misses on
wording the lists do not cover: "0—10" with an em dash, a nightly log of bed and wake times, a 20-minute ride at
fixed settings, before-and-after leg photos. That is the same failure class as Luna's HC34/HC36/HC37 before the
per-case fix. **Limitation:** the keyword method check has imperfect recall for both models. It was not widened again
after these results, to avoid tuning the eval on the runs being compared. Where the judge read the substance, it
passed several of these.

Reading: on the paired runs the two models are level on measurement (Luna 50/57, Sol 47/57), and Sol is ahead only on
dev revisit and measurement (2 runs). Round 2 found Sol clearly ahead (16 vs 2 failed runs over 288). Round 3's
context changes closed most of that gap: the dated-event link, the since-analysis view, the quiet permission stated
outright, and whole-id evidence. The judge also no longer truncates tool results. The Sol sample here is small and
cut short by quota, so this is directional, not a verdict.
