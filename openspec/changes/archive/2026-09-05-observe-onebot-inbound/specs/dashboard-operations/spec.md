## ADDED Requirements

### Requirement: Observe connection shows identity without implying delivery
The dashboard status SHALL expose whether OneBot is connected, whether a token is configured, and whether the authenticated bot QQ matches the configured bot, and SHALL keep outbound/delivery state separate.

#### Scenario: Connected observe session matches the bot
- **WHEN** the reverse WebSocket is connected and the authenticated `self_id` equals the configured bot QQ
- **THEN** status reports connected plus matching identity and does not report delivery as active
