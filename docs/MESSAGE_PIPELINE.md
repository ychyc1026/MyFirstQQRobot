# 统一消息链路（阶段一）

## 目标

NapCat 只负责 QQ/OneBot 协议；YCH 后端负责事件落库、路由、人格、记忆、模型与发送。两者不共享业务源码，也不让前端直接调用 NapCat。

```text
QQ / NapCat
  ├─ 反向 WebSocket事件 ──> YCH 事件入口
  │                           ├─ 机器人身份校验
  │                           ├─ 自身消息过滤
  │                           ├─ OneBot 消息段标准化
  │                           ├─ 幂等去重
  │                           └─ SQLite 事务落库
  │
  └─ OneBot HTTP API <────── YCH 持久化出站队列
                              （默认关闭）
```

## 已确定的约束

- 正式机器人账号固定为 `2000000002`；其他 `self_id` 事件直接忽略；
- QQ 号、群号、消息 ID 全部按字符串保存；
- 私聊会话键为 `private:<用户QQ>`，群聊会话键为 `group:<群号>`；
- 文本、图片、引用、语音、文件等消息段保留结构，不提前拼成不可逆字符串；
- `event_key` 在数据库中唯一，同一事件重复上报不会重复写入；
- 用户初始好友状态为 `unknown`，旧聊天读取策略为 `none`；
- 消息写入和审计记录位于同一事务；
- 发送任务先进入 outbox，只有显式打开 `YCH_OUTBOUND_ENABLED` 才允许执行；
- 默认运行模式为 `observe_only` 且持久急停；只有显式配置、实时保护门和主人控制共同允许时才会升级。

## 连接方式

YCH 在 `127.0.0.1:8765/onebot/v11/ws` 接收 NapCat 反向 WebSocket。NapCat HTTP API 暂保留在 `127.0.0.1:3000`，只用于出站动作。

正式启用前必须为两端配置相同的 OneBot token；token 为空时 YCH 拒绝反向 WebSocket。管理页面与消息通道不能共用模型 API 密钥。

观察模式联调：YCH 进程先启动并监听该路径，NapCat 再反向连入。本机可用 `scripts/start-observe.bat` 按该顺序启动。连接成功且 `self_id` 为 `2000000002` 只表示可以收事件。默认 `YCH_OUTBOUND_ENABLED` 与回复 worker 关闭，因此不会发 QQ。主号点名的 owner-private shadow 可打开回复 worker 和对话路由，仍必须保持外发关闭。一键脚本不会把外发打开。

## SQLite 表

- `inbound_events`：原始事件与幂等键；
- `users`：用户身份、好友状态和历史读取策略；
- `conversations`：私聊/群聊会话；
- `messages`：标准化消息、原消息段及来源事件；
- `outbox`：可恢复、可防重的发送队列；
- `audit_log`：重要写入与操作记录；
- `schema_migrations`：数据库结构版本。

## OpenSpec 重构进度（2026-08-30）

`rebuild-message-pipeline` 已完成 1.1–4.6，并已进入归档验收：

- 邻近入站消息先进入可恢复的 burst settle 窗口；
- 同一会话只允许一个工作进程持有回复任务租约，不同会话可独立推进；
- 模型调用前先生成 typed context manifest，分别记录核心身份、授权、触发消息、手工人格、
  已审批资料派生、记忆和获准历史的 scope、subject QQ、记录 ID 与策略原因；
- 指定范围历史只从本地 `messages` 查询，当前触发消息不会重复进入历史；
- 独立 fail-closed 隔离验证器会拒绝跨用户 subject、群聊私人来源、错误会话键和重复 section；
- 固定预算优先级为核心身份、授权、当前触发消息、人格、资料、记忆、历史；前三层必须完整，
  其余层按顺序截断或省略，并记录 `truncated`、`omitted_chars`、总预算和实际使用量；
- 多用户私聊与群聊可并发组装且来源 ID 不交叉；历史查询截止到本任务最早触发消息之前，
  组装期间后到的消息只允许进入后继任务；
- 租约不匹配、已过期或组装中被接管时，上下文 manifest 不会落库。
- 默认暂停的 fake-first Reply Worker 通过现有模型预算、调用租约和熔断保护层使用注入的假模型；
  成功结果只保存为 `reply_fake` / `not_for_delivery` 候选并停在 `planning_reply`，失败只记录脱敏分类。
- 回复规划使用 run 与候选文本稳定决定用户配置范围内的气泡数，保持完整文本，每个气泡获得
  `{run_id}:bubble:{sequence}` 幂等键；容量不足时整项失败，计划只写回 run 并停在 `creating_outbox`。
- 已验证计划与全部气泡在一个 SQLite 事务中写入 outbox，并同时将 run 推进到
  `awaiting_delivery`；outbox ID 由气泡幂等键稳定生成，重试只恢复原记录，不会重复插入。
- 任一气泡的目标、消息段或稳定 ID 与既有幂等记录冲突时整笔事务回滚，不留下部分回复；
  多气泡使用稳定时间序列保持领取顺序。
- 回复 outbox 的每次 `queued`、`sending`、`delivered`、`rejected` 和 `delivery_unknown`
  都会记录可审计证据；只有上一气泡确认送达，下一气泡才可领取。
- OneBot 明确返回失败时记为 `delivery_rejected`；超时、断连、无效响应或进程在发送中断时
  记为 `delivery_unknown`。两者均停止该 run 并取消未发送气泡，不进入自动重试。
- 模型超时和空白/超容量等无效模型结果会在创建 outbox 前失败，并只保存脱敏错误分类。
- 管理端新增只读接口 `/api/v1/reply-runs`、`/api/v1/reply-runs/{run_id}` 和
  `/api/v1/reply-pipeline/readiness`，全部要求短时管理员会话。
- 详情仅显示触发文本预览、上下文来源/决策、模型统计、气泡元数据和送达结果；不会返回
  完整上下文、模型候选正文、回复正文、prompt hash、原始 OneBot 数据或租约 token。
- readiness 分别报告安全观察与正式启用状态；回复 worker 已接入应用生命周期，但默认配置关闭且真实网络
  开关关闭，当前会明确返回 `observe_only` 和结构化阻塞项。
- 仪表盘新增「消息链路」页，集中展示 readiness 阻塞项、任务阶段筛选、失败/抑制原因、
  脱敏触发预览、上下文 provenance、模型用量和逐气泡送达轨迹；页面只读，不提供绕过开关。
- 合成 OneBot 私聊 burst 与群聊混合消息段已贯通假模型、回复规划、事务 outbox 和假传输；
  群聊验证私人资料/记忆/历史均被拒绝，送达不明验证为终止且不盲重发。
- `assembling_context` 阶段的过期租约现在可在重启或电脑休眠后安全接管；旧 token 立即失效。
  重放事件、并发领取、跨会话独立推进和 outbox 持久暂停均有回归测试。

## 运维验收

- 页面：`/pipeline`；需要管理员短时会话；证据区只读，运行时变更必须一次性预览后明确确认。
- 正式启用前必须让 readiness 的全部结构化阻塞项清零；不能以页面能打开代替启用验收。
- `delivery_unknown` 必须人工核对，禁止自动重发；过期租约只自动恢复可安全重做的上下文组装阶段。
- 回归命令：Ruff 全量检查、完整 Pytest、前端 `npm run build` 和隔离浏览器视觉检查。
- 2026-08-30 验收结果：`275 passed`，前端构建通过，桌面与 390px 窄屏浏览器检查无横向溢出或控制台错误；
  所有真实入站、模型网络、外发、空间、主动消息与汇报开关保持关闭。

生产 Reply Worker 已接入应用生命周期，但配置默认关闭、持久急停开启且模式上限为
`observe_only`。分阶段启用、主人命令和回滚见 `PRODUCTION_REPLY_RUNTIME.md`。
