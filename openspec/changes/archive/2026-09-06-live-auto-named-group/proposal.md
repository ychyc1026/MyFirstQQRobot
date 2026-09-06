# Change: Live auto replies for one named group

## Why

Named private `auto` is enough for other private users. The owner named group `2000000004` for the first live group reply, including `@` the bot. Unconstrained group auto would answer every message in every group.

## What Changes

- Keep existing private `auto` allowlist: owner private and `2000000003` private.
- Add exactly `{bot_qq}:group:2000000004` to the live `auto` conversation allowlist.
- In that group, invoke the model and create a sendable outbox item only when a trigger message `@` the exact bot QQ.
- Other groups and unnamed private chats stay stored without a model call and without a sendable outbox item.
- Do not attach stickers, send generated images, or enable vision-in-reply in this change.
- Logs and this chat SHALL NOT include prompt or reply bodies.

## Non-goals

- Opening all groups or any group other than `2000000004`.
- Adding more private auto users.
- Group replies that are not an `@` of the bot.
- Multi-bubble policy changes, sticker outbound, vision-in-production-reply, or image-to-QQ.

## Real effects and data handling

Automated tests use temporary SQLite and a fake chat gateway. The owner-authorized live step may restart YCH and send one `@` from the owner QQ in group `2000000004`. The bot may send one group reply there. Docs, logs, and this chat SHALL NOT quote the message or model output.
