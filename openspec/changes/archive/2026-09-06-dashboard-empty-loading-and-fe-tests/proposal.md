# Change: Unify dashboard empty/loading states and layer frontend tests

## Why

Operator pages still mix bare muted paragraphs for empty lists, unselected detail panes, and loading skeletons. The same “no data” situation looks different on every page, and frontend tests have no shared support layer like the backend.

## What Changes

- Shared `EmptyState` tones for list empty, filter-no-match, and “select from left” panes.
- Shared `LoadingState` for content-area loading skeletons.
- Replace bare empty/loading markup on all main operator pages that still use ad-hoc paragraphs.
- Keep safety-critical wording (no network, no outbox, gates closed) when present.
- Add `frontend/src/test/` README and `_support`-style helpers; keep page/component tests colocated; add EmptyState/LoadingState tests.

## Non-goals

- Redesigning page layouts or information architecture.
- Changing backend APIs, authorization, or reply eligibility.
- Migrating every historical empty string into English or new product copy beyond the agreed Chinese patterns.

## Real effects and data handling

Frontend-only. Automated tests use mocks and temporary fixtures. No QQ, model, or storage side effects.
