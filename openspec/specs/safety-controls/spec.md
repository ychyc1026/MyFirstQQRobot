# Safety Controls

## Purpose

Describe the deny-by-default runtime gates, approvals, auditability, and recovery protections already provided by the system.

## Requirements

### Requirement: Deny-by-default external effects
The system SHALL keep model network calls, real message delivery, Qzone publication, history collection, and background execution disabled unless their independent configuration and runtime gates permit them.

#### Scenario: A feature is configured but its network gate is off
- **WHEN** code requests an external effect while the applicable network or execution gate is disabled
- **THEN** no external call is made and the reason is observable

### Requirement: Audited privileged mutations
The system SHALL record privileged control, approval, worker, privacy, Qzone, and maintenance mutations with actor or source context and timestamps.

#### Scenario: Old migration backups are quarantined
- **WHEN** an authenticated operator confirms a current cleanup preview
- **THEN** the system records the quarantine identifier, file summary, result, and owner identity

### Requirement: Migration-safe database startup
The system SHALL check database integrity and create a verified SQLite backup before applying a schema migration to an existing database.

#### Scenario: Required migration backup fails
- **WHEN** an existing database needs migration and a verified backup cannot be created
- **THEN** startup stops before repository migration and workers start

### Requirement: Recoverable backup retention
The system SHALL preserve the newest three managed migration backups, treat only verified backups older than 90 days as retention candidates, and require a fresh signed preview before moving candidates into a recoverable quarantine.

#### Scenario: Candidate files change after preview
- **WHEN** the operator submits a token whose candidate evidence no longer matches
- **THEN** cleanup is rejected without moving files

### Requirement: Backend-enforced reply activation transitions
The system SHALL validate reply-mode transitions in the backend against authenticated authority, mandatory readiness gates, allowed transition rules, and current emergency-pause state.

#### Scenario: Dashboard hides a restriction incorrectly
- **WHEN** a client directly requests a more permissive mode without satisfying backend checks
- **THEN** the request is rejected regardless of the client UI

#### Scenario: Non-owner QQ requests activation
- **WHEN** a QQ command sender is not the authenticated current-instance owner
- **THEN** no reply-runtime state changes

### Requirement: Independent real-effect gates
The system SHALL require reply-worker enablement, a delivering activation mode, protected chat-model enablement, model-network enablement, outbound enablement, active outbox delivery, valid OneBot authentication, and matching bot connectivity as independent conditions for automatic real replies.

#### Scenario: All but one gate are enabled
- **WHEN** any required real-effect gate is false
- **THEN** the system performs no automatic real reply and reports the exact blocker

### Requirement: Persistent emergency stop
The system SHALL provide a persistent emergency stop that immediately prevents new model calls and outbox handoffs, survives restart, has precedence over every requested mode, and requires an authenticated explicit resume action.

#### Scenario: Process restarts after emergency stop
- **WHEN** the owner previously activated emergency stop
- **THEN** the reply runtime remains effectively paused after restart even if configuration requests `auto`

#### Scenario: Emergency stop is activated with queued reply bubbles
- **WHEN** unsent reply bubbles already exist in outbox
- **THEN** the reply dispatcher does not claim them while paused and does not discard their audit evidence

### Requirement: Audited reply-runtime control
The system SHALL audit requested mode changes, rejected transitions, allowlist mutations, approval decisions, emergency stop/resume, worker failures, and recovery takeovers with actor, source, timestamp, prior state, resulting state, and sanitized reason.

#### Scenario: Owner enables a limited-auto conversation
- **WHEN** the authenticated owner adds an eligible conversation
- **THEN** the audit trail records the exact bot and conversation scope without storing message content or secrets

### Requirement: Readiness-gated external effects
The system SHALL require a current passing `controlled_real_effect` readiness result for the exact bot, process instance, capability, and scope in addition to every existing independent real-effect gate, and SHALL fail closed when the result is blocked, unknown, stale, or mismatched.

#### Scenario: Existing gates pass but readiness is stale
- **WHEN** a reply, proactive message, owner report, history read, model call, or Qzone effect reaches its external boundary after readiness evidence expires
- **THEN** the effect is not attempted and a structured blocker is recorded

#### Scenario: Readiness failure affects one capability
- **WHEN** image-generation readiness is blocked but protected chat readiness still passes
- **THEN** the image effect remains disabled without falsely disabling or enabling the independent chat capability

### Requirement: Recoverable managed-artifact cleanup
The system SHALL require current integrity, reference protection, an authenticated revision-bound preview, and an atomic project-confined quarantine operation before changing the location of a managed backup or privacy artifact, and SHALL NOT silently permanently delete it.

#### Scenario: Cleanup request bypasses preview
- **WHEN** a client directly requests a filesystem move without current confirmation evidence
- **THEN** the backend rejects the operation regardless of what the dashboard displays

### Requirement: Qualification side-effect isolation
The system SHALL compose model qualification without production outbox, NapCat, history, Qzone, search, proactive-delivery, or owner-report-delivery dependencies and SHALL stop qualification if any prohibited effect becomes active during a run.

#### Scenario: Fake qualification is executed
- **WHEN** any capability suite runs in fake mode
- **THEN** no real external client is constructed and no production effect table or user-data record is read or changed

#### Scenario: Outbound delivery becomes active mid-run
- **WHEN** controlled live qualification detects that a prohibited reply, delivery, Qzone, history, proactive, or report effect is active
- **THEN** future model attempts are blocked and the run records a sanitized side-effect-isolation blocker

### Requirement: Qualification budget containment
The system SHALL apply qualification-specific request, token, image, and cost ceilings in addition to the existing capability quota, timeout, retry, readiness, circuit-breaker, and emergency-pause controls.

#### Scenario: Existing daily quota permits more than the preview
- **WHEN** a confirmed run reaches its smaller qualification request or token ceiling
- **THEN** no further provider attempt occurs even though the general daily quota remains available

#### Scenario: Emergency pause activates
- **WHEN** emergency pause becomes active before the next qualification case
- **THEN** the case is not claimed for a provider call and the run stops with a stable blocker

### Requirement: Observe inbound is independent of outbound
The system SHALL treat reverse-WebSocket ingest as an independent real effect from QQ delivery and SHALL NOT enable outbound, the reply worker, model-network production replies, Qzone, history collection, or owner-report delivery when observe ingest is connected.

#### Scenario: Observe connection is live
- **WHEN** NapCat is connected and inbound events are being stored
- **THEN** outbound, reply-worker, Qzone, history, and owner-report gates remain in their prior disabled state

### Requirement: Observe start script does not enable delivery
The operator start script SHALL start the YCH listener before launching sibling NapCat, SHALL NOT change outbound, reply-worker, Qzone, history, or owner-report flags, and SHALL NOT use owner QQ `2000000001` as a NapCat login target.

#### Scenario: Operator runs observe start while delivery stays off
- **GIVEN** outbound is disabled
- **WHEN** the operator runs `scripts/start-observe.bat` with max mode `observe_only` or `shadow`
- **THEN** YCH is listening before NapCat is launched and delivery flags remain unchanged

#### Scenario: Operator start refuses NapCat when outbound is already on
- **GIVEN** outbound is enabled and reply runtime max mode is not one of `owner_approved`, `limited_auto`, or `auto`
- **WHEN** the operator runs the observe start script
- **THEN** the script does not launch NapCat and does not set those flags to enabled

#### Scenario: Operator start refuses NapCat when limited_auto or auto is configured
- **GIVEN** reply runtime max mode is `auto` and outbound is enabled
- **WHEN** the operator runs `scripts/start-observe.bat`
- **THEN** YCH is listening before NapCat is launched and those flags remain unchanged

#### Scenario: Operator runs start while owner_approved outbound is already on
- **GIVEN** reply runtime max mode is `owner_approved` and outbound is enabled
- **WHEN** the operator runs `scripts/start-observe.bat`
- **THEN** YCH is listening before NapCat is launched and those flags remain unchanged

#### Scenario: Operator runs start while limited_auto outbound is already on
- **GIVEN** reply runtime max mode is `limited_auto` and outbound is enabled
- **WHEN** the operator runs `scripts/start-observe.bat`
- **THEN** YCH is listening before NapCat is launched and those flags remain unchanged

### Requirement: Live shadow does not enable QQ delivery
Raising the reply runtime to `shadow` for owner-private live inference SHALL NOT enable outbound, Qzone publish, Qzone profile collection, history collection, or owner-report delivery.

#### Scenario: Shadow worker runs while outbound stays off
- **WHEN** the reply worker processes an owner-private shadow run
- **THEN** outbound remains disabled and no NapCat send client is required

### Requirement: Live owner-approved send requires explicit owner approval
Raising the reply runtime to `owner_approved` for owner-private live send SHALL create a sendable outbox item only after the authenticated owner approves the exact pending plan, and SHALL NOT enable Qzone publish, Qzone profile collection, history collection, owner-report delivery, `limited_auto`, or `auto`.

#### Scenario: Approval creates one outbox item
- **WHEN** the owner approves a pending owner-private plan while outbound remains enabled
- **THEN** exactly that plan may be handed to the reply outbox and later dispatched

#### Scenario: No approval means no QQ send
- **WHEN** a generated owner-private plan is waiting for approval
- **THEN** no NapCat send is attempted for that run

### Requirement: Live limited-auto send requires explicit eligibility
Raising the reply runtime to `limited_auto` for owner-private live send SHALL create a sendable outbox item only for the exact allowlisted owner private conversation, and SHALL NOT enable Qzone publish, Qzone profile collection, history collection, owner-report delivery, or `auto`.

#### Scenario: Allowlisted owner private message may send
- **WHEN** the owner private conversation is eligible and effective mode is `limited_auto`
- **THEN** exactly that generated plan may be handed to the reply outbox without a new per-message approval

#### Scenario: Missing eligibility means no QQ send
- **WHEN** effective mode is `limited_auto` and the owner private conversation is not eligible
- **THEN** no sendable outbox item is created for that run

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

### Requirement: Live auto send covers this bot's conversations
Raising the reply runtime to `auto` SHALL create a sendable outbox item for every private conversation of the exact configured bot and for every group conversation of that bot when a trigger mentions the exact bot.

#### Scenario: Any private user receives a sendable reply
- **WHEN** effective mode is `auto` and a private sender talks to the configured bot
- **THEN** a sendable private outbox item may be created without per-message approval

#### Scenario: Any group mention receives a sendable reply
- **WHEN** effective mode is `auto` and a message in any group mentions the configured bot
- **THEN** a sendable group outbox item may be created without per-message approval

#### Scenario: Group messages without mention do not send
- **WHEN** effective mode is `auto` and a group message does not mention the configured bot
- **THEN** no sendable outbox item is created

### Requirement: Production keeps search and image generation closed
Opening production `auto` for real users SHALL NOT enable web search, image generation, privacy-job execution, or document OCR.

#### Scenario: Search remains disabled
- **WHEN** production `auto` is active for real users
- **THEN** the web-search adapter is not constructed and search credentials are not required

#### Scenario: Image generation remains disabled
- **WHEN** production `auto` is active for real users
- **THEN** image-generation tasks are not executed
