## ADDED Requirements

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
