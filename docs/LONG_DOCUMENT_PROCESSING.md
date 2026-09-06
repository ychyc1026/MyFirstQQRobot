# 长资料解析、炼化与证据链

## 两种用途严格分离

每次导入在创建时只能选择一种用途：

1. `user_understanding`：提炼关于该用户的事实、偏好和沟通线索；
2. `persona_design`：设计机器人面对该用户时的互动风格。

用户理解只是低优先级、不可信参考数据，不能成为机器人指令。资料派生人格只能影响目标
用户私聊中的互动风格，不能进入群聊，也不能修改 YCH、维护者、主号 `2000000001` 或
机器人 `2000000002` 等核心身份。

## 文件解析

当前支持：

- UTF-8 TXT、Markdown；QQ 电脑版文本导出会识别为 `chat_txt`（页眉 + 时间戳发言块），
  普通笔记不会被误判；
- JSON、CSV，并识别常见聊天记录字段后规范成时间/发送者/正文；支持 OneBot/NapCat
  的 `raw_message`、嵌套 `sender.nickname` 和 Unix 时间（Asia/Shanghai）；聊天导出走流式解析，
  受记录数和展开体积上限约束，非聊天大 JSON 不会整文件急切加载；
- HTML，只提取可见文字并忽略 script/style/noscript；带 `message`/`msg` 结构的 QQ HTML
  导出会规范成 `chat_html`；
- DOCX，提取段落、表格、页眉和页脚；
- 带文本层的 PDF，保留页码标记；扫描页仅在 `YCH_DOCUMENT_OCR_ENABLED=true` 且注入
  OCR 引擎时识别，页标记为 `[PDF page N][OCR]`，格式为 `pdf_ocr`。

安全限制：原文件大小、PDF 页数、DOCX 条目数和解压体积均有界；拒绝加密 PDF/DOCX、
可疑 DOCX 压缩比、二进制伪装文本和无可读文字的文件。OCR 默认关闭，测试只用假引擎，
不接入 Tesseract 或网络；纯文本层 PDF 即使打开开关也不会调用引擎。扫描 PDF 在 OCR
关闭时以明确错误拒绝，不会假装已识别。

原文件写入 `storage/imports/knowledge/`，被 Git 忽略；数据库保存文件 SHA-256、文本块
SHA-256、顺序和格式元数据。隐私删除会先生成备份，再清理数据库记录与对应本地导入文件。

## 可恢复处理

SQLite schema v13 使用：

- `knowledge_job_checkpoints`：处理状态、租约、尝试次数和脱敏失败类型；
- `knowledge_chunk_analyses`：每个文本块的结构化结果、块号和 provider request ID；
- `persona_profile_evidence`：批准后的人格证据链；
- 原有 `user_understanding_profiles.evidence_json`：批准后的用户理解证据链。

处理步骤：

```text
领取租约
  → 只处理尚未完成的块
  → 每块结果立即提交检查点
  → 分层归并全部块结果
  → 统一审批 + 主号 action_required 报告
  → 批准后才版本化写入画像或单用户人格
```

若模型或程序中途失败，任务回到可重试状态；下一次处理复用已经完成的块，不重算。若原
进程失联，租约过期后其他执行器可以接管。默认关闭的常驻 worker 已实现：一次只处理一
个任务，失败使用指数退避，并在达到配置的最大尝试次数后停止自动重试；人工显式处理仍
可用于诊断或恢复。

## 证据约束

- 块级模型输出必须是严格 JSON；
- 每条短引用必须能在对应原文块中找到，编造引用会使本次尝试失败且不写检查点；
- 最终汇总只能复用已验证的 `chunk_index + quote` 组合；
- 每块观察数、引用长度、结果体积和模型输出体积均有限制；
- 人格草稿在块级与汇总级都检查核心身份覆盖；
- 处理完成仍只是预览，不会自动写入人格、画像或记忆；
- 目前不会根据资料自动创建永久记忆。

## 控制入口

后台：

- `POST /api/v1/knowledge/documents`：上传并创建任务；
- `GET /api/v1/knowledge/jobs`：查看块进度、尝试次数、审批状态；
- `POST /api/v1/knowledge/jobs/{id}/process`：显式处理或续跑；
- `POST /api/v1/knowledge/jobs/{id}/approve`：批准预览。
- `GET /api/v1/knowledge/worker`：查看运行、退避、尝试与最近结果；
- `POST /api/v1/knowledge/worker/run-once|pause|resume`：单次执行、暂停和恢复。

主号私聊：

```text
/资料 处理 <任务ID>
/资料 状态 <任务ID>
/资料 自动 状态
/资料 自动 暂停
/资料 自动 恢复
/确认 <审批码>
/拒绝 <审批码>
```

人工真实处理必须同时启用对话模型路由、全局模型网络开关和
`YCH_KNOWLEDGE_PROCESSING_ENABLED=true`。常驻自动处理还必须额外设置
`YCH_KNOWLEDGE_WORKER_ENABLED=true`。默认全部关闭，上传任务停在
`awaiting_model_config`，不会触发模型网络。worker 的轮询间隔、最大尝试次数和退避基数
分别通过独立配置控制；运行时暂停写入 `worker_runtime_state`，进程重启后仍保持暂停，
直到主号或仪表盘显式恢复。`.env` 里的 worker 开关仍是独立关卡。扫描 OCR 另由
`YCH_DOCUMENT_OCR_ENABLED` 控制，默认关闭；`GET /api/v1/privacy/status` 暴露
`document_ocr_enabled`。

## 尚待完善

- 真实 OCR 引擎与 `.mht` 等少见导出封装；
- 原文件二进制纳入可恢复的隐私删除备份包；
- 审批过期后的重新发起功能；
- 多进程 leader 选举；
- 真实供应商 API 到位后的影子联调和质量评测。
