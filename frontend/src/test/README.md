# Frontend test layout

Keep `*.test.tsx` next to the unit under test (`components/`, `pages/`).
Shared helpers live here so page tests do not import each other.

| Location | Scope |
| --- | --- |
| `components/**/*.test.tsx` | Shared UI and operator widgets |
| `pages/**/*.test.tsx` | Page shells against mocked `api` |
| `test/support/` | Render helpers and common mocks |

Run:

```powershell
npm --prefix frontend test
```
