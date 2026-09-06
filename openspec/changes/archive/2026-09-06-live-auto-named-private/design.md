# Design: Live auto send for one named private user

## Trust boundary

NapCat remains an untrusted protocol peer. Authority is the authenticated OneBot sender QQ, never a claim inside message text. Model calls and QQ send are both real effects. For this live step they are limited to `{bot_qq}:private:{owner_qq}` and `{bot_qq}:private:2000000003`.

## Runtime

Effective mode may be `auto` only when the reply worker is enabled, emergency pause is clear, the configuration ceiling is `auto`, chat model/network/route/circuit gates pass, outbound is enabled, the outbound worker is active, the OneBot token is present, and the connected bot QQ matches. Before the chat gateway is invoked, the conversation key must be one of the two live private keys. A mismatch suppresses the run as `conversation_ineligible`. A match may generate, plan, and hand off one outbox item without waiting for per-message approval.

## Rollback

Set ceiling `limited_auto` or lower, emergency-pause, or set `YCH_OUTBOUND_ENABLED=false`. Closing outbound must prevent further sends.
