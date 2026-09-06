# Design: Observe-mode OneBot inbound

## Trust boundary

NapCat is an untrusted protocol peer. The reverse WebSocket is accepted only with the configured OneBot token. `self_id` in events is evidence of which QQ NapCat claims; it is compared to the configured bot QQ `2000000002` (or the enabled bot-account row). Text inside messages never authenticates authority.

## Inbound path

1. Token check fails closed when the configured token is empty or mismatched.
2. `meta_event` frames update `authenticated_bot_qq` and do not increment ignored conversational counters.
3. Message events for a foreign `self_id` are ignored and not stored.
4. Accepted private/group messages persist idempotently. Reply orchestration may create settling runs. The reply worker stays disabled, so no model call and no deliverable outbox item is created.
5. `LazyNapCatClient` remains unconstructed because observe ingest never calls send, friend list, history, or Qzone HTTP methods.

## Readiness

`local_start` may pass without a live NapCat connection. Observe ingest does not require the outbound half of `controlled_real_effect`. A connected foreign bot still blocks any later send profile.

## Operator start script

`scripts/start-observe.ps1` is a convenience wrapper around existing tools, not a new QQ client. It starts the YCH listener, waits for `/health/live` and `/health/ready`, then launches sibling NapCat `NapCatWinBootMain.exe` in a visible console for QR login.

It does not write `.env`, does not set outbound/reply-worker/Qzone/history flags, does not pass owner QQ `2000000001`, and does not call `KillQQ`. If outbound, the reply worker, or a max mode above `observe_only` is already enabled, it still ensures YCH is up but refuses to start NapCat. Allowlisted status fields may be printed; tokens, API keys, and message bodies are not.

## Rollback

Stop the YCH process or disconnect NapCat reverse WebSocket. Leave `YCH_OUTBOUND_ENABLED=false` and `YCH_REPLY_WORKER_ENABLED=false`. Stored inbound evidence remains.
