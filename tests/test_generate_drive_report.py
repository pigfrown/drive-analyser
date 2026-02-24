from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from generate_drive_report import (
    classify_media_by_content,
    is_program_asset_image,
    write_list,
)


def test_write_list_handles_surrogateescape_chars(tmp_path: Path) -> None:
    out = tmp_path / "paths.txt"
    bad_path = "bad\udce9name"

    write_list(out, [bad_path])

    text = out.read_text(encoding="utf-8")
    assert "\\udce9" in text


def test_classify_media_by_content_rejects_corrupt_extension_file(tmp_path: Path) -> None:
    fake_jpg = tmp_path / "not_really_image.jpg"
    fake_jpg.write_bytes(b"this is not an image")

    assert classify_media_by_content(fake_jpg, ".jpg") is None


def test_is_program_asset_image_detects_steam_and_minecraft() -> None:
    assert is_program_asset_image(Path("/mnt/usb/.steam/steamapps/common/game/texture.png"))
    assert is_program_asset_image(Path("/mnt/usb/.minecraft/assets/objects/a/b/c.png"))
