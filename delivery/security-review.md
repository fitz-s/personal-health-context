# Security review (independent reviewer agent, read-only) — 2026-09-23

Risk level reported: MEDIUM (0 critical, 0 high, 4 medium, 1 low). `pip-audit`: no known vulnerabilities.
All five were confirmed and fixed; verification below was run after the fix.

| # | Finding | Fix | Verification |
|---|---|---|---|
| 1 | SSRF: `is_global` accepts NAT64 `64:ff9b::a9fe:a9fe` (embeds 169.254.169.254) | `download.public_ip` rejects IPv6 prefixes that embed IPv4 (64:ff9b::/96, 64:ff9b:1::/48, 2002::/16, 2001::/32) | NAT64/6to4/Teredo → False; 2606:4700::1111, 8.8.8.8 → True |
| 2 | `context_capture_file` replay returned the old receipt for a *different* file under the same request_id | request envelope hash (host file_id + text + time + tz + payload) stored with the record; mismatch → `idempotency_conflict` | same file/new signed URL → same receipt, 1 fetch; different file → `idempotency_conflict` |
| 3 | Ingest DoS: `Content-Length: -1` read to EOF; TLS handshake inside `accept()` | reject missing/zero/non-digit length (411/400); per-connection 20 s socket timeout; `do_handshake_on_connect=False` so the handshake runs in the connection thread | `-1` → 400 immediately while a stalled raw TCP connection is held open |
| 4 | Oura-connected ChatGPT session would keep local write tools | separate read-only tunnel (`ops/tunnel.sh … readonly` → `phctx mcp --profile readonly`, writers absent + refused); USER_ACTIONS §6 | script syntax check; readonly profile covered by unit tests |
| 5 | (low) background prompt passed in argv | prompt sent on stdin (`codex exec -- -`) | harness E001/E006 PASS over stdin |

Pairing brake: per source address — after 5 wrong codes a peer waits 30 s, doubling per further failure up to 1 h (HTTP 429); other peers and outstanding codes are untouched. (The first version burned every outstanding code after 10 wrong guesses from anyone, which let any LAN client block pairing; replaced 2026-09-24 after a security review.) A code is 8 chars from a 32-letter alphabet and expires in 10 minutes.
Full suite after fixes: 143/143 OK.
