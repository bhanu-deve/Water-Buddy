"""The character artwork: loads the animated GIF and prepares frames for display.

Why Pillow instead of Qt's QMovie?
  * We need every frame as its *own picture*. Later the animation controller will say
    things like "show frame 4 (drinking) now", and QMovie can only play a GIF in order.
  * Pillow composes GIF frames correctly (transparency + disposal rules).
  * Pillow's Lanczos resizing keeps the edges of the character clean when it is scaled.

The GIF already has real transparency, so no background removal is required.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QPixmap


class SpriteLoadError(RuntimeError):
    """The character artwork is missing or unreadable (message is safe to show to the user)."""


class CharacterSprites:
    """All frames of the character GIF, ready to be drawn at any size."""

    _MAX_CACHED_PIXMAPS = 64  # plenty: 8 frames x a few sizes

    def __init__(self, gif_path: Path) -> None:
        self.path = Path(gif_path)
        frames, self._durations_ms = self._load_frames(self.path)
        # Shift every frame (by whole pixels, nothing is resized or redrawn) so that the
        # soles of the shoes are on the same line in all of them. Otherwise the feet would
        # visibly jump up and down whenever the character changes pose.
        self._frames, self._ground_offsets = self._align_to_ground(frames)
        self._cache: dict[tuple[int, int, int, float], QPixmap] = {}

    # ----------------------------------------------------------------- loading
    @staticmethod
    def _load_frames(path: Path) -> tuple[list[Image.Image], list[int]]:
        if not path.is_file():
            raise SpriteLoadError(f"The character animation was not found:\n{path}")
        try:
            with Image.open(path) as gif:
                frames, durations = [], []
                for frame in ImageSequence.Iterator(gif):
                    durations.append(int(frame.info.get("duration", 100)))
                    # convert("RGBA") returns an independent copy of the fully composed
                    # frame and keeps the transparency, so the GIF file can be closed.
                    frames.append(frame.convert("RGBA"))
        except OSError as error:  # Pillow's "cannot identify image file" is an OSError
            raise SpriteLoadError(f"The character animation could not be read:\n{path}\n\n{error}") from error

        if not frames:
            raise SpriteLoadError(f"The character animation has no frames:\n{path}")
        if any(frame.size != frames[0].size for frame in frames):
            raise SpriteLoadError(f"All frames of the character animation must be the same size:\n{path}")
        return frames, durations

    @staticmethod
    def _align_to_ground(frames: list[Image.Image]) -> tuple[list[Image.Image], tuple[int, ...]]:
        """Move each frame up/down so its lowest visible pixel matches the first (standing) frame."""
        bottoms = []
        for frame in frames:
            box = frame.getchannel("A").point(lambda value: 255 if value > 127 else 0).getbbox()
            bottoms.append(box[3] if box else None)  # None = completely empty frame
        ground = bottoms[0]
        if ground is None:
            return frames, tuple(0 for _ in frames)

        aligned, offsets = [], []
        for frame, bottom in zip(frames, bottoms):
            shift = 0 if bottom is None else ground - bottom
            if shift:
                moved = Image.new("RGBA", frame.size, (0, 0, 0, 0))
                moved.paste(frame, (0, shift))
                frame = moved
            aligned.append(frame)
            offsets.append(shift)
        return aligned, tuple(offsets)

    # -------------------------------------------------------------- information
    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def durations_ms(self) -> tuple[int, ...]:
        """How long the original GIF shows each frame (milliseconds)."""
        return tuple(self._durations_ms)

    @property
    def ground_offsets(self) -> tuple[int, ...]:
        """Pixels each frame was moved down (+) or up (-) to put the feet on a common ground line."""
        return self._ground_offsets

    @property
    def native_size(self) -> QSize:
        """Size of one frame as stored in the GIF (pixels)."""
        width, height = self._frames[0].size
        return QSize(width, height)

    # ----------------------------------------------------------------- drawing
    def pixmap(self, index: int, logical_size: QSize, device_pixel_ratio: float = 1.0) -> QPixmap:
        """Frame `index`, scaled to fill `logical_size`.

        `logical_size` is in Qt's device-independent pixels. On a screen with display
        scaling (e.g. 125 %) one logical pixel is more than one real pixel, so we render
        at the *real* pixel size and tell Qt the ratio. That keeps the character sharp.
        """
        pixel_width = max(1, round(logical_size.width() * device_pixel_ratio))
        pixel_height = max(1, round(logical_size.height() * device_pixel_ratio))
        key = (index, pixel_width, pixel_height, round(device_pixel_ratio, 3))

        pixmap = self._cache.get(key)
        if pixmap is None:
            frame = self._frames[index]  # IndexError for a bad index is intentional
            if frame.size != (pixel_width, pixel_height):
                # Pillow premultiplies alpha while resizing, so no dark/light fringes appear.
                frame = frame.resize((pixel_width, pixel_height), Image.Resampling.LANCZOS)
            pixmap = _to_pixmap(frame)
            pixmap.setDevicePixelRatio(device_pixel_ratio)
            if len(self._cache) >= self._MAX_CACHED_PIXMAPS:
                self._cache.clear()
            self._cache[key] = pixmap
        return pixmap


def _to_pixmap(image: Image.Image) -> QPixmap:
    """Convert a Pillow RGBA image to a QPixmap (alpha preserved)."""
    data = image.tobytes("raw", "RGBA")
    # QImage(data, ...) only *borrows* the bytes; copy() makes Qt own its pixels.
    qimage = QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimage)
