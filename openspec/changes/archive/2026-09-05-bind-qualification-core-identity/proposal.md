# Change: Bind qualification chat to compiled core identity

## Why

The first controlled live chat run called SiliconFlow successfully but failed identity checks. Qualification sent only the synthetic user fixture and required every case to name the creator. That is not how production chat works: production injects compiled YCH identity, and disclosure says to name the creator only when the topic is relevant.

Without this bind, repeating the full chat suite would spend money and fail for the same structural reason.

## Goals

- Chat, vision, and stats qualification requests SHALL include compiled `CORE_IDENTITY` directives and a synthetic non-owner conversation directive.
- Qualification SHALL NOT add personas, memories, QQ history, Qzone content, or imported documents.
- Creator-identity blocking SHALL require an explicit creator disclosure only on fixtures that declare `creator_identity`; other fixtures fail only when the reply contradicts immutable identity.
- Persist sanitized check results so a failed or passed run can be inspected without prompt or response bodies.
- Re-run the bounded official-CNY chat suite after the bind.

## Non-goals

- Enabling reply delivery, outbox, NapCat, Qzone, history, or changing `.env`.
- Qualifying vision, image, or stats in this change unless the owner names them after chat.
- Replacing advisory style scores with a model judge.

## Real effects and data handling

Implementation itself does not call a provider. After tests pass, the owner-authorized continuation may execute one bounded chat live run against SiliconFlow using synthetic fixtures only. Evidence remains sanitized.

## User decisions

Already decided:

- Continue chat qualification rather than stop after the first failed identity case.
- Use the configured official-CNY chat route; do not silently rewrite `.env`.
