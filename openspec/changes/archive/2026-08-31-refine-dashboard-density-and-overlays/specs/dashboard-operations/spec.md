## ADDED Requirements

### Requirement: Operational activity MUST be readable as a log

The dashboard MUST distinguish the recent activity summary from the full combined audit, control-command and owner-report log.

#### Scenario: Operations shows recent activity
- **WHEN** recent activity exists
- **THEN** Operations shows at most three localized rows and a clear route to the full log

#### Scenario: Operator opens the full log
- **WHEN** the activity route loads
- **THEN** up to 50 server-backed rows appear in a filterable table with localized action, source, subject and time fields
