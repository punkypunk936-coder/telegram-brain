import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clipboard_utils import ClipboardError, copy_media, copy_text


class ClipboardUtilsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def file(self, name: str) -> Path:
        path = self.root / name
        path.write_bytes(b"test")
        return path

    def test_text_is_copied_without_changing_its_layout(self):
        value = "Title\n\nA paragraph with a link: https://example.com"
        with patch("clipboard_utils._run_clipboard") as run:
            copy_text(value)

        run.assert_called_once_with("text", text=value)

    def test_blank_text_is_rejected(self):
        with self.assertRaisesRegex(ClipboardError, "no text"):
            copy_text(" \n ")

    def test_static_image_is_copied_as_paste_ready_image(self):
        image = self.file("meme.png")
        with patch("clipboard_utils._run_clipboard") as run:
            copied = copy_media([image])

        self.assertEqual(copied, 1)
        run.assert_called_once_with("image", [image.resolve()])

    def test_gif_is_copied_as_original_file(self):
        gif = self.file("reaction.gif")
        with patch("clipboard_utils._run_clipboard") as run:
            copied = copy_media([gif])

        self.assertEqual(copied, 1)
        run.assert_called_once_with("files", [gif.resolve()])

    def test_album_keeps_all_original_assets_and_avoids_duplicates(self):
        first = self.file("first.jpg")
        second = self.file("second.mp4")
        with patch("clipboard_utils._run_clipboard") as run:
            copied = copy_media([first, second, first])

        self.assertEqual(copied, 2)
        run.assert_called_once_with(
            "files",
            [first.resolve(), second.resolve()],
        )

    def test_missing_media_is_reported(self):
        with self.assertRaisesRegex(ClipboardError, "unavailable"):
            copy_media([self.root / "missing.gif"])


if __name__ == "__main__":
    unittest.main()
