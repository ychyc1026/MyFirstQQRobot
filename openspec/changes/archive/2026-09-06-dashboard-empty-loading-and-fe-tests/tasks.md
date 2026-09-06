## 1. Contracts and shared UI

- [x] 1.1 Record shared empty/loading requirements in the change delta
- [x] 1.2 Extend `EmptyState` with `panel` / `inline` / `select` tones; add `LoadingState`
- [x] 1.3 Add colocated component tests for EmptyState and LoadingState

## 2. Frontend test layering

- [x] 2.1 Add `frontend/src/test/README.md` and `test/support` helpers
- [x] 2.2 Point existing page/component tests at shared support where it reduces duplication

## 3. Page rollout (option A)

- [x] 3.1 Replace bare empties on master-detail pages: Approvals, Memories, Knowledge, Proactive, Qzone, Pipeline, Conversations, Personas, Users
- [x] 3.2 Replace bare empties on list/ops pages: Privacy, Materials, Diary, Quotas, Ops, Stats, Chatlog, Overview/Activity if still ad-hoc
- [x] 3.3 Use `LoadingState` on pages that already gate content behind a loading flag

## 4. Docs and verify

- [x] 4.1 Note the shared empty/loading pattern in operator dashboard docs / IA
- [x] 4.2 Run frontend tests and build; validate OpenSpec
