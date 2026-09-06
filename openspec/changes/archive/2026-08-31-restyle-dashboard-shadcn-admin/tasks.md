## 1. Baseline and design-system contract

- [x] 1.1 Inventory every routed page, shared component, API-driven state, mutation, responsive behavior, and current visual dependency.
- [x] 1.2 Record desktop and laptop baseline screenshots using an authenticated local session with real QQ, model, Qzone, history, and destructive gates closed.
- [x] 1.3 Define the neutral light/dark token table, typography scale, radius, spacing, elevation, status colors, chart colors, breakpoints, and focus behavior.
- [x] 1.4 Define the page-archetype and route-to-component matrix, including loading, empty, failure, disabled, disconnected, stale, and success states.
- [x] 1.5 Add a UI acceptance checklist that preserves route behavior, API contracts, authorization, sanitization, and high-risk preview/confirmation semantics.

## 2. Shared shadcn-compatible primitives

- [x] 2.1 Add only the required Radix/shadcn support dependencies and shared `cn` utility; document any copied MIT-licensed source.
- [x] 2.2 Implement Button, Card, Badge, Input, Textarea, Label, Select, Checkbox, Switch, Separator, Skeleton, Alert, Progress, Tooltip, and ScrollArea primitives.
- [x] 2.3 Implement Tabs, Accordion, Collapsible, DropdownMenu, Popover, Calendar, Command, Dialog, AlertDialog, Sheet/Drawer, Breadcrumb, Avatar, and toast primitives.
- [x] 2.4 Implement reusable DataTable, toolbar, pagination, empty state, error state, page header, metric, timeline, status indicator, detail sheet, and form-section compositions.
- [x] 2.5 Add component tests for keyboard behavior, focus, disabled state, dialog dismissal, destructive confirmation, and light/dark token use.

## 3. Application shell and authentication

- [x] 3.1 Replace the current rounded colored rail with the reference-style grouped collapsible sidebar and active-item treatment.
- [x] 3.2 Add sticky header, breadcrumbs, `Ctrl/Cmd+K` safe command palette, health summary, light/dark toggle, and expiring-session menu.
- [x] 3.3 Add responsive sheet navigation and ensure content remains usable at 1024px and narrow widths.
- [x] 3.4 Rebuild login, suspense, not-found, disconnected, and session-expired surfaces with the new primitives.
- [x] 3.5 Remove the five-palette selector and prevent theme state from leaking into the new light/dark model.

## 4. Observability and pipeline routes

- [x] 4.1 Migrate Overview to compact metrics, restrained charts, pending work, health summary, and recent activity without fake KPI data.
- [x] 4.2 Migrate Operations and Statistics to tabs, filters, charts, status badges, timelines, and truthful closed/unavailable states.
- [x] 4.3 Migrate Reply Pipeline to readiness alerts, stage stepper, server-backed runs table, sanitized detail sheet, and delivery timeline.
- [x] 4.4 Verify loading, no-event, backend-disconnected, circuit-open, worker-paused, approval-waiting, and `delivery_unknown` presentations.

## 5. People, context, and knowledge routes

- [x] 5.1 Migrate Users and Quotas to searchable/filterable tables, relationship badges, master-detail tabs, bounded forms, and usage progress.
- [x] 5.2 Migrate Personas and Memories to scoped tabs, validated editors, version/history tables, context previews, conflict dialogs, and forget confirmation.
- [x] 5.3 Migrate Knowledge and Materials to upload/drop-zone flows, processing tables, progress, evidence accordions, validation, and approval detail.
- [x] 5.4 Migrate Conversations and Chat Logs to two-pane inspectors, safe context sections, peer/message navigation, filters, and explicit replay controls.
- [x] 5.5 Migrate Approvals to an inbox-style queue, accessible detail surface, current-state evidence, and risk-appropriate confirmation.

## 6. Scheduling, publishing, and system routes

- [x] 6.1 Migrate Proactive Messaging to calendar/queue views, timezone-aware task detail, policy forms, switches, and missed-task outcomes.
- [x] 6.2 Migrate Qzone to draft/publish calendars, visibility controls, event timelines, and publish/cancel/revoke alert dialogs that retain backend approvals.
- [x] 6.3 Migrate Diary to a date-aware editor with save state, unsaved-change protection, and history navigation.
- [x] 6.4 Migrate Accounts to bot/owner tables, audited edit dialogs, status switches, and readiness context.
- [x] 6.5 Migrate Privacy and System to server-filtered audit tables, artifact verification, destructive confirmations, immutable identity, preflight/backup health, versions, and runtime audit timelines.

## 7. Legacy removal and reliability

- [x] 7.1 Remove ember/paper tokens, theme swatches, five-theme code, decorative gradients, oversized page titles, and legacy theme storage migration after all routes use semantic tokens.
- [x] 7.2 Remove or rewrite SurfaceCard, custom Select, SegmentSlider, Tag, old date/calendar, and other bespoke components only after their consumers are migrated.
- [x] 7.3 Ensure filters, pagination, safe selected identifiers, and tabs survive reload without placing sensitive data in URLs or browser logs.
- [x] 7.4 Verify route lazy loading, chunk sizes, large-table pagination, chart accessibility summaries, and no sensitive text in command search or toasts.
- [x] 7.5 Update frontend architecture, visual language, component mapping, and operator documentation.

## 8. Acceptance and archive

- [x] 8.1 Run TypeScript and Vite production build, frontend component tests, full backend tests, Ruff format/check, and strict OpenSpec validation.
- [x] 8.2 Run a sensitive-data scan proving API keys, tokens, prompts, raw OneBot, private documents, and reusable confirmations are not rendered, logged, or added to URLs.
- [x] 8.3 Browser-check every route in light and dark mode at 1440px, 1280px, 1024px, and a narrow viewport, covering normal, loading, empty, disabled, disconnected, error, and high-risk states.
- [x] 8.4 Execute the operator flow: login, search, inspect user, edit persona, resolve memory conflict, upload knowledge, review approval, schedule proactive work, inspect pipeline, verify system state, and logout using only fake or safely disabled real-effect adapters.
- [x] 8.5 Compare the finished shell, density, typography, navigation, controls, tables, charts, and feedback against the shadcn-admin reference while preserving YCH content and identity.
- [x] 8.6 Archive `restyle-dashboard-shadcn-admin` only after all route and regression checks pass and no page retains the legacy visual system.

