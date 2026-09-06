# YCH Bot

> Public template export. Brand stays **YCH**; the creator display name in this
> tree is **维护者**. Example QQ numbers are fictional placeholders
> (`2000000001` owner / `2000000002` bot). Do not treat them as a live account.


> AI 接手本项目时，必须先阅读 `openspec/config.yaml`、`openspec/specs/` 和当前
> `openspec/changes/`，再阅读项目根目录的 `HANDOFF_TO_CURSOR.md`。OpenSpec 是行为
> 与变更的唯一需求基线；交接文档和 `docs/` 提供实施状态、运维细节与历史背景，索引
> 见 `docs/README.md`。

YCH Bot 是基于 NapCat 的本机 QQ 智能机器人重构项目。当前目录是全新工程边界，旧机器人代码不再作为新系统依赖。

## 当前状态

- 已将 NapCat 视为独立的 QQ 协议运行时。
- 正式机器人账号固定为 `2000000002`；其他 NapCat 账号配置默认不由 YCH 启动或处理。
- 已为后端、前端、迁移资料和运行数据建立隔离目录。
- 对话模型与图片生成模型使用两套独立配置。
- 所有密钥只允许写入本机 `.env`，不得提交到版本库。
- 旧聊天资料只作为只读迁移源，未经确认不会自动注入新记忆库。
- 统一消息链路与生产回复运行时 OpenSpec 已完成：可恢复回复任务、来源隔离上下文、受保护模型、
  确定性气泡规划、事务 outbox、送达不明隔离，以及带预览确认的「消息链路」控制面；安全默认和所有网络门仍关闭。
- 主动消息第一轮安全闭环已完成：真实日历任务、主号审批、每用户权限、安静时段、限频、错过策略和默认关闭 worker。
- QQ 空间第一轮任务闭环已完成：草稿/审批/真实日历、安静时段、限频、不确定发布禁止重试、已发布 `tid` 撤销、定向用户隐私；真实发布/删除关闭。
- QQ 空间用户资料读取第一轮已完成：默认关闭、逐用户授权、只产生带过期时间的候选；真实读取关闭。
- 主号汇报第一轮投递桥已完成：去重合并、即时/digest/仅仪表盘、worker 与 outbox 三道关卡；真实私聊关闭。
- 生产 readiness 与受管文件控制面已完成：三个 profile、证据过期/进程/身份/暂停检查，迁移/隐私/导入产物重新校验、引用保护、单次确认与可恢复隔离；不提供永久删除或网页覆盖运行库。
- 零网络生产组合验收使用临时 SQLite/文件、固定时钟/进程和合成启动证据；关闭状态不会构造真实 NapCat、模型或搜索网络客户端。
- 本机生产 `auto` 覆盖机器人 `2000000002` 的全部私聊，以及该机器人所在群在真实 `@` 时。仓库 `.env.example` 默认仍关闭外发。搜索和文生图保持关闭。

## 目录

```text
ych-bot/
├─ backend/                 新后端与测试
├─ frontend/                新 YCH 管理后台（登录 / 总览 / 近况第一刀）
├─ docs/                    架构、决策和实施计划
├─ migration/               旧数据与旧配置的只读迁移区
├─ openspec/                当前行为规格与进行中的变更提案
├─ scripts/                 开发与运维脚本
└─ storage/                 导入文件、备份和运行数据（不入库）
```

工作区边界与后续实施顺序见 `docs/WORKSPACE.md`、`docs/MASTER_PLAN.md` 和 `docs/NEXT_PHASE.md`。主号日常说明和指令在 `docs/operator/`，一次性的阶段记录在 `docs/history/`。

## OpenSpec 工作流

`openspec/specs/` 只描述当前系统应满足的行为，`openspec/changes/` 保存尚未实现或正在实施的变更。任何较大的功能、架构调整、权限变化或真实外部能力启用，都应先完成 `proposal → specs → design → tasks` 并通过严格校验，再修改代码。

```powershell
npx --yes @fission-ai/openspec@1.11.0 validate --all --strict
npx --yes @fission-ai/openspec@1.11.0 list --specs
```

## 本地开发

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run ych-bot
```

观察模式一键启动（先 YCH，再 NapCat 扫码，不打开外发）：

```powershell
.\scripts\start-observe.bat
```

双击 `scripts\start-observe.bat`，或同目录里的「YCH 观察启动」。快捷方式只放在 `scripts\`，不放桌面。请用机器人 `2000000002` 扫码。可选仪表盘仍是 `npm --prefix frontend run dev`，不要把管理员令牌发到聊天。开机连上不等于正式观察验收。

启动后可访问：

- `http://127.0.0.1:8765/health/live`
- `http://127.0.0.1:8765/health/ready`
- `http://127.0.0.1:8765/api/v1/status`

不要在配置 OneBot token 并完成首次联调前启用 NapCat 中的 `YCH_Reverse_WS`。

生产启动、证据刷新和回滚顺序见 `docs/STARTUP_READINESS.md`；备份、隐私归档和导入文件的保留/隔离语义见 `docs/MANAGED_ARTIFACT_RETENTION.md`。主号私聊发送 `/帮助` 可查看命令分区，该命令只读且不会启用任何能力。

仪表盘控制接口还需要单独配置 `YCH_ADMIN_ACCESS_TOKEN`；它不与 OneBot token、模型 API 密钥共用。

管理后台第一刀：

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173`，用长期令牌登录。Vite 代理 `/api` 到后端 `8765`。主号日常说明和指令在仪表盘「操作手册」，仓库副本在 `docs/operator/`。用户页可以给 QQ 写备注。
登录后可在 `/pipeline` 查看回复任务阶段、启用阻塞项、上下文来源和送达证据，并通过一次性预览/确认控制生产回复运行时。`.env.example` 默认仍为 `observe_only`、持久急停和真实网络关闭；本机生产 `auto` 覆盖该机器人全部私聊以及群内真实 `@`。详见 `docs/PRODUCTION_REPLY_RUNTIME.md`。
