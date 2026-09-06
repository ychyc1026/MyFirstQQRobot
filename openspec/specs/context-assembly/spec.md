# context-assembly Specification

## Purpose
Define how YCH assembles isolated, provenance-aware, policy-checked, and bounded context before any reply model invocation.

## Requirements

### Requirement: Provenance-aware context
The system SHALL construct context as typed sections whose manifest identifies source class, scope, subject, record references, policy decision, and truncation before rendering a model request.

#### Scenario: Operator inspects a reply run
- **WHEN** the run has completed context assembly
- **THEN** the operator can determine which persona, knowledge, memory, and history records were included or denied without exposing secrets

### Requirement: Private-user isolation
The system SHALL include user-bound material only when its subject QQ matches the private conversation peer.

#### Scenario: User A and User B have different personas
- **WHEN** building context for User A
- **THEN** no persona, understanding, memory, document, or private history belonging to User B is eligible

### Requirement: Group privacy boundary
The system SHALL exclude all per-user private persona, understanding, memory, document, and private-history sections from group-chat model context.

#### Scenario: Known private user speaks in a group
- **WHEN** that member has rich private-chat material
- **THEN** the group run does not load that material

### Requirement: Deterministic context budget
The system SHALL apply a deterministic priority and truncation policy and record what was omitted when context exceeds the configured budget.

#### Scenario: Long approved profile exceeds the budget
- **WHEN** higher-priority identity and recent conversation evidence consume the available budget
- **THEN** the profile is truncated or omitted predictably and the manifest records the decision
