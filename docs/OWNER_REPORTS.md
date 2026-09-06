# 主号汇报去重与 durable outbox 投递

更新时间：2026-08-13

## 已实现的安全闭环

主号汇报不是把所有日志私聊给 `2000000001`。落库、去重合并、投递策略、汇报 worker、
`YCH_OWNER_REPORTS_ENABLED` 和真实 outbox 发送是相互独立的关卡。默认全部关闭，不会向
主号发 QQ。

一条汇报要真正进入主号私聊，需要依次通过：

1. 业务写入 `owner_reports`，并创建 `owner_report_runtime` / `owner_report_events`；
2. 未确认的相同 `dedupe_key` 在去重窗口内合并，不重复入队；
3. 到达即时或本地 digest 时间；
4. `YCH_OWNER_REPORT_WORKER_ENABLED=true`，worker 才领取到期汇报；
5. `YCH_OWNER_REPORTS_ENABLED=true`，才允许写入 durable outbox；
6. `YCH_OUTBOUND_ENABLED=true`，通用 outbox worker 才真实发送。

`dashboard_only` 只落库，永不入 outbox。worker 关闭或投递路由关闭时，汇报状态不变，
只记 `disabled`。

## 去重与摘要

默认 `dedupe_key` 为 `category:related_type:related_id:severity`。窗口默认 1800 秒。
未 `acknowledged` 的相同键在窗口内合并：更新正文/标题、增加 `occurrence_count`，
保留同一汇报 ID。

Digest 不是把多条汇报合成一条 QQ 消息，而是把单条 `digest` 策略汇报延迟到下一个
本地 `09:00`（Asia/Shanghai）后再入队。

默认策略：

- `action_required` / `critical`：立即私聊；
- `warning` / `info`：等到 digest；
- 后台可改为 `dashboard_only`。

## 入队与失败

Worker 对每条汇报只入队一次，幂等键为 `owner_report:{id}`。outbox 可用自己的重试；
尝试耗尽后标记 `failed`，不再重新入队。发送失败只落库并在仪表盘可见，不能无限刷屏。

已入队后，worker 会核对 outbox `sent` / `failed` 并回写汇报状态。入 outbox 时
`available_at` 使用真实当前时间，避免假时钟测试或计划时间晚于真实时钟时无法领取。

## 状态机

```text
pending / waiting_digest
  └─> queued ──outbox──> delivered
        │
        ├─> failed（不再自动重入队）
        └─> suppressed（dashboard_only）
```

已读 `/汇报 已读 <ID>` 只改变 `owner_reports.status`，已入队的 outbox 不会因此撤回。

## 主号 QQ 命令

命令只接受主号与机器人的真实私聊：

```text
/汇报 队列
/汇报 状态 <汇报ID>
/汇报 已读 <汇报ID>
/汇报 自动 状态
/汇报 自动 暂停
/汇报 自动 恢复
```

## 仪表盘 API

以下接口都要求管理员短时会话：

- `GET /api/v1/owner/reports/summary`：worker、投递路由和按状态汇总；
- `GET/PUT /api/v1/owner/reports/policy`：digest 时间、去重窗口和各级投递策略；
- `GET/POST /api/v1/owner/reports/worker...`：状态、单次运行、暂停和恢复；
- `GET /api/v1/owner/reports`：列表；
- `GET /api/v1/owner/reports/{id}`：详情与事件时间线。

公开 `/api/v1/status` 只显示 worker 是否启用和投递路由是否打开。摘要接口默认关闭时
不暴露待办正文。

## 隐私与审计

用户导出包含与该用户相关的主号报告及其 `owner_report_runtime` / `owner_report_events`。
关联范围与既有报告一致：知识任务、主动任务，以及 `target_uins` 含该用户的定向空间帖。
删除影响预览计入这些记录；硬删除 `owner_reports` 后运行时与事件级联删除。
系统级 `owner_report_delivery_policy` 不属于单用户数据。

## 当前边界

- 影子推理显式重跑失败会写入 `info` / `shadow_inference` 汇报，默认走 digest，不会自动发 QQ；
- 尚未对真实 NapCat / 主号私聊做联调；测试仅使用假时钟与假发送端；
- digest 是按条延迟，不是合并多条汇报正文；
- worker 运行时暂停已跨进程重启持久化；配置总开关仍是独立关卡；
- 真实运行库仍可能停留在 schema 1–4；下次启动应用才会创建 5–19 号表。
