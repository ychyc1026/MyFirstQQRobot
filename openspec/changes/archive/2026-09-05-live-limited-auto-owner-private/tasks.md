## 1. Limited-auto conversation gate

- [x] 1.1 Fail closed: non-owner conversations in limited_auto do not call the chat gateway
- [x] 1.2 Owner private limited_auto run can call the fake gateway and create outbox only when eligible

## 2. Operator path

- [x] 2.1 Allow the start script to keep NapCat up when max mode is `limited_auto` and outbound is on
- [x] 2.2 Document live limited-auto as owner-private and allowlisted only; do not enable auto
- [x] 2.3 Run focused pytest plus ruff on touched files
