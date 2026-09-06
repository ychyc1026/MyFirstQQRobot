# Design: Named-group live auto

## Trust boundary

Authority is the authenticated OneBot sender and the exact conversation key `{bot_qq}:group:{group_id}`. Group text that does not contain an `at` segment for the bot QQ is not a mention, even if the characters `@2000000002` appear as plain text.

## Eligibility

`live_auto_conversation_keys` stays the single allowlist. Group keys in that set still require at least one trigger `at` matching `bot_qq` before `before_model` returns true. A mismatch suppresses the run as `group_not_mentioned` or `conversation_ineligible`.

## Isolation

Group context continues to use core identity and global persona only. Per-user personas, understanding, memories, and private history must not enter the group prompt.

## Rollback

Remove the group key from the allowlist or emergency-pause. Existing private auto allowlist is unchanged.
