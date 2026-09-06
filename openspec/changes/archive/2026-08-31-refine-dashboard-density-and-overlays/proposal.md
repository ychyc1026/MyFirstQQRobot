# Change: Refine dashboard density and overlays

## Why

The shadcn-style shell is in place, but several operational details still feel disconnected: native select menus ignore the design system, recent activity is compressed and exposes machine labels, and some two-column pages leave large visual voids or create almost-aligned cards with mismatched heights.

## What Changes

- Add a full operation-log page and keep only a localized recent summary on Operations.
- Replace every native select with one shared accessible overlay component.
- Standardize chart tooltips and active cursors with the page surface tokens.
- Recompose persona, memory, knowledge, automation and system workspaces so columns do not create misleading row alignment or large dead zones.
- Add browser regression checks for overlays, sparse data and empty states.

## Impact

- Frontend routes and shared UI components only; existing API contracts and safety gates remain unchanged.
- Adds `/activity` as a read-only view over the existing operations activity endpoint.
