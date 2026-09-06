from pathlib import Path

from ych_bot.config import Settings
from ych_bot.domain.system_identity import CORE_IDENTITY


def test_core_identity_locks_brand_and_allows_configurable_qq(tmp_path: Path) -> None:
    assert CORE_IDENTITY.brand == "YCH"
    assert CORE_IDENTITY.creator_name == "维护者"
    assert CORE_IDENTITY.locked is True

    settings = Settings(project_root=tmp_path, owner_qq="10001", bot_qq="20002")
    assert settings.owner_qq == "10001"
    assert settings.bot_qq == "20002"
