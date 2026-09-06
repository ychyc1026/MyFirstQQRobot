# QQ 空间任务与发布闭环

更新时间：2026-08-13

## 已实现的安全闭环

QQ 空间发布不是“主号说一句就直接发说说”。草稿、待审批发布、真实日历时间、安静时段、
频率限制、worker 总开关和 NapCat 发布总开关是相互独立的关卡。真实发布默认全部关闭。

一次说说要真正调用 NapCat，需要依次通过：

1. 创建草稿或发布请求；发布请求立即生成统一审批单和主号待办；
2. 主号 `2000000001` 明确 `/确认` 或后台批准；
3. 到达真实日历时间；
4. 安静时段与全站频率检查通过；
5. `YCH_QZONE_WORKER_ENABLED=true`，worker 才领取到期任务；
6. `YCH_QZONE_PUBLISH_ENABLED=true`，才允许发起 `send_qzone_msg` 或 `delete_qzone_msg`。

草稿永远不会生成审批，也不会进入发布 worker。批准只把任务变成可调度，不等于已发布。

## 可见范围与定向用户

NapCat `ugc_right` 使用：

- `1` 所有人；
- `4` 好友；
- `16` 指定好友，必须提供 `target_uins`；
- `64` 仅自己；
- `128` 排除指定好友，必须提供 `target_uins`。

`target_uins` 只允许 5–15 位数字 QQ，最多 50 个，自动去重。非指定/排除可见范围若携带
目标 QQ 会直接拒绝，避免静默忽略。

## 错过与失败

默认错过策略是 `require_reapproval`：超出调度容差后不补发，生成新的审批码，主号再次
确认后才继续。安静时段默认推迟到下一个允许时间。

发布前评估失败（安静时段、限频、日上限、worker/发布开关关闭）只记为普通失败，并明确
记录“未发起网络请求”。

一旦任务进入 `publishing` 后出现超时、异常或进程中断，状态变为 `delivery_uncertain`：
禁止自动重试，向主号生成 `critical` 待办。空间发布存在“请求超时但实际可能已发出”的
重复风险，因此第一版绝不盲目重发。

已发布撤销同样需要主号确认。只有 `published` 且保存了 `tid` 的帖才能申请删除；草稿、
未发布和 `delivery_uncertain`（无 tid）一律拒绝。批准后 worker 调用 `delete_qzone_msg`。
进入 `deleting` 后超时或进程中断变为 `delete_uncertain`，禁止自动重试。拒绝/过期/取消
未批准的撤销申请只把帖恢复为 `published`，不会改平台内容。

## 状态机

```text
draft
pending_approval -> scheduled/approved -> evaluating -> publishing -> published
                         |                     |                        |
                         |                     +-> waiting_quiet_hours  |
                         |                     +-> waiting_rate_limit   |
                         |                     +-> reapproval_required  |
                         |                     +-> missed               |
                         |                     +-> delivery_uncertain   |
                         +-> cancelled / rejected / approval_expired    |
                                                                        v
                         pending_delete_approval -> delete_approved -> deleting
                                                         |                |
                                                         |                +-> deleted
                                                         |                +-> delete_uncertain
                                                         +-> 拒绝/过期/取消回到 published
```

- 领取有租约，过期租约会恢复；若恢复时发现仍卡在 `publishing`/`deleting`，直接标为结果不确定；
- 计划时间是带时区的真实日历时间，不按程序启动后多久计算；
- 成功发布会保存 NapCat `tid`；撤销只按 `tid` 调用 `delete_qzone_msg`，本地仍保留记录；
- 撤销不受安静时段和日上限限制；worker 与 `YCH_QZONE_PUBLISH_ENABLED` 仍是独立关卡；
- worker 运行时暂停已跨进程重启持久化；配置总开关仍是独立关卡。

## 主号 QQ 命令

命令只接受主号与机器人的真实私聊：

```text
/空间 队列
/空间 草稿 <内容>
/空间 发布 <内容>
/空间 定时 <YYYY-MM-DD HH:MM> <内容>
/空间 取消 <任务ID>
/空间 撤销 <任务ID>
/空间 暂停
/空间 恢复
/确认 <审批码>
/拒绝 <审批码>
```

`/空间 发布` 不会绕过审批。QQ 命令中的无偏移时间按系统默认时区解释。

## 仪表盘 API

以下接口都要求管理员短时会话：

- `GET /api/v1/qzone/summary`：worker、聚合状态和“禁止自动网络重试”标记；
- `POST /api/v1/qzone/drafts`：只存草稿；
- `POST /api/v1/qzone/posts`：创建待审批发布，可带可见范围和 `target_uins`；
- `GET /api/v1/qzone/posts`：按状态和时间范围读取日历；
- `GET /api/v1/qzone/posts/{id}`：详情与事件时间线；
- `POST /api/v1/qzone/posts/{id}/cancel`：安全取消未发布任务，或撤回未批准的撤销申请；
- `POST /api/v1/qzone/posts/{id}/revoke`：对已发布且有 `tid` 的帖申请删除，仍需主号确认；
- `GET/PUT /api/v1/qzone/policy`：安静时段、日上限和最短间隔；
- `GET/POST /api/v1/qzone/worker...`：状态、单次运行、暂停和恢复。

公开 `/api/v1/status` 只显示 worker 是否启用和发布路由是否打开，不暴露密钥。

## 隐私与审计

只有 `target_uins` 中包含该用户的空间帖才属于该用户的隐私范围。好友可见或公开说说
不会因为正文碰巧出现 QQ 号而被导出或删除。

用户导出包含：定向帖正文、运行时、事件、关联审批/载荷、主号报告和空间审计。
删除影响预览会计入这些记录；硬删除会先解除审批外键，再删除报告、审计和帖子。
运行时与事件随帖子级联删除。系统级 `qzone_schedule_policy` 不属于单用户数据。

创建、审批、错过、发布成功、撤销申请/成功和发布/删除结果不确定均有持久化事件或审计记录。

## 当前边界

- 尚未对真实 NapCat / QQ 空间做联调；测试仅使用假时钟与假 Qzone 客户端；
- 第一版不自动下载远程图片 URL，也不绑定图片任务产物；
- 已发布撤销 / `tid` 删除第一轮已完成；尚未对真实 NapCat 删除做联调；
- 主号汇报去重/摘要与 durable outbox 投递桥已完成，详见 `docs/OWNER_REPORTS.md`；
- 用户 QQ 空间资料读取第一轮已完成：默认关闭、逐用户授权、只产生带过期时间的候选；详见 `docs/QZONE_PROFILE.md`。真实读取仍关闭。
