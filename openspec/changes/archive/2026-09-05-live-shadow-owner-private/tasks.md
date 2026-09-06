## 1. Shadow conversation gate

- [x] 1.1 Fail closed: non-owner conversations in shadow do not call the chat gateway
- [x] 1.2 Owner private shadow run can call the fake gateway and finish `shadow_completed` with empty outbox

## 2. Operator path

- [x] 2.1 Allow the observe start script to keep NapCat up when max mode is `shadow` and outbound is still off
- [x] 2.2 Document live shadow as owner-private only; do not enable outbound
- [x] 2.3 Run focused pytest plus ruff on touched files
- [x] 2.4 Refresh stale launcher evidence at the chat-model boundary using current-process port ownership
