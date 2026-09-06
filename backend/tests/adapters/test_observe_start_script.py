from __future__ import annotations

import importlib.util
from pathlib import Path

from _support.paths import PROJECT_ROOT
from ych_bot.config import Settings

SCRIPTS = PROJECT_ROOT / "scripts"
START_PS1 = SCRIPTS / "start-observe.ps1"
START_BAT = SCRIPTS / "start-observe.bat"
INSTALL_BAT = SCRIPTS / "install-observe-shortcut.bat"
SETTINGS_PY = SCRIPTS / "print_observe_settings.py"

BOT_QQ = "2000000002"
OWNER_QQ = "2000000001"


def test_observe_start_scripts_exist() -> None:
    assert START_PS1.is_file()
    assert START_BAT.is_file()
    assert INSTALL_BAT.is_file()
    assert SETTINGS_PY.is_file()


def test_observe_start_script_starts_ych_before_napcat() -> None:
    text = START_PS1.read_text(encoding="utf-8-sig")
    assert text.index("Wait-YchLive") < text.index("Start-NapCatWindow $qqExe")
    assert "KillQQ" not in text
    assert "YCH_OUTBOUND_ENABLED" not in text
    assert "YCH_REPLY_WORKER_ENABLED" not in text
    assert "-q $BotQq" in text
    assert f"-q {OWNER_QQ}" not in text
    assert BOT_QQ in text
    assert OWNER_QQ in text
    assert "health/live" in text
    assert "health/ready" in text
    assert "print_observe_settings.py" in text
    assert 'GetFolderPath("Desktop")' not in text
    assert 'Join-Path $PSScriptRoot "YCH 观察启动.lnk"' in text
    assert ' @("observe_only", "shadow", "owner_approved", "limited_auto", "auto")' in text
    assert "$ready.outbound_enabled" in text
    assert "$settings.outbound_enabled -or $settings.reply_worker_enabled" not in text


def test_observe_start_bat_invokes_powershell_wrapper() -> None:
    bat_bytes = START_BAT.read_bytes()
    assert b"\r\n" in bat_bytes
    assert not bat_bytes.startswith(b"\xef\xbb\xbf")
    assert b"chcp 65001" not in bat_bytes
    bat = bat_bytes.decode("ascii")
    assert "start-observe.ps1" in bat
    assert "ExecutionPolicy Bypass" in bat
    assert r"System32\WindowsPowerShell\v1.0\powershell.exe" in bat
    assert "pause" in bat
    install_bytes = INSTALL_BAT.read_bytes()
    assert b"\r\n" in install_bytes
    assert b"chcp 65001" not in install_bytes
    install = install_bytes.decode("ascii")
    assert "-InstallShortcut" in install
    assert "-SkipStart" in install
    assert "pause" in install


def test_print_observe_settings_source_has_no_secret_fields() -> None:
    source = SETTINGS_PY.read_text(encoding="utf-8")
    assert "admin_access_token" not in source
    assert "API_KEY" not in source
    assert "onebot_access_token" in source
    assert "bool(loaded.onebot_access_token)" in source


def _load_print_observe_settings():
    spec = importlib.util.spec_from_file_location("print_observe_settings", SETTINGS_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_allowlisted_observe_settings_omits_secrets(tmp_path: Path) -> None:
    allowlisted_observe_settings = _load_print_observe_settings().allowlisted_observe_settings

    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "observe.sqlite3",
        onebot_access_token="must-not-appear",
        admin_access_token="dashboard-must-not-appear",
        chat_api_key="chat-must-not-appear",
        outbound_enabled=False,
        reply_worker_enabled=False,
        reply_runtime_max_mode="observe_only",
        ingest_enabled=True,
        bot_qq=BOT_QQ,
        admin_host="127.0.0.1",
        admin_port=8765,
    )
    payload = allowlisted_observe_settings(settings)
    dumped = str(payload)
    assert payload["outbound_enabled"] is False
    assert payload["reply_worker_enabled"] is False
    assert payload["onebot_token_configured"] is True
    assert payload["bot_qq"] == BOT_QQ
    assert "must-not-appear" not in dumped
    assert "dashboard-must-not-appear" not in dumped
    assert "chat-must-not-appear" not in dumped
    assert set(payload) == {
        "admin_host",
        "admin_port",
        "bot_qq",
        "ingest_enabled",
        "outbound_enabled",
        "reply_worker_enabled",
        "reply_runtime_max_mode",
        "qzone_publish_enabled",
        "qzone_profile_collection_enabled",
        "owner_reports_enabled",
        "privacy_jobs_enabled",
        "onebot_token_configured",
        "env_file_present",
    }
