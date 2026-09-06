# QQ 空间用户资料读取

更新时间：2026-08-13

读取其他用户的 QQ 空间资料只用于初始化画像候选，不是永久记忆或人格来源。
真实 NapCat / QQ 空间访问默认关闭；当前测试只使用假客户端。

## 安全关卡

必须同时满足：

1. 全局开关 `YCH_QZONE_PROFILE_COLLECTION_ENABLED=true`；
2. 目标用户未被冻结；
3. 该用户有显式授权：`one_time` 或 `ttl`。默认 `deny`。

好友关系（包括 `existing_friend`）不会自动授权。一次授权在读取前原子消费；限期授权到期后立即拒绝。

## 写入规则

允许的预览只做三件事：

- 保存带 `purpose`、来源、抓取时间和过期时间的快照；
- 把昵称、签名和近期说说写成 `qzone_derived` 候选记忆，状态只能是 `candidate`，并带 `expires_at`；
- 把允许/拒绝写入 `data_access_log`，数据类别为 `qzone_content`。

禁止：

- 直接写入 `active` 记忆；
- 直接写入人格或用户理解；
- 把空间内容当作永久事实进入回复上下文。

候选默认 7 天后过期。检索层只使用 `active` 记忆，因此未批准的候选不会影响回复。

## 主号命令

```text
/用户 <QQ> 空间资料 状态
/用户 <QQ> 空间资料 禁止
/用户 <QQ> 空间资料 一次授权 <最大条数>
/用户 <QQ> 空间资料 限期 <天数> <最大条数>
/用户 <QQ> 空间资料 预览
```

一次授权和限期授权生成 30 分钟审批码，主号 `/确认 <审批码>` 后才生效。禁止立即生效。默认扫 10 条，不到 10 条有几条算几条，硬上限 20 条，限期 1–90 天。不下载视频文件；图和视频封面最多识 3 张。主号指令和仪表盘预览都会给主号发一条只含条数和原因的私聊回执。

## 仪表盘

- `GET /api/v1/privacy/status`：全局采集开关与授权分布；
- `GET /api/v1/users/{qq}`：含 `qzone_profile_policy`；
- `GET /api/v1/users/{qq}/qzone-profile`：授权、最新快照和候选；
- `POST /api/v1/users/{qq}/qzone-profile/preview`：在授权与全局开关通过后预览；
- `POST /api/v1/control/commands`：与主号共用命令。

## 隐私

导出包含 `qzone_profile_access_policies` 和 `qzone_profile_snapshots`。
删除会清掉授权、快照以及该用户的 `qzone_derived` 记忆。冻结立即阻止新的预览。

## 当前边界

- `get_qzone_msg_list` 适配器已预留，但未对真实 NapCat 联调；
- 没有后台自动巡检 worker；只在主号/仪表盘显式预览时读取；
- 候选不会自动晋升为永久记忆或人格。
