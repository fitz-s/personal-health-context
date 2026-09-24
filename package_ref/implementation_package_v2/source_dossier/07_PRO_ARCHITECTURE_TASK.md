# 07 — Pro Architecture Task

## Read order

Read these files in order:

1. `01_CONTEXT_HANDOFF.md`
2. `02_PRODUCT_CONTRACT.md`
3. `03_SAMPLE_INTERACTIONS.md`
4. `04_ACCEPTANCE_CRITERIA.md`
5. `05_DEFERRED_IMPLEMENTATION_QUESTIONS.md`
6. `06_RAW_INTENT_NOTES.md`

Do not redesign the product before reading all six.

---

# Your role

You are the principal architect for a **single-user conversational personal-health context agent**.

Your job is to turn the product contract into the smallest credible buildable system.

This is not a generic health-app architecture exercise.

The user has already rejected:

- another dashboard;
- another daily report generator;
- another rigid tracker;
- another app to maintain;
- a narrow planner that over-constrains stronger reasoning models;
- excessive edge-case bureaucracy.

---

# Primary objective

Design the minimum viable architecture that lets the user experience:

> “I tell one intelligent system what is happening, and when I have a question about myself, it already has the context to investigate.”

The visible interface should remain ChatGPT-style conversation.

The backend can be technically complex, but the product should not feel complex.

---

# Fixed product decisions

Treat these as requirements unless current platform reality makes one literally impossible:

- primary visible interface: ChatGPT-style conversation;
- local Mac as canonical backend;
- Mac expected to remain online;
- initial sources: Apple Health / Apple Watch and Oura;
- natural input: text, voice transcript, image, file;
- durable context survives across conversations;
- serious questions may begin in fresh conversations;
- capable models should retain broad investigative freedom;
- proactive output should be sparse and tunable;
- unknown-unknown / measurement-gap discovery is desirable;
- no visible second health dashboard unless technically unavoidable;
- avoid over-engineering trivial logging corrections;
- future sources should be addable later.

---

# What to verify

Before making implementation claims, verify current official capabilities where they matter materially, especially:

- Apple Health / HealthKit access path;
- Oura data access and current AI / retention constraints;
- current ChatGPT / OpenAI tool interface possibilities for a local Mac backend;
- current options for model/tool calls, file/image inputs, and persistent local state.

Do not spend the whole answer on vendor-policy trivia.

If a requirement is awkward but solvable with a different transport, solve it.

If something is genuinely blocked, say so and give the least intrusive workaround.

---

# Design principle

Use engineering to **enable model agency**, not to replace it with a deterministic bureaucracy.

A capable model should be able to decide what data/tools it needs for serious questions.

The backend should provide:

- durable facts;
- source access;
- search/query tools;
- files;
- historical context;
- external research when needed;
- safe side-effect boundaries.

Avoid a brittle design where a weaker planner permanently hides potentially relevant context from the stronger model.

---

# Deliverables

Produce one coherent technical design with these sections.

## 1. Minimal system architecture

Show the smallest set of components actually needed.

Explicitly identify:

- what runs on the Mac;
- what ChatGPT interacts with;
- what accesses Apple Health;
- what accesses Oura;
- where durable context lives;
- where model calls happen;
- what is optional.

Prefer fewer moving pieces.

## 2. End-to-end data flows

Show at least these flows:

### Flow A
User says:

> “今天 supplements 就 the usual。”

### Flow B
User uploads a meal photo.

### Flow C
User uploads a lab report.

### Flow D
User starts a fresh conversation and asks:

> “我最近两个月这个训练计划到底有没有产生实际效果？”

### Flow E
Background process decides there is nothing worth saying.

### Flow F
Background process discovers that a previously unresolved question is now worth revisiting.

### Flow G
System decides another kind of measurement would add more information than continued wearable collection.

For each, show only the steps that actually matter.

## 3. Durable context model

Design the minimum persistent data model needed to support:

- imported measurements;
- user-provided facts;
- uploaded files;
- important long-running questions;
- prior meaningful analyses;
- future revisitability;
- provenance.

Do not create a giant ontology unless needed.

Explain what should **not** be stored as durable state.

## 4. Tool surface exposed to the model

Design the model-facing tools / MCP / function boundary.

The model should have enough capability to investigate broadly.

Examples might include:

- search/query personal data;
- retrieve a time range;
- inspect a file/report;
- store a user event;
- search past questions;
- inspect source/vendor context;
- run deterministic computation;
- search the web/research;
- save a meaningful conclusion or open question.

Do not give the model raw dangerous system access if a narrower tool suffices.

## 5. Context strategy

Explain how to combine:

- fresh ChatGPT conversations;
- durable local personal context;
- serious analysis;
- prior important questions;
- large historical datasets;

without requiring one giant permanent prompt.

Avoid over-engineered hidden agent trees unless there is a clear need.

## 6. Background loop

Design the smallest useful proactive loop.

It should support:

- silence;
- meaningful change;
- revisiting an old question;
- noticing a measurement gap;
- surfacing a new potentially useful question.

The user should be able to reduce aggressiveness conversationally.

## 7. Storage / retrieval strategy

Choose practical local-first technologies.

Explain:

- what belongs in a normal DB;
- what belongs as files;
- whether semantic retrieval is actually needed;
- whether RAG is needed and for what;
- whether SQLite is enough initially;
- expected scale.

Avoid unnecessary infrastructure.

## 8. Model usage strategy

Use the fewest model roles that work.

Do not over-fragment the system into dozens of specialist prompts.

Explain:

- what can be deterministic code;
- what should be handled by the main reasoning model;
- whether low-cost parsing models are actually worth the complexity;
- when a stronger reasoning run is justified.

The user prefers capability over over-optimization at this stage.

## 9. Apple Health and Oura first-build integration plan

Give a realistic first implementation path.

Do not require all future sources.

Mark any vendor constraint that genuinely changes the architecture.

## 10. Failure modes that matter

Focus on product-relevant failures:

- data silently not stored;
- duplicate or stale data;
- model invents causal stories;
- important old context cannot be found;
- too much proactive noise;
- model cannot inspect enough context;
- local service unavailable;
- vendor access fails.

Do not create an encyclopedic list of edge cases.

## 11. Minimal repo layout

Provide a small repository structure suitable for a local coding agent.

Avoid premature microservices.

## 12. Phased build plan

Give the shortest sequence that can prove the core experience.

Each phase needs:

- implementation scope;
- acceptance test;
- explicit non-goals.

## 13. Genuine unresolved decisions

End with only the decisions that **actually require the user**.

Do not ask about things a competent engineer can default reasonably.

---

# Technical bias

Prefer:

- local-first;
- standard databases;
- simple services;
- official APIs where practical;
- model tool use;
- inspectable provenance;
- replaceable model layer;
- thin integrations.

Be skeptical of introducing:

- Kubernetes;
- microservices;
- graph databases;
- vector databases;
- event buses;
- complex orchestration frameworks;
- many-model pipelines;
- elaborate deterministic rule engines.

Use them only if you can show a concrete need.

---

# Tone of the deliverable

Be decisive.

Do not produce another brainstorm.

Do not re-explain why wearables cannot measure muscle mass.

Do not lecture about medical limitations the user already understands.

Do not turn every ambiguity into a clarification question.

Make reasonable defaults.

Where uncertainty is implementation-specific, label it and move on.

The output should be detailed enough that a strong local coding agent can begin implementation from it after one review pass.
