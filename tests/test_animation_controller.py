import random
import time
import unittest

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from water_assistant import messages
from water_assistant.animation_controller import MIN_WALK_PX, AnimationController, State, Timing, _trapezoid
from water_assistant.character_window import CharacterWindow
from water_assistant.paths import asset_path
from water_assistant.poses import Pose
from water_assistant.speech_bubble import SpeechBubble
from water_assistant.sprites import CharacterSprites

app = QApplication.instance() or QApplication([])
GIF = asset_path("character", "water_reminder.gif")


class ControllerTestCase(unittest.TestCase):
    """Builds a real window + bubble + controller (offscreen) running 5x faster than normal."""

    timing = Timing(scale=0.2)

    @classmethod
    def setUpClass(cls):
        cls.sprites = CharacterSprites(GIF)

    def setUp(self):
        self.window = CharacterWindow(self.sprites)
        self.bubble = SpeechBubble()
        self.controller = AnimationController(self.window, self.bubble, self.timing, random.Random(7))
        self.window.move_to_bottom_right()
        self.window.show()

        self.states: list[State] = []
        self.frames: list[int] = []
        self.entries: list[tuple[State, int]] = []  # (state, how many frames had been shown when it began)
        self.controller.stateChanged.connect(self.states.append)
        self.controller.stateChanged.connect(lambda state: self.entries.append((state, len(self.frames))))

        real_set_frame = self.window.set_frame

        def recording_set_frame(index):  # remembers every frame the controller asks for
            self.frames.append(int(index))
            real_set_frame(index)

        self.window.set_frame = recording_set_frame
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.controller.stop()
        self.bubble.close()
        self.window.close()

    # ------------------------------------------------------------- helpers
    def wait_until(self, condition, timeout_ms=5000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if condition():
                return True
            QTest.qWait(5)
        return bool(condition())

    def wait_for_state(self, state, timeout_ms=5000):
        return self.wait_until(lambda: self.controller.state is state, timeout_ms)

    def frames_in(self, state):
        """The frames shown during the first time the controller was in `state`."""
        for position, (entered, first) in enumerate(self.entries):
            if entered is state:
                end = self.entries[position + 1][1] if position + 1 < len(self.entries) else len(self.frames)
                return self.frames[first:end]
        return []


class IdleTest(ControllerTestCase):
    def test_idle_shows_the_standing_pose(self):
        self.controller.start(greet=False)
        self.assertIs(self.controller.state, State.IDLE)
        self.assertEqual(self.frames[-1], Pose.STAND)

    def test_breathing_is_subtle_and_can_never_grow_out_of_the_window(self):
        self.controller.start(greet=False)
        for step in range(200):
            scale_x, scale_y, lean = self.controller._procedural_pose(self.controller._state_started + step * 0.1)
            self.assertTrue(0.985 <= scale_y <= 1.0, scale_y)  # it only ever squashes a little, never stretches
            self.assertTrue(1.0 <= scale_x <= 1.005, scale_x)
            self.assertLessEqual(abs(lean), 0.5 + 1e-9)

    def test_it_holds_still_while_the_user_is_using_it(self):
        self.controller.start(greet=False)
        self.window.interactionStarted.emit()
        self.assertEqual(self.controller._procedural_pose(self.controller._state_started + 1.3), (1.0, 1.0, 0.0))

    def test_the_pose_is_really_applied_to_the_window_by_the_timer(self):
        self.controller.start(greet=False)
        self.assertTrue(self.wait_until(lambda: self.window.pose != (1.0, 1.0, 0.0), 2000))


class GreetingTest(ControllerTestCase):
    def test_greeting_shows_the_bubble_then_goes_back_to_idle_and_hides_it(self):
        self.controller.start(greet=True)
        self.assertIs(self.controller.state, State.TALKING)
        self.assertEqual(self.bubble.text, messages.GREETING)
        self.assertTrue(self.bubble.isVisible())
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertTrue(self.wait_until(lambda: not self.bubble.isVisible()))


class WalkingTest(ControllerTestCase):
    def test_walking_moves_smoothly_to_the_target_using_the_step_frames(self):
        self.controller.start(greet=False)
        start = self.window.pos()
        target_x = start.x() - 300
        self.controller.walk_to(target_x)
        self.assertIs(self.controller.state, State.WALKING)

        xs = []
        while self.controller.state is State.WALKING:
            QTest.qWait(4)
            xs.append(self.window.x())

        self.assertEqual(self.window.x(), target_x)
        self.assertEqual(self.window.y(), start.y())  # it walks along the ground, never up or down
        self.assertEqual(xs, sorted(xs, reverse=True))  # always heading left, never stepping back
        self.assertGreater(len(set(xs)), 10)  # many intermediate positions = smooth, not a jump
        self.assertEqual(self.states, [State.IDLE, State.WALKING, State.IDLE])
        self.assertEqual(set(self.frames_in(State.WALKING)), {Pose.STEP_RIGHT, Pose.STAND, Pose.STEP_LEFT})
        self.assertEqual(self.frames[-1], Pose.STAND)  # and it stands again at the end

    def test_walking_never_leaves_the_screen(self):
        self.controller.start(greet=False)
        left, right = self.controller._walk_limits()
        area = self.window.available_area()

        self.controller.walk_to(-100000)
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertEqual(self.window.x(), left)
        self.assertGreaterEqual(self.window.geometry().left(), area.left())

        self.controller.walk_to(+100000)
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertEqual(self.window.x(), right)
        self.assertLessEqual(self.window.geometry().right(), area.right())

    def test_walk_across_goes_to_the_far_side(self):
        self.controller.start(greet=False)  # it starts at the right edge
        left, right = self.controller._walk_limits()
        self.controller.walk_across()
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertEqual(self.window.x(), left)
        self.controller.walk_across()
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertEqual(self.window.x(), right)

    def test_a_tiny_walk_is_skipped(self):
        self.controller.start(greet=False)
        start_x = self.window.x()
        self.controller.walk_to(start_x - (MIN_WALK_PX - 1))
        self.assertIs(self.controller.state, State.IDLE)
        self.assertNotIn(State.WALKING, self.states)
        self.assertEqual(self.window.x(), start_x)

    def test_a_new_command_cancels_the_walk(self):
        self.controller.start(greet=False)
        self.controller.walk_across()
        self.assertTrue(self.wait_until(lambda: self.window.x() < self.controller._walk_limits()[1] - 20))
        self.controller.say("Stop!")
        self.assertIs(self.controller.state, State.TALKING)
        x = self.window.x()
        QTest.qWait(250)
        self.assertEqual(self.window.x(), x)  # the old walk is really gone

    def test_walk_speed_follows_the_character_size(self):
        self.controller.start(greet=False)
        self.controller.walk_to(self.window.x() - 300)
        small = self.controller._anim.duration()
        self.controller.go_idle()
        self.window.move_to_bottom_right()
        self.window.set_scale(0.8)
        self.window.move_to_bottom_right()
        self.controller.walk_to(self.window.x() - 300)
        big = self.controller._anim.duration()
        self.assertAlmostEqual(big, small / 2, delta=2)  # twice as big -> twice as fast


class ReminderTest(ControllerTestCase):
    def test_reminder_hops_points_then_talks_then_goes_back_to_idle(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.controller.remind("Drink up!")
        self.assertIs(self.controller.state, State.REMINDER)

        heights = []
        while self.controller.state is State.REMINDER:
            QTest.qWait(3)
            heights.append(self.window.y())

        self.assertLess(min(heights), base.y())  # it hopped...
        self.assertEqual(self.window.pos(), base)  # ...and landed exactly where it was
        self.assertEqual(set(self.frames_in(State.REMINDER)), {Pose.POINT, Pose.SIGN_DRINK_WATER})
        self.assertIs(self.controller.state, State.TALKING)
        self.assertEqual(self.bubble.text, "Drink up!")
        self.assertTrue(self.bubble.isVisible())
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertEqual(self.states, [State.IDLE, State.REMINDER, State.TALKING, State.IDLE])

    def test_reminder_messages_rotate(self):
        self.controller.start(greet=False)
        spoken = []
        for _ in range(len(messages.REMINDER_MESSAGES) + 1):
            self.controller.remind()
            self.assertTrue(self.wait_for_state(State.TALKING))
            spoken.append(self.bubble.text)
            self.controller.go_idle()
        self.assertEqual(spoken[:-1], list(messages.REMINDER_MESSAGES))
        self.assertEqual(spoken[-1], messages.REMINDER_MESSAGES[0])  # and round again

    def test_a_cancelled_hop_puts_the_character_back(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.controller.remind("x")
        self.assertTrue(self.wait_until(lambda: self.window.y() < base.y()))  # it is in the air
        self.controller.go_idle()
        self.assertEqual(self.window.pos(), base)

    def test_grabbing_the_character_mid_hop_does_not_snap_it_back(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.controller.remind("x")
        self.assertTrue(self.wait_until(lambda: self.window.y() < base.y()))
        self.window.interactionStarted.emit()  # the user takes hold of it
        y = self.window.y()
        QTest.qWait(200)
        self.assertEqual(self.window.y(), y)  # the controller no longer moves it


class TalkingTest(ControllerTestCase):
    def test_say_shows_the_bubble_while_gesturing_then_hides_it(self):
        self.controller.start(greet=False)
        self.controller.say("Hello!", seconds=1.0)
        self.assertIs(self.controller.state, State.TALKING)
        self.assertEqual(self.bubble.text, "Hello!")
        self.assertTrue(self.bubble.isVisible())
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertTrue(set(self.frames_in(State.TALKING)) <= {Pose.POINT, Pose.STAND, Pose.STEP_LEFT})
        self.assertTrue(self.wait_until(lambda: not self.bubble.isVisible()))

    def test_the_bubble_follows_the_character(self):
        self.controller.start(greet=False)
        self.controller.say("Hello!", seconds=5.0)
        x = self.bubble.x()
        self.window.move(self.window.x() - 40, self.window.y())
        self.assertEqual(self.bubble.x(), x - 40)

    def test_the_bubble_is_hidden_when_another_state_takes_over(self):
        self.controller.start(greet=False)
        self.controller.say("Hello!", seconds=5.0)
        self.controller.drink()
        self.assertTrue(self.wait_until(lambda: not self.bubble.isVisible()))


class DrinkingAndCelebratingTest(ControllerTestCase):
    def test_drinking_then_celebrating_with_a_message(self):
        self.controller.start(greet=False)
        self.controller.drink(then=lambda: self.controller.celebrate("Nice!"))
        self.assertIs(self.controller.state, State.DRINKING)
        self.assertEqual(self.window.frame_index, Pose.DRINK)

        self.assertTrue(self.wait_for_state(State.CELEBRATING))
        self.assertEqual(self.bubble.text, "Nice!")
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertEqual(self.states, [State.IDLE, State.DRINKING, State.CELEBRATING, State.IDLE])
        self.assertEqual(self.frames_in(State.DRINKING), [Pose.DRINK])
        self.assertEqual(set(self.frames_in(State.CELEBRATING)), {Pose.THUMBS_UP, Pose.SIGN_STAY_HYDRATED})

    def test_celebrating_without_a_message_shows_no_bubble(self):
        self.controller.start(greet=False)
        self.controller.celebrate()
        self.assertFalse(self.bubble.isVisible())
        self.assertTrue(self.wait_for_state(State.IDLE))

    def test_celebrating_hops_and_lands_where_it_started(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.controller.celebrate()
        self.assertTrue(self.wait_until(lambda: self.window.y() < base.y()))
        self.assertTrue(self.wait_for_state(State.IDLE))
        self.assertEqual(self.window.pos(), base)


class OriginalGifTest(ControllerTestCase):
    def test_plays_all_eight_frames_in_order_twice_then_idles(self):
        self.controller.start(greet=False)
        self.controller.play_original_gif()
        self.assertIs(self.controller.state, State.SHOWCASE)
        # the original frames are shown untouched: no squash, no lean
        self.assertEqual(self.controller._procedural_pose(time.monotonic()), (1.0, 1.0, 0.0))
        self.assertTrue(self.wait_for_state(State.IDLE, timeout_ms=8000))
        self.assertEqual(self.frames_in(State.SHOWCASE), list(range(8)) * 2)


class WanderingTest(ControllerTestCase):
    timing = Timing(scale=0.2, first_wander_s=0.5, wander_gap_s=(0.5, 0.8))

    def test_it_strolls_across_the_screen_by_itself_and_comes_back_to_idle(self):
        self.controller.start(greet=False)
        self.assertTrue(self.wait_for_state(State.WALKING, 3000))
        self.assertTrue(self.wait_for_state(State.IDLE, 5000))
        left, right = self.controller._walk_limits()
        self.assertTrue(left <= self.window.x() <= right)

    def test_every_stroll_is_long_enough_to_be_worth_it(self):
        self.controller.start(greet=False)
        for _ in range(5):
            here = self.window.x()
            target = self.controller._pick_wander_target()
            self.assertTrue(target is None or abs(target - here) >= 120)

    def test_wandering_can_be_switched_off_and_on(self):
        self.controller.set_wandering_enabled(False)
        self.controller.start(greet=False)
        QTest.qWait(500)  # five times longer than the first stroll would take
        self.assertNotIn(State.WALKING, self.states)

        self.window.wanderingToggled.emit(True)  # what the menu does
        self.assertTrue(self.window.wandering_enabled)
        self.assertTrue(self.wait_for_state(State.WALKING, 3000))

    def test_no_stroll_while_the_user_holds_the_character_or_has_its_menu_open(self):
        self.controller.start(greet=False)
        self.window.interactionStarted.emit()
        QTest.qWait(500)
        self.assertNotIn(State.WALKING, self.states)
        self.window.interactionFinished.emit()
        self.assertTrue(self.wait_for_state(State.WALKING, 3000))


class InteractionTest(ControllerTestCase):
    def test_grabbing_the_character_stops_a_walk_at_once(self):
        self.controller.start(greet=False)
        self.controller.walk_across()
        right = self.controller._walk_limits()[1]
        self.assertTrue(self.wait_until(lambda: self.window.x() < right - 30))
        self.window.interactionStarted.emit()
        self.assertIs(self.controller.state, State.IDLE)
        x = self.window.x()
        QTest.qWait(250)
        self.assertEqual(self.window.x(), x)

    def test_resizing_stops_a_walk(self):
        self.controller.start(greet=False)
        self.controller.walk_across()
        right = self.controller._walk_limits()[1]
        self.assertTrue(self.wait_until(lambda: self.window.x() < right - 30))
        self.window.set_scale(0.6)
        self.assertIs(self.controller.state, State.IDLE)

    def test_the_bubble_follows_while_the_user_drags(self):
        self.controller.start(greet=False)
        self.controller.say("Hello!", seconds=5.0)
        x = self.bubble.x()
        self.window.interactionStarted.emit()
        self.window.move(self.window.x() - 25, self.window.y())
        self.assertEqual(self.bubble.x(), x - 25)


class ScheduledReminderBehaviourTest(ControllerTestCase):
    """What the character does when a reminder comes due at an awkward moment."""

    def test_a_reminder_waits_while_the_user_has_hold_of_the_character(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.window.interactionStarted.emit()  # the mouse button is down on the character
        self.controller.remind("Drink up!")
        QTest.qWait(300)
        self.assertIs(self.controller.state, State.IDLE)
        self.assertNotIn(State.REMINDER, self.states)
        self.assertFalse(self.bubble.isVisible())
        self.assertEqual(self.window.pos(), base)  # nothing hops out from under the mouse
        self.window.interactionFinished.emit()  # the mouse button is up
        self.assertIs(self.controller.state, State.REMINDER)
        self.assertTrue(self.wait_for_state(State.TALKING))
        self.assertEqual(self.bubble.text, "Drink up!")

    def test_only_the_latest_reminder_that_came_due_meanwhile_is_given(self):
        self.controller.start(greet=False)
        self.window.interactionStarted.emit()
        self.controller.remind("first")
        self.controller.remind("second")
        self.window.interactionFinished.emit()
        self.assertTrue(self.wait_for_state(State.TALKING))
        self.assertEqual(self.bubble.text, "second")
        self.assertEqual(self.states.count(State.REMINDER), 1)

    def test_a_reminder_shows_a_hidden_character(self):
        self.controller.start(greet=False)
        self.window.hide()
        self.assertFalse(self.window.isVisible())
        self.controller.remind("Drink up!")
        self.assertTrue(self.window.isVisible())
        self.assertIs(self.controller.state, State.REMINDER)

    def test_a_reminder_stops_a_walk_and_the_character_stays_where_it_is(self):
        self.controller.start(greet=False)
        self.controller.walk_across()
        right = self.controller._walk_limits()[1]
        self.assertTrue(self.wait_until(lambda: self.window.x() < right - 30))
        self.controller.remind("Drink up!")
        self.assertIs(self.controller.state, State.REMINDER)
        x = self.window.x()
        QTest.qWait(250)
        self.assertEqual(self.window.x(), x)  # it hops on the spot, it does not carry on walking

    def test_clicking_the_character_in_mid_hop_puts_it_back_on_the_ground(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.controller.remind("x")
        self.assertTrue(self.wait_until(lambda: self.window.y() < base.y()))  # in the air
        self.window.interactionStarted.emit()
        self.window.clicked.emit(QPoint(0, 0))
        self.assertEqual(self.window.pos(), base)

    def test_a_menu_opened_in_mid_hop_puts_the_character_back_when_it_closes(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.controller.remind("x")
        self.assertTrue(self.wait_until(lambda: self.window.y() < base.y()))
        self.window.interactionStarted.emit()  # the right-click menu opens...
        self.window.interactionFinished.emit()  # ...and closes without anything having been dragged
        self.assertEqual(self.window.pos(), base)

    def test_dragging_the_character_in_mid_hop_leaves_it_where_it_was_dropped(self):
        self.controller.start(greet=False)
        base = self.window.pos()
        self.controller.remind("x")
        self.assertTrue(self.wait_until(lambda: self.window.y() < base.y()))
        self.window.interactionStarted.emit()
        dropped = self.window.pos() + QPoint(-120, -40)
        self.window.move(dropped)  # the user drags it somewhere else
        self.window.interactionFinished.emit()
        self.assertEqual(self.window.pos(), dropped)  # it is NOT pulled back to where the hop began

    def test_the_stop_of_the_controller_forgets_a_waiting_reminder(self):
        self.controller.start(greet=False)
        self.window.interactionStarted.emit()
        self.controller.remind("x")
        self.controller.stop()
        self.window.interactionFinished.emit()
        self.assertNotIn(State.REMINDER, self.states)


class PreviewMenuTest(ControllerTestCase):
    def test_every_preview_entry_starts_the_matching_state(self):
        self.controller.start(greet=False)
        expected = {
            "walk": State.WALKING,
            "reminder": State.REMINDER,
            "talk": State.TALKING,
            "drink": State.DRINKING,
            "celebrate": State.CELEBRATING,
            "original_gif": State.SHOWCASE,
            "idle": State.IDLE,
        }
        for _label, key in CharacterWindow.PREVIEW_ACTIONS:
            self.window.previewRequested.emit(key)
            self.assertIs(self.controller.state, expected[key], key)

    def test_an_unknown_preview_key_is_ignored(self):
        self.controller.start(greet=False)
        self.window.previewRequested.emit("no-such-animation")
        self.assertIs(self.controller.state, State.IDLE)


class MotionProfileTest(unittest.TestCase):
    def test_walk_speeds_up_cruises_and_slows_down(self):
        values = [_trapezoid(i / 200) for i in range(201)]
        self.assertEqual(values[0], 0.0)
        self.assertAlmostEqual(values[-1], 1.0)
        self.assertTrue(all(later >= earlier for earlier, later in zip(values, values[1:])))
        self.assertAlmostEqual(_trapezoid(0.5), 0.5)
        self.assertAlmostEqual(_trapezoid(0.3) + _trapezoid(0.7), 1.0)  # symmetric
        self.assertLess(_trapezoid(0.02), 0.01)  # starts gently
        self.assertGreater(_trapezoid(0.98), 0.99)  # stops gently

    def test_values_outside_the_range_are_clamped(self):
        self.assertEqual(_trapezoid(-1), 0.0)
        self.assertEqual(_trapezoid(2), 1.0)


if __name__ == "__main__":
    unittest.main()
