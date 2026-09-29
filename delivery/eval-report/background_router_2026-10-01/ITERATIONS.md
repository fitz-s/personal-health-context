# Background loop — round 4: context beside the values, then deterministic guards (Luna, medium, Sol judge)

Synthetic data only. Router backend, `cx/gpt-6-luna` at medium; judge `cx/gpt-6-sol` (effort high, full tool
results, surface candidates only). Tuning uses dev and dev2 only. holdout2 and holdout3 have been read and are
regression checks only.

**holdout4** (`evals/cases_holdout4.jsonl`, generator `evals/make_cases_holdout4.py`, 33 cases: measurement 13,
revisit 10, silence 10) was written and committed (fbe5d3d) **before** any round-4 code change, with per-case
method and outcome vocabulary written up front (the holdout3 lesson). It is run once, at the end, on the frozen loop.

## Changes (commit 174516a)

Context (put next to the values, where the round-2/3 failures showed the information was present but far away):
- **Comparability beside the trigger.** A triggering record with a number carries `comparability`: the measurement
  conditions it and the question's earlier valued records state (lab, unit, device/equipment, venue, protocol,
  method), side by side, with their `differences`. It reports what the texts state, not a verdict. Checked on the
  round-2 noncomparable cases: GR02 trail/400 m climbing vs course, GR04 fingerstick kit/different method vs Lab A,
  GR06 5x5 vs 3RM protocol, GR10 treadmill/incline vs course, GR12 Lab C mg/dL vs Lab B mmol/L, GR18 straps/belt vs
  protocol. The comparable cases (GR01, GR03, GR05, GR08) show no differences.
- **Already delivered beside the trigger.** Every trigger on a question lists
  `already delivered <date>: <claim> — evidence <ids>` for each insight the user was shown.

Deterministic safety nets (they back up the context, they do not replace it; each run records what they caught):
- **Numeric fidelity** (in `parse_candidate`). Every number in topic/why_now/what_changed must appear in this run's
  tool results or packet, follow from them by one difference or percentage, or be a small count (≤ 31). Values are
  compared at the precision stated; m:ss, "8分40秒" and JSON values are read. On failure, one repair turn names the
  numbers; a second failure fails the candidate (`candidate_numbers_unverified`).
- **Duplicate guard** (in `execute`, before the gate). A surface is dropped to silence when every evidence id it cites
  is either in a delivered insight's evidence or a record that only restates it (no number of its own beyond that
  insight), and its factual fields state no number beyond the insight and those records.

Backtests on stored candidates (synthetic fixtures rebuilt):
- Duplicate guard: it catches all three stored re-surfaces (iter0 B05 r3, round-2 Luna GR14 r1 and r3) and drops
  none of 75 other stored surfaces, including corrections (E042, E043) and new same-condition values.
- Numeric fidelity: it flags none of 639 stored surface candidates after reading m:ss, 分秒 and JSON. **Limitation:**
  it also does not catch the two stored cases the judge called fabricated (Sol GC04 r1 "78.00 kg", Sol E046 r1
  "80.00 kg"). Both write a returned value (78, 80) with added zero decimals, which is not a changed value. The judge's
  complaint there was about which date the value belonged to, which a number check cannot see.

## Runs
The first launch hit the router's Luna quota (`429 usage limit, reset after ~20 min`) and was discarded (only 3 runs
finished, all no-model). Runs restart after the reset.

The runs restarted after the reset (14:12). The background runner was killed twice, so all later campaigns ran in
foreground chunks under 600 s each, merged by `merge_chunks.py` (chunk metadata kept in `run_meta.json`). Every
campaign below has 0 NOT_RUN.

## Results on the frozen guarded loop 174516a (Luna medium, Sol judge, 3 runs per case; rescored with rescore.py)
| split | silence | revisit | measurement | expected surface | expected silence | trust | s | prompt tok |
|---|---|---|---|---|---|---|---|---|
| dev (24 cases) | 36/36 | 20/21 | 15/15 | 21/21 | 42/42 | 0 | 32.5 | 16.8k |
| dev2 (26) | 18/18 | 24/24 | 34/36 | 31/33 | 45/45 | 0 | 28.2 | — |
| **holdout4 (33, fresh, run once)** | **30/30** | **30/30** | **38/39** | 41/42 | 57/57 | **0** | 47.7 | 15.6k |

**holdout4 meets the milestone: silence 1.00, revisit 1.00, measurement 0.97, 0 critical trust failures.** It is
the first held-out split that meets the milestone with no eval correction: as run equals rescored, and the per-case
vocabulary was written before any result. The only failure was KC53 r3: "abdominal imaging or a validated
visceral-fat measurement" named no concrete method.

Remaining dev/dev2 failures, all long-standing borderline cases:
- B14 r2 (dev): copied the five dated values with an extra value. The numeric check did not catch it, because each
  listed number is a real value from the packet. Only the count and order were wrong, which a set-of-numbers check
  cannot see.
- GC07 r1 (dev2): "a standardised run/walk field test" named no protocol.
- GC11 r2 (dev2): proposed a 7-night diary with 5 days left, although the packet says "5 days left".

### What each guard caught (safety nets, reported separately as asked)
Counted over every investigation in the four campaigns (342):
- **Duplicate guard:** 0 drops. No run tried to re-surface delivered content once the `already delivered` line sat
  beside the trigger. On stored round-2/3 candidates the same guard catches 3/3 real re-surfaces; the context fix
  removed the behaviour it was built for.
- **Numeric fidelity:** 0 repair turns. No candidate stated a number that the tool results or packet do not support.
  Its known blind spot is B14-type errors (right numbers in the wrong count or order).
- (For comparison, the existing evidence-id check triggered 6 repair turns, all resolved.)

So the question "do the guards alone bring Luna to the milestone on holdout4?" gets a negative answer: **the guards
fired zero times on holdout4, so Luna's holdout4 result comes from the loop and context (dated events beside the
trigger, comparability, already-delivered, since-analysis), not from the guards.** The guards remain as safety nets
that are not exercised by current behaviour. Comparability flags: holdout2's noncomparable cases (GR10, GR12, GR18)
were not rerun in this round, and holdout4's noncomparable cases (KR52 different erg and damper, KR54 other lab and
unit, KR58 phone app vs box) all passed 3/3.

## Secondary: Sol as candidate on holdout4 (same frozen loop, Sol judge)
| split | model | silence | revisit | measurement | trust | s | prompt tok | completion tok | turns |
|---|---|---|---|---|---|---|---|---|---|
| holdout4 | Luna | 30/30 | 30/30 | 38/39 | 0 | 47.7 | 15.6k | 390 | 3.2 |
| holdout4 | Sol | 30/30 | 30/30 | 36/39 | 0 | 33.3 | 10.3k | 579 | 2.4 |

Sol's 3 failures were all KC51. It proposed a photo comparison for shoulder size, which fails the method check, and
the judge agreed that photos do not measure size. Luna passed KC51 3/3. On this fresh split the two models are level;
the round-2 gap (Luna 16 vs Sol 2 failed runs) does not reappear on the current loop.
