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
