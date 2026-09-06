# 后端测试布局

测试按「测什么」分层。目录名同时是 pytest marker，由 `conftest.py` 自动打上，不需要在文件里手写 `@pytest.mark`。

| 目录 | 范围 |
| --- | --- |
| `domain/` | 纯领域逻辑。不碰数据库、不起 HTTP、不接适配器 |
| `adapters/` | 外部客户端与启动脚本，只对假传输层 |
| `repository/` | SQLite 表结构与持久化行为 |
| `services/` | 应用服务，跑在临时数据库上 |
| `dashboard/` | 已认证的操作者 HTTP 面 |
| `qualification/` | 模型资格认定这条纵向链路 |
| `acceptance/` | 跨层的生产闸门与端到端流程 |

## 只跑一层

```powershell
.\.venv\Scripts\python.exe -m pytest -m domain
.\.venv\Scripts\python.exe -m pytest backend/tests/dashboard
```

## 共享工具

`_support/` 是唯一的共享工具位置。**测试文件之间不要互相 import**，否则改名或移动会连带弄坏别的测试。

| 模块 | 提供 |
| --- | --- |
| `_support/paths.py` | `PROJECT_ROOT`、`SOURCE_ROOT`、`FIXTURES_ROOT` 等锚点，不随文件深度变化 |
| `_support/owner.py` | 固定主号/机器人 QQ、私聊消息构造、指令构造、好友列表假传输 |
| `_support/readiness.py` | 就绪边界假件。`ALLOW_READINESS` 是单例，`conftest.py` 会在每个用例前后清空它的调用记录 |
| `_support/qualification_runs.py` | 假模式资格运行的准备逻辑 |

## 约束

- 默认用临时数据库和假传输层。真实外部客户端被意外构造时，测试必须失败。
- 新测试放进对应层的目录；不确定就按「最外层依赖」归类。
- marker 是 `--strict-markers`，拼错会直接报错。
