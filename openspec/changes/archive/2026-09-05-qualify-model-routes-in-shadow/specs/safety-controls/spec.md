## ADDED Requirements

### Requirement: Qualification side-effect isolation
The system SHALL compose model qualification without production outbox, NapCat, history, Qzone, search, proactive-delivery, or owner-report-delivery dependencies and SHALL stop qualification if any prohibited effect becomes active during a run.

#### Scenario: Fake qualification is executed
- **WHEN** any capability suite runs in fake mode
- **THEN** no real external client is constructed and no production effect table or user-data record is read or changed

#### Scenario: Outbound delivery becomes active mid-run
- **WHEN** controlled live qualification detects that a prohibited reply, delivery, Qzone, history, proactive, or report effect is active
- **THEN** future model attempts are blocked and the run records a sanitized side-effect-isolation blocker

### Requirement: Qualification budget containment
The system SHALL apply qualification-specific request, token, image, and cost ceilings in addition to the existing capability quota, timeout, retry, readiness, circuit-breaker, and emergency-pause controls.

#### Scenario: Existing daily quota permits more than the preview
- **WHEN** a confirmed run reaches its smaller qualification request or token ceiling
- **THEN** no further provider attempt occurs even though the general daily quota remains available

#### Scenario: Emergency pause activates
- **WHEN** emergency pause becomes active before the next qualification case
- **THEN** the case is not claimed for a provider call and the run stops with a stable blocker
