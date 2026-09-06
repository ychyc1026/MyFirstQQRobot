# 旧资料清单

整理时间：2026-08-12

## 汇总

- 旧数据文件：18 个，共 77,570 字节；
- 旧规则配置：4 个，共 13,135 字节；
- 原始位置：`../napcat/data/`、`../napcat/user_memories.json` 和旧机器人专用配置；
- 当前状态：已移出 NapCat 运行目录，作为只读迁移源保留；
- Git 状态：被 `.gitignore` 排除，不提交隐私内容。

## 数据类别

- 用户与群组：`groups.json`、`nicknames.json`、`user_nicknames.json`；
- 旧记忆：`memory.json`、两份 `user_memories.json`、一份备份及根目录旧记忆；
- 用户笔记：2 个 QQ 号对应的文本文件；
- 人格：6 个 JSON 文件；
- 主动消息：`proactive_last_sent.json`、`sent_history.json`；
- 其他状态：`info_store.json`；
- 旧规则：图片、主动消息和两版 YAML 规则配置。

## 默认迁移策略

- `test.json`、`test_debug.json`、`test_user.json` 明确排除；
- `12345.json`、`67890.json` 视为疑似占位数据，人工确认前不导入；
- `.bak` 文件只用于差异审计，不作为最新值导入；
- 真实用户资料在新系统的数据模型完成后，先预览、再确认、后写入；
- 旧主动消息时间只作为历史记录，不直接恢复发送任务，避免意外补发。
