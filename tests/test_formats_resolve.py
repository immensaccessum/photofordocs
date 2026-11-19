import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from photofordocs import formats
from photofordocs.formats import (
    build_alias_map,
    clear_user_format_profile,
    count_user_format_files,
    load_formats,
    resolve_format_id,
    visible_formats,
)
from photofordocs.session import read_ready, ready_photo_path, split_ready_name


class FormatResolveTest(unittest.TestCase):
    def test_resolve_prefers_existing_catalog_id(self) -> None:
        catalog = load_formats()
        alias = build_alias_map(catalog)
        resolved = resolve_format_id("jp_41", alias, catalog)
        if any(item.id == "jp_41" for item in catalog):
            self.assertEqual(resolved, "jp_41")
        else:
            self.assertEqual(resolved, "passport-rf-2013")

    def test_visible_core_without_user_profile(self) -> None:
        with patch.object(formats, "user_has_format_profile", return_value=False):
            all_items = load_formats()
            shown = visible_formats(all_items, user_has_profile=False)
            self.assertEqual(len([item for item in shown if item.tier == "core"]), len(shown))
            self.assertGreaterEqual(len(shown), 10)

    def test_clear_user_format_profile(self) -> None:
        previous = formats.USER_DIR
        with tempfile.TemporaryDirectory() as folder:
            user = Path(folder)
            formats.USER_DIR = user
            user.mkdir(parents=True, exist_ok=True)
            (user / "jp_41.yaml").write_text("id: jp_41\ndeleted: true\n", encoding="utf-8")
            (user / "jp_02.yaml").write_text(
                "id: jp_02\nname: x\nwidth_mm: 30\nheight_mm: 40\nhead_height_mm: 30\nchin_bottom_mm: 4\n",
                encoding="utf-8",
            )
            self.assertEqual(count_user_format_files(), 2)
            removed = clear_user_format_profile(reset_order=False)
            self.assertEqual(removed, 2)
            self.assertEqual(count_user_format_files(), 0)
            formats.USER_DIR = previous

    def test_ready_photo_path_uses_format_id(self) -> None:
        path = ready_photo_path(Path("C:/work/photo.jpg"), "passport-rf-2013")
        self.assertEqual(path.name, "photo.passport-rf-2013.jpg")

    def test_split_ready_name_canonical_jpg(self) -> None:
        parsed = split_ready_name("client.passport-rf-2013.jpg")
        self.assertIsNotNone(parsed)
        body, key, printed = parsed
        self.assertEqual(key, "jpg")
        self.assertFalse(printed)
        self.assertEqual(body, "client.passport-rf-2013")

    def test_read_ready_resolves_legacy_extension(self) -> None:
        catalog = load_formats()
        alias = build_alias_map(catalog)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "client.jpg"
            source.write_bytes(b"\xff\xd8\xff")
            ready = root / "Ready"
            ready.mkdir()
            photo = ready / "client.jp_41~p"
            photo.write_bytes(b"\xff\xd8\xff")
            rows = read_ready(source, {"client"}, alias_map=alias, formats=catalog)
            self.assertEqual(len(rows), 1)
            resolved = resolve_format_id("jp_41", alias, catalog)
            self.assertEqual(rows[0]["format_id"], resolved)

    def test_visible_hides_core_when_user_has_legacy(self) -> None:
        previous = formats.USER_DIR
        with tempfile.TemporaryDirectory() as folder:
            user = Path(folder)
            formats.USER_DIR = user
            user.mkdir(parents=True, exist_ok=True)
            (user / "jp_41.yaml").write_text(
                """
id: jp_41
name: Паспорт из профиля
width_mm: 35
height_mm: 45
head_height_mm: 34
chin_bottom_mm: 9
background: '#FFFFFF'
extension: jp_41
legacy_ext:
  - jp_41
tier: extended
""".strip(),
                encoding="utf-8",
            )
            catalog = load_formats()
            shown = visible_formats(catalog, user_has_profile=True)
            self.assertTrue(any(item.id == "jp_41" for item in shown))
            self.assertFalse(any(item.id == "passport-rf-2013" for item in shown))
            formats.USER_DIR = previous


if __name__ == "__main__":
    unittest.main()