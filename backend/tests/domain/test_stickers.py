import random
from pathlib import Path

from ych_bot.application.stickers import StickerLibrary
from ych_bot.domain.stickers import extract_sticker_tags

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_extract_sticker_tags_keeps_text_and_known_labels() -> None:
    cleaned, tags = extract_sticker_tags("今天不错【开心】，顺便【狗头】再【开心】。")
    assert cleaned == "今天不错，顺便再。"
    assert tags == ("开心", "狗头")


def test_extract_sticker_tags_ignores_unknown_and_image_urls() -> None:
    cleaned, tags = extract_sticker_tags("看看这个【未知标签】和 https://example/a.gif")
    assert "https://example/a.gif" in cleaned
    assert tags == ()


def test_sticker_library_resolves_local_files_without_outbound(tmp_path: Path) -> None:
    folder = tmp_path / "开心"
    folder.mkdir()
    (folder / "01.png").write_bytes(_TINY_PNG)
    (folder / "notes.txt").write_text("ignore", encoding="utf-8")
    library = StickerLibrary(tmp_path)
    catalog = library.catalog()
    assert catalog["outbound_attached"] is False
    assert catalog["ready_tags"] == 1
    happy = next(item for item in catalog["items"] if item["tag"] == "开心")
    assert happy["count"] == 1
    assert happy["files"] == ["01.png"]
    assert library.resolve("开心") == folder / "01.png"
    assert library.resolve("狗头") is None
    assert library.resolve("未知") is None


def test_sticker_library_resolves_random_file_among_tag(tmp_path: Path) -> None:
    folder = tmp_path / "狗头"
    folder.mkdir()
    (folder / "a.png").write_bytes(_TINY_PNG)
    (folder / "b.png").write_bytes(_TINY_PNG)
    library = StickerLibrary(tmp_path, rng=random.Random(7))
    chosen = {library.resolve("狗头") for _ in range(20)}
    assert chosen <= {folder / "a.png", folder / "b.png"}
    assert None not in chosen
    assert len(chosen) >= 1
