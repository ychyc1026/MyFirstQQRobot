## 1. Auto conversation gate

- [x] 1.1 Fail closed: unnamed conversations in auto do not call the chat gateway
- [x] 1.2 Named test private auto run can call the fake gateway and create outbox without approval
- [x] 1.3 Owner private auto run can still create outbox without approval

## 2. Operator path

- [x] 2.1 Allow the start script to keep NapCat up when max mode is `auto` and outbound is on
- [x] 2.2 Document live auto as owner private plus `2000000003` only
- [x] 2.3 Run focused pytest plus ruff on touched files
