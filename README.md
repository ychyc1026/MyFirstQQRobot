# YCH Bot

基于 NapCat / OneBot 的本机 QQ 助手（公开模板）。

- 品牌：**YCH**
- 公开树里的创造者显示名：**维护者**
- 示例 QQ（占位，不是真实账号）：主号 `2000000001`，机器人 `2000000002`

这是脱敏后的代码模板，不是任何人的本机生产环境。密钥、聊天记录、运行库都不会进仓库。

## 本地运行

```powershell
uv sync --extra dev
Copy-Item .env.example .env
# 填写你自己的 QQ、令牌和模型配置；默认外发与模型网络关闭
uv run ych-bot
```

前端：

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

观察模式启动（不打开外发）：

```powershell
.\scripts\start-observe.bat
```

## 仓库里有什么

- `backend/`：Python 后端与分层测试
- `frontend/`：操作仪表盘
- `openspec/specs/`：当前行为规格（变更归档只留在私有仓）
- `scripts/`：启动与提交闸门辅助
- `.env.example`：可提交的空配置样例

## 安全默认

外发 QQ、模型网络、空间发布、隐私任务、搜索、文生图、OCR 默认关闭。  
就绪检查通过也不等于已经授权真实效果。

## 私有内容

完整交接文档、阶段历史、OpenSpec 变更归档、Cursor 规则等
只保留在维护者本机私有仓，不推送到此公开仓库。
