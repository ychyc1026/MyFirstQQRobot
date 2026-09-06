# Design: Dashboard empty/loading and frontend test layering

## Decisions

1. **Reuse `EmptyState`, add tones**  
   - `panel`: dashed bordered card (existing; tables and primary empty areas).  
   - `inline`: compact list-column empty (“还没有…” / “没有匹配…”).  
   - `select`: detail pane when nothing is selected (“从左侧选…”).

2. **Add `LoadingState`**  
   Skeleton rows with `aria-busy`, used where a page already waits for a list/detail fetch before showing content. Refresh-button spinners stay as-is.

3. **Copy patterns (Chinese)**  
   - Empty list: title `还没有…`, optional detail with the next action.  
   - Filter miss: title `没有匹配…`.  
   - Unselected detail: title `从左侧选一条…` (or 一个任务 / 一条审批 as fits).  
   - Preserve explicit safety clauses already in copy (shadow off-network, candidates not outbox, etc.) as `detail`.

4. **Frontend tests**  
   Colocate `*.test.tsx` next to the unit under test. Put shared helpers in `frontend/src/test/support/`. Document markers/conventions in `frontend/src/test/README.md`. Do not force a mirror of backend tier directories unless test count grows large.

## Risks

- Over-wrapping tiny one-line empties inside charts may hurt density; for Stats chart holes use `inline` without forcing a full panel.
- Pages that intentionally use list-item `<li>` empties should keep a single list child wrapping `EmptyState` or an equivalent inline block so layout does not break.
