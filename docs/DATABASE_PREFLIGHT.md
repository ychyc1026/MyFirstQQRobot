# 数据库迁移预检与备份

## 目标

已有运行库不能在没有可验证备份的情况下自动升级。YCH 启动顺序固定为：

1. 只读打开 SQLite；
2. 执行 `PRAGMA quick_check` 和 `PRAGMA foreign_key_check`；
3. 对比当前 schema 与代码目标版本；
4. 需要迁移时先调用 SQLite Online Backup API；
5. 对备份再次执行 `quick_check`，计算 SHA-256 并写入清单；
6. 备份成功后才执行 schema 初始化与迁移；
7. 任一步失败都中止启动，不启动 worker，也不连接 NapCat。

新数据库不需要迁移备份。已经处于最新 schema 的数据库只做只读完整性检查。

## 配置

```env
YCH_MIGRATION_BACKUP_ENABLED=true
```

默认开启。若已有数据库需要迁移而该开关被关闭，程序会拒绝启动；它不是“跳过备份继续迁移”的开关。

备份保存在：

```text
storage/backups/migrations/
```

每次备份包含：

- SQLite 一致性快照 `*.sqlite3`；
- `*.manifest.json` 清单；
- 源/目标 schema 版本；
- 文件字节数和 SHA-256；
- 备份生成时间与完整性结果。

该目录已在 Git 忽略范围内，不得提交。

## 仪表盘与 API

认证接口：

```text
GET /api/v1/system/preflight
```

接口还会返回 `backup_health`：每次读取都会重新核对受管迁移备份的字节数、SHA-256 和 SQLite `quick_check`，而不是仅信任旧 manifest。统一受管文件清单也会采用同一校验结果。保留策略至少保留最近 3 份，且仅将超过 90 天的更早备份列为候选；`automatic_cleanup` 固定为 `false`，不会静默删除。

候选存在时，仪表盘使用 `/api/v1/operations/artifacts/retention/preview` 生成短时、单次、绑定当前进程与策略修订的确认令牌，再通过 `/confirm` 执行。候选集合、文件摘要、引用、主号、进程或策略发生变化，或者令牌过期/已使用，都会拒绝执行。执行结果不是永久删除，而是把数据库文件和 manifest 成对移入 `storage/trash/migration-backups/<batch_id>`；部分移动会逆序回滚，重启会核对中断批次，并写入脱敏审计与仅仪表盘可见的主号报告。旧 `/api/v1/system/backups/cleanup` 入口仍复用同一套安全语义。

返回数据库是否存在、当前/目标 schema、完整性结果、外键违规数、是否需要迁移、备份开关和最近一次迁移备份。不会返回数据库绝对路径、数据正文或密钥。

仪表盘「系统与审计」页显示同一结果。此页面是只读状态，不提供绕过备份或强制迁移按钮。

完整的启动 profile、证据有效期和故障处置见 `docs/STARTUP_READINESS.md`；所有受管产物类型和隔离恢复边界见 `docs/MANAGED_ARTIFACT_RETENTION.md`。

## 恢复边界

迁移备份用于数据库级回滚，与用户隐私导出不同。恢复数据库前必须停止 YCH、NapCat 连接和所有 worker，并保留当前故障库用于调查。当前阶段不提供网页一键覆盖运行库，避免选错备份或覆盖仍在写入的数据库。
