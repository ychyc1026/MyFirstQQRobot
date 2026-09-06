# Change: Observe-mode OneBot inbound integration

## Why

The owner named observe-mode OneBot integration. Qualification is closed. The reverse WebSocket ingest path already exists, but live connection still needs an explicit fail-closed token, exact-bot identity from lifecycle or events, and proof that receiving QQ events does not send, call a model, or construct the NapCat HTTP client.

## What Changes

- Require a configured OneBot access token before accepting the reverse WebSocket.
- Treat OneBot `meta_event` heartbeat/lifecycle frames as connection identity, not ignored conversational events.
- Persist exact-bot inbound messages while outbound, reply worker, model gateways, Qzone, history, and NapCat HTTP remain unused.
- Expose authenticated bot QQ versus the configured bot on status without implying delivery is active.
- Document the live observe procedure: start YCH first, then connect NapCat reverse WebSocket for bot `2000000002`.
- Provide a local operator start script that starts YCH, waits for health, then launches sibling NapCat for QR login without changing delivery flags.

## Non-goals

- Enabling outbound, the reply worker, shadow inference, Qzone, history, or any QQ send.
- Promoting reply mode above `observe_only`.
- Changing NapCat version or logging into QQ from this repository's tests.
- Using inbound messages as model qualification fixtures.
- Promoting the operator start script into an automatic login, outbound enabler, or combined QQ client.

## Real effects and data handling

Implementation tests use fake WebSocket clients and temporary SQLite. The owner-authorized live step may start the local YCH listener and connect sibling NapCat reverse WebSocket so real events for bot `2000000002` can be stored. Message bodies stay in the local database; logs, docs, and chat transcripts SHALL NOT include them. No send call is authorized.

## User decisions

Already decided:

- First real effect is receive-and-store only.
- Exact bot QQ is `2000000002`.
- Outbound and reply worker stay off.
- One-click start is YCH first, then NapCat QR login for bot `2000000002`. It does not open outbound.
