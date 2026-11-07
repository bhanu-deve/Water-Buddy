"""The floating desktop character: a frameless, see-through, always-on-top window.

Key ideas:
  * The window has NO border or title bar and a transparent background, so only the
    character itself is visible.
  * On Windows, a window with per-pixel transparency lets mouse clicks fall through the
    fully transparent pixels to whatever application is underneath. Only the character's
    own (visible) pixels react to the mouse, so other apps stay fully usable.
  * Dragging is done by hand (press / move / release), which is simple and easy to test.
  * This class only DRAWS the character (which GIF frame, plus a small squash/lean) and reports
    what the user does with it. WHAT the character does is decided by the AnimationController.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QContextMenuEvent,
    QGuiApplication,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QScreen,
    QWheelEvent,
)
from PySide6.QtWidgets import QMenu, QWidget

from .sprites import CharacterSprites


class CharacterWindow(QWidget):
    """Shows one frame of the character; the user can drag it, click it, resize it and right-click it."""

    exitRequested = Signal()  # the user chose "Exit" in the menu
    clicked = Signal(QPoint)  # a click (press and release without dragging) at this global position; the small menu follows
    previewRequested = Signal(str)  # the user chose an entry of the "Preview animation" menu (its key)
    wanderingToggled = Signal(bool)  # the user switched "Walk around on its own" on/off
    interactionStarted = Signal()  # the user grabbed the character or opened its menu
    interactionFinished = Signal()  # ...and let go / closed the menu
    scaleChanged = Signal(float)  # the size changed (mouse wheel or menu)
    geometryChanged = Signal()  # the window moved or was resized

    DEFAULT_SCALE = 0.4  # fraction of the GIF's native 382x510 size -> about 153x204 px
    MIN_SCALE = 0.25
    MAX_SCALE = 1.5  # above 1.0 the (0.25x) GIF has to be enlarged, so it gets slightly soft
    SIZE_PRESETS = (("Small", 0.3), ("Medium", DEFAULT_SCALE), ("Large", 0.6), ("Extra large", 1.0))
    WHEEL_ZOOM_PER_NOTCH = 1.1  # each mouse-wheel notch changes the size by 10 %
    MARGIN_RIGHT = 8  # start-up gap to the right edge of the usable screen area (px)
    MARGIN_BOTTOM = 0  # 0 = the character stands right on top of the taskbar
    CLICK_SLOP = 5  # the mouse may wobble this far (logical px) between press and release and it is still a click
    PREVIEW_ACTIONS = (  # (menu text, key sent in previewRequested)
        ("Idle (breathing)", "idle"),
        ("Walk across the screen", "walk"),
        ("Reminder (hop, sign, speech bubble)", "reminder"),
        ("Talk (speech bubble)", "talk"),
        ("Drink", "drink"),
        ("Celebrate", "celebrate"),
        ("Play the original GIF", "original_gif"),
    )

    def __init__(self, sprites: CharacterSprites, scale: float = DEFAULT_SCALE, frame_index: int = 0) -> None:
        super().__init__()
        self._sprites = sprites
        self._frame_index = frame_index
        self._scale = self._clamp_scale(scale)
        self._pose = (1.0, 1.0, 0.0)  # extra squash (x, y) and lean (degrees) around the feet
        self._wandering_enabled = True
        self._menu_sections: list[Callable[[QMenu], None]] = []  # added by other parts of the program (right-click menu)
        self._quick_sections: list[tuple[int, Callable[[QMenu], None]]] = []  # ...and the small click menu: (order, builder)
        self._drag_offset: QPoint | None = None  # where inside the window the user grabbed it
        self._press_global: QPoint | None = None  # where the left button went down (global position)
        self._dragging = False  # has the mouse moved far enough since the press for it to be a drag, not a click?

        self.setWindowTitle("Water Assistant Character")  # lets tools/tests find this window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint  # no title bar, no border
            | Qt.WindowType.WindowStaysOnTopHint  # float above normal windows
            | Qt.WindowType.Tool  # no taskbar button / Alt+Tab entry
            | Qt.WindowType.WindowDoesNotAcceptFocus  # clicking never steals keyboard focus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)  # per-pixel transparency
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # showing never steals focus
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # hint: "you can grab me"
        self.resize(self._scaled_size())

    # ------------------------------------------------------- what is drawn
    @property
    def sprites(self) -> CharacterSprites:
        return self._sprites

    @property
    def frame_index(self) -> int:
        """Which frame of the GIF is currently shown."""
        return self._frame_index

    def set_frame(self, index: int) -> None:
        if index != self._frame_index:
            self._frame_index = int(index)
            self.update()

    @property
    def pose(self) -> tuple[float, float, float]:
        """(squash x, squash y, lean in degrees) applied around the character's feet."""
        return self._pose

    def set_pose(self, scale_x: float = 1.0, scale_y: float = 1.0, rotation: float = 0.0) -> None:
        """Squash/stretch and lean the picture around the feet (used for breathing, bouncing, ...)."""
        pose = (round(scale_x, 4), round(scale_y, 4), round(rotation, 2))
        if pose != self._pose:
            self._pose = pose
            self.update()

    @property
    def wandering_enabled(self) -> bool:
        return self._wandering_enabled

    def set_wandering_enabled(self, enabled: bool) -> None:
        self._wandering_enabled = bool(enabled)

    # ------------------------------------------------------------------ size
    @property
    def scale(self) -> float:
        """Current size as a fraction of the GIF's native size (1.0 = 382x510)."""
        return self._scale

    @classmethod
    def _clamp_scale(cls, scale: float) -> float:
        return round(min(cls.MAX_SCALE, max(cls.MIN_SCALE, scale)), 2)

    def _scaled_size(self) -> QSize:
        native = self._sprites.native_size
        return QSize(round(native.width() * self._scale), round(native.height() * self._scale))

    def set_scale(self, scale: float) -> None:
        """Resize the character, keeping its feet (bottom-centre) where they are."""
        scale = self._clamp_scale(scale)
        if scale == self._scale:
            return
        old = self.geometry()
        self._scale = scale
        new_size = self._scaled_size()
        top_left = QPoint(
            old.x() + (old.width() - new_size.width()) // 2,
            old.y() + old.height() - new_size.height(),
        )
        # One setGeometry call (instead of resize + move) avoids a visible flicker.
        self.setGeometry(QRect(self._clamp_to_screens(top_left, new_size), new_size))
        self.update()
        self.scaleChanged.emit(scale)

    # -------------------------------------------------------------- position
    def move_to_bottom_right(self) -> None:
        """Stand the character in the bottom-right corner of the usable desktop (above the taskbar)."""
        area = QGuiApplication.primaryScreen().availableGeometry()  # screen minus the taskbar
        self.move(
            area.x() + area.width() - self.width() - self.MARGIN_RIGHT,
            area.y() + area.height() - self.height() - self.MARGIN_BOTTOM,
        )

    @staticmethod
    def _nearest_screen(point: QPoint) -> QScreen | None:
        """The screen containing `point` (or the closest screen if none does)."""
        best: QScreen | None = None
        best_distance = 0
        for screen in QGuiApplication.screens():
            area = screen.geometry()
            dx = max(area.left() - point.x(), 0, point.x() - area.right())
            dy = max(area.top() - point.y(), 0, point.y() - area.bottom())
            distance = dx * dx + dy * dy
            if best is None or distance < best_distance:
                best, best_distance = screen, distance
        return best

    def available_area(self) -> QRect:
        """The usable part (screen minus taskbar) of the screen the character is on."""
        screen = self._nearest_screen(self.geometry().center())
        return screen.availableGeometry() if screen is not None else QRect()

    def _clamp_to_screens(self, top_left: QPoint, size: QSize) -> QPoint:
        """Allow dragging anywhere, but keep the character's centre on a real screen
        so it can never be dragged completely out of sight."""
        centre = QPoint(top_left.x() + size.width() // 2, top_left.y() + size.height() // 2)
        screen = self._nearest_screen(centre)
        if screen is None:
            return top_left
        area = screen.geometry()
        x = min(max(centre.x(), area.left()), area.right())
        y = min(max(centre.y(), area.top()), area.bottom())
        return QPoint(x - size.width() // 2, y - size.height() // 2)

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: N802 (Qt naming)
        super().moveEvent(event)
        self.geometryChanged.emit()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.geometryChanged.emit()

    # --------------------------------------------------------------- drawing
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        # Start from a fully transparent canvas so no old pixels can ever "ghost" through.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        scale_x, scale_y, lean = self._pose
        if (scale_x, scale_y, lean) != (1.0, 1.0, 0.0):
            feet = QPointF(self.width() / 2, self.height())  # pivot: bottom-centre, so the feet stay planted
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.translate(feet)
            painter.rotate(lean)
            painter.scale(scale_x, scale_y)
            painter.translate(-feet)
        painter.drawPixmap(0, 0, self._current_pixmap())

    def _current_pixmap(self) -> QPixmap:
        return self._sprites.pixmap(self._frame_index, self.size(), self.devicePixelRatioF())

    # --------------------------------------------------------------- mouse
    # A left press is "a click" until the mouse has moved more than CLICK_SLOP: only then the character starts to
    # follow the mouse. So a click never nudges the character, and a click opens the small menu.
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.pos()
            self._dragging = False
            self.interactionStarted.emit()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is None or self._press_global is None:
            return
        if not event.buttons() & Qt.MouseButton.LeftButton:  # the button was released somewhere we did not see
            self._finish_press(click_at=None)
            return
        here = event.globalPosition().toPoint()
        if not self._dragging:
            if (here - self._press_global).manhattanLength() <= self.CLICK_SLOP:
                return  # just a wobble of the hand
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.move(self._clamp_to_screens(here - self._drag_offset, self.size()))
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            click_at = None if self._dragging else event.globalPosition().toPoint()
            self._finish_press(click_at=click_at)
            event.accept()

    def _finish_press(self, *, click_at: QPoint | None) -> None:
        """The left button is up. A press that never turned into a drag is a click: the small menu opens."""
        self._drag_offset = None
        self._press_global = None
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if click_at is not None:
            self.clicked.emit(click_at)
            self._open_quick_menu(click_at)  # waits here while the menu is open
        self.interactionFinished.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        notches = event.angleDelta().y() / 120.0  # one wheel notch = 120 units
        if notches:
            self.set_scale(self._scale * self.WHEEL_ZOOM_PER_NOTCH**notches)
            event.accept()

    # ---------------------------------------------------------- context menu
    def add_menu_section(self, builder: Callable[[QMenu], None]) -> None:
        """Let another part of the program add entries to the right-click menu.

        `builder` is called with the menu every time it is opened, so the entries always show the current state.
        """
        self._menu_sections.append(builder)

    def build_context_menu(self) -> QMenu:
        menu = QMenu(self)

        preview = menu.addMenu("Preview animation")
        for label, key in self.PREVIEW_ACTIONS:
            action = preview.addAction(label)
            action.triggered.connect(lambda _checked=False, key=key: self.previewRequested.emit(key))

        wander = menu.addAction("Walk around on its own")
        wander.setCheckable(True)
        wander.setChecked(self._wandering_enabled)
        wander.toggled.connect(self._on_wandering_toggled)
        for builder in self._menu_sections:
            builder(menu)
        menu.addSeparator()

        size_menu = menu.addMenu("Size")
        for label, preset in self.SIZE_PRESETS:
            action = size_menu.addAction(f"{label}  ({round(preset * 100)} %)")
            action.setCheckable(True)
            action.setChecked(abs(self._scale - preset) < 0.005)
            action.triggered.connect(lambda _checked=False, value=preset: self.set_scale(value))
        size_menu.addSeparator()
        size_menu.addAction("Tip: the mouse wheel also resizes").setEnabled(False)

        menu.addAction("Move to bottom-right corner", self.move_to_bottom_right)
        menu.addSeparator()
        menu.addAction("Exit", lambda: self.exitRequested.emit())
        return menu

    def _on_wandering_toggled(self, enabled: bool) -> None:
        self._wandering_enabled = enabled
        self.wanderingToggled.emit(enabled)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        self.interactionStarted.emit()
        menu = self.build_context_menu()
        self._popup(menu, event.globalPos())
        menu.deleteLater()
        self.interactionFinished.emit()

    # --------------------------------------------------------- the small menu
    def add_quick_menu_section(self, builder: Callable[[QMenu], None], order: int = 50) -> None:
        """Let another part of the program add entries to the small menu that opens when the character is clicked.

        Sections appear in the order of their `order` numbers (smaller first), each one separated from the next.
        `builder` is called with the menu every time it opens, so the entries always show the current state.
        "Exit" is always the last entry.
        """
        self._quick_sections.append((order, builder))

    def build_quick_menu(self) -> QMenu:
        menu = QMenu(self)
        for _order, builder in sorted(self._quick_sections, key=lambda section: section[0]):
            entries_before = len(menu.actions())
            builder(menu)
            if len(menu.actions()) > entries_before:
                menu.addSeparator()
        menu.addAction("Exit", lambda: self.exitRequested.emit())
        return menu

    def _open_quick_menu(self, global_pos: QPoint) -> None:
        menu = self.build_quick_menu()
        self._popup(menu, global_pos)
        menu.deleteLater()

    def _popup(self, menu: QMenu, global_pos: QPoint) -> None:
        """Show `menu` at `global_pos` and wait until it is closed. (The tests replace this: waiting would block them.)"""
        menu.exec(global_pos)
