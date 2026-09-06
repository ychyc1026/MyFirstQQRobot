# Handoff (public template)

This tree is a **privacy-scrubbed public export** of YCH Bot. It is not a runnable
copy of anyone's production machine.

## Identity (template)

- Brand: YCH
- Creator/developer display name in this export: 维护者
- Example owner QQ in docs/tests: `2000000001`
- Example bot QQ in docs/tests: `2000000002`

Replace these with your own values in `.env` after copy. Do not commit real tokens.

## Before coding

1. Read `openspec/config.yaml` and `openspec/specs/`.
2. Read `docs/README.md` and `docs/operator/` for operator surfaces.
3. Copy `.env.example` to `.env` and fill only local secrets.

## Safety defaults

Outbound QQ, model network, Qzone publish, privacy-job execution, search, image
generation, and OCR stay off until you configure and authorize them. Readiness
is necessary but never sufficient.

## Not included

- Real `.env`, SQLite runtime data, imports, exports, privacy archives
- Local operational handoff history and NapCat rollback paths
- Any live chat bodies
