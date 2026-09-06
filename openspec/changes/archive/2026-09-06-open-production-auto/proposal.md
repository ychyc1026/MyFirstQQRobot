# Change: Open production auto for real users

## Why

Named live probes are closed. The owner authorized production use for real users, with reply mode `auto`. Search and image generation stay closed. Keeping the three-key allowlist would store other users without a model call or sendable reply.

## What Changes

- When effective mode is `auto`, invoke the chat or vision model and create a sendable outbox item for every private conversation of the exact configured bot.
- When effective mode is `auto`, invoke the model and create a sendable group outbox item for every group conversation of the exact configured bot only when a trigger mentions that bot.
- Keep search, image generation, privacy-job execution, and document OCR closed.
- Open the remaining independent production gates that already have owner authorization and do not require those closed capabilities.

## Non-goals

- Enabling web search or image generation.
- Enabling privacy-job execution or document OCR.
- Group replies that are not a real `@` of the bot.
- Inferring eligibility from friendship, imported history, persona, or memory.
- Expanding `auto` to a different bot account.

## Real effects and data handling

Automated tests use temporary SQLite and fake transports. The owner-authorized production step may restart YCH, keep outbound and the reply worker on, and allow the configured bot to send private replies and `@`-gated group replies. Docs, logs, and this chat SHALL NOT quote message, model, Qzone, or history bodies.
