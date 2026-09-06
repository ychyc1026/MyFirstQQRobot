## ADDED Requirements

### Requirement: Live auto send may include one named group mention
Raising or keeping the reply runtime at `auto` for this live step SHALL create a sendable group outbox item only for `2000000002:group:2000000004` when a trigger mentions bot `2000000002`, and SHALL NOT send to any other group.

#### Scenario: Named group mention may send
- **WHEN** effective mode is `auto` and a message in group `2000000004` mentions the configured bot
- **THEN** exactly that generated plan may be handed to the reply outbox for that group

#### Scenario: Other groups do not send
- **WHEN** effective mode is `auto` and the conversation is a different group
- **THEN** no sendable outbox item is created for that run
