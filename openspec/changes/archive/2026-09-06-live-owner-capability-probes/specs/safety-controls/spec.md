## ADDED Requirements

### Requirement: Owner-private remaining probes stay off other chats
Enabling owner-report delivery, owner-only proactive send, Qzone publish, privacy export, or image generation for this live step SHALL NOT send QQ messages to any private user other than `2000000001` and SHALL NOT send to any group.

#### Scenario: Owner report or proactive item is delivered
- **WHEN** a newly enabled owner-report or owner-targeted proactive item is dispatched
- **THEN** the outbox target is the owner private conversation

#### Scenario: Privacy delete is requested
- **WHEN** the owner requests a privacy delete during this live step
- **THEN** the request waits for approval and is rejected instead of executed

### Requirement: Owner-private live history pull is count-only
A live history pull SHALL call NapCat only for owner QQ `2000000001`, SHALL consume at most one remaining one-time authorization, and SHALL persist only message count plus access-log metadata. It SHALL NOT persist, log, or send chat bodies.

#### Scenario: Owner history pull is authorized
- **GIVEN** live history read is enabled and owner `2000000001` has a remaining one-time history grant
- **WHEN** the owner sends `/用户 2000000001 历史 拉取` in private chat with the bot
- **THEN** NapCat may be called once and the stored result contains a count, not message bodies

#### Scenario: Other users are not pulled
- **WHEN** the owner requests a live history pull for a different QQ
- **THEN** NapCat is not called and no one-time grant is consumed

### Requirement: Owner-private vision reply uses the vision route
A production reply that includes a safe inbound image URL SHALL call the vision model route when that route is enabled, and SHALL NOT call the chat route for that run. Image bytes and model text SHALL NOT be persisted in control results, logs, or documentation.

#### Scenario: Owner private image uses vision
- **GIVEN** vision is enabled and the conversation is owner-private auto
- **WHEN** the owner sends an inbound image with a safe `http(s)` or `data:image/` URL
- **THEN** the reply worker invokes the vision gateway and records the vision model route

#### Scenario: Image-only stays suppressed without vision
- **WHEN** a trigger has an image URL but the vision gateway is unavailable and there is no supported text
- **THEN** the run is suppressed as unsupported input and no model is called

#### Scenario: Group mention uses a recent standalone image
- **GIVEN** vision is enabled and the conversation is the named live group
- **WHEN** the same sender first sends a standalone inbound image and later `@` the bot without attaching that image
- **THEN** the vision call may include that recent same-sender image, and a group image without `@` still does not start a model call

#### Scenario: Group vision reply may use two short bubbles
- **GIVEN** a named-group vision candidate is longer than one 80-character bubble and still within two bubbles
- **WHEN** the reply planner normalizes that candidate
- **THEN** the run SHALL create a sendable plan instead of failing as an invalid candidate
- **AND** a named-group text reply without vision SHALL keep the one-bubble default

#### Scenario: Group image is fetched through NapCat when the CDN rejects a direct download
- **GIVEN** vision is enabled and a named-group `@` has an inbound image file id
- **WHEN** the image URL host rejects a direct GET
- **THEN** the worker SHALL call NapCat `get_image` with that file id, SHALL NOT send the remote URL to the model, and SHALL fail closed if NapCat also cannot provide image bytes

### Requirement: Vision replies stay in conversational stance
A production vision reply SHALL instruct the model to answer as a chat participant reacting to the inbound image. It SHALL NOT instruct the model to describe or list image contents. The stance SHALL be attached only when the vision route is actually invoked. An image-only user turn SHALL be sent as a chat event, not a captioning placeholder. If the first vision completion opens like a caption and the user did not ask what the image is, the worker SHALL retry the vision call once with a conversational nudge and SHALL persist only the final text.

#### Scenario: Vision system prompt asks for a human reply
- **GIVEN** vision is enabled and the trigger includes a safe inbound image
- **WHEN** the reply worker builds the vision request
- **THEN** the system prompt tells the model to reply as a chat participant, forbids caption openers, and does not tell it to say what is in the picture

#### Scenario: Text-only replies do not receive the vision stance
- **GIVEN** vision is enabled
- **WHEN** a trigger has no inbound image
- **THEN** the chat-route request does not include the vision stance preamble

#### Scenario: Caption-like first vision reply is retried once
- **GIVEN** vision is enabled and the user did not ask what the image is
- **WHEN** the first vision completion opens with a caption formula such as "这是一张"
- **THEN** the worker calls vision once more with a conversational nudge and stores the second completion

### Requirement: Named Qzone profile collection stays bounded and owner-visible
A live Qzone profile preview SHALL read at most the authorized item count, defaulting to 10 and never more than 20. If fewer posts exist, the snapshot SHALL contain that smaller count. The collector SHALL persist text and media-kind metadata only. It SHALL NOT download video files or store image/video URLs in snapshots, command results, owner acks, or logs. Image posts and video covers MAY be inlined locally for at most three vision calls per preview. Video without a usable cover SHALL be recorded as unread. Collection SHALL create expiring candidate memories only. Both owner-command and dashboard previews SHALL enqueue one sanitized owner-private ack that reports counts and reason codes and SHALL NOT include post bodies or media.

#### Scenario: Fewer than ten posts are collected as-is
- **GIVEN** collection is enabled and the user has a remaining Qzone grant
- **WHEN** NapCat returns fewer than ten posts
- **THEN** the snapshot scanned count equals the returned post count

#### Scenario: Video files are not downloaded
- **GIVEN** a granted preview includes a video-only post
- **WHEN** the collector normalizes that post
- **THEN** no video bytes or video URL are persisted, and the post is either cover-only or unread-video

#### Scenario: Command and dashboard previews ack the owner
- **GIVEN** a preview is requested from the owner command or the dashboard
- **WHEN** the preview finishes or is denied
- **THEN** one owner-private outbox item is created with counts and reason only
