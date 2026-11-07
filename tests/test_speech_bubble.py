import unittest

from PySide6.QtCore import QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from water_assistant.speech_bubble import SpeechBubble

app = QApplication.instance() or QApplication([])
SCREEN = app.primaryScreen().availableGeometry()  # 800x600 with the offscreen platform


class SpeechBubbleTest(unittest.TestCase):
    def setUp(self):
        self.bubble = SpeechBubble()
        self.addCleanup(self.bubble.close)
        # a 153x204 character standing in the middle of the screen
        self.character = QRect(SCREEN.center().x() - 76, SCREEN.center().y() - 50, 153, 204)

    def test_is_a_click_through_overlay_that_never_takes_focus(self):
        flags = self.bubble.windowFlags()
        for flag in (
            Qt.WindowType.FramelessWindowHint,
            Qt.WindowType.WindowStaysOnTopHint,
            Qt.WindowType.Tool,
            Qt.WindowType.WindowDoesNotAcceptFocus,
            Qt.WindowType.WindowTransparentForInput,
        ):
            self.assertTrue(flags & flag, flag)
        self.assertTrue(self.bubble.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))

    def test_sits_above_the_character_with_the_tail_pointing_at_its_head(self):
        self.bubble.show_text("Hello there!", self.character)
        geometry = self.bubble.geometry()
        self.assertTrue(self.bubble.tail_points_down)
        # the tail tip is a few pixels above the top of the character's window...
        tip_y = geometry.y() + geometry.height() - SpeechBubble.MARGIN
        self.assertEqual(tip_y, self.character.top() - SpeechBubble.GAP)
        # ...and exactly over its middle
        self.assertEqual(self.bubble.tail_tip_x, self.character.center().x())

    def test_longer_text_gives_a_bigger_bubble_that_wraps_at_the_width_limit(self):
        self.bubble.show_text("Hi!", self.character)
        short = self.bubble.size()
        self.bubble.show_text("Your body needs water. Let's drink! " * 4, self.character)
        long = self.bubble.size()
        self.assertGreater(long.height(), short.height())
        limit = SpeechBubble.MAX_TEXT_WIDTH + 2 * SpeechBubble.PAD_X + 2 * SpeechBubble.MARGIN + 4
        self.assertLessEqual(long.width(), limit)

    def test_stays_on_screen_but_still_points_at_the_head_near_the_right_edge(self):
        character = QRect(SCREEN.right() - 153 - 8, SCREEN.bottom() - 204, 153, 204)
        self.bubble.show_text("Don't forget to stay hydrated!", character)
        self.assertLessEqual(self.bubble.geometry().right(), SCREEN.right())
        self.assertGreaterEqual(self.bubble.geometry().left(), SCREEN.left())
        self.assertEqual(self.bubble.tail_tip_x, character.center().x())

    def test_stays_on_screen_near_the_left_edge(self):
        character = QRect(SCREEN.left(), SCREEN.bottom() - 204, 153, 204)
        self.bubble.show_text("Come on, take a small water break.", character)
        self.assertGreaterEqual(self.bubble.geometry().left(), SCREEN.left())

    def test_goes_below_the_character_when_there_is_no_room_above(self):
        character = QRect(SCREEN.center().x() - 76, SCREEN.top() + 4, 153, 204)  # at the very top
        self.bubble.show_text("Hello there!", character)
        self.assertFalse(self.bubble.tail_points_down)
        self.assertGreater(self.bubble.geometry().top(), character.top())
        self.assertGreaterEqual(self.bubble.geometry().top(), character.bottom() - SpeechBubble.MARGIN)

    def test_follows_the_character_when_it_moves(self):
        self.bubble.show_text("Hello there!", self.character)
        x_before = self.bubble.x()
        self.bubble.follow(self.character.translated(-40, 10))
        self.assertEqual(self.bubble.x(), x_before - 40)
        self.assertEqual(self.bubble.tail_tip_x, self.character.center().x() - 40)

    def test_a_hidden_bubble_ignores_follow(self):
        self.bubble.follow(self.character)
        self.assertFalse(self.bubble.isVisible())

    def test_new_text_replaces_the_old_text(self):
        self.bubble.show_text("first", self.character)
        self.bubble.show_text("second", self.character)
        self.assertEqual(self.bubble.text, "second")
        self.assertTrue(self.bubble.isVisible())

    def test_hides_immediately_or_with_a_fade(self):
        self.bubble.show_text("Hello there!", self.character)
        self.assertTrue(self.bubble.isVisible())
        self.bubble.hide_bubble(animated=False)
        self.assertFalse(self.bubble.isVisible())

        self.bubble.show_text("Hello again!", self.character)
        self.bubble.hide_bubble()  # fades out over a fraction of a second
        QTest.qWait(SpeechBubble.FADE_OUT_MS + 250)
        self.assertFalse(self.bubble.isVisible())

    def test_showing_again_during_a_fade_out_keeps_the_bubble(self):
        self.bubble.show_text("one", self.character)
        QTest.qWait(SpeechBubble.FADE_IN_MS + 50)
        self.bubble.hide_bubble()
        QTest.qWait(60)  # fade-out is under way
        self.bubble.show_text("two", self.character)
        QTest.qWait(SpeechBubble.FADE_OUT_MS + SpeechBubble.FADE_IN_MS + 150)
        self.assertTrue(self.bubble.isVisible())
        self.assertGreater(self.bubble.windowOpacity(), 0.9)

    def test_the_bubble_is_actually_painted(self):
        self.bubble.show_text("Hello there!", self.character)
        image = self.bubble.grab().toImage()
        colours = {image.pixelColor(x, y).rgb() for x in range(0, image.width(), 3) for y in range(0, image.height(), 3)}
        self.assertGreater(len(colours), 4)  # transparent background, white fill, blue outline, text, ...


if __name__ == "__main__":
    unittest.main()
