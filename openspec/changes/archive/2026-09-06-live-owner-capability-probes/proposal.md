# Change: Live owner-private remaining capability probes

## Why

Reply modes and owner-private auto are archived. The owner authorized the next live probes from the owner QQ, sending only to bot `2000000002`. Group traffic, other senders, and bot sends to anyone except the owner remain out of scope.

## What Changes

- Allow owner-private probes for owner-report delivery, owner-only proactive send, Qzone draft/publish/revoke, owner history authorization plus one bounded live history pull, privacy export, production image generation, and one owner-private vision-in-reply.
- Keep web search, OCR, knowledge refining, group replies, and send-to-other-users untested unless a later named step provides the missing input or credentials.
- Privacy delete may be requested and must be rejected, not executed.
- Logs and this chat SHALL NOT include prompt, reply, Qzone, or export bodies.

## Non-goals

- Owner QQ sending to any chat except the bot private conversation.
- Bot QQ sending to any target except owner `2000000001` or an already-authorized named auto user.
- Group messages, other-user inbound tests, or unconstrained auto expansion.
- Confirming a privacy delete.

## Real effects and data handling

The owner-authorized live step may enable owner-report delivery, the proactive scheduler, Qzone publish/worker, live history read, privacy-job execution, image generation, and the vision model route. It may create one owner-private proactive item, one Qzone draft/publish/revoke, one owner history authorization, one bounded owner-private live history pull, one privacy export, one local image task, and one owner-private vision reply. The history pull SHALL persist only counts and access-log metadata, never message bodies. Vision replies SHALL persist only route name, status, and token counts, never image bytes or model text. Automated tests stay on temporary databases and fakes.
