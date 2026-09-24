#!/bin/bash
# Full engineering suite → delivery/test-report/p1_p5_engineering_tests.log (run under scripts/run_manifest.py).
cd "$(dirname "$0")/.." || exit 2
out=delivery/test-report/p1_p5_engineering_tests.log
{ echo "# final engineering suite $(date -u +%FT%TZ) rev $(git rev-parse --short HEAD)"
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v 2>&1; rc=$?
  echo "Command: PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v"
  echo "Date: $(date +%FT%T%z)"
  echo "Exit code: $rc"; } > "$out"
grep -q '^Exit code: 0$' "$out"
