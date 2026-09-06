# YCH 仪表盘视觉语言

更新时间：2026-08-31

界面以 `shadcn-admin` 的后台密度、分组导航和中性组件为参考，但不复制其品牌、示例数据或业务文案。品牌始终为 YCH。

## 设计系统

- 技术栈：React、Vite、TypeScript、Tailwind CSS、Radix UI、lucide-react、Recharts。
- 主题：只保留浅色和深色；存储键为 `ych.color-mode`，旧五色主题键会被清除。
- 色彩：组件只使用 `background`、`card`、`primary`、`secondary`、`muted`、`accent`、`destructive` 等语义 token。
- 字体：Inter 优先，中文回退到系统无衬线字体；页标题 24–30px，卡片标题 14–16px。
- 形状：常规圆角 8px、1px 边框、轻阴影；不使用装饰性渐变、暖纸底、彩色大卡或超大圆角。
- 密度：256px 桌面侧栏、56px 粘性顶栏、最大内容宽 1600px；数据表和表单保持紧凑。
- 状态：不以 0 冒充未知；加载、空、失败、关闭、断连和等待审批都有明确文案。

## 页面与组件映射

| 场景 | 组件 |
|---|---|
| 汇总数字 | Card + 指标行 + Progress |
| 服务端记录 | Table/DataTable + 筛选 + 分页 |
| 详情检查 | Sheet 或双栏 master-detail |
| 普通编辑 | Dialog / 表单分区 |
| 删除、发布、撤销 | AlertDialog + 服务端预览/确认 |
| 状态切换 | Switch，禁用原因紧邻控件 |
| 调度 | Calendar/日期控件 + 队列视图 |
| 事件过程 | Timeline + StatusBadge |
| 无数据 | EmptyState，不填充假数据 |

## 可访问性与安全

- 所有图标按钮必须有可访问名称；键盘焦点使用 `ring` token。
- `Ctrl/Cmd+K` 只搜索静态路由和安全关键词，不索引消息正文、令牌或私有文档。
- 弹窗支持 Escape 关闭和焦点约束；高风险操作继续由后端审批状态机决定。
- 深浅主题下使用成对的 foreground token，禁止固定白字造成对比度失效。
