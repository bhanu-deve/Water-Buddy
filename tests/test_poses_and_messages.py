import unittest

from water_assistant import messages
from water_assistant.poses import ATTENTION_CLIP, CELEBRATE_CLIP, TALK_CLIP, WALK_CLIP, Clip, Pose


class PosesTest(unittest.TestCase):
    def test_pose_values_are_the_frame_numbers_of_the_gif(self):
        self.assertEqual(
            [pose.value for pose in Pose],
            [0, 1, 2, 3, 4, 5, 6, 7],
        )
        self.assertEqual(Pose.STAND, 0)
        self.assertEqual(Pose.DRINK, 4)
        self.assertEqual(Pose.THUMBS_UP, 5)

    def test_every_clip_only_uses_frames_that_exist(self):
        for clip in (WALK_CLIP, TALK_CLIP, ATTENTION_CLIP, CELEBRATE_CLIP):
            self.assertTrue(all(0 <= frame <= 7 for frame in clip.frames))
            self.assertTrue(all(duration > 0 for duration in clip.durations_ms))

    def test_walk_cycle_alternates_the_feet(self):
        self.assertEqual(WALK_CLIP.frames, (Pose.STEP_RIGHT, Pose.STAND, Pose.STEP_LEFT, Pose.STAND))

    def test_clip_needs_one_duration_per_frame(self):
        with self.assertRaises(ValueError):
            Clip((Pose.STAND, Pose.POINT), (100,))
        with self.assertRaises(ValueError):
            Clip((), ())


class MessagesTest(unittest.TestCase):
    def test_reminder_messages_include_the_ones_from_the_brief(self):
        for expected in (
            "Hey! It's time to drink some water.",
            "Don't forget to stay hydrated!",
            "Come on, take a small water break.",
            "Your body needs water. Let's drink!",
        ):
            self.assertIn(expected, messages.REMINDER_MESSAGES)

    def test_there_are_plenty_of_different_short_messages(self):
        self.assertGreaterEqual(len(messages.REMINDER_MESSAGES), 6)
        self.assertEqual(len(set(messages.REMINDER_MESSAGES)), len(messages.REMINDER_MESSAGES))  # no duplicates
        for text in messages.REMINDER_MESSAGES + messages.CELEBRATION_MESSAGES + (messages.GREETING,):
            self.assertLessEqual(len(text), 70, text)  # fits in a speech bubble without becoming huge

    def test_the_briefs_praise_message_is_kept_for_after_drinking(self):
        praise = "Good job! Keep drinking water regularly."
        self.assertEqual(messages.CELEBRATION_MESSAGES[0], praise)
        self.assertNotIn(praise, messages.REMINDER_MESSAGES)  # praise before you have even drunk would be odd

    def test_messages_rotate_and_start_over(self):
        rotator = messages.MessageRotator(("a", "b", "c"))
        self.assertEqual([rotator.next() for _ in range(7)], ["a", "b", "c", "a", "b", "c", "a"])

    def test_rotator_needs_messages(self):
        with self.assertRaises(ValueError):
            messages.MessageRotator(())

    def test_reading_time_grows_with_the_text_but_has_a_minimum(self):
        self.assertEqual(messages.reading_time_seconds("Hi"), 3.0)
        self.assertGreater(messages.reading_time_seconds("x" * 120), messages.reading_time_seconds("x" * 60))


if __name__ == "__main__":
    unittest.main()
