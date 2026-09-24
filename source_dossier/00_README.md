# Personal Health Context Agent — Pro Handoff Package v1

This package freezes the **product intent** and gives a Pro-level architecture model everything needed to produce the implementation design without restarting the brainstorm.

## Read order

1. `01_CONTEXT_HANDOFF.md` — why the product exists, rejected directions, final mental model
2. `02_PRODUCT_CONTRACT.md` — short authoritative requirements
3. `03_SAMPLE_INTERACTIONS.md` — concrete behavior examples
4. `04_ACCEPTANCE_CRITERIA.md` — how to tell whether the MVP is actually the right product
5. `05_DEFERRED_IMPLEMENTATION_QUESTIONS.md` — things intentionally postponed to engineering
6. `06_RAW_INTENT_NOTES.md` — selected original user statements that preserve nuance
7. `07_PRO_ARCHITECTURE_TASK.md` — exact prompt/task for the Pro model

## How to use this package

Attach all seven files to a fresh Pro conversation.

Use the text below as the initial message:

> Read the attached handoff package in the numbered order. Treat `02_PRODUCT_CONTRACT.md` as the authoritative product requirements and `01_CONTEXT_HANDOFF.md` + `06_RAW_INTENT_NOTES.md` as the intent/history needed to avoid repeating discarded designs. Then execute `07_PRO_ARCHITECTURE_TASK.md` completely. Do not restart the product brainstorm. Do not ask me implementation questions that a competent engineer can decide with a reasonable default. Verify current external platform constraints only where they materially affect the architecture.

## What this package intentionally does not contain

It does not freeze:

- exact DB schema;
- exact MCP transport;
- exact OpenAI model IDs;
- exact Apple/Oura connector mechanics;
- exact notification thresholds;
- exact UI design;
- exact RAG implementation;
- exact correction semantics.

Those belong to the technical architecture pass.

## Current product in one sentence

> **A single conversational personal-health agent, backed by a durable local personal-context store, that can record what happens, investigate serious questions across history, revisit unresolved questions later, and occasionally identify what new information would be worth obtaining — without becoming another dashboard or daily-summary app.**
