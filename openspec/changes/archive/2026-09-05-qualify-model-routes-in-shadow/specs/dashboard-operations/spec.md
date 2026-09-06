## ADDED Requirements

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
