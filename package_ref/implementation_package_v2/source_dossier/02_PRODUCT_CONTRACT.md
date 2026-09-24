# 02 — Product Contract

This is the shortest authoritative product contract for the next architecture pass.

## Non-negotiable product shape

1. **One visible interface**
   - ChatGPT-style conversation is the preferred user surface.
   - Do not require the user to manage another dashboard or health app.

2. **One durable personal context**
   - Personal facts, imported data, files, and important long-running context survive across conversations.
   - A single conversation is not the database.

3. **Fresh reasoning when useful**
   - Serious questions may start in fresh conversations.
   - Durable context is retrieved from the backend rather than relying on one infinitely long chat.

4. **Broad model agency**
   - A capable model should be free to inspect relevant personal data, past context, tools, and research.
   - Do not prematurely reduce every problem to a tiny predetermined evidence bundle.

5. **Natural input**
   - Text, voice, photo, and file inputs should be handled conversationally.
   - The LLM should absorb ordinary ambiguity instead of forcing the user through forms.

6. **Initial sources**
   - Apple Health / Apple Watch
   - Oura
   - Additional sources are deferred.

7. **Local-first**
   - Mac is the expected backend.
   - Mac is effectively always online.
   - Total archive expected to remain below ~100 GB.
   - iCloud is sufficient for backup if needed.

8. **Background work may be frequent; user output should be sparse**
   - The system may sync, compare, revisit, and research often.
   - Silence is valid when nothing important happened.

9. **Unknown-unknown discovery is desirable**
   - The agent may surface useful questions the user did not think to ask.
   - It may also identify when a new measurement would add more information than continued passive tracking.

10. **Native apps remain specialists**
   - The system may use vendor outputs and native apps when useful.
   - It does not need to replace vendor-specific algorithms or views.

11. **Not a medical institution**
   - The system can organize, reason, compare, retrieve evidence, and suggest informative measurements.
   - It should not manufacture certainty or invent diagnoses from missing data.

12. **Do not over-engineer trivial logging edge cases**
   - Minor ambiguity is generally something the LLM can handle.
   - Do not design the whole product around one-capsule-vs-two-capsule corrections.

## Product success condition

The user should feel:

> “I can tell one intelligent system what is happening, and later ask it serious questions about myself. It already knows where to look, what history matters, what it still cannot know, and when another measurement would actually help.”
