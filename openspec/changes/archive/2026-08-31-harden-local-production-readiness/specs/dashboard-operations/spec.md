## MODIFIED Requirements

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

## ADDED Requirements

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

