## ADDED Requirements

### Requirement: Operator can label QQ numbers
The authenticated dashboard and owner-private commands SHALL let the owner set, replace, or clear a short label for a user QQ or group QQ, and SHALL present that label beside the number on operator lists.

#### Scenario: Owner names a user
- **WHEN** the owner saves label `小明` for user `2000000003`
- **THEN** the user list and user detail show `小明` as the primary name and still show the QQ

#### Scenario: Search by label
- **WHEN** the user list filter is `小明`
- **THEN** the matching labeled user remains visible even if the typed text is not the QQ

#### Scenario: Clear a label
- **WHEN** the owner clears the label
- **THEN** operator surfaces fall back to the QQ number only

### Requirement: Operator handbook is in-product and in-repo
The system SHALL keep the owner command catalog in `docs/operator/` and SHALL present the same catalog on an authenticated dashboard handbook page.

#### Scenario: Owner opens the handbook
- **WHEN** the owner opens the dashboard handbook
- **THEN** daily operating rules and grouped `/` commands are visible without a model call
