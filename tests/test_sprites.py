import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from water_assistant.paths import asset_path
from water_assistant.sprites import CharacterSprites, SpriteLoadError

app = QApplication.instance() or QApplication([])
GIF = asset_path("character", "water_reminder.gif")


class CharacterSpritesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sprites = CharacterSprites(GIF)

    def test_loads_all_eight_frames_at_native_size(self):
        self.assertEqual(self.sprites.frame_count, 8)
        self.assertEqual(self.sprites.native_size, QSize(382, 510))

    def test_every_frame_keeps_its_transparency(self):
        native = self.sprites.native_size
        for index in range(self.sprites.frame_count):
            image = self.sprites.pixmap(index, native, 1.0).toImage()
            corners = [(0, 0), (image.width() - 1, 0), (0, image.height() - 1), (image.width() - 1, image.height() - 1)]
            for x, y in corners:
                self.assertEqual(image.pixelColor(x, y).alpha(), 0, f"frame {index}: corner ({x},{y}) is not transparent")

    def test_standing_frame_has_visible_character_in_the_middle(self):
        image = self.sprites.pixmap(0, self.sprites.native_size, 1.0).toImage()
        self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2).alpha(), 255)

    def test_feet_of_every_frame_are_on_the_same_ground_line(self):
        def lowest_visible_row(image):
            data, stride = bytes(image.constBits()), image.bytesPerLine()
            for y in range(image.height() - 1, -1, -1):
                if any(alpha > 127 for alpha in data[y * stride + 3 : (y + 1) * stride : 4]):  # alpha bytes
                    return y

        rows = [
            lowest_visible_row(self.sprites.pixmap(index, self.sprites.native_size, 1.0).toImage())
            for index in range(self.sprites.frame_count)
        ]
        self.assertEqual(len(set(rows)), 1, f"lowest visible row per frame: {rows}")

    def test_ground_alignment_only_moved_the_frames_that_needed_it(self):
        # Measured from the artwork: frame 2 (pointing) floated 11 px above the others, frames 4-6 sat 3 px lower.
        self.assertEqual(self.sprites.ground_offsets, (0, 1, 11, 2, -3, -3, -3, 0))

    def test_original_frame_timing_is_kept(self):
        self.assertEqual(self.sprites.durations_ms, (560,) * 8)

    def test_pixmap_is_rendered_at_real_pixel_size_on_scaled_displays(self):
        pixmap = self.sprites.pixmap(0, QSize(191, 255), 1.25)  # e.g. Windows display scaling 125 %
        self.assertEqual(pixmap.size(), QSize(239, 319))  # real pixels: 191*1.25, 255*1.25 (rounded)
        self.assertEqual(pixmap.devicePixelRatio(), 1.25)

    def test_same_request_is_served_from_the_cache(self):
        first = self.sprites.pixmap(0, QSize(100, 133), 1.0)
        self.assertIs(self.sprites.pixmap(0, QSize(100, 133), 1.0), first)

    def test_unknown_frame_raises_index_error(self):
        with self.assertRaises(IndexError):
            self.sprites.pixmap(99, QSize(100, 133), 1.0)

    def test_missing_file_gives_a_clear_error(self):
        with self.assertRaises(SpriteLoadError) as context:
            CharacterSprites(Path("this_file_does_not_exist.gif"))
        self.assertIn("not found", str(context.exception))

    def test_unreadable_file_gives_a_clear_error(self):
        with tempfile.TemporaryDirectory() as folder:
            bogus = Path(folder) / "broken.gif"
            bogus.write_bytes(b"this is not a gif")
            with self.assertRaises(SpriteLoadError):
                CharacterSprites(bogus)


if __name__ == "__main__":
    unittest.main()
