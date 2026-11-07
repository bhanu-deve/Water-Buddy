import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from water_assistant.character_window import CharacterWindow
from water_assistant.paths import asset_path
from water_assistant.sprites import CharacterSprites

app = QApplication.instance() or QApplication([])
GIF = asset_path("character", "water_reminder.gif")


def send_mouse(widget, event_type, local_pos: QPoint, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
    """Deliver a mouse event straight to the widget (deterministic, unlike moving the real cursor)."""
    local = QPointF(local_pos)
    global_pos = QPointF(widget.mapToGlobal(local_pos))
    event = QMouseEvent(event_type, local, local, global_pos, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def move_mouse_to(widget, local_pos: QPoint, global_pos: QPoint, buttons=Qt.MouseButton.LeftButton):
    """Deliver a mouse-move event that says the mouse is now at `global_pos` on the screen."""
    local = QPointF(local_pos)
    event = QMouseEvent(QEvent.Type.MouseMove, local, local, QPointF(global_pos), Qt.MouseButton.NoButton, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def wheel(widget, notches: int):
    pos = QPointF(widget.rect().center())
    event = QWheelEvent(
        pos,
        QPointF(widget.mapToGlobal(widget.rect().center())),
        QPoint(0, 0),
        QPoint(0, 120 * notches),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


class CharacterWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sprites = CharacterSprites(GIF)

    def setUp(self):
        self.window = CharacterWindow(self.sprites)
        # Opening a real menu waits until it is closed, which would block the test: record the request instead.
        self.popped = []
        self.window._popup = lambda menu, global_pos: self.popped.append((menu, global_pos))
        self.window.move_to_bottom_right()
        self.window.show()
        self.addCleanup(self.window.close)
        # A menu that was opened is queued for deletion, and its "Exit" entry refers back to the window. If the window
        # is dropped before that deletion happens, the next event-loop spin (in ANY later test) deletes the menu, which
        # frees the window, which deletes the menu again: a crash. (The real program is safe: it keeps the window until
        # exit, and Qt carries out pending deletions right after "about to quit".) So do them now, while all exists.
        self.addCleanup(QCoreApplication.sendPostedEvents, None, QEvent.Type.DeferredDelete.value)

    # ---------------------------------------------------------- appearance
    def test_window_is_frameless_transparent_and_on_top(self):
        flags = self.window.windowFlags()
        self.assertTrue(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.WindowType.Tool)  # no taskbar button
        self.assertTrue(flags & Qt.WindowType.WindowDoesNotAcceptFocus)
        self.assertTrue(self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))

    def test_default_size_is_a_fraction_of_the_gif(self):
        self.assertEqual(self.window.scale, CharacterWindow.DEFAULT_SCALE)
        native = self.sprites.native_size
        self.assertEqual(
            self.window.size(),
            QSize(round(native.width() * CharacterWindow.DEFAULT_SCALE), round(native.height() * CharacterWindow.DEFAULT_SCALE)),
        )

    def test_starts_in_the_bottom_right_corner_above_the_taskbar(self):
        area = app.primaryScreen().availableGeometry()
        geometry = self.window.geometry()
        self.assertEqual(geometry.x() + geometry.width() + CharacterWindow.MARGIN_RIGHT, area.x() + area.width())
        self.assertEqual(geometry.y() + geometry.height() + CharacterWindow.MARGIN_BOTTOM, area.y() + area.height())

    # ------------------------------------------------------------ dragging
    def test_dragging_moves_the_window_by_the_mouse_movement(self):
        start_pos = self.window.pos()
        left = Qt.MouseButton.LeftButton
        grab = QPoint(90, 120)  # somewhere on the character
        send_mouse(self.window, QEvent.Type.MouseButtonPress, grab, left, left)
        # Each move event is given in window coordinates *after* the previous move,
        # exactly like real mouse events (the global mouse position is what matters).
        global_start = self.window.mapToGlobal(grab)
        target_global = global_start + QPoint(-100, -60)
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(grab),
            QPointF(grab),
            QPointF(target_global),
            Qt.MouseButton.NoButton,
            left,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.window, event)
        send_mouse(self.window, QEvent.Type.MouseButtonRelease, grab, left, Qt.MouseButton.NoButton)
        self.assertEqual(self.window.pos(), start_pos + QPoint(-100, -60))

    def test_window_does_not_move_when_not_dragging(self):
        start_pos = self.window.pos()
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(10, 10),
            QPointF(10, 10),
            QPointF(500, 500),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.window, event)
        self.assertEqual(self.window.pos(), start_pos)

    def test_character_cannot_be_dragged_completely_off_screen(self):
        left = Qt.MouseButton.LeftButton
        grab = QPoint(90, 120)
        send_mouse(self.window, QEvent.Type.MouseButtonPress, grab, left, left)
        for far_away in (QPointF(-9000, -9000), QPointF(9000, 9000), QPointF(-9000, 9000)):
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(grab), QPointF(grab), far_away,
                                Qt.MouseButton.NoButton, left, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(self.window, event)
            screen = app.primaryScreen().geometry()
            self.assertTrue(screen.contains(self.window.geometry().center()),
                            f"centre {self.window.geometry().center()} left the screen {screen}")
        send_mouse(self.window, QEvent.Type.MouseButtonRelease, grab, left, Qt.MouseButton.NoButton)

    # ------------------------------------------------- clicking (small menu)
    GRAB = QPoint(90, 120)  # somewhere on the character

    def click_with_wobble(self, wobble: QPoint):
        """Press on the character, move the mouse by `wobble`, let go."""
        left = Qt.MouseButton.LeftButton
        send_mouse(self.window, QEvent.Type.MouseButtonPress, self.GRAB, left, left)
        start = self.window.mapToGlobal(self.GRAB)
        if not wobble.isNull():
            move_mouse_to(self.window, self.GRAB, start + wobble)
        send_mouse(self.window, QEvent.Type.MouseButtonRelease, self.GRAB, left, Qt.MouseButton.NoButton)
        return start

    def test_a_click_opens_the_small_menu_where_the_mouse_is(self):
        clicks = []
        self.window.clicked.connect(clicks.append)
        start_pos = self.window.pos()
        where = self.click_with_wobble(QPoint(0, 0))
        self.assertEqual(clicks, [where])
        self.assertEqual(len(self.popped), 1)
        self.assertEqual(self.popped[0][1], where)
        self.assertEqual(self.window.pos(), start_pos)  # a click never nudges the character

    def test_a_wobble_of_the_hand_is_still_a_click_and_nothing_moves(self):
        start_pos = self.window.pos()
        for wobble in (QPoint(3, 0), QPoint(0, -4), QPoint(2, 3), QPoint(-5, 0)):  # all within the slop of 5
            self.popped.clear()
            self.click_with_wobble(wobble)
            self.assertEqual(len(self.popped), 1, f"wobble {wobble}")
            self.assertEqual(self.window.pos(), start_pos, f"wobble {wobble}")

    def test_moving_further_than_the_slop_is_a_drag_not_a_click(self):
        clicks = []
        self.window.clicked.connect(clicks.append)
        start_pos = self.window.pos()
        self.click_with_wobble(QPoint(6, 0))  # one more than the slop
        self.assertEqual(self.popped, [])
        self.assertEqual(clicks, [])
        self.assertEqual(self.window.pos(), start_pos + QPoint(6, 0))

    def test_the_drag_follows_the_mouse_exactly_once_it_has_started(self):
        left = Qt.MouseButton.LeftButton
        start_pos = self.window.pos()
        send_mouse(self.window, QEvent.Type.MouseButtonPress, self.GRAB, left, left)
        start = self.window.mapToGlobal(self.GRAB)
        move_mouse_to(self.window, self.GRAB, start + QPoint(4, 0))
        self.assertEqual(self.window.pos(), start_pos)  # still within the slop: nothing moves
        move_mouse_to(self.window, self.GRAB, start + QPoint(-40, -25))
        self.assertEqual(self.window.pos(), start_pos + QPoint(-40, -25))  # no leftover offset from the slop
        send_mouse(self.window, QEvent.Type.MouseButtonRelease, self.GRAB, left, Qt.MouseButton.NoButton)
        self.assertEqual(self.popped, [])

    def test_a_right_click_is_not_a_click(self):
        clicks = []
        self.window.clicked.connect(clicks.append)
        right = Qt.MouseButton.RightButton
        send_mouse(self.window, QEvent.Type.MouseButtonPress, self.GRAB, right, right)
        send_mouse(self.window, QEvent.Type.MouseButtonRelease, self.GRAB, right, Qt.MouseButton.NoButton)
        self.assertEqual(clicks, [])
        self.assertEqual(self.popped, [])

    def test_the_interaction_signals_surround_the_click_and_its_menu(self):
        order = []
        self.window.interactionStarted.connect(lambda: order.append("grabbed"))
        self.window.clicked.connect(lambda _pos: order.append("clicked"))
        self.window.interactionFinished.connect(lambda: order.append("released"))
        self.window._popup = lambda menu, pos: order.append("menu open")
        self.click_with_wobble(QPoint(0, 0))
        self.assertEqual(order, ["grabbed", "clicked", "menu open", "released"])

    def test_losing_the_button_release_ends_the_press_without_a_click(self):
        # Qt throws away button-less mouse moves unless mouse tracking is on, so switch it on to reach this safety net.
        self.window.setMouseTracking(True)
        order = []
        self.window.interactionStarted.connect(lambda: order.append("grabbed"))
        self.window.interactionFinished.connect(lambda: order.append("released"))
        left = Qt.MouseButton.LeftButton
        send_mouse(self.window, QEvent.Type.MouseButtonPress, self.GRAB, left, left)
        move_mouse_to(self.window, self.GRAB, self.window.mapToGlobal(self.GRAB), buttons=Qt.MouseButton.NoButton)  # button is up
        self.assertEqual(order, ["grabbed", "released"])
        self.assertEqual(self.popped, [])
        start_pos = self.window.pos()
        move_mouse_to(self.window, self.GRAB, self.window.mapToGlobal(self.GRAB) + QPoint(50, 0))  # no longer dragging
        self.assertEqual(self.window.pos(), start_pos)

    def test_the_small_menu_without_sections_is_just_exit(self):
        menu = self.window.build_quick_menu()
        self.assertEqual([action.text() for action in menu.actions()], ["Exit"])

    def test_the_small_menu_shows_the_sections_in_order_and_exit_last(self):
        self.window.add_quick_menu_section(lambda menu: menu.addAction("C1"), order=30)
        self.window.add_quick_menu_section(lambda menu: (menu.addAction("A1"), menu.addAction("A2")), order=10)
        self.window.add_quick_menu_section(lambda menu: menu.addAction("B1"), order=20)
        self.window.add_quick_menu_section(lambda menu: None, order=5)  # a section with nothing to show adds nothing
        menu = self.window.build_quick_menu()
        shown = ["-" if action.isSeparator() else action.text() for action in menu.actions()]
        self.assertEqual(shown, ["A1", "A2", "-", "B1", "-", "C1", "-", "Exit"])

    def test_sections_with_the_same_order_keep_the_order_they_were_added_in(self):
        self.window.add_quick_menu_section(lambda menu: menu.addAction("first"))
        self.window.add_quick_menu_section(lambda menu: menu.addAction("second"))
        shown = [action.text() for action in self.window.build_quick_menu().actions() if not action.isSeparator()]
        self.assertEqual(shown, ["first", "second", "Exit"])

    def test_the_small_menu_is_rebuilt_every_time_it_opens(self):
        built = []
        self.window.add_quick_menu_section(lambda menu: built.append(menu))
        self.click_with_wobble(QPoint(0, 0))
        self.click_with_wobble(QPoint(0, 0))
        self.assertEqual(len(built), 2)

    def test_exit_in_the_small_menu_asks_the_application_to_exit(self):
        requested = []
        self.window.exitRequested.connect(lambda: requested.append(True))
        next(a for a in self.window.build_quick_menu().actions() if a.text() == "Exit").trigger()
        self.assertEqual(requested, [True])

    def test_the_right_click_menu_opens_through_the_same_popup_and_reports_the_interaction(self):
        order = []
        self.window.interactionStarted.connect(lambda: order.append("grabbed"))
        self.window.interactionFinished.connect(lambda: order.append("released"))
        where = self.window.mapToGlobal(self.GRAB)
        QApplication.sendEvent(self.window, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, self.GRAB, where))
        self.assertEqual(len(self.popped), 1)
        self.assertEqual(self.popped[0][1], where)
        self.assertEqual(order, ["grabbed", "released"])
        self.assertIn("Preview animation", [a.text() for a in self.popped[0][0].actions()])  # it IS the right-click menu

    # ------------------------------------------------------------ resizing
    def test_mouse_wheel_resizes_and_keeps_the_feet_in_place(self):
        before = self.window.geometry()
        feet_x = before.x() + before.width() / 2
        feet_y = before.y() + before.height()

        wheel(self.window, +1)

        after = self.window.geometry()
        self.assertEqual(self.window.scale, round(CharacterWindow.DEFAULT_SCALE * CharacterWindow.WHEEL_ZOOM_PER_NOTCH, 2))
        self.assertGreater(after.width(), before.width())
        self.assertAlmostEqual(after.x() + after.width() / 2, feet_x, delta=1)
        self.assertEqual(after.y() + after.height(), feet_y)

        wheel(self.window, -1)
        self.assertEqual(self.window.scale, CharacterWindow.DEFAULT_SCALE)

    def test_size_is_limited(self):
        self.window.set_scale(50)
        self.assertEqual(self.window.scale, CharacterWindow.MAX_SCALE)
        self.window.set_scale(0.001)
        self.assertEqual(self.window.scale, CharacterWindow.MIN_SCALE)

    def test_window_size_follows_the_scale(self):
        self.window.set_scale(1.0)
        self.assertEqual(self.window.size(), self.sprites.native_size)

    # ------------------------------------------------- frames, pose, signals
    def test_set_frame_changes_what_is_drawn(self):
        before = self.window.grab().toImage()
        self.window.set_frame(4)  # drinking
        self.assertEqual(self.window.frame_index, 4)
        self.assertNotEqual(self.window.grab().toImage(), before)

    def test_squash_and_lean_change_the_picture_around_the_feet(self):
        before = self.window.grab().toImage()
        self.window.set_pose(1.02, 0.95, 3.0)
        self.assertEqual(self.window.pose, (1.02, 0.95, 3.0))
        self.assertNotEqual(self.window.grab().toImage(), before)
        self.window.set_pose()  # back to normal
        self.assertEqual(self.window.pose, (1.0, 1.0, 0.0))
        self.assertEqual(self.window.grab().toImage(), before)

    def test_signals_report_moving_resizing_and_dragging(self):
        events = []
        self.window.geometryChanged.connect(lambda: events.append("geometry"))
        self.window.scaleChanged.connect(lambda scale: events.append(("scale", scale)))
        self.window.interactionStarted.connect(lambda: events.append("grabbed"))
        self.window.interactionFinished.connect(lambda: events.append("released"))

        self.window.move(self.window.x() - 10, self.window.y())
        self.assertIn("geometry", events)

        events.clear()
        self.window.set_scale(0.6)
        self.assertIn(("scale", 0.6), events)

        events.clear()
        left = Qt.MouseButton.LeftButton
        send_mouse(self.window, QEvent.Type.MouseButtonPress, QPoint(90, 120), left, left)
        send_mouse(self.window, QEvent.Type.MouseButtonRelease, QPoint(90, 120), left, Qt.MouseButton.NoButton)
        self.assertEqual([e for e in events if isinstance(e, str) and e != "geometry"], ["grabbed", "released"])

    def test_available_area_is_the_usable_part_of_the_screen(self):
        self.assertEqual(self.window.available_area(), app.primaryScreen().availableGeometry())

    # ---------------------------------------------------------------- menu
    def test_menu_has_preview_wander_size_move_and_exit_entries(self):
        menu = self.window.build_context_menu()
        titles = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(
            titles,
            ["Preview animation", "Walk around on its own", "Size", "Move to bottom-right corner", "Exit"],
        )

    def test_other_parts_of_the_program_can_add_menu_entries(self):
        seen = []
        self.window.add_menu_section(lambda menu: (seen.append(menu), menu.addAction("Extra entry")))
        menu = self.window.build_context_menu()
        titles = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertEqual(
            titles,
            ["Preview animation", "Walk around on its own", "Extra entry", "Size", "Move to bottom-right corner", "Exit"],
        )
        self.window.build_context_menu()
        self.assertEqual(len(seen), 2)  # the section is rebuilt every time the menu opens

    def test_preview_menu_asks_for_every_animation(self):
        keys = []
        self.window.previewRequested.connect(keys.append)
        menu = self.window.build_context_menu()
        preview = next(a for a in menu.actions() if a.text() == "Preview animation").menu()
        for action in preview.actions():
            action.trigger()
        self.assertEqual(keys, ["idle", "walk", "reminder", "talk", "drink", "celebrate", "original_gif"])

    def test_wandering_switch_in_the_menu(self):
        changes = []
        self.window.wanderingToggled.connect(changes.append)
        self.assertTrue(self.window.wandering_enabled)
        menu = self.window.build_context_menu()
        wander = next(a for a in menu.actions() if a.text() == "Walk around on its own")
        self.assertTrue(wander.isChecked())
        wander.trigger()  # un-check
        self.assertFalse(self.window.wandering_enabled)
        self.assertEqual(changes, [False])

    def test_size_menu_marks_the_current_size_and_applies_a_choice(self):
        menu = self.window.build_context_menu()
        size_menu = next(a for a in menu.actions() if a.text() == "Size").menu()
        presets = [a for a in size_menu.actions() if a.isCheckable()]
        self.assertEqual([a.isChecked() for a in presets], [False, True, False, False])  # "Medium" = the default size
        next(a for a in presets if a.text().startswith("Large")).trigger()
        self.assertEqual(self.window.scale, 0.6)

    def test_exit_entry_asks_the_application_to_exit(self):
        requested = []
        self.window.exitRequested.connect(lambda: requested.append(True))
        next(a for a in self.window.build_context_menu().actions() if a.text() == "Exit").trigger()
        self.assertEqual(requested, [True])


if __name__ == "__main__":
    unittest.main()
