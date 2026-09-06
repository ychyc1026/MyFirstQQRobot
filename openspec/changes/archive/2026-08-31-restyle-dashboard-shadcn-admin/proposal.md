## Why

The current dashboard exposes most YCH capabilities, but its warm paper palette, oversized editorial headings, custom rounded cards, five color themes, and page-specific controls no longer match the requested product direction. The operator wants the dashboard to follow the interaction density and visual language of the shadcn-admin reference: neutral surfaces, compact typography, thin borders, restrained radius, consistent navigation, and standard accessible controls chosen according to each workflow.

This change is not a palette swap. YCH has tables, queues, calendars, timelines, editors, high-risk approvals, conversation inspectors, and system readiness views. Each capability needs a component appropriate to its information shape and risk instead of being placed inside another decorative card.

## What Changes

- Replace the current ember/paper design tokens and five-theme system with a shadcn-compatible neutral light/dark token set.
- Introduce a reusable `components/ui` layer for buttons, forms, cards, tables, tabs, badges, alerts, dialogs, sheets, calendars, commands, progress, skeletons, tooltips, scroll areas, and related primitives.
- Rebuild the application shell around a compact grouped sidebar, sticky header, breadcrumbs, global command palette, health indicators, user/session menu, and responsive sheet navigation.
- Define reusable page archetypes for dashboards, data tables, master-detail workspaces, editors, calendars, timelines, and settings instead of styling every page independently.
- Map each YCH capability to the component that best represents it: data tables for high-volume records, sheets for contextual detail, dialogs for bounded edits, alert dialogs for high-risk confirmation, timelines for durable events, calendars for wall-clock tasks, and forms for policy changes.
- Add complete loading, empty, disabled, disconnected, permission-denied, partial-success, failed, and stale-state presentation.
- Preserve backend authorization, current API contracts, sanitization, approval semantics, audit behavior, reply-runtime gates, and real-network defaults.
- Add keyboard navigation, focus treatment, accessible labels, responsive behavior, route-aware command search, and truthful feedback.
- Verify visual consistency and behavior at desktop, laptop, and narrow layouts before archiving the change.

The redesign does **not** enable QQ messaging, model calls, Qzone access, history reading, destructive privacy jobs, or any other real network effect. It does not change database schemas or backend policy. It also does not copy the reference site's sample business data, branding, or navigation labels; YCH identity and domain content remain intact.

## Capabilities

### New Capabilities

- `dashboard-design-system`: Neutral shadcn-admin visual tokens, shared accessible primitives, page archetypes, functional component mapping, responsive behavior, and UI-state standards.

### Modified Capabilities

- `dashboard-operations`: Present all existing operator workflows through consistent, truthful, risk-appropriate components without weakening backend authorization or sanitization.

## Impact

- `frontend/package.json` gains the focused Radix/shadcn support dependencies actually used by YCH rather than importing an entire template application.
- `frontend/src/index.css`, Tailwind configuration, application shell, authentication surface, shared components, and every routed page are restyled or migrated.
- The legacy `lib/theme.ts`, ember color tokens, theme swatches, large editorial headings, decorative tone cards, and five-palette selector are removed.
- Existing React Router routes, API client types, session storage behavior, lazy loading, backend endpoints, and protected-action flows are retained unless a narrow frontend adapter is needed.
- Visual and interaction verification uses local synthetic or existing sanitized data. It must not enable model networking, QQ outbound, Qzone, history collection, or destructive actions.
- If MIT-licensed reference code is copied rather than independently reimplemented, its required license notice is preserved in repository documentation.

