## Context

YCH currently has more than twenty frontend routes and a working API client, authentication flow, lazy-loaded route tree, and safety-aware operator workflows. The existing visual system was intentionally editorial and highly branded, but the owner has explicitly replaced that direction with the shadcn-admin reference. The redesign must therefore be comprehensive while avoiding a backend rewrite or a loss of domain-specific behavior.

The reference is used for visual density, shell composition, neutral color semantics, and interaction patterns. YCH remains a local Windows control plane with Chinese content, long operational tables, sensitive records, approval workflows, and safety gates that do not exist in the reference demo.

## Goals / Non-Goals

### Goals

- Make every route look and behave like one coherent shadcn-admin product.
- Select components according to data shape, interaction scope, and risk.
- Keep status and safety information truthful and visible.
- Support efficient desktop use at 1440, 1280, and 1024 CSS pixels, with a usable narrow-screen fallback.
- Preserve route lazy loading, API behavior, authentication, sanitization, and backend enforcement.
- Provide complete loading, empty, error, disabled, stale, and disconnected states.
- Keep Chinese typography compact and readable.

### Non-Goals

- Copying reference demo metrics, names, branding, or placeholder business content.
- Replacing React Router or rewriting the backend API to match the template repository.
- Introducing arbitrary theme palettes, drag-and-drop dashboard builders, or user-customizable widget layouts.
- Turning every domain object into a card.
- Exposing API keys, tokens, prompts, raw OneBot payloads, private message bodies, or reusable confirmation secrets.
- Enabling any real model, QQ, Qzone, history, or destructive behavior.
- Making complex operational tables fully featured on small phones; narrow layouts may use sheets, stacked summaries, and horizontal table scrolling.

## Decisions

### 1. Reimplement the reference language on the existing application

YCH keeps Vite, React, TypeScript, React Router, the API client, and route-level lazy loading. It adopts shadcn-compatible primitives and layout patterns without replacing the application with the reference repository. This limits regression risk and avoids importing unrelated authentication, routing, sample data, and feature modules.

### 2. Use semantic neutral tokens with only light and dark modes

CSS variables follow the shadcn semantic model: `background`, `foreground`, `card`, `popover`, `primary`, `secondary`, `muted`, `accent`, `destructive`, `border`, `input`, `ring`, and chart tokens. The five YCH palette themes, ember naming, colored canvas, decorative gradients, and oversized custom radii are removed. Radius defaults to a restrained approximately 8px scale. Shadows remain subtle and are not used as the primary hierarchy mechanism.

### 3. Establish one shared primitive layer

Reusable primitives live under `frontend/src/components/ui`. They own focus rings, disabled states, sizes, variants, ARIA behavior, and light/dark styling. Page code composes these primitives and must not recreate local button, badge, input, select, modal, or table styles. Focused dependencies may include Radix primitives, `class-variance-authority`, `clsx`, `tailwind-merge`, command-menu support, and toast support.

### 4. Use page archetypes instead of page-specific shells

The redesign provides these reusable compositions:

- dashboard: page header, compact metrics, chart, recent activity, and action queue;
- data explorer: toolbar, filters, data table, pagination, and detail sheet;
- master-detail: searchable object list with tabs and a contextual detail/editor pane;
- editor: metadata header, form or text editor, unsaved state, and version history;
- calendar/queue: calendar or date range plus task table and event timeline;
- settings: grouped forms, read-only facts, readiness alerts, and audited dangerous actions.

Routes select the closest archetype rather than inventing another layout.

### 5. Map domain functions to risk-appropriate components

- Overview uses compact metric cards, one restrained chart, pending-action rows, and recent activity.
- Operations uses badges, progress, tabs, charts, and an event timeline.
- Reply pipeline uses a stage stepper, sortable runs table, detail sheet, blocker alert, and delivery timeline.
- Users uses a searchable data table or master list, avatars, relationship badges, tabs, and an audited detail pane.
- Personas uses scope tabs, validated text areas, version history, and context-preview sheets.
- Memories uses filterable tables, status badges, conflict comparison dialogs, and explicit forget confirmation.
- Knowledge uses a drop zone, jobs table, progress, chunk accordion, evidence preview, and approval dialog.
- Conversations uses a two-pane inspector, context accordion, sanitized run detail, and explicit replay dialog.
- Chat logs use a peer list, message viewport, date filters, and contextual detail without becoming a generic card grid.
- Approvals uses an inbox-like master-detail layout with alert-dialog confirmation for consequential decisions.
- Proactive messaging and Qzone use calendars, queues, policy forms, switches, event timelines, and explicit publish/cancel/revoke confirmation.
- Diary and materials use editors, upload controls, status indicators, history tables, and validation feedback.
- Accounts and quotas use tables, forms, switches, usage progress, and bounded edit dialogs.
- Privacy uses server-filtered audit tables, artifact verification detail, export status, and destructive alert dialogs.
- System uses read-only descriptions, health badges, readiness alerts, separators, version facts, and audit timelines.

### 6. Keep global shell state concise and operational

The sidebar is grouped, collapsible, scrollable, and route-aware. The header provides breadcrumbs, global command search, a concise connection/readiness indicator, light/dark toggle, and session menu. Narrow layouts use a sheet sidebar. Global search indexes routes and explicitly safe object metadata only; it never indexes secrets, prompts, or unrestricted private text.

### 7. Make asynchronous and unavailable states first-class

Every data-bearing view defines skeleton, empty, error, disconnected, and stale behavior. Disabled capabilities display their blocking reason instead of a healthy zero. Backend disconnection makes mutation surfaces unavailable and visibly read-only. Toasts contain only safe summaries and identifiers; detailed failures remain in sanitized detail views.

### 8. Preserve backend control as the authorization boundary

Frontend hidden or disabled controls are usability aids, never authorization. Protected mutations continue through authenticated APIs. High-risk actions use the existing preview/confirm or approval mechanisms and an `AlertDialog` that states target, scope, and consequence. The UI does not invent success before the backend confirms persisted state.

### 9. Preserve URL and session safety

Pagination, filters, selected tabs, and non-sensitive object identifiers may be represented in the URL for reload and deep linking. Tokens, prompts, message bodies, private source material, and confirmation secrets may not appear in URLs. The login field is password-like, sessions expose expiry without revealing the token, and expiration returns the user to login.

### 10. Validate with a reference matrix, not subjective spot checks

The first implementation slice establishes the shell, login page, overview, one table, one form, one sheet, one alert dialog, and one chart. Those become reference fixtures for the rest of the migration. Final verification covers every route at 1440, 1280, 1024, and a narrow viewport; keyboard navigation; light/dark modes; loading/empty/error/disabled states; build output; and API regression.

## Risks / Trade-offs

- A whole-site migration can leave mixed visual languages if pages are migrated independently. Shared primitives and archetypes must land before route conversion.
- Replacing bespoke controls may expose behavior differences. Component tests and route-level browser checks are required before removing legacy components.
- Dense tables do not translate cleanly to phones. The desktop-first control plane keeps tables scrollable and moves contextual actions into sheets rather than converting every row into a card.
- Adding many Radix packages can increase bundle size. Only used primitives are installed, routes remain lazy, and production chunk sizes are checked.
- Dark mode can double visual QA. It remains because it is part of the reference interaction model, but no additional palette themes are allowed.
- Directly copying the reference application could create licensing and maintenance noise. Prefer local implementation; preserve MIT notices for any copied source.

## Migration Plan

1. Record baseline screenshots and current functional-route inventory without changing backend state.
2. Add semantic tokens, utility helpers, focused dependencies, and shared primitives alongside the legacy components.
3. Build the new shell and first-slice fixtures; verify them before route-wide migration.
4. Migrate routes by archetype, keeping old components only while remaining consumers exist.
5. Remove legacy theme code, ember tokens, palette selector, and obsolete bespoke components after all consumers are migrated.
6. Run frontend build, backend regression tests, strict OpenSpec validation, sensitive-data scan, and browser verification with real-effect gates closed.
7. Update dashboard documentation and archive the change only after every route passes the acceptance matrix.

Rollback is a normal Git revert of frontend commits. No database rollback or data conversion is required because this change does not alter persistence or backend contracts.

