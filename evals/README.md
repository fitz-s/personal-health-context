# Synthetic behavioral acceptance cases

56 scenario cases, split dev/holdout. These are specifications and fixture seeds, NOT previously executed model evaluations. The production evaluator must instantiate each named scenario with synthetic records/observations/originals in an isolated profile before it runs the actual model and tools. Scenario names alone are not executable evidence. Missing fixture implementations must be reported NOT_RUN, not silently skipped.

Run using the production agent's real tools and model loop, following 05_TEST_EVAL_AND_TUNING.md. Report trace/evidence IDs, exact model and prompt/code/fixture versions. Never put production health data or real Oura values in this corpus. Expected capabilities express the behavior needed; equivalent multi-step tool paths are acceptable when evidence proves the same scope, not merely when a grader guesses.

Scoring input is JSONL with one result per case: case_id, status (PASS/FAIL/NOT_RUN), hard_failure (boolean), reason, model_id, prompt_sha256, trace_id, evidence_file. `python3 evals/score.py RESULTS.jsonl` refuses missing/duplicate cases and incomplete PASS metadata. It aggregates supplied judgments; it does NOT perform the model calls or independently verify semantic truth. A real evaluation harness and judgment process are mandatory local work.

Ordinary-day silence must be accompanied by evidence that the worker actually ran. Positive revisit cases prevent the degenerate all-silence policy from passing. Critical trust failures block release regardless of average score.
