# YCH 管理后台

YCH 的本机管理控制台，采用接近 shadcn-admin 的中性后台风格。旧暖纸底、焦橙大卡、装饰渐变和五色主题已经移除。

## 启动

在项目根目录 `.env` 配置 `YCH_ADMIN_ACCESS_TOKEN`，先启动后端：

```powershell
cd D:\My_code\NapCat.Shell.Windows.Node\ych-bot
uv run ych-bot
```

再启动前端：

```powershell
cd D:\My_code\NapCat.Shell.Windows.Node\ych-bot\frontend
npm install
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 默认代理到 `http://127.0.0.1:8765`；隔离测试可设置 `YCH_DASHBOARD_API_PROXY_TARGET`。

## 验证

```powershell
npm test
npm run build
npm audit
```

组件使用 Radix/shadcn 的 MIT 风格实现，源代码位于 `src/components/ui`。路由继续按需加载；命令搜索仅包含安全的页面名称与功能关键词。

完整规则见 `VISUAL_LANGUAGE.md`、`INFORMATION_ARCHITECTURE.md` 和 `OPS_DASHBOARD.md`。
