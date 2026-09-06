# Design: Live owner-private limited-auto send

## Trust boundary

NapCat remains an untrusted protocol peer. Authority is the authenticated OneBot sender QQ. Model calls and QQ send are both real effects. For this live step they are limited to `{bot_qq}:private:{owner_qq}` and also require an explicit limited-auto eligibility row for that same key.

## Runtime

Effective mode may be `limited_auto` only when the reply worker is enabled, emergency pause is clear, the configuration ceiling is at least `limited_auto`, chat model/network/route/circuit gates pass, outbound is enabled, the outbound worker is active, the OneBot token is present, and the connected bot QQ matches. Before the chat gateway is invoked, the conversation key must equal `{bot_qq}:private:{owner_qq}`. A mismatch suppresses the run as `conversation_ineligible`. A match that is not allowlisted may call the model and terminalize as `shadow_completed` with no outbox. A match that is allowlisted may generate, plan, and hand off one outbox item without waiting for per-message approval. Outbox dispatch may then send through NapCat HTTP.

## Rollback

Set ceiling `owner_approved` or lower, remove the eligibility row, emergency-pause, or set `YCH_OUTBOUND_ENABLED=false`. Closing outbound must prevent further sends even if eligibility remains.
