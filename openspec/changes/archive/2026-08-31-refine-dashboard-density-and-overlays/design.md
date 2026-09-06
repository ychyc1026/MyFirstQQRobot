# Design

## Decisions

1. Operations keeps three recent events as a compact summary; `/activity` provides a 50-row filterable table.
2. Machine action identifiers are mapped to Chinese labels. Unknown identifiers receive a safe Chinese category label while the raw value is retained only as secondary diagnostic text on the full log page.
3. A Radix Select implementation replaces native dropdown menus so triggers, portals, focus, dark mode and selected states are consistent.
4. Two-column workspaces use independent column stacks and balanced width ratios. Empty master/detail pairs collapse into one coherent empty surface instead of two matching blank cards.
5. Charts use the same bordered popover style as menus; the active cursor must not cover the plot with an opaque block.
