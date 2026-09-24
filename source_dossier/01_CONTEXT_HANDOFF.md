# 01 — Context Handoff

## Purpose

This document gives the Pro architecture model the product history and mental model. It is intentionally not a final architecture.

The central instruction is:

> **Do not over-design the product before understanding the user's mental model.**
>
> The product should feel like one intelligent person, one interface, one conversation box. The backend may be complex; the user-facing experience should not be.

---

## 1. Starting point

The user already has or expects to use multiple personal-health data sources, especially:

- Apple Watch / Apple Health
- Oura Ring
- potentially later: WHOOP, Eight Sleep, CPAP / respiratory data, smart scales, labs, body-composition assessments, posture / movement assessments, and other sources

The original problem was not lack of data. It was that data and interpretation are scattered across multiple apps, each with its own metrics, scores, and interaction model.

The user does not want to open five or six apps merely to understand what has been happening.

A previous attempt at combining multiple sources into an LLM-generated analysis failed because the output became repetitive and low-value:

- “you slept later”
- “your stress was higher”
- “your recovery was lower”
- generic advice that could have been produced from one app alone

This failure is a first-class product requirement.

The new system must not collapse into “daily wearable summary + nicer prose.”

---

## 2. First false direction: another report generator

An early idea was:

- pull data from all devices;
- compare trends;
- generate daily or weekly reports;
- alert on changes;
- show “progress.”

The user challenged this directly.

If the system simply wakes up an LLM more often and asks it to summarize the same data, it is not a new capability. It is just repeated summarization.

A one-off LLM call over wearable data and a scheduled LLM call over the same wearable data are behaviorally almost the same product.

The product must create a new capability, not just a new cadence.

---

## 3. Second false direction: endless “improvement” as the reward loop

Another early framing was that the product should reinforce healthy behavior by showing continuous improvement.

The user rejected this as structurally impossible.

A human body does not improve forever:

- sleep cannot get better every day indefinitely;
- recovery eventually plateaus;
- training adaptation levels off;
- aging exists;
- health fluctuates;
- maintenance is often the correct outcome.

Therefore the product cannot depend on “you are better than last week” as its engagement engine.

The durable value must instead come from:

> accumulating a better map of what is happening, what has changed, what remains uncertain, what evidence exists, and what observation could reduce uncertainty next.

The system should produce meaning, not artificial positivity.

---

## 4. Third false direction: asking wearables to answer unmeasured questions

The user's important questions are often not:

> “How did I sleep last night?”

They are closer to:

- Is a posture-correction plan actually working?
- Is a suspected pelvic / biomechanical issue improving?
- Am I gaining muscle?
- Is fat decreasing?
- Is visceral fat changing?
- Is a training block producing real adaptation?
- Are there hormone-related changes?
- Why has mood changed?
- Is a supplement producing a meaningful effect?
- Is there some important question I have not even thought to ask?

Oura and Apple Health do not directly measure many of these things.

The user already understands that.

The product implication is not “invent answers from wearables.”

It is:

> recognize when the current data has reached its limit, and sometimes identify what new measurement or assessment would actually make the question more answerable.

That may mean a lab, body-composition assessment, movement evaluation, standardized performance measurement, or simply waiting for more data.

---

## 5. Data as option value

The user prefers to preserve as much useful data as practical when collection cost is low.

Not because every data point must produce an immediate conclusion, but because future questions are unknown.

Examples:

- wearable data may later contextualize a lab result;
- training history may matter when reviewing body composition;
- supplement timing may matter only after another question emerges;
- old subjective notes may become useful once paired with later evidence;
- a future clinician or specialist may benefit from a coherent longitudinal archive.

The archive therefore has option value.

---

## 6. Core product idea that survived the brainstorm

The strongest remaining idea is:

> **One intelligent agent sits between the user and all underlying personal-health data, apps, measurements, research, and past context.**

The user should not have to decide:

- which app to open;
- which metric matters;
- which source to inspect;
- which time window to use;
- whether literature is needed;
- whether a prior lab or assessment is relevant;
- whether a new measurement would be informative.

The visible experience should be:

> **one interface, one person, one dialog box.**

The user explicitly does not want another Guava-like app, another dashboard, or another health interface to maintain.

The backend may be complicated. The visible product should not be.

---

## 7. Preferred visible interface

The preferred user-facing interface is ChatGPT.

The user wants to interact naturally through:

- text;
- voice transcript;
- photo;
- file upload;
- arbitrary questions.

Examples:

> “今天 supplements 就 the usual。”

> “我今天吃了一个 Big Mac 和薯条。”

> “这张照片就是我今天晚饭。”

> “这是新的 lab report。”

> “我最近两个月这个训练到底有没有意义？”

> “我以前那个体态问题现在到底有没有改善？”

The user does not want to maintain structured forms.

The LLM should absorb ordinary ambiguity and transform messy input into whatever representation the backend needs.

Earlier drafts over-specified correction semantics, review inboxes, uncertainty objects, confirmation flows, and routine schemas.

Some of those may be useful internally, but they are implementation details, not the user experience.

The user explicitly does not want trivial logging edge cases to dominate the design.

---

## 8. One agent, not a maze of deterministic gates

Earlier thinking proposed a narrow planner that would first decide which small subset of data a reasoning model was allowed to see.

The user pushed back.

The concern is that a weaker planner may discard information that a stronger model would realize is relevant.

The preferred principle is:

> for serious analysis, give the capable model broad access to the available personal data and tools, and let it decide what to inspect.

This does not mean giving an LLM unrestricted unsafe side effects.

It does mean avoiding a rigid ontology that says “this question can only look at these six metrics.”

Engineering should support the agent's intelligence rather than replacing it with hundreds of tiny deterministic rules.

---

## 9. Context isolation: what “context pollution” actually meant

The user’s concern about context pollution does not require an elaborate hidden multi-agent architecture.

The intended mental model is simpler:

- durable personal facts and context live outside one chat;
- serious analytical questions can start in fresh conversations;
- the model can retrieve what it needs from the durable store;
- unrelated conversation history does not need to contaminate the new analysis.

Think:

> **shared durable ground truth + fresh reasoning conversations**

Do not over-engineer context sharding unless implementation later proves it necessary.

---

## 10. Passive data and active user input

### Passive / imported data

Initial source scope should stay small:

- Apple Health / Apple Watch
- Oura

Other sources may be added later.

### Active user input

The user may tell the agent:

- meals;
- supplements;
- symptoms;
- mood;
- subjective workout experience;
- routine changes;
- training changes;
- discomfort or injuries;
- lab results;
- professional assessments;
- relevant events that may matter later.

The user does not want to manually maintain the data schema.

The LLM is expected to interpret and store useful context.

---

## 11. External app interaction

The user is interested in the agent also interacting with native apps when useful.

For example, if the user tells ChatGPT what was eaten or which supplements were taken, the system may store it locally and optionally write a related item into Oura or another native app.

But this is not a current product-definition blocker.

Do not let this phase become stuck on:

- exact Oura API write capabilities;
- exact HealthKit mechanics;
- browser automation details;
- MCP plan restrictions;
- auth transport;
- connector quirks.

Those are implementation problems for the coding / architecture pass.

The product requirement is simply:

> when useful and practical, the agent may interact with native systems as part of its work.

---

## 12. Canonical storage preference

The user expects the total archive to remain comfortably below ~100 GB.

Preferred deployment philosophy:

- local Mac backend;
- Mac effectively always online;
- local canonical data store;
- iCloud is acceptable for backup;
- no cloud-first data platform unless implementation genuinely requires one.

This is a single-user personal system.

Avoid infrastructure for infrastructure’s sake.

---

## 13. Background behavior

The system may continuously or periodically:

- sync data;
- parse and store user-entered facts;
- maintain historical context;
- compare new information with old;
- run analysis;
- revisit long-running questions;
- notice when a question becomes more answerable;
- notice when existing data is no longer adding much information;
- identify when a new measurement could reduce uncertainty;
- search professional literature when useful.

But frequent background work does not imply frequent user-visible reports.

The system may run often and speak rarely.

---

## 14. Proactive behavior

The user is comfortable with a relatively exploratory proactive agent.

The exact aggressiveness is a tunable preference, not a core architecture decision.

The user expects that if it becomes annoying, they can simply say:

> “Talk less.”

More interesting proactive events include:

### A. Something meaningful changed

Not a single noisy day, but a real shift.

### B. A long-running question gained evidence

A question that used to be unresolved now has new information.

### C. A question became answerable

The system can say:

> “You asked this before. At the time we did not have enough evidence. We do now.”

### D. A new measurement is now worth doing

The system can say:

> “Continuing to collect the same signals is unlikely to answer this. If you still care about it, measurement X would add much more information.”

### E. A new question may be worth asking

The system may surface an important question the user never thought to ask.

This is desirable, but it must not become fear-generating pseudo-diagnosis.

---

## 15. Psychological function

The user explicitly reflected that part of the motivation is psychological.

Humans seek certainty through religion, divination, science, medicine, quantified-self systems, and now AI.

The user recognizes that biological systems are chaotic and total certainty is impossible.

Therefore the product should not pretend to provide ultimate certainty.

But it should provide the feeling that:

- things are being observed;
- information is accumulating;
- important questions are not forgotten;
- evidence can be recovered later;
- uncertainty can sometimes be reduced;
- the system may notice something the user did not think to investigate.

It is partly an analytical tool and partly a long-term cognitive prosthesis.

That function should be acknowledged, not exploited.

---

## 16. What the system is not

The system is explicitly **not**:

- another dashboard;
- another health app;
- another daily wellness newsletter;
- a fake doctor;
- a causal-story generator;
- a rigid decision tree;
- a giant confirmation workflow;
- a compliance-theater project;
- an architecture museum;
- a universal “health score.”

Do not redesign it into Oura, WHOOP, Guava, Apple Health, or a medical portal.

---

## 17. Why native vendor apps still matter

The system does not need to replace what the vendors already do well.

Oura can continue to compute Oura-specific interpretations.

Apple can continue to provide Apple Health data.

The new system does not need to invent a “better Oura score.”

Its value is:

> the user no longer has to personally act as the integration layer across every source, every app, every question, and every time horizon.

---

## 18. What the LLM should be allowed to do

For serious questions, the model should be allowed to decide whether it needs:

- recent Apple Health data;
- longer-term Oura history;
- workouts;
- sleep;
- meals;
- supplements;
- uploaded labs;
- prior assessments;
- previous questions;
- external professional research;
- calculations;
- a recommendation for another measurement;
- no additional data at all.

The system should not force every question through one fixed workflow.

The model should be able to investigate.

---

## 19. Reporting philosophy

Periodic internal runs may exist.

A periodic user-visible report does not have to exist.

If there is nothing worth saying, silence is a valid output.

When the system does speak, the useful shape is approximately:

1. Why now?
2. What matters?
3. What evidence supports this?
4. What remains unknown?
5. What next, if anything?

This is a product principle, not a mandatory template.

---

## 20. Important reversals

The Pro model should understand these reversals so it does not recreate discarded ideas.

### Dashboard -> agent
Initial idea: unify metrics into one view.  
Current preference: ChatGPT is the visible interface.

### Daily report -> investigation
Initial idea: produce daily/weekly health summaries.  
Current preference: background analysis can run often; user-visible output should be sparse.

### Progress score -> uncertainty reduction
Initial idea: reinforce behavior by showing improvement.  
Current preference: bodies plateau; value comes from understanding and information gain.

### Narrow retrieval -> broad model discretion
Initial idea: a planner tightly filters what the reasoning model may inspect.  
Current preference: let a capable model investigate broadly.

### Architecture-first -> product-first
Earlier discussion over-focused on APIs, permissions, correction semantics, exact model routing, and companion apps.  
Current preference: define the experience first; implementation details can be solved during coding.

### Extra companion UI -> one interface
The user does not want a visible extra app. If an invisible helper is technically required later, that is an implementation detail.

---

## 21. Current first-build source scope

Start with:

- Apple Health / Apple Watch
- Oura

Do not let the source list explode before the interaction loop is proven.

---

## 22. Actual MVP question

The MVP is not:

> “Can we sync Apple Health and Oura?”

The MVP question is:

> **Can one ChatGPT-style interface become the user’s default way to record, investigate, and revisit personal health context without making the user manage multiple apps or read useless summaries?**

A successful MVP should make the user feel:

- I can tell it something naturally;
- it remembers;
- I can ask a serious question later;
- it can inspect relevant history;
- I do not have to choose the dataset manually;
- it sometimes surfaces something genuinely worth knowing;
- it can tell me when a different measurement would be more informative;
- I do not have to maintain another health app.

---

## 23. Strong instruction against over-execution

This brainstorm repeatedly became less useful when the assistant tried to solve too much too early.

Avoid over-specifying:

- correction semantics;
- uncertainty-state taxonomies;
- report templates;
- write-back workflows;
- model routing;
- proactive thresholds;
- retrieval gates;
- companion-app details;
- micro-permission models.

The user’s correction is:

> **The LLM itself is part of the system. Let it absorb some complexity.**

Engineering should support the agent rather than replacing its intelligence with a deterministic bureaucracy.

---

## 24. Final product mental model

The best current description is:

> **A single conversational personal-health agent backed by a durable local personal-context store.**

The user talks to one agent.

That agent can:

- remember;
- read passive health data;
- record new context;
- search history;
- use research;
- compare periods;
- revisit old questions;
- notice when more information is needed;
- sometimes suggest what to measure next;
- interact with native systems when useful;
- stay quiet when there is nothing worth saying.

The user should not experience:

> “Apple Health + Oura + DB + MCP + SQL + RAG + background workers + model routing.”

The user should experience:

> **“I tell one intelligent system what is happening, and when I have a question about myself, it already has the context to investigate.”**
