## ADDED Requirements

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
