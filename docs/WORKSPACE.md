# 工作区边界

## 保留区

工作区根目录中的 NapCat Windows 运行文件、`win64/`、`napcat/native/`、`napcat/node_modules/`、`napcat/static/`、`napcat/worker/`、NapCat 登录配置和内置插件继续保留。它们负责 QQ 登录、事件接收与 OneBot 能力，不承载 YCH 的业务逻辑。

## 新工程区

`ych-bot/` 是唯一的新业务工程。后端不得再把业务源码写进 `napcat/`，前端不得直接读写 NapCat 配置或用户资料。

后端分层约束：

- `api`：管理后台 API 与鉴权边界；
- `application`：消息处理、资料炼化、主动消息等用例编排；
- `domain`：用户、会话、人格、记忆、任务等核心模型；
- `infrastructure`：NapCat、模型、数据库、文件与 QQ 空间适配器；
- `workers`：可恢复的后台任务与定时发送执行器。

## 数据区

- `migration/legacy-data/`：旧资料，只读、仅迁移使用；
- `migration/legacy-config/`：可供分析的旧规则，不直接运行；
- `storage/imports/`：后台上传的用户资料或人格资料；
- `storage/backups/migrations/`：数据库迁移前备份，是唯一受管的备份根；
- `storage/runtime/`：新系统数据库、任务状态和派生文件。

前三类隐私数据和运行数据默认不进入 Git。

`storage/backups/` 下只放受管的 `migrations/`。NapCat 升级回滚这类不受 YCH 保留策略管理的大体积备份必须放在仓库外，位置见 `NAPCAT_UPDATE.md`。

## 测试与提交闸门

- 后端测试按层分目录，目录名即 pytest marker，说明见 `../backend/tests/README.md`；
- 测试之间不得互相 import，共享工具只放 `backend/tests/_support/`；
- 提交前闸门在 `../.githooks/pre-commit`，本机启用一次：`git config core.hooksPath .githooks`。它会跑 ruff 并扫描敏感路径与密钥形状，可用 `scripts/precommit_guard.py --all` 手动全量自检。

## 配置区

仓库只保存 `.env.example`。真实密钥稍后由开发者提供并写入 `.env`；对话模型、图片生成模型必须是两套独立配置和客户端。

正式机器人 QQ 为 `2000000002`。`2000000001` 仅保留原有 NapCat 配置，不作为 YCH 身份、不自动启动，也不接入消息处理链路。

机器人账号原有的 `Webhook_To_Main` 客户端属于已删除的旧 Python 主程序，现已禁用。本机 HTTP API 暂时保留在 `127.0.0.1:3000`；统一消息链路确定后再启用带 token 的正式事件通道。
