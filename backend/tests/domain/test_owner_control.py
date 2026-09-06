from _support.owner import (
    BOT_QQ,
    OWNER_QQ,
    private_message,
)
from _support.owner import (
    parse_owner_text as parse,
)
from ych_bot.domain.control import OwnerCommandKind, parse_owner_command
from ych_bot.infrastructure.napcat.parser import parse_message_event


def test_owner_can_request_local_control_help() -> None:
    command = parse("/帮助")

    assert command is not None
    assert command.kind is OwnerCommandKind.HELP
    assert command.arguments == {}


def test_owner_can_request_qzone_revoke() -> None:
    command = parse("/空间 撤销 123e4567-e89b-12d3-a456-426614174000")

    assert command is not None
    assert command.kind is OwnerCommandKind.QZONE_REVOKE
    assert command.arguments["post_id"] == "123e4567-e89b-12d3-a456-426614174000"


def test_owner_can_create_qzone_publish_request() -> None:
    command = parse("/空间 发布 今天完成了新的消息链路")

    assert command is not None
    assert command.kind is OwnerCommandKind.QZONE_PUBLISH
    assert command.actor_qq == OWNER_QQ
    assert command.arguments["content"] == "今天完成了新的消息链路"


def test_owner_can_schedule_qzone_post() -> None:
    command = parse("/空间 定时 2026-08-13 09:30 早上好")

    assert command is not None
    assert command.kind is OwnerCommandKind.QZONE_SCHEDULE
    assert command.arguments == {
        "scheduled_for": "2026-08-13 09:30",
        "content": "早上好",
    }


def test_non_owner_cannot_issue_control_command() -> None:
    assert parse("/空间 发布 不应执行", sender="123456789") is None


def test_group_message_is_never_an_owner_command() -> None:
    payload = private_message("/空间 发布 群内命令不应执行")
    payload.update({"message_type": "group", "group_id": 888888})
    message = parse_message_event(payload, expected_bot_qq=BOT_QQ)

    assert parse_owner_command(message, owner_qq=OWNER_QQ) is None


def test_owner_can_control_user_qzone_profile_policy() -> None:
    status = parse("/用户 123456789 空间资料 状态")
    deny = parse("/用户 123456789 空间资料 禁止")
    once = parse("/用户 123456789 空间资料 一次授权 10")
    ttl = parse("/用户 123456789 空间资料 限期 7 20")
    preview = parse("/用户 123456789 空间资料 预览")

    assert status is not None
    assert status.kind is OwnerCommandKind.USER_QZONE_PROFILE_STATUS
    assert deny is not None
    assert deny.kind is OwnerCommandKind.USER_QZONE_PROFILE_DENY
    assert once is not None
    assert once.kind is OwnerCommandKind.USER_QZONE_PROFILE_ONE_TIME
    assert once.arguments == {"user_qq": "123456789", "max_items": "10"}
    assert ttl is not None
    assert ttl.kind is OwnerCommandKind.USER_QZONE_PROFILE_TTL
    assert ttl.arguments == {"user_qq": "123456789", "days": "7", "max_items": "20"}
    assert preview is not None
    assert preview.kind is OwnerCommandKind.USER_QZONE_PROFILE_PREVIEW


def test_owner_can_control_user_history_policy() -> None:
    command = parse("/用户 123456789 历史 禁止")

    assert command is not None
    assert command.kind is OwnerCommandKind.USER_HISTORY_DENY
    assert command.arguments["user_qq"] == "123456789"


def test_owner_can_restore_evidence_based_friend_state() -> None:
    command = parse("/用户 123456789 恢复自动判定")

    assert command is not None
    assert command.kind is OwnerCommandKind.USER_RESTORE_EVIDENCE
    assert command.arguments == {"user_qq": "123456789"}


def test_owner_can_request_user_privacy_freeze() -> None:
    command = parse("/隐私 冻结 123456789")

    assert command is not None
    assert command.kind is OwnerCommandKind.PRIVACY_FREEZE
    assert command.arguments["user_qq"] == "123456789"


def test_history_range_command_requires_explicit_bounds_and_limit() -> None:
    command = parse("/用户 123456789 历史 指定范围 2026-08-01 2026-08-10 200")

    assert command is not None
    assert command.kind is OwnerCommandKind.USER_HISTORY_SELECTED_RANGE
    assert command.arguments == {
        "user_qq": "123456789",
        "selected_from": "2026-08-01",
        "selected_to": "2026-08-10",
        "max_messages": "200",
    }


def test_incomplete_history_range_is_not_a_command() -> None:
    assert parse("/用户 123456789 历史 指定范围") is None


def test_owner_can_request_live_history_pull() -> None:
    command = parse("/用户 2000000001 历史 拉取")

    assert command is not None
    assert command.kind is OwnerCommandKind.USER_HISTORY_PULL
    assert command.arguments == {"user_qq": "2000000001"}


def test_owner_can_create_and_run_image_tasks() -> None:
    create = parse("/图片 创建 一张克制、现代的 YCH 插画")
    generate = parse("/图片 生成 123e4567-e89b-12d3-a456-426614174000")
    status = parse("/图片 状态 123e4567-e89b-12d3-a456-426614174000")
    renew = parse("/图片 续期 123e4567-e89b-12d3-a456-426614174000")
    scan = parse("/图片 巡检")
    worker_status = parse("/图片 巡检 状态")
    pause = parse("/图片 巡检 暂停")
    resume = parse("/图片 巡检 恢复")

    assert create is not None
    assert create.kind is OwnerCommandKind.IMAGE_CREATE
    assert create.arguments["prompt"] == "一张克制、现代的 YCH 插画"
    assert generate is not None
    assert generate.kind is OwnerCommandKind.IMAGE_GENERATE
    assert status is not None
    assert status.kind is OwnerCommandKind.IMAGE_STATUS
    assert renew is not None
    assert renew.kind is OwnerCommandKind.IMAGE_RENEW
    assert scan is not None
    assert scan.kind is OwnerCommandKind.IMAGE_ORPHAN_SCAN
    assert worker_status is not None
    assert worker_status.kind is OwnerCommandKind.IMAGE_ORPHAN_WORKER_STATUS
    assert pause is not None
    assert pause.kind is OwnerCommandKind.IMAGE_ORPHAN_WORKER_PAUSE
    assert resume is not None
    assert resume.kind is OwnerCommandKind.IMAGE_ORPHAN_WORKER_RESUME


def test_owner_can_process_and_inspect_knowledge_jobs() -> None:
    job_id = "123e4567-e89b-12d3-a456-426614174000"
    process = parse(f"/资料 处理 {job_id}")
    status = parse(f"/资料 状态 {job_id}")

    assert process is not None
    assert process.kind is OwnerCommandKind.KNOWLEDGE_PROCESS
    assert process.arguments["job_id"] == job_id
    assert status is not None
    assert status.kind is OwnerCommandKind.KNOWLEDGE_STATUS


def test_owner_can_control_knowledge_worker() -> None:
    status = parse("/资料 自动 状态")
    pause = parse("/资料 自动 暂停")
    resume = parse("/资料 自动 恢复")

    assert status is not None
    assert status.kind is OwnerCommandKind.KNOWLEDGE_WORKER_STATUS
    assert pause is not None
    assert pause.kind is OwnerCommandKind.KNOWLEDGE_WORKER_PAUSE
    assert resume is not None
    assert resume.kind is OwnerCommandKind.KNOWLEDGE_WORKER_RESUME


def test_owner_can_create_and_control_proactive_tasks() -> None:
    create = parse("/主动 创建 123456789 2026-08-14 09:30 记得吃早餐")
    status = parse("/主动 状态 123e4567-e89b-12d3-a456-426614174000")
    enable = parse("/主动 用户 123456789 开启")
    pause = parse("/主动 自动 暂停")
    outbound = parse("/外发 状态")

    assert create is not None
    assert create.kind is OwnerCommandKind.PROACTIVE_CREATE
    assert create.arguments == {
        "target_qq": "123456789",
        "scheduled_for": "2026-08-14 09:30",
        "content": "记得吃早餐",
    }
    assert status is not None
    assert status.kind is OwnerCommandKind.PROACTIVE_STATUS
    assert enable is not None
    assert enable.kind is OwnerCommandKind.PROACTIVE_USER_ENABLE
    assert pause is not None
    assert pause.kind is OwnerCommandKind.PROACTIVE_WORKER_PAUSE
    assert outbound is not None
    assert outbound.kind is OwnerCommandKind.OUTBOUND_STATUS


def test_owner_can_control_independent_proactive_content_permissions() -> None:
    status = parse("/主动 用户 123456789 状态")
    content_on = parse("/主动 内容 123456789 开启")
    content_off = parse("/主动 内容 123456789 关闭")
    diary_on = parse("/主动 日记 123456789 开启")
    diary_off = parse("/主动 日记 123456789 关闭")

    assert status is not None
    assert status.kind is OwnerCommandKind.PROACTIVE_USER_STATUS
    assert content_on is not None
    assert content_on.kind is OwnerCommandKind.PROACTIVE_AUTO_CONTENT_ENABLE
    assert content_off is not None
    assert content_off.kind is OwnerCommandKind.PROACTIVE_AUTO_CONTENT_DISABLE
    assert diary_on is not None
    assert diary_on.kind is OwnerCommandKind.PROACTIVE_DIARY_ENABLE
    assert diary_off is not None
    assert diary_off.kind is OwnerCommandKind.PROACTIVE_DIARY_DISABLE


def test_owner_can_query_and_control_report_worker() -> None:
    queue = parse("/汇报 队列")
    status = parse("/汇报 状态 123e4567-e89b-12d3-a456-426614174000")
    ack = parse("/汇报 已读 123e4567-e89b-12d3-a456-426614174000")
    worker_status = parse("/汇报 自动 状态")
    pause = parse("/汇报 自动 暂停")
    resume = parse("/汇报 自动 恢复")

    assert queue is not None
    assert queue.kind is OwnerCommandKind.REPORT_QUEUE
    assert status is not None
    assert status.kind is OwnerCommandKind.REPORT_STATUS
    assert ack is not None
    assert ack.kind is OwnerCommandKind.REPORT_ACK
    assert worker_status is not None
    assert worker_status.kind is OwnerCommandKind.REPORT_WORKER_STATUS
    assert pause is not None
    assert pause.kind is OwnerCommandKind.REPORT_WORKER_PAUSE
    assert resume is not None
    assert resume.kind is OwnerCommandKind.REPORT_WORKER_RESUME


def test_owner_can_read_model_qualification_status_but_cannot_start_paid_run() -> None:
    status = parse("/模型 状态")
    confirm = parse("/模型 确认 paid-handle")
    preview = parse("/模型 预览 chat")

    assert status is not None
    assert status.kind is OwnerCommandKind.MODEL_STATUS
    assert confirm is None
    assert preview is None


def test_owner_can_inspect_and_replay_shadow_inference() -> None:
    run_id = "123e4567-e89b-12d3-a456-426614174000"
    status = parse("/影子 状态")
    recent = parse("/影子 最近")
    detail = parse(f"/影子 查看 {run_id}")
    replay = parse(f"/影子 重跑 {run_id}")

    assert status is not None
    assert status.kind is OwnerCommandKind.SHADOW_STATUS
    assert recent is not None
    assert recent.kind is OwnerCommandKind.SHADOW_RECENT
    assert detail is not None
    assert detail.kind is OwnerCommandKind.SHADOW_DETAIL
    assert detail.arguments["run_id"] == run_id
    assert replay is not None
    assert replay.kind is OwnerCommandKind.SHADOW_REPLAY
    assert replay.arguments["message_id"] == run_id


def test_owner_can_manage_chat_quotas() -> None:
    status = parse("/额度 状态")
    today = parse("/额度 今天 30003 1000")
    user_limit = parse("/额度 用户 30003 500000")
    group_limit = parse("/额度 群 888 2000000")

    assert status is not None
    assert status.kind is OwnerCommandKind.QUOTA_STATUS
    assert status.arguments["bot_qq"] == BOT_QQ
    assert today is not None
    assert today.kind is OwnerCommandKind.QUOTA_TODAY_BONUS
    assert today.arguments == {
        "bot_qq": BOT_QQ,
        "user_qq": "30003",
        "amount": "1000",
    }
    assert user_limit is not None
    assert user_limit.kind is OwnerCommandKind.QUOTA_USER_LIMIT
    assert user_limit.arguments["daily_limit"] == "500000"
    assert group_limit is not None
    assert group_limit.kind is OwnerCommandKind.QUOTA_GROUP_LIMIT
    assert group_limit.arguments["group_id"] == "888"
    assert group_limit.arguments["daily_limit"] == "2000000"
