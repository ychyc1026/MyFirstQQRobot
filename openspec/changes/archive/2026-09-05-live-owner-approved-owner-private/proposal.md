# Change: Live owner-private owner-approved replies

## Why

Owner-private shadow passed: the chat model drafts locally and QQ does not send. The owner named the next real effect as `owner_approved`: generate, wait for the instance owner to approve, then send exactly that plan to QQ. Current owner-approved mode would call the model for every conversation. The first live send stays limited to the owner private chat.

## What Changes

- Raise the reply runtime ceiling to `owner_approved` and enable outbound for this named step only.
- Keep Qzone, history, owner-report delivery, proactive send, `limited_auto`, and `auto` disabled.
- When effective mode is `owner_approved`, call the chat model only for `2000000002:private:2000000001`.
- Other conversations stay stored without a model call and without a sendable outbox item.
- An owner-private plan waits in `awaiting_approval`. QQ send happens only after the owner approves that exact run and plan revision.
- Logs and this chat SHALL NOT include prompt or reply bodies.

## Non-goals

- Raising the ceiling to `limited_auto` or `auto`.
- Sending to any non-owner private user or group.
- Enabling Qzone, history collection, or owner-report delivery.
- Cursor/chat approval. Only the authenticated owner QQ or dashboard owner action may approve.

## Real effects and data handling

Automated tests use temporary SQLite, a fake chat gateway, and local outbox evidence. The owner-authorized live step may enable outbound, restart YCH, set requested mode to `owner_approved`, send one owner private message to the chat provider, then send one approved reply through NapCat HTTP to QQ. Docs, logs, and this chat SHALL NOT quote the message or model output.

## User decisions

Already decided:

- Scope remains owner private chat only.
- Approval is by the instance owner in QQ (`/机器人 批准 <审批ID>`) or the dashboard, not by Cursor.
- After approval, exactly that plan may appear in the owner private chat.
