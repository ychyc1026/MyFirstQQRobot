# Design: Owner-private remaining capability probes

## Vision reply stance

Vision is a seeing capability, not a captioning task. When the vision route is invoked, the system prompt tells the model it is chatting on QQ, forbids caption openers, and must not ask the model to describe or list what is in the picture. An image-only turn is sent as a chat event, not `[图片]`. Accompanying text stays part of the same turn. Only an explicit ask such as "这是什么" or "帮我看" may get a direct helpful answer, still in spoken voice. If the first completion still opens like a caption, retry the vision call once. Text-only turns keep the chat route and do not receive this stance.

## Qzone profile collection

Collection is owner-specified, never automatic. Default scan is 10 posts, hard cap 20. Image and video bytes are not a feed: at most three local image/cover inlines may call vision; full videos are never downloaded. Public DTOs, owner acks, and logs expose counts and reason codes only. Candidate memories stay user-scoped and inactive until approved.

## Trust boundary

Authority is the authenticated OneBot sender QQ. The operator desktop client may send only to `2000000002`. New outbound from the bot is limited to owner private `2000000001`. Qzone publish is a public-surface exception named by the owner.

## Rollback

Set owner-report, proactive, Qzone, live-history, privacy-job, and vision-model flags back to false, emergency-pause, or set outbound false. Reject any unused high-risk approvals.
