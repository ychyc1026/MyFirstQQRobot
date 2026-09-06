# Change: Live auto replies for one named private user

## Why

Owner-private `limited_auto` passed. The owner named the next real effect as `auto` and named one test account: QQ `2000000003`. Unconstrained `auto` would send to every conversation that passes delivery gates. The first live `auto` step stays limited to the owner private chat and that exact test private chat.

## What Changes

- Raise the reply runtime ceiling to `auto`. Keep outbound and the reply worker on.
- Keep Qzone, history, owner-report delivery, and proactive send disabled.
- When effective mode is `auto`, call the chat model only for `2000000002:private:2000000001` and `2000000002:private:2000000003`.
- Those two conversations may create a sendable outbox item without per-message approval.
- Other private users and all groups stay stored without a model call and without a sendable outbox item.
- Logs and this chat SHALL NOT include prompt or reply bodies.

## Non-goals

- Sending to any private user other than the owner and `2000000003`.
- Sending to any group.
- Enabling Qzone, history collection, or owner-report delivery.
- Treating friendship, documents, persona, or memory as eligibility.

## Real effects and data handling

Automated tests use temporary SQLite, a fake chat gateway, and local outbox evidence. The owner-authorized live step may raise the ceiling to `auto`, restart YCH, set requested mode to `auto`, send one private message from `2000000003` to the chat provider, and dispatch one reply through NapCat HTTP to that private chat. Docs, logs, and this chat SHALL NOT quote the message or model output.

## User decisions

Already decided:

- Mode is `auto`.
- The named live test account is QQ `2000000003`, private chat with bot `2000000002`.
- Owner private may keep automatic send.
- Do not expand to groups or other users in this change.
