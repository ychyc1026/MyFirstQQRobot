## 1. Scoring and request assembly

- [x] 1.1 Require creator disclosure only on fixtures that declare `creator_identity`; contradiction still fails every chat case
- [x] 1.2 Prepend compiled core-identity system messages for chat, vision, and stats qualification requests
- [x] 1.3 Persist and reload sanitized check results; copy blocking reason codes onto failed decisions

## 2. Verification

- [x] 2.1 Add focused tests for omission-vs-contradiction, system-message presence, and persisted checks
- [x] 2.2 Run focused pytest, then one owner-authorized bounded live chat suite
