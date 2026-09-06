# Change: Qualify model routes in controlled shadow mode

## Why

The project has independent chat, vision, image, and stats gateways with budgets, circuit breakers, readiness guards, sanitized evidence, and a chat shadow path. The local configuration now contains SiliconFlow candidate routes, but those routes have not been accepted for protocol compatibility, behavioral quality, cost, identity adherence, private-context isolation, or failure handling. Enabling real QQ replies before this evidence exists would turn configuration into an unearned production decision.

The next safe step is a reproducible qualification system that can run entirely with fakes and can optionally perform a small, explicitly confirmed live model-only test with synthetic fixtures. A passing result is evidence for later operator selection; it is not permission to send QQ messages or enable a worker.

## Goals

- Define versioned synthetic qualification suites and deterministic structural checks for chat, vision, stats, and image routes.
- Keep the four capabilities independent in credentials, model selection, quotas, circuit state, run state, evidence, and pass/fail decisions.
- Check protocol compatibility, response validity, latency, usage/cost evidence, error classification, identity adherence, prompt-injection resistance, and private-context isolation.
- Require authenticated preview and single-use confirmation before any live provider call, with an explicit route, fixture count, token/image ceiling, expiry, and estimated maximum cost.
- Record sanitized, reproducible qualification evidence without prompts, response bodies, secrets, raw provider errors, private files, or generated image bytes in API DTOs and logs.
- Add dashboard controls and detailed results using the existing YCH/shadcn design system.
- Prove that qualification never creates reply outbox work, delivers QQ content, accesses history/Qzone, or activates a production route.

## Non-goals

- Enabling the reply worker, outbox dispatcher, automatic replies, proactive messages, owner-report delivery, QQ history collection, or QQ space access/publication.
- Using real QQ messages, chats, histories, user documents, diaries, personas, memories, or Qzone data as qualification fixtures.
- Automatically changing `.env`, selecting a production model, enabling a failed or passed route, or implementing cross-provider failover.
- Adding streaming replies or provider-specific features unrelated to qualification.
- Treating aesthetic image scoring or model-judged prose scoring as an unquestionable security boundary.

## Real effects and data handling

Creating this OpenSpec performs no network call. Its implementation may introduce one new operator-triggered external effect: a bounded model request to the configured provider for a single selected capability. The live path SHALL remain unavailable until an authenticated operator previews and confirms an unexpired plan. It SHALL use only repository-owned synthetic fixtures and SHALL not construct or invoke NapCat, QQ, Qzone, history, search, proactive, owner-report, or delivery clients.

Provider credentials remain in gitignored `.env` and process memory. Qualification evidence stores route/model identifiers, suite versions, hashes, timestamps, latency, usage, cost calculation inputs, status codes, stable failure categories, checks and scores; it does not store credentials, Authorization headers, complete prompts, complete responses, provider error bodies or generated image bytes.

## User decisions

Already decided:

- Provider family: SiliconFlow for the current candidate routes.
- Chat and image generation must be separate; vision and stats also remain independent routes.
- Model choices should avoid excessive cost without accepting obviously weak quality.
- Real QQ effects remain off during this stage.

Deferred until implementation reaches candidate review:

- Exact final model identifiers and accepted cost ceilings. The implementer should verify the current official SiliconFlow catalog and prices because these are time-sensitive, then present candidates and evidence without exposing the API key.
- Whether a route that passes qualification should later become the production route. Passing never applies that change automatically.

## Risks

- **Synthetic tests overfit**: keep suites versioned, cover adversarial and ordinary cases, separate structural blockers from quality scores, and retain human review samples through safe ephemeral display only.
- **Cost estimate differs from provider billing**: store price-catalog version/source and conservative ceiling inputs; stop at the request/token/image cap even if price metadata is unavailable.
- **A prompt or response leaks into logs**: use fixture/result hashes and allowlisted evidence DTOs; test log, database, audit, and error redaction.
- **Qualification accidentally reaches delivery code**: compose the runner without outbox or NapCat dependencies and assert unchanged effect tables with fail-if-called fakes.
- **One passing capability authorizes another**: key all previews, runs, budgets, circuits and decisions by exact capability and route revision.
- **Model-based scoring hides unsafe behavior**: core identity, schema, isolation, forbidden-field and artifact checks remain deterministic blockers; optional judge scores are advisory and separately labeled.

## Migration

Implementation is expected to add an additive schema migration after v32 for suite definitions, route revisions, previews, runs, case results and sanitized artifacts. New tables start empty and no route starts qualified. Existing model protection events and counters remain authoritative for request budgets and circuit state.

Rollback disables qualification execution, cancels unclaimed previews/runs, removes no evidence, and leaves all delivery/history/Qzone gates off. If code rollback is required, additive tables remain inert. Do not down-migrate or restore over a running database.
