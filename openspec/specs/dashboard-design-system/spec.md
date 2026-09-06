# dashboard-design-system Specification

## Purpose
Define the dashboard's shared visual language, accessible interaction primitives, responsive page structure, and safe component patterns so every operational workflow remains consistent, legible, and trustworthy.

## Requirements

### Requirement: Unified shadcn-admin visual language
The dashboard SHALL use one neutral shadcn-compatible light/dark visual system with compact typography, restrained radius, thin borders, subtle elevation, semantic status colors, and consistent spacing across every route, and SHALL NOT retain the legacy ember/paper palettes or five-theme selector.

#### Scenario: Operator moves between unrelated workflows
- **WHEN** the operator navigates from a table-heavy audit page to an editor, calendar, or pipeline page
- **THEN** the shell, typography, controls, spacing, status colors, and interaction feedback remain visibly consistent

#### Scenario: Existing theme preference is present
- **WHEN** a browser contains a legacy YCH palette value
- **THEN** the application safely resolves to the supported light or dark mode without loading a mixed legacy palette

### Requirement: Function-appropriate component mapping
The dashboard SHALL represent information and actions with components appropriate to their shape and risk rather than placing every capability inside decorative cards.

#### Scenario: Operator reviews many durable records
- **WHEN** a workflow presents messages, tasks, runs, users, reports, approvals, or audit events
- **THEN** it uses searchable or filterable tables/lists, pagination where required, and a contextual detail surface instead of a grid of repeated cards

#### Scenario: Operator performs a dangerous action
- **WHEN** an action can send, publish, revoke, delete, expose private data, or change a protected runtime mode
- **THEN** the UI presents the current target, scope, and consequence in an explicit alert-dialog or existing preview/confirm flow before calling the backend

### Requirement: Reusable accessible primitives
The dashboard SHALL compose shared keyboard-accessible primitives for controls, forms, navigation, overlays, tables, calendars, feedback, and status presentation, and routed pages SHALL NOT create visually divergent local replacements for those primitives.

#### Scenario: Keyboard operator opens and closes an overlay
- **WHEN** the operator uses Tab, Enter or Space, and Escape on a menu, command palette, dialog, sheet, popover, or select
- **THEN** focus order, activation, dismissal, restoration, and visible focus treatment follow the shared component behavior

### Requirement: Complete asynchronous and capability states
Every data-bearing view SHALL define loading, empty, error, disconnected, disabled, stale, and success presentation, and SHALL explain a blocked capability rather than showing a misleading zero or inert unlabeled control.

#### Scenario: Backend connection is lost
- **WHEN** the dashboard cannot reach its local backend
- **THEN** affected views identify the disconnection, mutation controls become unavailable, existing safe content remains readable where possible, and the UI does not claim a successful mutation

#### Scenario: Real-effect capability is disabled
- **WHEN** QQ outbound, model networking, Qzone, history, or a worker is disabled
- **THEN** the relevant view shows the disabled state and structured blocker without presenting generated sample activity

### Requirement: Responsive operator shell
The dashboard SHALL provide a grouped route-aware sidebar and compact header on desktop, a sheet-based navigation surface on narrow screens, and usable content at 1440px, 1280px, and 1024px widths without hiding required safety information.

#### Scenario: Operator uses a 1024px laptop viewport
- **WHEN** the sidebar and a dense route are displayed
- **THEN** navigation remains reachable, the primary task is readable, contextual detail can move to a sheet, and tables remain usable through bounded scrolling or pagination

### Requirement: Safe global search and feedback
Global command search, URLs, toasts, error summaries, and browser-visible diagnostics SHALL contain only routes, safe metadata, sanitized identifiers, and non-sensitive summaries.

#### Scenario: Search indexes available objects
- **WHEN** the operator opens the global command palette
- **THEN** it can find routes and explicitly safe objects but does not index API keys, tokens, prompts, raw OneBot payloads, private document text, unrestricted message bodies, or reusable confirmation secrets

### Requirement: Reference-based visual acceptance
The dashboard SHALL be visually verified against the shadcn-admin reference for shell composition, density, typography, neutral palette, controls, tables, charts, overlays, and responsive behavior while retaining YCH branding and real domain content.

#### Scenario: Redesign is ready to archive
- **WHEN** the implementation claims completion
- **THEN** every route has passed the reference matrix in light and dark mode and no route still depends on the legacy visual system

### Requirement: Overlays MUST share the dashboard surface language

Select menus, chart tooltips and other transient surfaces MUST use semantic background, foreground, border, radius, shadow, focus and selected-state tokens in both themes.

#### Scenario: Operator opens a select
- **WHEN** a select trigger is opened by pointer or keyboard
- **THEN** its portal aligns to the trigger, uses the dashboard popover surface, identifies the selected option and supports Escape dismissal

#### Scenario: Operator inspects a chart point
- **WHEN** a chart tooltip is visible
- **THEN** the plot remains legible and the tooltip uses the same compact bordered surface as other overlays

### Requirement: Sparse workspaces MUST preserve visual rhythm

Master-detail and list-form pages MUST avoid implying row relationships between unrelated cards and MUST avoid large unexplained voids while useful content continues in the adjacent column.

#### Scenario: No master record exists
- **WHEN** both a collection and its detail are empty
- **THEN** the page presents one coherent empty state and keeps the creation action visually primary

#### Scenario: Columns have unequal content
- **WHEN** one column contains substantially more cards than the other
- **THEN** each column flows independently and card sizing does not create accidental near-alignment
