# Design: Open production auto for real users

## Trust boundary

NapCat remains an untrusted protocol peer. Authority is the authenticated OneBot sender QQ and the exact `self_id` bot account, never a claim inside message text.

## Eligibility

Replace the named three-key allowlist with conversation-kind checks on the exact bot:

- `{bot_qq}:private:{user_qq}` is eligible for model and sendable outbox in `auto`.
- `{bot_qq}:group:{group_qq}` is eligible only when a trigger `at` matches `bot_qq`.
- A key for a different bot account stays ineligible.
- Friendship, imported documents, persona, and memory still do not grant eligibility.

`before_model` remains the fail-closed gate. Group messages without a real mention stay `group_not_mentioned`. Wrong-bot or malformed keys stay `conversation_ineligible`.

## Independent gates that stay closed

- `YCH_WEB_SEARCH_ENABLED`
- `YCH_IMAGE_MODEL_ENABLED` and image orphan/content-review workers
- `YCH_PRIVACY_JOBS_ENABLED`
- `YCH_DOCUMENT_OCR_ENABLED`

Qzone profile collection may stay enabled; it still requires a named user and does not scrape from friendship.

## Rollback

Set `YCH_REPLY_RUNTIME_MAX_MODE` below `auto`, emergency-pause, or disable the reply worker / outbound. Do not down-migrate the database.
