# Design: Live owner-private owner-approved send

## Trust boundary

NapCat remains an untrusted protocol peer. Authority is the authenticated OneBot sender QQ. Model calls and QQ send are both real effects and are limited to `{bot_qq}:private:{owner_qq}`.

## Runtime

Effective mode may be `owner_approved` only when the reply worker is enabled, emergency pause is clear, the configuration ceiling is at least `owner_approved`, chat model/network/route/circuit gates pass, outbound is enabled, the outbound worker is active, the OneBot token is present, and the connected bot QQ matches. Before the chat gateway is invoked, the conversation key must equal `{bot_qq}:private:{owner_qq}`. A mismatch suppresses the run as `conversation_ineligible`. A match may generate, plan, and wait in `awaiting_approval` with no outbox row. Owner approval of that exact plan hash and runtime revision hands off one outbox item. Outbox dispatch may then send through NapCat HTTP.

## Rollback

Set `YCH_OUTBOUND_ENABLED=false`, ceiling `shadow` or `observe_only`, emergency-pause, and disable the reply worker. Pending approvals expire or suppress; they must not send after outbound is closed.
