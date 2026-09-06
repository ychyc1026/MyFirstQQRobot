# Dashboard Operations

## Purpose

Define the authenticated local dashboard as a truthful, auditable operator surface.

## Requirements

### Requirement: Authenticated control surface
The dashboard SHALL require an expiring authenticated session for protected APIs and SHALL NOT rely on hidden UI controls as authorization.

#### Scenario: Unauthenticated client requests system status
- **WHEN** a protected dashboard endpoint receives no valid session
- **THEN** it rejects the request

### Requirement: Truthful capability state
The dashboard SHALL distinguish configured, enabled, connected, paused, pending, failed, and unavailable states without representing disabled features as healthy zero values.

#### Scenario: NapCat is disconnected
- **WHEN** no OneBot connection exists
- **THEN** the dashboard displays “未连接” rather than an ambiguous success state

### Requirement: Visible safety and recovery state
The dashboard SHALL expose immutable creator identity, operational ownership, privacy-job status, worker controls, database preflight, backup health, and recoverable quarantine outcomes.

#### Scenario: Backup integrity check fails
- **WHEN** a managed migration backup no longer matches its manifest
- **THEN** the system page visibly reports an anomaly and does not delete the file

### Requirement: Reply pipeline observability
The dashboard SHALL expose sanitized reply-run stages, trigger messages, policy blockers, retry state, context manifest summary, model evidence summary, and delivery outcomes.

#### Scenario: Reply does not reach QQ
- **WHEN** an operator opens the failed run
- **THEN** the dashboard distinguishes suppression, model failure, confirmed send rejection, and ambiguous delivery

### Requirement: Reply-runtime control surface
The dashboard SHALL display requested and effective mode, persistent pause state, worker lifecycle, last claim and progress times, current allowlist scope, approval backlog, and structured activation blockers, and SHALL route mutations through authenticated audited backend operations.

#### Scenario: Requested auto mode is downgraded
- **WHEN** the effective mode is more restrictive because a gate is false
- **THEN** the dashboard shows both modes and the exact downgrade reason rather than displaying the runtime as active

#### Scenario: Operator activates emergency stop
- **WHEN** an authenticated dashboard session confirms the stop action
- **THEN** the page reflects the persisted stopped state returned by the backend and does not rely on local UI state

### Requirement: Owner QQ and dashboard control parity
The dashboard and authenticated current-instance owner QQ commands SHALL expose the same runtime status, mode, limited-auto eligibility, approval, emergency-stop, and resume semantics through one backend control policy.

#### Scenario: Owner pauses by QQ
- **WHEN** the authenticated owner sends the emergency-stop command in private chat
- **THEN** the dashboard reports the same persisted pause state and audit event

### Requirement: High-risk activation confirmation
The dashboard SHALL require an explicit current-state confirmation before transitioning into a delivering mode and SHALL present which real-effect gates and conversation scopes will become eligible.

#### Scenario: State changes after confirmation preview
- **WHEN** readiness or eligibility evidence changes before confirmation is submitted
- **THEN** the transition is rejected and a fresh confirmation is required

### Requirement: Workflow-specific operator presentation
The authenticated dashboard SHALL present existing YCH workflows through risk-appropriate tables, master-detail layouts, forms, calendars, timelines, editors, sheets, dialogs, and alerts while preserving the backend's authorization, approval, audit, sanitization, and persisted-state semantics.

#### Scenario: Frontend is visually replaced
- **WHEN** a migrated route invokes an existing protected operation
- **THEN** it sends the same authorized API intent, waits for the persisted backend result, and does not weaken confirmation or manufacture success locally

### Requirement: Global operational readiness visibility
The application shell SHALL expose a concise truthful summary of backend reachability, current readiness profile, NapCat connection and bot identity, model availability, outbound gate, emergency pause, worker state, and session state without exposing credentials or implying that configured means active; the system page SHALL expose the corresponding structured blockers, warnings, evidence sources, revisions, and freshness.

#### Scenario: Models are configured but outbound is disabled
- **WHEN** the operator views the shell or readiness detail
- **THEN** model configuration and outbound capability are represented as separate states and the dashboard does not describe the bot as fully active

#### Scenario: Readiness evidence expires
- **WHEN** a previously passing probe becomes stale after Windows sleep or elapsed TTL
- **THEN** the shell no longer presents the applicable profile as ready and the detail view identifies which evidence must be refreshed

#### Scenario: One profile passes and another fails
- **WHEN** local startup is healthy but controlled real effects are blocked
- **THEN** the dashboard displays both outcomes rather than collapsing them into one health color or percentage

### Requirement: Recoverable route and form state
The dashboard SHALL preserve non-sensitive pagination, filters, selected tabs, safe identifiers, and unsaved-edit warnings across normal navigation and reload where appropriate, and SHALL never persist protected bodies or secrets for convenience.

#### Scenario: Operator leaves an edited persona
- **WHEN** unsaved text would be discarded by route navigation
- **THEN** the dashboard warns the operator and offers to remain or intentionally discard without storing the text in the URL

### Requirement: Consistent destructive-action disclosure
Every dashboard action that can delete data, send QQ messages, publish or revoke Qzone content, read protected history or profiles, execute privacy jobs, or broaden reply-runtime effects SHALL identify the target and consequences immediately before the protected backend operation.

#### Scenario: Operator confirms a real-effect action
- **WHEN** the confirmation surface is shown
- **THEN** it states the affected bot or user, scope, current safety gates, and irreversible or ambiguous outcomes while retaining any backend revision-bound confirmation requirement

### Requirement: Operational activity MUST be readable as a log

The dashboard MUST distinguish the recent activity summary from the full combined audit, control-command and owner-report log.

#### Scenario: Operations shows recent activity
- **WHEN** recent activity exists
- **THEN** Operations shows at most three localized rows and a clear route to the full log

#### Scenario: Operator opens the full log
- **WHEN** the activity route loads
- **THEN** up to 50 server-backed rows appear in a filterable table with localized action, source, subject and time fields

### Requirement: Managed artifact control surface
The authenticated dashboard SHALL display managed artifact counts, types, user or system scopes, bytes, verification and reference states, retention candidates, quarantine batches, and anomalies, and SHALL route refresh, preview, and confirm operations through the authorized audited backend service.

#### Scenario: Operator previews retention action
- **WHEN** eligible candidates exist
- **THEN** the confirmation surface identifies artifact types, user scopes, count, bytes, quarantine semantics, and the fact that no permanent deletion or live-database restore will occur

#### Scenario: Preview becomes stale
- **WHEN** the backend rejects a confirmation because candidate or readiness evidence changed
- **THEN** the dashboard discards the preview, refreshes persisted state, and does not manufacture success locally

### Requirement: Readiness and retention failure reporting
The dashboard SHALL expose sanitized readiness and retention anomalies in the full operations log and SHALL allow creation of a durable owner-report event without requiring or enabling real QQ delivery.

#### Scenario: Backup or privacy artifact verification fails
- **WHEN** a scheduled or manual refresh detects corruption or path escape
- **THEN** the affected page shows a blocked state and the operations log contains a safe correlated event without file contents or arbitrary absolute paths

### Requirement: Model qualification control surface
The authenticated YCH dashboard SHALL show chat, vision, image, and stats configuration, protection, readiness, qualification, usage, and cost evidence as separate states and SHALL provide backend-authorized preview, confirmation, cancellation, run history, case evidence, blockers, scores, and stale reasons.

#### Scenario: Route is configured but unqualified
- **WHEN** a capability has a model and enabled configuration but no current passing decision
- **THEN** the dashboard labels it configured and unqualified rather than ready or production-enabled

#### Scenario: Operator previews a paid run
- **WHEN** an authenticated operator selects one route and suite
- **THEN** the dashboard displays exact fixture count, maximum requests, token or image ceiling, conservative maximum cost, expiry, and effect-isolation statement before confirmation

#### Scenario: Long case history is displayed
- **WHEN** a run contains more results than fit in one viewport
- **THEN** the dashboard uses the shared full-width table or list with pagination or intentional scrolling and does not introduce competing full-height nested scrollbars

### Requirement: Server-owned qualification decisions
The dashboard SHALL NOT calculate, submit, or override a passing qualification decision and SHALL render loading, empty, disconnected, blocked, stale, cancelled, partial, failed, passed, and inconclusive states from backend evidence.

#### Scenario: Client attempts to submit a passing score
- **WHEN** a modified client sends a pass flag or altered score with a confirmation request
- **THEN** the backend ignores or rejects the client decision and derives status from persisted case evidence and policy

### Requirement: Observe connection shows identity without implying delivery
The dashboard status SHALL expose whether OneBot is connected, whether a token is configured, and whether the authenticated bot QQ matches the configured bot, and SHALL keep outbound/delivery state separate.

#### Scenario: Connected observe session matches the bot
- **WHEN** the reverse WebSocket is connected and the authenticated `self_id` equals the configured bot QQ
- **THEN** status reports connected plus matching identity and does not report delivery as active

### Requirement: Operator can label QQ numbers
The authenticated dashboard and owner-private commands SHALL let the owner set, replace, or clear a short label for a user QQ or group QQ, and SHALL present that label beside the number on operator lists.

#### Scenario: Owner names a user
- **WHEN** the owner saves label `小明` for user `2000000003`
- **THEN** the user list and user detail show `小明` as the primary name and still show the QQ

#### Scenario: Search by label
- **WHEN** the user list filter is `小明`
- **THEN** the matching labeled user remains visible even if the typed text is not the QQ

#### Scenario: Clear a label
- **WHEN** the owner clears the label
- **THEN** operator surfaces fall back to the QQ number only

### Requirement: Operator handbook is in-product and in-repo
The system SHALL keep the owner command catalog in `docs/operator/` and SHALL present the same catalog on an authenticated dashboard handbook page.

#### Scenario: Owner opens the handbook
- **WHEN** the owner opens the dashboard handbook
- **THEN** daily operating rules and grouped `/` commands are visible without a model call

### Requirement: Shared empty and loading presentation
Authenticated operator pages SHALL present empty lists, filter misses, unselected detail panes, and content-area loading through shared dashboard empty/loading components rather than ad-hoc muted paragraphs, and SHALL preserve existing safety wording that states a capability is off-network or non-delivering.

#### Scenario: List has no items
- **WHEN** an operator opens a workflow list with zero items and no active filter miss
- **THEN** the page shows the shared empty presentation with a “还没有…” title and optional next-step detail

#### Scenario: Filter matches nothing
- **WHEN** an operator filter yields zero rows while data may exist under other filters
- **THEN** the page shows the shared empty presentation with a “没有匹配…” title rather than the zero-items-onboarding copy

#### Scenario: Detail pane has no selection
- **WHEN** a master-detail page has no selected row
- **THEN** the detail pane shows the shared select-hint empty presentation telling the operator to pick an item from the left

#### Scenario: Content is loading
- **WHEN** a page is waiting on the first authenticated list or detail fetch
- **THEN** the content area shows the shared loading skeleton presentation with a busy accessibility signal

#### Scenario: Safety wording is preserved
- **WHEN** an empty state previously explained that shadow mode does not touch the network or that candidates do not enter the outbox
- **THEN** that safety explanation remains visible in the empty presentation detail
