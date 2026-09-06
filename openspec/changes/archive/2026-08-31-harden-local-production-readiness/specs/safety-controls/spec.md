## ADDED Requirements

### Requirement: Readiness-gated external effects
The system SHALL require a current passing `controlled_real_effect` readiness result for the exact bot, process instance, capability, and scope in addition to every existing independent real-effect gate, and SHALL fail closed when the result is blocked, unknown, stale, or mismatched.

#### Scenario: Existing gates pass but readiness is stale
- **WHEN** a reply, proactive message, owner report, history read, model call, or Qzone effect reaches its external boundary after readiness evidence expires
- **THEN** the effect is not attempted and a structured blocker is recorded

#### Scenario: Readiness failure affects one capability
- **WHEN** image-generation readiness is blocked but protected chat readiness still passes
- **THEN** the image effect remains disabled without falsely disabling or enabling the independent chat capability

### Requirement: Recoverable managed-artifact cleanup
The system SHALL require current integrity, reference protection, an authenticated revision-bound preview, and an atomic project-confined quarantine operation before changing the location of a managed backup or privacy artifact, and SHALL NOT silently permanently delete it.

#### Scenario: Cleanup request bypasses preview
- **WHEN** a client directly requests a filesystem move without current confirmation evidence
- **THEN** the backend rejects the operation regardless of what the dashboard displays

