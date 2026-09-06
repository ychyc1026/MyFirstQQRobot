# Design: Operator labels and handbook

## Trust

Labels are operator UI metadata. Authority remains OneBot sender QQ and dashboard session. A label never grants history, Qzone, proactive, or auto-reply rights.

## Storage

Additive schema v34 table `operator_labels(subject_kind, subject_id, label, updated_at, updated_by)`.

- `subject_kind` is `user` or `group`
- `subject_id` is digits only
- `label` is 1–32 visible characters after trim; empty clears the row
- Newlines and control characters are rejected

## Isolation

Context assembly, reply planning, and group prompts SHALL NOT read `operator_labels`. The field is returned only on owner-command results and authenticated dashboard APIs.

## Handbook

`docs/operator/` is the file-folder source. The dashboard `/handbook` page renders the same catalog so the owner does not have to open the repo while operating. `/帮助` points at that page and the folder, and lists the remark commands.
