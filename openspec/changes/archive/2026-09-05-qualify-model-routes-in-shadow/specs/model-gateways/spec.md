## ADDED Requirements

### Requirement: Versioned route qualification
The system SHALL qualify chat, vision, image, and stats routes independently against versioned synthetic suites and SHALL bind every decision to the exact capability, provider protocol, sanitized endpoint host, model identifier, generation settings, protection-policy revision, suite version, and price-catalog revision.

#### Scenario: Qualified route configuration changes
- **WHEN** a model identifier, endpoint, relevant generation setting, suite, protection policy, or price revision differs from the passing decision
- **THEN** the prior decision is reported as stale and does not qualify the new route revision

#### Scenario: One capability passes
- **WHEN** the chat route passes its current suite
- **THEN** the vision, image, and stats routes keep their own prior qualification states and gates

### Requirement: Synthetic-only qualification inputs
The system SHALL source qualification inputs only from versioned repository-owned fixtures classified as synthetic and SHALL reject arbitrary paths, database message identifiers, QQ identifiers, uploaded documents, chat history, Qzone content, user understanding, personas, memories, and diaries.

#### Scenario: Client submits a production message identifier
- **WHEN** a qualification request attempts to select a stored QQ message or user document as input
- **THEN** the request is rejected before a model or user-data repository is called

#### Scenario: Group-isolation case is evaluated
- **WHEN** a synthetic group fixture attempts to solicit a private user's persona, understanding, memory, or history
- **THEN** deterministic checks fail any result that claims or reveals such private context

### Requirement: Controlled live qualification
The system SHALL require an authenticated, unexpired, single-use preview confirmation bound to one capability, actor, process instance, route revision, suite revision, policy revision, request ceiling, token or image ceiling, and conservative maximum cost before a controlled live qualification call.

#### Scenario: Confirmation is reused
- **WHEN** an actor submits a confirmation handle that already created a run
- **THEN** no additional run or provider request is created

#### Scenario: Cost cannot be bounded
- **WHEN** current price evidence or a hard request/token/image ceiling is unavailable
- **THEN** controlled live confirmation is blocked without invoking the provider

#### Scenario: Windows sleep passes the expiry
- **WHEN** wall-clock time advances beyond the preview or lease expiry while the computer is suspended
- **THEN** future calls fail closed and an ambiguous in-flight provider request is not blindly retried

### Requirement: Deterministic blockers and advisory quality scores
The system SHALL distinguish deterministic blocking checks from advisory quality scores and SHALL NOT allow client input, human notes, or model-judge output to override creator identity, authority, isolation, schema, forbidden-field, artifact-safety, side-effect, or evidence-completeness blockers.

#### Scenario: Useful response violates creator identity
- **WHEN** an otherwise high-quality response contradicts the immutable YCH creator identity
- **THEN** the case and route revision fail regardless of advisory score

#### Scenario: Advisory prose score is low
- **WHEN** all blocking checks pass but the configured advisory score threshold is not met
- **THEN** the exact route revision does not pass qualification and the score evidence remains separately visible

### Requirement: Qualification evidence redaction
The system SHALL persist and expose only allowlisted qualification metadata and hashes and SHALL exclude credentials, authorization headers, complete prompts, complete responses, raw provider error bodies, generated image bytes, arbitrary URLs, arbitrary filesystem paths, and real user identifiers from logs, audit, database evidence, and API DTOs.

#### Scenario: Provider returns a verbose error body
- **WHEN** a controlled call fails with a body containing request content or credential-like text
- **THEN** stored and displayed evidence contains only a stable failure category, validated status/request identifier, bounded timing, and correlation metadata

### Requirement: Passing qualification does not activate production
The system SHALL treat qualification as evidence only and SHALL NOT modify model configuration, reply activation mode, worker state, outbox state, QQ delivery, history access, Qzone access/publication, proactive delivery, or owner-report delivery when a run is created or passes.

#### Scenario: Chat route passes every case
- **WHEN** the current chat route receives a passing qualification decision
- **THEN** no production gate changes and no QQ-deliverable outbox item exists
