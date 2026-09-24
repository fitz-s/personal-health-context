# 04 — Acceptance Criteria

These criteria are product-level. They are not intended to over-specify implementation.

## A. Capture

The MVP passes if the user can naturally provide:

- a short text fact;
- a voice-transcribed fact;
- a photo;
- a document;

and the system can turn that into durable personal context without requiring the user to manage a separate tracking UI.

## B. Memory across conversations

The MVP passes if:

- the user can start a fresh conversation;
- refer to an earlier issue or long-running question;
- and the system can recover relevant durable context without relying on the old chat transcript alone.

## C. Cross-source investigation

The MVP passes if a serious question can cause the model to inspect relevant information across at least:

- Apple Health / Apple Watch;
- Oura;
- user-entered context;
- uploaded files / notes where relevant.

The user should not have to manually select which source to query.

## D. Broad model discretion

The MVP fails if every question is forced through a rigid narrow retrieval plan that routinely hides potentially relevant context from the stronger reasoning model.

The architecture may still use indexing, SQL, retrieval, summaries, and caching.

The criterion is that these mechanisms support investigation rather than artificially constraining it.

## E. Sparse proactive behavior

The MVP passes if the background system can run without forcing a daily report.

There must be a valid “nothing worth telling the user” outcome.

## F. Revisitability

The MVP passes if the system can retain an important unresolved question and later revisit it when new evidence becomes available.

## G. Measurement-gap awareness

The MVP passes if, for at least one representative question that cannot be answered from current wearable data, the system can identify that additional passive collection is insufficient and explain what kind of new measurement or assessment would make the question more answerable.

## H. No fake certainty

The MVP fails if the system routinely converts correlation, coincidence, or missing measurements into confident causal stories.

## I. One-interface experience

The MVP fails from a product perspective if normal use requires the user to maintain a second visible health dashboard or repeatedly switch to a custom data-entry app.

Invisible helper components are implementation details and are acceptable if necessary.

## J. Local-first persistence

The durable personal context must remain under the user's local control on the Mac, with ordinary backup possible.

The system should not depend on a cloud-only proprietary archive as the sole source of truth.

## K. Extensibility without premature integration sprawl

The MVP should support Apple Health and Oura first.

The architecture should make later sources possible, but implementation is not required to integrate every wearable before proving the core experience.

## L. User trust

A representative test session should leave the user with the sense:

> “This agent knows where to look, does not make me manually manage the data, does not spam me, and can tell the difference between what it knows and what would require a new measurement.”
