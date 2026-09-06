# 图片生成任务与审批

## 当前闭环

图片生成是独立任务，不是聊天回复的副作用：

```text
创建任务 → 显式执行 → 独立图片模型 → 本地产物校验
        → 可选内容审核（默认关闭、假引擎）
        → 待主号审批 + 主号待办报告 → 批准 / 拒绝 / 过期后续期
```

批准只表示产物可供后续流程选择，不会自动发送 QQ、写入 outbox 或发布 QQ 空间。以后
把已批准图片用于回复或空间时，目标动作仍需自己的策略与审批。

SQLite schema v12 + v19 使用：

- `image_generation_tasks`：Prompt、用途、路由、状态、审批和脱敏错误；
- `image_artifacts`：本地相对路径、SHA-256、真实媒体类型、字节数和尺寸；
- `image_artifact_reviews`：默认可关闭的内容审核结论；
- `image_orphan_scans`：未登记文件与缺失产物巡检结果；
- 现有 `approval_requests`：统一审批码；
- 现有 `owner_reports`：向主号生成待办或信息汇报。

## 控制入口

后台 API：

- `POST /api/v1/images/tasks`：创建任务；
- `GET /api/v1/images/tasks`：列表；
- `GET /api/v1/images/tasks/{id}`：任务与产物元数据；
- `POST /api/v1/images/tasks/{id}/generate`：显式执行；
- `POST /api/v1/images/tasks/{id}/renew-approval`：审批过期后续期；
- `GET /api/v1/images/artifacts/{id}/content`：认证后预览本地文件；
- `POST /api/v1/images/orphans/scan`：扫描未登记/缺失文件；
- `GET/POST /api/v1/images/orphans/worker...`：巡检 worker 状态、单次运行、暂停和恢复；
- `GET /api/v1/owner/reports`：主号待办与汇报。

主号私聊命令：

```text
/图片 创建 <Prompt>
/图片 生成 <任务ID>
/图片 状态 <任务ID>
/图片 续期 <任务ID>
/图片 巡检
/图片 巡检 状态
/图片 巡检 暂停
/图片 巡检 恢复
/确认 <审批码>
/拒绝 <审批码>
/汇报 已读 <报告ID>
```

主号命令只接受 QQ `2000000001` 的真实私聊事件；群聊和其他用户文本无效。

## 产物安全边界

- OpenAI-compatible 图片请求默认要求 `response_format=b64_json`；
- 远程 URL 不自动下载，避免 SSRF、内网探测和不受控重定向；
- base64 解码前后均有限额；
- 当前只接受通过文件头、结尾和尺寸检查的 PNG/JPEG；
- 限制单文件字节数、像素总数和单任务产物数；
- 文件保存到 `storage/generated/images/<task-id>/`，整个生成目录被 Git 忽略；
- 每次预览重新检查文件大小和 SHA-256；
- 数据库和报告不保存 base64 正文或远程签名 URL；
- API key、供应商响应正文不会进入任务错误记录。

默认限制：单文件 20 MB、四千万像素、每任务最多 4 个产物。均可通过 `.env` 调整，
但模型网络、图片路由和任务执行仍需显式打开。

## 内容审核

`YCH_IMAGE_CONTENT_REVIEW_ENABLED` 默认关闭；测试只用假引擎，不接真实审核网络。
关闭时生成路径不变，仍进入主号使用审批。打开且假引擎放行时写入 `allow` 记录后继续
审批；拦截则状态为 `review_blocked`，不创建使用审批，只发 `info` 汇报。

## 审批续期

过期审批会把任务标为 `approval_expired`。主号或后台可显式续期：旧码作废，重新签发
30 分钟确认码，并再发一条 `action_required` 待办。未过期的待审批任务不能续期。

## 孤儿巡检

`YCH_IMAGE_ORPHAN_SCAN_ENABLED` 默认关闭。打开后可扫描 `storage/generated/images/`
中未登记文件，以及数据库记录但磁盘缺失的产物；发现异常时发 `info` 汇报，不自动删除。
默认关闭的巡检 worker 支持单次执行、暂停和跨重启保持暂停。

`GET /api/v1/privacy/status` 暴露 `image_content_review_enabled` 与
`image_orphan_scan_enabled`。

## 当前限制

- 尚无真实内容审核供应商；当前只有假引擎；
- 不接受供应商只返回 URL 的结果，拿到具体 API 后再设计域名白名单与安全下载器；
- 暂未支持 GIF/WebP；
- 暂无任务取消；巡检不会自动删除孤儿文件；
- 已批准产物尚未接入聊天回复或 QQ 空间发布。
