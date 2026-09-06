# 模型网关与启用条件

## 独立路由

对话模型和图片模型拥有完全独立的：

- 协议类型；
- API base；
- API key；
- model；
- 路由启用开关；
- 超时；
- 最大重试次数；
- 每日请求预算；
- 对话 token 预算；
- 连续失败熔断；
- gateway 实例。

不能把图片模型配置回退到对话模型，也不能共用用量统计。
识图是第三条独立路由（`YCH_VISION_*`，chat/completions 多模态），不要填进 `YCH_IMAGE_*`。
统计是第四条独立路由（`YCH_STATS_*`），即使模型名与对话相同，凭证和额度也不共用。

2026-09-05 官方人民币价目快照（`siliconflow-2026-09-05`）建议的受控鉴定候选：

| 能力 | 模型 | 说明 |
|---|---|---|
| chat | `deepseek-ai/DeepSeek-V3.2` | 本机权威通过路由；官方人民币单价 |
| chat / stats 候选 | `Qwen/Qwen3.5-35B-A3B` | 官方人民币单价，保守按 ≥128k 高档计；统计权威通过此模型 |
| vision | `zai-org/GLM-4.5V` | 本机权威通过路由 |
| image | `Tongyi-MAI/Z-Image-Turbo` | 本机权威通过路由；官方人民币 `image_cny` ¥0.10/张 |
| image 候选 | `Kwai-Kolors/Kolors` | 官方人民币价目为 0 元/张 |

`Qwen/Qwen2.5-7B-Instruct` 和 `Qwen/Qwen3-VL-8B-Instruct` 只出现在英文页 USD 标价中，没有官方人民币单价，受控实跑确认会被 `cost_unbounded` 拦截。填写模型名、打开网络门或 readiness 通过都不是鉴定通过。

## 当前支持

当前可选协议为 `openai_compatible`，实现位于：

```text
backend/src/ych_bot/infrastructure/models/openai_compatible.py
```

对话端点使用相对路径 `chat/completions`，图片端点使用 `images/generations`。API base
应包含供应商要求的版本前缀，例如末尾的 `/v1`。具体供应商若不兼容此结构，新增独立
adapter，不要在现有适配器里堆叠不可解释的供应商判断。

## 启用闸门

对话网络请求必须同时满足：

1. `YCH_CHAT_API_PROTOCOL=openai_compatible`；
2. `YCH_CHAT_API_BASE` 与 `YCH_CHAT_MODEL` 已配置；
3. `YCH_MODEL_NETWORK_ENABLED=true`；
4. `YCH_CHAT_MODEL_ENABLED=true`；
5. `YCH_SHADOW_INFERENCE_ENABLED=true`。

识图网络请求必须同时满足：

1. `YCH_VISION_API_PROTOCOL=openai_compatible`；
2. `YCH_VISION_API_BASE` 与 `YCH_VISION_MODEL` 已配置；
3. `YCH_MODEL_NETWORK_ENABLED=true`；
4. `YCH_VISION_MODEL_ENABLED=true`；
5. `YCH_SHADOW_INFERENCE_ENABLED=true`。

图片适配器实例化必须同时满足：

1. `YCH_IMAGE_API_PROTOCOL=openai_compatible`；
2. `YCH_IMAGE_API_BASE` 与 `YCH_IMAGE_MODEL` 已配置；
3. `YCH_MODEL_NETWORK_ENABLED=true`；
4. `YCH_IMAGE_MODEL_ENABLED=true`。

图片任务服务已经实现，但只有经过认证的后台 `generate` 动作或主号明确的
`/图片 生成 <任务ID>` 才会调用 gateway。默认所有网络闸门关闭，因此创建任务只会停在
`awaiting_model_config`。

## 持久化保护层

`infrastructure/models/protection.py` 在供应商适配器外增加独立保护层。聊天和图片分别
持有 `ModelCallGuard`，状态写入 SQLite schema v11 的 `model_call_events`：

- 每日请求量按 `YCH_TIMEZONE` 的真实日历日期计算，程序重启不会清零；
- 对话调用在发出前保守预留输入与最大输出 token，成功后记录供应商返回的实际用量；
- 图片只计算独立请求预算，不借用对话 token 配额；
- 连续失败达到阈值后熔断，冷却结束只允许一个探测调用；
- 进程中断遗留的调用在租约到期后记为失败，不会永久卡在运行中；
- 本地预算或熔断拒绝发生在 HTTP 适配器之前，不会访问网络。

默认配置：

| 路由 | 每日请求 | 每日 token | 失败阈值 | 冷却 |
|---|---:|---:|---:|---:|
| chat | 200 | 500,000 | 5 | 300 秒 |
| image | 20 | 不适用 | 3 | 600 秒 |

这些是保护上限，不会自动启用模型网络。后台可通过 `GET /api/v1/models/protection`
查看路由状态、当日用量、拒绝次数和脱敏事件；公开状态接口只提供汇总。

## 错误与日志

- API key 只保存在进程内 HTTP Authorization header，不写数据库和审计。
- HTTP 错误只保留状态码和供应商 request ID，不保留响应正文。
- transport 错误只记录异常类型和有界错误信息。
- 408、429、500、502、503、504 与 transport/timeout 错误按配置进行有界重试。
- 影子运行记录 prompt hash，不保存完整系统 Prompt。
- 主号可用 `/影子 状态`、`/影子 最近`、`/影子 查看 <运行ID>`、`/影子 重跑 <消息ID>`；
  后台另有运行详情和显式重跑 API。重跑只对已存消息调用假/真实网关，结果仍是 `shadow`
  候选，绝不进入 outbox。重跑失败写入 `info` 主号汇报。
- 保护事件不保存 Prompt、回复正文、图片、响应正文或 API key，只保存路由、请求 ID、
  状态、token 数、失败类型和时间。

## 模型资格鉴定

鉴定与影子推理、生产启用是三件不同的事。鉴定只证明「这一条路由的这一次修订」对仓库合成套件的表现，不会打开 `YCH_*_MODEL_ENABLED`、外发、回复 worker 或 QQ。

### 受控实跑手续

1. 仪表盘「模型资格」选择一条能力，只勾选仓库合成夹具。
2. 先「预览受控实跑」。预览给出请求数、token/图片上限、保守费用上界和过期时间；此时不调用供应商。
3. 已认证管理员再「确认付费调用」。确认码一次性，绑定操作者、进程、路由和套件。
4. 确认只创建 `PREPARED` 运行。执行器仍会在每次供应商请求前复核急停、readiness、路由指纹、限额和熔断。
5. 主号 QQ 只能发送 `/模型 状态` 读取脱敏摘要，不能预览或确认付费调用。

### 取消与证据

- 可取消 `prepared` / `running` 运行。取消阻止后续用例领取，不删除已有证据，也不假装进行中的供应商请求未被计费。
- 证据只保留路由、套件、状态、哈希、用量、价格目录修订、稳定失败码和时间。不保存 Prompt、完整响应、密钥、Authorization、原始供应商错误或图片字节。
- `passed` 只表示该修订通过该套件；`stale` 表示路由或套件已变。默认拒绝。

### 回滚

关闭鉴定执行并取消未领取的预览/运行。证据保留。生产门控、outbox、QQ 和空间开关保持关闭。不要对运行中的数据库做降级迁移或仪表盘覆盖恢复。

## 当前仍缺少

- 主/备模型回退策略；
- 流式响应；
- 供应商特有字段，例如某些模型的 `max_completion_tokens`；
- 生产图片任务对任意供应商 URL 的通用安全下载器（鉴定入库只允许无跳转的 `siliconflow.cn` / `siliconflow.com` 后缀主机，或 DNS 标签恰好为 `sf-maas` 的 https PNG）；
- 已批准图片接入回复或 QQ 空间时的第二层策略与审批；
- 把鉴定 `passed` 变成生产回复启用（禁止自动发生）。

仓库 `.env.example` 默认仍全部关闭。本机 gitignored `.env` 里的旧 7B / VL-8B 标识不是官方人民币候选。
不要把 VL 填进 `YCH_IMAGE_*`（那是 `images/generations` 文生图）。
