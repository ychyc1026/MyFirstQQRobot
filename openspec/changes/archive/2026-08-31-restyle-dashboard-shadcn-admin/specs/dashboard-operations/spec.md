## ADDED Requirements

### Requirement: Workflow-specific operator presentation
The authenticated dashboard SHALL present existing YCH workflows through risk-appropriate tables, master-detail layouts, forms, calendars, timelines, editors, sheets, dialogs, and alerts while preserving the backend's authorization, approval, audit, sanitization, and persisted-state semantics.

#### Scenario: Frontend is visually replaced
- **WHEN** a migrated route invokes an existing protected operation
- **THEN** it sends the same authorized API intent, waits for the persisted backend result, and does not weaken confirmation or manufacture success locally

### Requirement: Global operational readiness visibility
The application shell SHALL expose a concise truthful summary of backend reachability, NapCat connection, model availability, outbound gate, emergency pause, and session state without exposing credentials or implying that configured means active.

#### Scenario: Models are configured but outbound is disabled
- **WHEN** the operator views the shell or readiness detail
- **THEN** model configuration and outbound capability are represented as separate states and the dashboard does not describe the bot as fully active

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

