# 主动消息与真实日历调度

更新时间：2026-08-28

## 已实现的安全闭环

主动消息不是按“程序启动后经过 N 分钟”计算。每个任务保存带时区的原计划时间和
规范化 UTC 时间，SQLite 是唯一事实源。程序关闭期间时间仍然流逝；恢复后 worker 会根据
任务自己的错过策略决定跳过、在宽限期内补入发件箱，或重新请求主号审批。

一次消息要进入真实 QQ 发送，需要依次通过：

1. 任务创建并生成统一审批单；
2. 主号 `2000000001` 明确批准；
3. 目标用户的主动消息策略已显式开启；
4. 到达真实日历时间；
5. 安静时段与每用户频率检查通过；
6. `YCH_PROACTIVE_SCHEDULER_ENABLED=true`，调度器才可写入 durable outbox；
7. `YCH_OUTBOUND_ENABLED=true`，发件箱 worker 才可调用 NapCat。

前六项通过也不等于已发送。任务先进入带幂等键 `proactive:<task_id>` 的本地 outbox；
只有 NapCat 返回成功，任务才从 `enqueued` 变成 `sent`。默认两个执行开关均为 `false`。

## 默认策略

新目标用户首次出现时会建立一条默认关闭的策略：

- 主动消息权限：关闭；
- 每日自动找话题：关闭；
- 发送 YCH 当日日记：关闭；
- 时区：`Asia/Shanghai`；
- 安静时段：`22:00–08:00`；
- 安静时段处理：推迟到下一个允许时间；
- 每日上限：3 条；
- 最短间隔：3600 秒；
- 任务最长可计划到 366 天后。

这些是安全默认值，可在认证后台按用户修改。创建任务不会暗中为用户开启权限。
手工定时任务、自动材料/最近上下文和当日日记是三个独立授权：打开普通主动消息权限
不会顺带打开自动找话题或日记。

自动内容由主动 worker 最多每 5 分钟检查一次，并先检查逐用户授权与安静时段，之后才会
读取材料、上下文、搜索或调用模型。材料/上下文每人每天至多一条，仍受每日上限和最短
间隔约束。日记是单独授权、每天至多一次；日记为空不发，跨日未发标记过期。两个自动
授权都只是主号授予的持续策略，不会绕过 `YCH_PROACTIVE_SCHEDULER_ENABLED` 和
`YCH_OUTBOUND_ENABLED` 两道全局执行关卡。

## 错过策略

每个任务独立保存一种策略：

- `skip`：默认；超出 60 秒调度容差后标记 `missed`，不补发，并生成主号警告；
- `send_within_grace`：只在任务设置的宽限秒数内继续，超出后跳过；
- `require_reapproval`：转成 `reapproval_required`，生成新的审批码，主号再次批准后才继续。

安静时段也可按用户改成重新审批。重新审批是一次明确例外：跳过本次“已错过/安静时段”
拦截，但仍受每日上限和最短间隔约束。

## 状态与恢复

主要状态：

```text
pending_approval -> scheduled -> evaluating -> enqueued -> sent
                         |             |
                         |             +-> waiting_quiet_hours
                         |             +-> waiting_rate_limit
                         |             +-> policy_blocked
                         |             +-> reapproval_required
                         |             +-> missed
                         +-> cancelled / rejected / approval_expired
```

- 调度领取有租约，进程异常后过期租约会恢复；
- outbox 使用稳定幂等键，重启不会重复建消息；
- 启用真实外发 worker 时，会在启动时回收上次进程遗留的 `sending` 项；
- 失败发送使用指数退避，默认最多 5 次；
- 已进入 outbox 但尚处于 `pending/failed` 的任务仍可撤销，撤销会原子删除 outbox 项；
- 已被发送 worker 领取或已经发送的任务不可伪装成“撤销成功”。

worker 的运行时暂停已跨进程重启持久化；配置总开关仍是独立关卡。

## 主号 QQ 命令

命令只接受主号与机器人的真实私聊：

```text
/主动 创建 <QQ> <YYYY-MM-DD HH:MM> <内容>
/主动 状态 <任务ID>
/主动 取消 <任务ID>
/主动 用户 <QQ> 开启
/主动 用户 <QQ> 关闭
/主动 用户 <QQ> 状态
/主动 内容 <QQ> 开启
/主动 内容 <QQ> 关闭
/主动 日记 <QQ> 开启
/主动 日记 <QQ> 关闭
/主动 自动 状态
/主动 自动 暂停
/主动 自动 恢复
/外发 状态
/外发 暂停
/外发 恢复
/确认 <审批码>
/拒绝 <审批码>
```

QQ 命令中的无偏移时间按系统默认时区解释。后台 API 要求 `scheduled_for` 明确包含 UTC
偏移，并另外保存展示时区。

## 仪表盘 API

以下接口都要求管理员短时会话：

- `GET /api/v1/proactive/summary`：调度、真实外发和任务聚合；
- `POST /api/v1/proactive/tasks`：创建待审批任务；
- `GET /api/v1/proactive/tasks`：按用户、状态和时间范围读取日历；
- `GET /api/v1/proactive/tasks/{id}`：详情、outbox 状态与事件时间线；
- `POST /api/v1/proactive/tasks/{id}/cancel`：安全撤销；
- `GET/PUT /api/v1/proactive/users/{qq}/policy`：每用户手工任务、自动内容、日记、安静时段和频率；
- `GET/POST /api/v1/proactive/worker...`：状态、单次运行、暂停和恢复；
- `GET/POST /api/v1/outbox/worker...`：真实外发 worker 状态与控制。

列表同时返回 UTC 时间和按任务时区转换的 `*_local` 字段，供以后 YCH 日历界面直接显示。

## 隐私与审计

任务正文、用户策略、投递事件、关联审批和主号报告均进入用户隐私导出与删除影响预览。
执行用户删除时，会先解除外键并删除未发送 outbox，再清理审批、事件、任务和策略。
创建、策略修改、入队、发送、取消和错过均有持久化事件或审计记录。

## 当前边界

- 尚未对真实 NapCat 发送做联调；测试仅使用假时钟与假发送端；
- 当前主动内容只支持文本段，图片/引用需要独立的素材授权与审核；
- 尚未提供“编辑原任务”，改内容或改期应取消并重新创建、重新审批；
- 系统不会从好友关系、聊天内容或资料中推断用户已同意主动触达，必须由开发者显式开启；
- 主号报告已落库、去重并进入 outbox 投递桥；`YCH_OWNER_REPORTS_ENABLED=false` 时不会真实私聊主号。详见 `docs/OWNER_REPORTS.md`。
