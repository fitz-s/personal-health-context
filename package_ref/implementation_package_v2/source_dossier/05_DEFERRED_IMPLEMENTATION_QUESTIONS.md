# 05 — Deferred Implementation Questions

These are deliberately **not** product-design blockers.

The Pro architecture pass may resolve them, prototype them, or defer them.

Do not ask the user about them unless a real fork remains after technical investigation.

## Data / storage

- exact database choice;
- whether to use SQLite only or add Parquet / DuckDB;
- whether semantic retrieval is needed and where;
- raw-object file layout;
- snapshot / iCloud backup mechanism;
- exact indexing strategy.

## Apple Health / Oura integration

- exact Apple Health transport path;
- exact Oura API / MCP / export path;
- write-back mechanics;
- browser or app automation fallbacks;
- auth storage;
- rate limits;
- deletion semantics;
- source deduplication.

## ChatGPT / local backend transport

- MCP vs another tool interface;
- plan-specific write limitations;
- secure tunnel details;
- whether a small relay is needed;
- exact local daemon protocol.

## Model orchestration

- exact model names;
- exact routing thresholds;
- whether a cheap model pre-processes input;
- whether serious questions call another model in the background;
- caching;
- retry policy;
- context-window engineering.

## Logging internals

- exact schema for meals / supplements / symptoms;
- correction semantics;
- routine-version semantics;
- confidence fields;
- whether low-confidence entries need review.

Use reasonable engineering defaults.

Do not expose these details to the user unless they materially affect the experience.

## Proactive system

- exact schedule;
- exact thresholds;
- exact notification channel;
- exact “talk less” control representation;
- exact ranking of proactive candidates.

The only hard product requirement is that proactive behavior be useful, tunable, and allowed to remain silent.

## UI

- no need to design a dashboard;
- no need to decide colors, layout, mobile design system, or chart library;
- no visible companion app unless implementation truly requires it.

## Clinical / professional export

- useful later;
- not required to prove the core experience;
- should remain possible because durable provenance is valuable.

## Security / privacy implementation

- must be reasonable for sensitive personal data;
- exact encryption/keychain/runtime design belongs to the technical pass;
- do not let security mechanics redefine the product experience unless necessary.
