# 文档索引

行为基线永远看 `openspec/specs/` 和当前 `openspec/changes/`。本目录只解释实施细节、运维步骤和背景。

## 主号日常

给 YCH 自己用，仪表盘「操作手册」页与此同源。

- `operator/README.md`：每天怎么开、怎么认人、指令从哪发
- `operator/COMMANDS.md`：完整指令表
- `operator/DASHBOARD.md`：仪表盘各页怎么用

## 当前状态

- `IMPLEMENTATION_STATUS.md`：已实现范围与当前验证结果
- `NEXT_PHASE.md`：下一阶段候选
- `MASTER_PLAN.md`：总体实施顺序
- `WORKSPACE.md`：目录边界

## 设计与机制

- 身份与隐私：`CORE_IDENTITY_AND_CONTEXT.md`、`IDENTITY_AND_PRIVACY.md`
- 消息与回复：`MESSAGE_PIPELINE.md`、`PRODUCTION_REPLY_RUNTIME.md`
- 模型与内容：`MODEL_GATEWAYS.md`、`IMAGE_GENERATION.md`、`LONG_DOCUMENT_PROCESSING.md`
- 主号控制：`CONTROL_SURFACE_STANDARD.md`、`OWNER_CONTROL_AND_QZONE.md`、`OWNER_REPORTS.md`
- 自动化：`PROACTIVE_MESSAGES.md`、`QZONE_PUBLISHING.md`、`QZONE_PROFILE.md`
- 运行与数据：`STARTUP_READINESS.md`、`DATABASE_PREFLIGHT.md`、`MANAGED_ARTIFACT_RETENTION.md`、`NAPCAT_UPDATE.md`

## 历史

`history/` 里是一次性的阶段记录，只作背景，不作当前计划。

- `history/HANDOFF_2026-09-05.md`：旧的完整交接文档
- `history/IMPLEMENTATION_STATUS` 之外的阶段快照：`history/PROGRESS_AND_ROADMAP.md`、`history/PHASE8_ACCEPTANCE_EVIDENCE.md`、`history/CLEANUP_REPORT.md`

## 相关目录

- 后端测试怎么分层：`../backend/tests/README.md`
- 仪表盘信息架构：`../frontend/INFORMATION_ARCHITECTURE.md`
