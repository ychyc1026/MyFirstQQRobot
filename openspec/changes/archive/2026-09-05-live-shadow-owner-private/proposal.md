# Change: Live owner-private shadow replies

## Why

Observe-mode inbound ingest passed: exact-bot events store without sending. The owner named the next real effect as shadow: the chat model may draft a reply, but QQ must not show it. Current shadow mode would call the model for every conversation. The owner limited the first live shadow step to the owner private chat only.

## What Changes

- Keep outbound, Qzone, history, and owner-report delivery disabled.
- Allow the reply runtime ceiling to rise to `shadow` and the reply worker to run.
- When effective mode is `shadow`, call the chat model only for conversation `2000000002:private:2000000001`.
- Other conversations remain stored observe-only: no model call, no sendable outbox item.
- Shadow completions persist as `shadow_completed` / not-for-delivery evidence. Logs and chat transcripts SHALL NOT include prompt or reply bodies.

## Non-goals

- Enabling `YCH_OUTBOUND_ENABLED` or creating sendable reply outbox items.
- Raising the ceiling to `owner_approved`, `limited_auto`, or `auto`.
- Calling vision, image, or stats routes from this inbound path.
- Shadow inference for group chats or any non-owner private user.
- Archiving `observe-onebot-inbound` in this change.

## Real effects and data handling

Automated tests use temporary SQLite and a fake chat gateway. The owner-authorized live step may enable model-network plus the chat route, restart the YCH listener, resume the reply runtime into `shadow`, and send one owner private message. That message body may be sent to the already-qualified chat provider. QQ send remains unauthorized. Docs, logs, and this chat SHALL NOT quote the message or model output.

## User decisions

Already decided:

- First live shadow trigger is owner private chat only: `2000000001` → `2000000002`.
- Model draft is local evidence only; it must not appear in QQ.
- Outbound stays off.
