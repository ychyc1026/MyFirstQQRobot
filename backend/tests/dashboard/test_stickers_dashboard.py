from pathlib import Path

from fastapi.testclient import TestClient
from ych_bot.api.app import create_app
from ych_bot.config import Settings

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_dashboard_sticker_catalog_is_read_only_and_not_outbound(tmp_path: Path) -> None:
    sticker_dir = tmp_path / "storage" / "stickers" / "狗头"
    sticker_dir.mkdir(parents=True)
    (sticker_dir / "a.png").write_bytes(_TINY_PNG)
    settings = Settings(
        project_root=tmp_path,
        database_path=tmp_path / "stickers-ui.sqlite3",
        owner_qq="10001",
        bot_qq="20002",
        admin_access_token="dashboard-token",
        outbound_enabled=False,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/session",
            headers={"Authorization": "Bearer dashboard-token"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        catalog = client.get("/api/v1/stickers", headers=headers)
        assert catalog.status_code == 200
        body = catalog.json()
        assert body["outbound_attached"] is False
        assert body["ready_tags"] == 1
        dog = next(item for item in body["items"] if item["tag"] == "狗头")
        assert dog["count"] == 1
