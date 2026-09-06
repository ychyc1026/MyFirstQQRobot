# Change: Live owner-private limited-auto replies

## Why

Owner-private `owner_approved` passed: generate, wait for owner approval, then send one QQ reply. The owner named the next real effect as `limited_auto`: after an explicit allowlist of one exact conversation, that conversation may send without per-message approval. The first live automatic send stays limited to the owner private chat.

## What Changes

- Raise the reply runtime ceiling to `limited_auto`. Keep outbound and the reply worker on.
- Keep Qzone, history, owner-report delivery, proactive send, and `auto` disabled.
- When effective mode is `limited_auto`, call the chat model only for `2000000002:private:2000000001`.
- Create a sendable outbox item only when that exact conversation is also in the limited-auto eligibility set.
- Other conversations stay stored without a model call and without a sendable outbox item, even if later allowlisted.
- Friendship, imported documents, persona, memory, and history SHALL NOT grant eligibility.
- Logs and this chat SHALL NOT include prompt or reply bodies.

## Non-goals

- Raising the ceiling to `auto`.
- Allowlisting or sending to any non-owner private user or group.
- Enabling Qzone, history collection, or owner-report delivery.
- Removing owner-approved as a selectable lower mode.

## Real effects and data handling

Automated tests use temporary SQLite, a fake chat gateway, and local outbox evidence. The owner-authorized live step may raise the ceiling to `limited_auto`, restart YCH, set requested mode to `limited_auto`, allowlist `2000000002:private:2000000001`, send one owner private message to the chat provider, and dispatch one reply through NapCat HTTP to QQ without a per-message approval. Docs, logs, and this chat SHALL NOT quote the message or model output.

## User decisions

Already decided:

- Scope remains owner private chat only for this first live automatic send.
- Eligibility is explicit: `/机器人 允许 2000000002:private:2000000001`.
- Do not enable `auto` in this change.
