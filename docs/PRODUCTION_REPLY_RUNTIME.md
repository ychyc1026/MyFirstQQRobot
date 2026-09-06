# Production reply runtime

The production reply runtime closes the durable path from an inbound OneBot event to a
transactional local outbox. It does not enable real network access by itself: model and outbound
network gates remain independent and are disabled in `.env.example`.

## Safe defaults and gates

Fresh and migrated bot instances start with requested mode `observe_only`, persistent emergency
pause, an empty limited-auto eligibility set, worker lifecycle `disabled`,
`YCH_REPLY_WORKER_ENABLED=false`, and `YCH_REPLY_RUNTIME_MAX_MODE=observe_only`.

Effective mode is the lower of requested mode and the configuration ceiling, further reduced by
persistent pause, worker enablement, model configuration/network/route/circuit state, outbound
enablement/worker state, OneBot token, and matching bot connectivity. A delivery gate failure
downgrades model-capable operation to shadow; a model gate failure downgrades it to observe-only.

| Mode | Model | Outbox | Additional condition |
| --- | --- | --- | --- |
| `observe_only` | no | no | default |
| `shadow` | yes, owner-private live step only | no | terminalizes as `shadow_completed` |
| `owner_approved` | yes, owner-private live step only | after owner approval | plan hash + runtime revision, 30-minute expiry |
| `limited_auto` | yes, owner-private live step only | exact eligible owner-private conversation | default deny; friendship/docs/persona/memory never grant eligibility |
| `auto` | yes, this-bot conversations | every private chat of the configured bot, and every group of that bot when `@` the bot | no per-message approval; other-bot keys and group messages without a real `@` are suppressed |

Live gates are re-evaluated immediately before the model and before outbox handoff. Runtime control
DTOs/events never expose API keys, prompt/context bodies, reply text, raw OneBot payloads, or lease
tokens.

## Dashboard and owner control

`/pipeline` shows requested/effective mode, downgrade blockers, worker lifecycle, last progress,
recovered lease count, approval backlog, exact limited-auto scope, and delivery-unknown evidence.
Every state-changing dashboard action uses a single-use preview followed by explicit confirmation.
The preview expires after five minutes and is rejected if runtime revision or readiness changes.

Authenticated endpoints:

- `GET /api/v1/reply-runtime`
- `POST /api/v1/reply-runtime/preview`
- `POST /api/v1/reply-runtime/confirm`
- `GET /api/v1/reply-runtime/approvals`
- `POST /api/v1/reply-runtime/worker/run-once`

Only the exact current instance owner (`2000000001`) may issue these commands in a private QQ chat
with bot `2000000002`; group senders, non-owner accounts, and text claiming owner identity do not
change runtime state:

```text
/机器人 状态
/机器人 暂停
/机器人 恢复
/机器人 模式 observe_only|shadow|owner_approved|limited_auto|auto
/机器人 允许 2000000002:private:<用户QQ>
/机器人 移除 2000000002:private:<用户QQ>
/机器人 批准 <审批ID>
/机器人 拒绝 <审批ID>
/机器人 取消 <审批ID>
```

Dashboard and QQ commands share `ReplyRuntimeControlService`, CAS policy, and sanitized events.

## Staged rollout

1. Run database preflight and retain its verified backup. With every network gate off, start YCH and
   verify schema 29, `observe_only`, and persistent pause.
2. Configure the chat provider separately, keep outbound off, raise the configuration ceiling to
   `shadow`, enable the reply worker, explicitly resume, and inspect `shadow_completed` evidence.
   The first live shadow step (`live-shadow-owner-private`) calls the chat model only for
   `2000000002:private:2000000001`. Other conversations stay stored without a model call. QQ send
   remains unauthorized.
3. Raise ceiling and requested mode to `owner_approved` and enable outbound. The first live send
   (`live-owner-approved-owner-private`) passed for `2000000002:private:2000000001`: the chat model
   ran, the plan waited for owner approval, and QQ send happened only after that exact approval.
   `limited_auto` and `auto` remain unauthorized.
4. Raise the ceiling to `limited_auto`, allowlist exactly
   `2000000002:private:2000000001`, and observe that conversation before adding another.
   The first live automatic send (`live-limited-auto-owner-private`) passed for that owner
   private key: the chat model ran and QQ send happened without a new per-message approval.
   `auto` remained unauthorized until the owner named it.
5. Raise the ceiling to `auto` only for `{bot_qq}:private:{owner_qq}` and
   `2000000002:private:2000000003`. The first live auto send
   (`live-auto-named-private`) passed for that named private key.
6. Add exactly `2000000002:group:2000000004` when a trigger `@` the bot
   (`live-auto-named-group`). A mention whose only non-`@` content is a QQ
   `face` or `mface` uses the sanitized placeholder `[表情]`.
7. Owner-authorized production (`open-production-auto`) widens `auto` to every
   private chat of bot `2000000002` and every group of that bot when a trigger
   `@` the bot. Search, image generation, privacy-job execution, and OCR stay
   closed. Group messages without a real `@` stay stored without a model call.

## Emergency stop, rollback, and recovery

Emergency stop persists the pause and pauses outbound claiming. Queued rows and delivery evidence
remain auditable. Resume only after readiness is healthy; it does not recreate queued work.
`delivery_unknown` remains quarantined with no blind retry control.

For rollback, stop YCH and all workers, retain the incident database, and restore the verified
pre-migration backup described in `DATABASE_PREFLIGHT.md`. Schema 29 is additive except for rebuilding
the reply-run stage check; migration copies every run and verifies foreign keys before version 29 is
recorded. Never downgrade by editing `schema_migrations`.

The no-network acceptance harness uses temporary SQLite, synthetic OneBot events, an injected fake
chat gateway, and local outbox evidence. It exercises all modes, exact scope, approval expiry, stale
revisions, concurrent CAS, deferred successors, lease recovery, worker crashes, emergency pause, and
idempotent outbox handoff without calling NapCat, QQ, Qzone, history, search, or real model clients.
