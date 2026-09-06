## 1. Owner-approved conversation gate

- [x] 1.1 Fail closed: non-owner conversations in owner_approved do not call the chat gateway
- [x] 1.2 Owner private owner_approved run can call the fake gateway, wait for approval, and create outbox only after approve

## 2. Operator path

- [x] 2.1 Allow the start script to keep NapCat up when max mode is `owner_approved` and outbound is on
- [x] 2.2 Document live owner-approved as owner-private only; do not enable limited_auto or auto
- [x] 2.3 Run focused pytest plus ruff on touched files
