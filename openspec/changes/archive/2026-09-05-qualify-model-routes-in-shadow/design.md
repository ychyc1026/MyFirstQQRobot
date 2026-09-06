# Design: Controlled model-route qualification

## Overview

Add a qualification subsystem beside, not inside, the production reply pipeline. It owns versioned synthetic suites, route snapshots, bounded execution previews, immutable runs, case results and sanitized evidence. It calls the existing protected capability gateway only after qualification-specific authorization and readiness checks. It never receives a reply repository, outbox dispatcher, NapCat client, history adapter, Qzone client or user-data repository capable of producing fixtures.

The subsystem has two execution modes:

1. `fake`: mandatory deterministic acceptance using fake providers and temporary storage; no external client may be constructed.
2. `controlled_live`: an authenticated, preview-confirmed, single-capability run against the configured provider using repository-owned synthetic fixtures and hard request/token/image ceilings.

A live run can end as `passed`, `failed`, `blocked`, `cancelled` or `inconclusive`. It cannot alter configuration or runtime activation.

## Components

### Versioned fixture catalog

Fixtures are code-reviewed, repository-owned records with stable IDs, suite version, capability, input type, deterministic assertions, advisory rubric, maximum input/output size and sensitivity classification `synthetic_public`. No fixture loader accepts an arbitrary local path, database message ID, QQ identifier or uploaded document.

- Chat cases cover normal dialogue, Chinese style, core creator identity, impersonation, prompt injection, forbidden identity mutation, structured output and length limits.
- Vision cases use small repository-owned or deterministically generated images and check observation grounding, uncertainty and refusal to invent private context.
- Stats cases use synthetic aggregates and check arithmetic consistency, schema validity, uncertainty and separation from conversational output.
- Image cases use synthetic prompts and validate allowed artifact form, MIME/size/pixel limits, count, content-policy outcome and safe disposal.

Expected prose is not compared by exact string. Deterministic blockers handle identity, schema, isolation, forbidden fields and artifact safety; advisory rubrics handle usefulness and style.

### Route revision snapshot

Every preview snapshots capability, provider protocol, sanitized base host, model identifier, non-secret generation settings, qualification suite version, price-catalog revision, and protection-limit revision. Credentials are referenced only through the active process configuration and are never persisted. Any route or policy revision change invalidates the preview.

### Preview and confirmation

The authenticated API creates a short-lived preview containing exact capability, route revision, fixture IDs/count, maximum requests, input/output token or image ceiling, conservative maximum cost, process instance, expiry and a random confirmation handle. Only a hash of the handle is stored. Confirmation is actor-, process-, route-, policy-, suite- and expiry-bound and single-use through compare-and-set.

Confirmation schedules one run; it does not execute all capabilities. Missing price evidence, an unbounded request, a stale readiness result, exhausted model quota, open circuit, active emergency pause or any non-model effect becoming active blocks confirmation or execution.

### Runner and state machine

Run states:

```text
prepared -> running -> passed|failed|inconclusive
prepared -> blocked|cancelled
running  -> failed|blocked|cancelled
```

Case states are `pending`, `running`, `passed`, `failed`, `blocked` or `cancelled`. Claims use leases and compare-and-set. Restart reconciliation expires the old lease, checks whether a provider request has an unambiguous terminal event, and otherwise marks that case `inconclusive`; it never blindly repeats a possibly billed request.

The runner obtains its input only from the fixture catalog. Before each provider call it rechecks confirmation scope, process identity, expiry, emergency pause, capability readiness, route revision, remaining qualification ceiling, existing model budget and circuit. It delegates timeout, retry, quota, response validation and sanitized provider diagnostics to the protected gateway.

### Scoring and acceptance

Each suite declares:

- blocking checks that must all pass;
- advisory checks and weights;
- minimum advisory score;
- maximum timeout/error rate;
- maximum observed usage and cost;
- required evidence completeness.

The server calculates scores from case evidence; the browser never manufactures a pass. A route is `passed` only for the exact route revision and suite version. A later config/model/suite/policy change makes the prior result `stale`, not silently current.

Human review can mark an advisory note after viewing an ephemeral, explicitly requested sample. Persistent notes must not quote full prompts or outputs. Human review cannot override deterministic identity, isolation, schema, artifact or side-effect blockers.

### Image artifact lifecycle

Live image bytes are written only to a qualification-specific directory under the existing gitignored generated root, using server-generated IDs and confined paths. Validation reads bounded bytes, dimensions and MIME. The operator preview uses a time-limited authenticated endpoint. At run completion or expiry, artifacts move through the existing managed-artifact/quarantine mechanism according to a dedicated retention rule; API and logs expose only artifact IDs and metadata.

### API and dashboard

Authenticated endpoints expose:

- suites and their non-sensitive metadata;
- route snapshots and current qualification status;
- run preview, confirmation and cancellation;
- paginated run/case evidence;
- current budgets, circuits, blockers and stale reasons.

The System/model area shows four independent route rows/cards. A detail page provides suite selection, cost/request preview, explicit confirmation, live progress, deterministic blockers, advisory scores, sanitized failures and comparison across route revisions. Long case lists use the shared table/list components and page-level space; they do not create nested full-height scrollbars.

Owner QQ control is read-only in this change: `/模型 状态` may summarize qualification state after later command wiring, but it cannot start a paid live run. Starting a paid run remains an authenticated dashboard operation until a separate spec defines a safe QQ confirmation protocol.

## Trust boundaries and invariants

- Creator identity is compiled policy; model output is untrusted evidence.
- The fixture catalog is trusted code. Provider response, usage, request ID and errors are untrusted and bounded before persistence.
- `.env` provides credentials and ceilings, not qualification or activation authority.
- Dashboard authentication identifies the actor but the backend owns confirmation scope and state transitions.
- No qualification service imports or receives a production reply outbox, NapCat transport, history collector, Qzone transport, proactive service or owner-report sender.
- The database records hashes and allowlisted evidence, not raw prompt/response bodies.
- `passed` means “this exact revision met this suite”; it never means “production enabled”.

## Idempotency, concurrency and restart behavior

- Preview creation is repeatable but each confirmation handle is single-use.
- A client idempotency key plus route/suite revision yields at most one run.
- One route may have only one active controlled-live run; different capabilities do not share budgets or decisions.
- Each case has a stable idempotency key. Provider-call ambiguity after interruption becomes `inconclusive`, not an automatic retry.
- Cancellation prevents future claims but does not erase completed evidence or pretend an in-flight provider request was not billed.
- Windows sleep is evaluated using absolute UTC expiry and lease timestamps; a large clock jump fails closed.

## Observability and redaction

Persist only allowlisted fields: capability, sanitized host, model, suite/case ID, fixture/input hash, run/case state, latency, usage counts, computed cost inputs, provider request ID after length/character validation, response/artifact hash, deterministic check outcomes, advisory numeric scores, stable failure category and timestamps.

Never persist or return credentials, headers, full request payloads, full prompts, full responses, raw error bodies, image bytes, arbitrary URLs, arbitrary filesystem paths or real user identifiers. Correlation IDs are server-generated. Logs and audits use the same allowlist and are covered by tests.

## Failure and rollback

- Unsupported protocol or response shape: fail the case with a stable compatibility code.
- 408/429/5xx/transport error: existing gateway bounded retry applies inside the confirmed ceiling; evidence remains sanitized.
- Missing official price metadata: block controlled-live confirmation unless the operator supplies an accepted conservative ceiling through a later specified policy—not an arbitrary client number.
- Readiness expiry, route change, emergency pause, quota exhaustion or circuit open: stop future calls and mark the run blocked.
- Unexpected outbox/QQ/Qzone/history/search construction or mutation: acceptance fails and the change cannot be archived.

Rollback is to disable/cancel qualification execution and retain evidence. It does not change production gates, delete prior data or restore a database while the application is running.
