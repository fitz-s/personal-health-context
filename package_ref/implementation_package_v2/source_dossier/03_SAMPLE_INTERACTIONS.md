# 03 — Sample Interactions

These examples define the desired behavior more reliably than premature schemas.

---

## 1. Simple supplement logging

**User**

> 今天 supplements 就 the usual。

**Desired behavior**

- Infer that the user is recording an actual event, not asking a hypothetical question.
- Resolve “the usual” from available personal context if possible.
- Persist useful context.
- Reply minimally.
- Do not launch a long analysis unless there is a real reason.

**Bad behavior**

- Ask the user to fill in every supplement manually every time.
- Generate generic advice about supplements.
- Turn one logging statement into a medical lecture.

---

## 2. Meal logging by text

**User**

> I had a Big Mac and fries around 8:30.

**Desired behavior**

- Treat as a real event.
- Save the user's statement.
- Parse what is reasonably useful.
- Avoid pretending that uncertain nutrition estimates are exact.
- Stay quiet unless something materially needs clarification.

---

## 3. Meal logging by image

**User**

> Dinner.

*(photo attached)*

**Desired behavior**

- Understand that the image is intended as a log entry.
- Preserve the photo/context.
- Extract what can be inferred reasonably.
- Do not force the user into a nutrition-tracker workflow.

---

## 4. Lab upload

**User**

> 这是新的 lab report，帮我记下来。

**Desired behavior**

- Treat the document as durable personal context.
- Extract useful structured facts if appropriate.
- Keep the original report available.
- Do not overreact to every abnormal-looking value.
- The user should be able to ask about it later in a fresh conversation.

---

## 5. Serious training question

**User**

> 我最近两个月这个训练计划到底有没有产生实际效果？

**Desired behavior**

The model should be free to inspect whatever is relevant, for example:

- workouts;
- heart-rate response;
- sleep/recovery;
- past performance markers;
- user-reported training changes;
- uploaded body-composition data;
- prior related questions;
- external research if needed.

It should not merely summarize training volume.

It may conclude:

- some adaptations are supported;
- some are not measurable from current data;
- a different measurement would be needed for a stronger answer.

---

## 6. Posture question

**User**

> 我的体态纠正到底有没有改善？

**Desired behavior**

- Do not infer posture from HRV or sleep.
- Search for relevant photos, professional assessments, movement notes, symptoms, and training context.
- If current data cannot answer the question, say so clearly.
- Explain what kind of assessment would materially move the question forward.

---

## 7. “Why am I feeling worse?”

**User**

> 我最近总觉得状态变差了，看看有什么值得注意的。

**Desired behavior**

- Investigate broadly rather than forcing one metric.
- Look across recent context.
- Separate observed changes from plausible explanations.
- If no strong conclusion is available, identify what is known and what remains uncertain.
- Avoid manufacturing a causal story.

---

## 8. Old question becomes answerable

**Background**

Three months earlier the user asked whether a new training block was producing meaningful adaptation. At the time, the system said evidence was insufficient.

**New situation**

Enough relevant data has accumulated.

**Desired proactive message**

> 你三个月前问过这个问题。当时证据不够。现在有新的可比数据，值得重新看一次。

Then the system should be able to perform the new analysis.

---

## 9. Same data is no longer informative

**Background**

The user cares about muscle growth, but has only accumulated more wearable data.

**Desired proactive behavior**

> 继续收集同样的 wearable 数据对“肌肉量是否增长”这个问题的边际价值已经很低。如果你仍然在意这个问题，下一次真正提高可回答性的会是更直接的 body-composition measurement。

The exact measurement recommendation is an analysis decision, not hard-coded into the product definition.

---

## 10. Unknown unknown

**Situation**

The user never asked about a potentially relevant domain.

**Desired behavior**

The system may surface a new question only if it can explain why the information might change understanding or decisions.

Good:

> 你最近一直在追踪 X，但现有数据无法区分 A 和 B。如果你还在意这个目标，可能值得考虑是否要测 Y。

Bad:

> 你从来没测 Y，所以你可能有问题。

---

## 11. Ordinary day with nothing interesting

**Desired behavior**

No message.

Silence is correct.

---

## 12. User says “talk less”

**User**

> 最近你提醒得太多了，少说一点。

**Desired behavior**

- Treat this as a persistent interaction preference.
- Reduce proactive interruption rate.
- No need for a settings maze.

---

## 13. Fresh conversation, same durable context

**User starts a new chat**

> 我之前不是有那个体态问题吗？现在重新看一下。

**Desired behavior**

- The new chat should not need the entire old conversation history.
- Durable context should make the question recoverable.
- Retrieve relevant facts and prior investigation state as needed.

---

## 14. Vendor-native context remains useful

**User**

> Oura 最近为什么一直给我这个状态？

**Desired behavior**

- Inspect Oura-specific information when relevant.
- Respect the fact that Oura's proprietary interpretation may itself be useful evidence.
- Do not unnecessarily replace Oura's own specialist interpretation with a homemade score.

---

## 15. Broad investigative freedom

**User**

> 我最近几个月到底发生了什么？有没有什么我自己没注意到但值得看的？

**Desired behavior**

This is intentionally broad.

The model should be allowed to:

- inspect multiple data domains;
- compare time periods;
- search prior notes/questions;
- use external research;
- decide that nothing important stands out;
- identify a possible measurement gap;
- surface a small number of genuinely interesting findings.

It should not be forced into a fixed report template.
