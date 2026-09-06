## ADDED Requirements

### Requirement: Reply pipeline observability
The dashboard SHALL expose sanitized reply-run stages, trigger messages, policy blockers, retry state, context manifest summary, model evidence summary, and delivery outcomes.

#### Scenario: Reply does not reach QQ
- **WHEN** an operator opens the failed run
- **THEN** the dashboard distinguishes suppression, model failure, confirmed send rejection, and ambiguous delivery
