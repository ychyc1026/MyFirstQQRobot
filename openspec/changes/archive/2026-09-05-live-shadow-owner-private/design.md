# Design: Live owner-private shadow

## Trust boundary

NapCat remains an untrusted protocol peer. Authority is the authenticated OneBot sender QQ, not text. Shadow model calls are a new real network effect and are limited to the compiled/configured owner private conversation key `{bot_qq}:private:{owner_qq}`.

## Runtime

Effective mode may be `shadow` only when the reply worker is enabled, emergency pause is clear, the configuration ceiling is at least `shadow`, and chat model/network/route/circuit gates pass. Outbound staying false is required: delivery checks keep the runtime from creating sendable outbox items.

Before the chat gateway is invoked, the run's conversation key is compared to `{bot_qq}:private:{owner_qq}`. A mismatch suppresses the run as `conversation_ineligible` without constructing a provider request. A match may assemble context, call chat, plan, and terminalize as `shadow_completed`.

## Rollback

Set `YCH_REPLY_RUNTIME_MAX_MODE=observe_only`, disable the reply worker, and emergency-pause. Leave outbound false. Stored shadow candidates remain local evidence and are not delivered.
