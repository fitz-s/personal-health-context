Repository: /Users/leofitz/personal-health-context (Python 3.12 venv at .venv; run tests with: cd /Users/leofitz/personal-health-context && PYTHONPATH=src .venv/bin/python -m unittest discover -s tests). The independent acceptance review is at delivery/acceptance/consult_round1_574fcc2.txt — read the findings named for your slice there first (they cite file:line at commit 574fcc2; the code has moved slightly since). The shared normalization contract is contracts/normalization.md — follow it exactly.

Rules:
- Edit ONLY the files listed as yours; other agents edit other files in parallel (never touch or revert them).
- All test data synthetic. No network except loopback. Never touch the production data root (~/Library/Application Support/PersonalHealthContext) and never import real exports. Do not git commit.
- Every fix comes with a test that FAILS before the fix and passes after (write the test first, run it, see it fail, then fix).
- Tight code in the existing style (short names, no speculative abstractions).
- At the end run the FULL test suite and report the exact pass/fail line.
