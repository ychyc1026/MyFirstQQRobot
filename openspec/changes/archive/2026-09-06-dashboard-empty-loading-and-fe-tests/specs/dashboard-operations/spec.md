## ADDED Requirements

### Requirement: Shared empty and loading presentation
Authenticated operator pages SHALL present empty lists, filter misses, unselected detail panes, and content-area loading through shared dashboard empty/loading components rather than ad-hoc muted paragraphs, and SHALL preserve existing safety wording that states a capability is off-network or non-delivering.

#### Scenario: List has no items
- **WHEN** an operator opens a workflow list with zero items and no active filter miss
- **THEN** the page shows the shared empty presentation with a “还没有…” title and optional next-step detail

#### Scenario: Filter matches nothing
- **WHEN** an operator filter yields zero rows while data may exist under other filters
- **THEN** the page shows the shared empty presentation with a “没有匹配…” title rather than the zero-items-onboarding copy

#### Scenario: Detail pane has no selection
- **WHEN** a master-detail page has no selected row
- **THEN** the detail pane shows the shared select-hint empty presentation telling the operator to pick an item from the left

#### Scenario: Content is loading
- **WHEN** a page is waiting on the first authenticated list or detail fetch
- **THEN** the content area shows the shared loading skeleton presentation with a busy accessibility signal

#### Scenario: Safety wording is preserved
- **WHEN** an empty state previously explained that shadow mode does not touch the network or that candidates do not enter the outbox
- **THEN** that safety explanation remains visible in the empty presentation detail
