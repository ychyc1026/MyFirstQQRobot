## 1. Runtime gate

- [x] 1.1 Add the named group key to the live auto allowlist
- [x] 1.2 Suppress named-group runs that do not `@` the exact bot
- [x] 1.3 Keep unnamed private chats and other groups ineligible

## 2. Verification

- [x] 2.1 Cover `@`, no-`@`, and other-group cases with fake transports
- [x] 2.2 Live-test one owner `@` in group `2000000004` after restart
- [x] 2.3 Treat `@` plus a face-only trigger as supported text via a sanitized placeholder
- [x] 2.4 Live-retest one owner `@` plus a face in group `2000000004` after restarting YCH
