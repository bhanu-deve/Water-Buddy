"""A cartoon speech bubble that floats above the character.

It is a window of its own (instead of being drawn inside the character's window) so that it can be
as big as the text needs, can flip below the character when there is no room above, and never makes
the character's window larger. It is completely click-through: it can never block the application
underneath it, and it never takes keyboard focus.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QPropertyAnimation, QRect, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class SpeechBubble(QWidget):
    """A rounded white bubble with a blue outline and a tail pointing at the character's head."""

    MAX_TEXT_WIDTH = 240  # text wraps to several lines beyond this width (logical px)
    PAD_X, PAD_Y = 16, 11  # space between the text and the outline
    RADIUS = 16  # corner radius
    TAIL_HEIGHT, TAIL_WIDTH = 13, 22
    MARGIN = 6  # room around the shape for the outline and the soft shadow
    GAP = 4  # space between the tail tip and the top of the character's window
    FADE_IN_MS, FADE_OUT_MS = 140, 220

    FILL = QColor(255, 255, 255)
    OUTLINE = QColor(30, 125, 215)  # the blue of the character's shirt / his "Drink Water!" bubble
    TEXT = QColor(10, 42, 94)  # the dark navy of the lettering in the artwork
    SHADOW = QColor(0, 0, 0, 40)

    _TEXT_FLAGS = int(
        Qt.TextFlag.TextWordWrap.value | Qt.AlignmentFlag.AlignHCenter.value | Qt.AlignmentFlag.AlignVCenter.value
    )

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Water Assistant Speech Bubble")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput  # mouse clicks fall straight through the bubble
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._font = QFont("Segoe UI", 11)
        self._font.setWeight(QFont.Weight.DemiBold)

        self._text = ""
        self._text_size = QSizeF(0, 0)
        self._tail_down = True  # True: bubble is above the character, tail points down
        self._tail_x = 0.0  # x of the tail tip inside this window
        self._path = QPainterPath()
        self._text_rect = QRectF()

        self._hiding = False
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.finished.connect(self._on_fade_finished)

    # ------------------------------------------------------------------ public
    @property
    def text(self) -> str:
        return self._text

    @property
    def tail_points_down(self) -> bool:
        """True while the bubble sits above the character (the normal case)."""
        return self._tail_down

    @property
    def tail_tip_x(self) -> int:
        """Screen x coordinate of the tail tip."""
        return round(self.x() + self._tail_x)

    def show_text(self, text: str, anchor: QRect) -> None:
        """Show `text`, positioned relative to `anchor` (the character window's rectangle)."""
        self._text = text
        self._measure()
        self._place(anchor)
        self._hiding = False  # cancels a fade-out that may still be running
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        if self.windowOpacity() < 1.0:
            self._start_fade(1.0, self.FADE_IN_MS)
        self.raise_()

    def follow(self, anchor: QRect) -> None:
        """Keep the bubble attached to the character while the character moves."""
        if self.isVisible() and self._text:
            self._place(anchor)

    def hide_bubble(self, animated: bool = True) -> None:
        if not self.isVisible():
            return
        if animated:
            self._start_fade(0.0, self.FADE_OUT_MS)
            self._hiding = True  # only AFTER restarting the fade: stopping the old fade must not hide us early
        else:
            self._fade.stop()
            self._hiding = False
            self.hide()

    # ------------------------------------------------------------------ layout
    def _measure(self) -> None:
        metrics = QFontMetricsF(self._font)
        box = metrics.boundingRect(QRectF(0, 0, self.MAX_TEXT_WIDTH, 0), self._TEXT_FLAGS, self._text)
        self._text_size = QSizeF(math.ceil(box.width()) + 2, math.ceil(box.height()) + 2)

    def _place(self, anchor: QRect) -> None:
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()

        width = math.ceil(self._text_size.width() + 2 * self.PAD_X + 2 * self.MARGIN)
        height = math.ceil(self._text_size.height() + 2 * self.PAD_Y + self.TAIL_HEIGHT + 2 * self.MARGIN)
        head_x, head_y = anchor.center().x(), anchor.top()

        # Preferred: above the character. The tail tip is MARGIN px above this window's bottom edge.
        y_above = head_y - self.GAP - (height - self.MARGIN)
        tail_down = y_above >= area.top()
        if tail_down:
            y = y_above
        else:  # no room above (character near the top of the screen): go below it instead
            y = min(anchor.bottom() + 1 + self.GAP - self.MARGIN, area.bottom() + 1 - height)
        x = min(max(head_x - width // 2, area.left()), max(area.left(), area.right() + 1 - width))

        # Keep the tail pointing at the head even if the bubble had to slide sideways to stay on screen.
        reach = self.TAIL_WIDTH / 2 + self.RADIUS + self.MARGIN
        self._tail_x = min(max(head_x - x, reach), width - reach)
        self._tail_down = tail_down
        self._build_shape(width, height)
        self.setGeometry(x, y, width, height)
        self.update()

    def _build_shape(self, width: int, height: int) -> None:
        body_top = self.MARGIN if self._tail_down else self.MARGIN + self.TAIL_HEIGHT
        body = QRectF(
            self.MARGIN,
            body_top,
            width - 2 * self.MARGIN,
            height - 2 * self.MARGIN - self.TAIL_HEIGHT,
        )
        tx, half = self._tail_x, self.TAIL_WIDTH / 2
        tail = QPainterPath()
        if self._tail_down:
            base, tip = body.bottom(), body.bottom() + self.TAIL_HEIGHT
        else:
            base, tip = body.top(), body.top() - self.TAIL_HEIGHT
        middle = (base + tip) / 2
        tail.moveTo(tx - half, base)
        tail.quadTo(QPointF(tx - 1, middle), QPointF(tx, tip))
        tail.quadTo(QPointF(tx + 1, middle), QPointF(tx + half, base))
        tail.closeSubpath()

        path = QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)
        self._path = path.united(tail)  # one outline, so there is no seam where the tail joins
        self._text_rect = QRectF(
            body.left() + self.PAD_X, body.top() + self.PAD_Y, self._text_size.width(), self._text_size.height()
        )

    # ----------------------------------------------------------------- drawing
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.SHADOW)
        painter.drawPath(self._path.translated(0, 2))

        painter.setBrush(self.FILL)
        painter.setPen(QPen(self.OUTLINE, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(self._path)

        painter.setFont(self._font)
        painter.setPen(self.TEXT)
        painter.drawText(self._text_rect, self._TEXT_FLAGS, self._text)

    # -------------------------------------------------------------------- fade
    def _start_fade(self, target: float, milliseconds: int) -> None:
        self._fade.stop()
        self._fade.setDuration(milliseconds)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(target)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._hiding:
            self._hiding = False
            self.hide()
