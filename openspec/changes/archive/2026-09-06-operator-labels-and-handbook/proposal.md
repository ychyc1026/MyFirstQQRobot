# Change: Operator labels and handbook

## Why

The owner operates from QQ commands and the dashboard, but the command list is scattered and every person is shown as a raw QQ number. That is not usable when recognizing friends or looking up what to type.

## What Changes

- Store an operator-only label for a user QQ or group QQ.
- Show the label next to the QQ on operator surfaces; search by label or number.
- Let the owner set or clear a label from the user page or `/用户 <QQ> 备注` / `/群 <QQ> 备注`.
- Keep labels out of model context, group replies, and other users' views.
- Add `docs/operator/` as the handbook folder and an in-dashboard 操作手册 page with the same command catalog.

## Non-goals

- Importing QQ friend remarks automatically.
- Using labels as persona, memory, or model instructions.
- Changing reply eligibility or authorization.

## Real effects and data handling

Automated tests use temporary SQLite. Setting a label does not send QQ, call a model, or change reply mode. Docs and this chat SHALL NOT quote private chat bodies.
