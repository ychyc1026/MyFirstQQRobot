"""Print allowlisted observe-start settings. Never print secrets."""

from __future__ import annotations

import json
import sys

from ych_bot.config import Settings


def allowlisted_observe_settings(settings: Settings | None = None) -> dict[str, object]:
    loaded = settings or Settings.load()
    return {
        "admin_host": loaded.admin_host,
        "admin_port": loaded.admin_port,
        "bot_qq": loaded.bot_qq,
        "ingest_enabled": loaded.ingest_enabled,
        "outbound_enabled": loaded.outbound_enabled,
        "reply_worker_enabled": loaded.reply_worker_enabled,
        "reply_runtime_max_mode": loaded.reply_runtime_max_mode,
        "qzone_publish_enabled": loaded.qzone_publish_enabled,
        "qzone_profile_collection_enabled": loaded.qzone_profile_collection_enabled,
        "owner_reports_enabled": loaded.owner_reports_enabled,
        "privacy_jobs_enabled": loaded.privacy_jobs_enabled,
        "onebot_token_configured": bool(loaded.onebot_access_token),
        "env_file_present": (loaded.project_root / ".env").exists(),
    }


def main() -> int:
    json.dump(allowlisted_observe_settings(), sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
