# Bug found by recovery drill (2026-09-23)
Step: db_busy_bounded_then_retry. Another connection held BEGIN IMMEDIATE; Store._mutate raised raw
`sqlite3.OperationalError: database is locked` from `c.execute('BEGIN IMMEDIATE')` (store.py line 236), which sits
outside the try/except that maps lock errors to StoreError('storage_busy'). Tool callers would get an unmapped error.
Fix: move BEGIN IMMEDIATE inside the guarded block (and map it the same way). Regression: rerun the drill + unit tests.

# Drill design correction (same run)
readonly_db_refuses_write first FAILED because the drill only chmod'ed context.sqlite3; in WAL mode the write goes
to context.sqlite3-wal, so the write legitimately succeeded — the simulation did not produce a read-only DB.
Corrected the simulation to chmod 0400 on context.sqlite3, -wal and -shm. No product code changed for this item.
