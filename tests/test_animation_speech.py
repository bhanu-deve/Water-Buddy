"""The animation controller together with a (silent, fake) speech engine."""

import random
import time
import unittest

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from water_assistant import messages
from water_assistant.animation_controller import AnimationController, State, Timing
from water_assistant.character_window import CharacterWindow
from water_assistant.paths import asset_path
from water_assistant.speech_bubble import SpeechBubble
from water_assistant.speech_service import SpeechService
from water_assistant.sprites import CharacterSprites

from .fakes import FakeBackend

app = QApplication.instance() or QApplication([])
GIF = asset_path("character", "water_reminder.gif")

# Everything runs 5x faster than normal: the minimum reading time of a bubble is 3.0 s * 0.2 = 0.6 s and the pause
# after the voice is 0.5 s * 0.2 = 0.1 s. The fake voices talk in real time (not sped up), so the safety net that
# gives up on a voice that never answers must stay far away: (3.0 + 25) * 0.2 = 5.6 s.
FAST = Timing(scale=0.2, speech_pause_s=0.5, first_wander_s=1000.0)
# Only for the test of that safety net: it fires after (3.0 + 2.0) * 0.2 = 1.0 s.
IMPATIENT = Timing(scale=0.2, speech_pause_s=0.5, speech_timeout_s=2.0, first_wander_s=1000.0)


class SpeakingControllerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sprites = CharacterSprites(GIF)

    def build(self, backend=None, *, timing=FAST, **speech_settings):
        self.backend = backend or FakeBackend()
        self.speech = SpeechService(self.backend.factory(), **speech_settings)
        self.window = CharacterWindow(self.sprites)
        self.bubble = SpeechBubble()
        self.controller = AnimationController(self.window, self.bubble, timing, random.Random(3), speech=self.speech)
        self.window.set_wandering_enabled(False)
        self.window.move_to_bottom_right()
        self.window.show()
        self.states = []
        self.controller.stateChanged.connect(self.states.append)

        # cleanups run in reverse order: the backend is released first (in case a test left it hung), then everything stops
        self.addCleanup(self.window.close)
        self.addCleanup(self.bubble.close)
        self.addCleanup(self.speech.shutdown)
        self.addCleanup(self.controller.stop)
        self.addCleanup(self.backend.release)

        self.speech.start()
        self.assertTrue(self.wait_until(lambda: self.speech.voices or not self.speech.available), "voice not ready")

    def wait_until(self, condition, timeout_ms=5000):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if condition():
                return True
            QTest.qWait(5)
        return bool(condition())

    def seconds_until_idle(self, timeout_ms=8000):
        started = time.monotonic()
        self.assertTrue(self.wait_until(lambda: self.controller.state is State.IDLE, timeout_ms), "never went back to idle")
        return time.monotonic() - started


class BubbleAndVoiceTest(SpeakingControllerTestCase):
    def test_the_bubble_stays_until_the_voice_has_finished(self):
        self.build(FakeBackend(seconds_per_character=0.15))  # "Hello there" takes the voice 1.65 s
        self.controller.say("Hello there")
        QTest.qWait(1100)  # well past the 0.6 s reading time...
        self.assertIs(self.controller.state, State.TALKING)  # ...but the voice is still talking
        self.assertTrue(self.bubble.isVisible())
        elapsed = 1.1 + self.seconds_until_idle()
        self.assertGreaterEqual(elapsed, 1.65 + 0.1 - 0.05)  # the voice, then the short pause
        self.assertLess(elapsed, 3.5)
        self.assertEqual(self.backend.finished_texts, ["Hello there"])
        self.assertTrue(self.wait_until(lambda: not self.bubble.isVisible()))

    def test_a_short_sentence_still_stays_long_enough_to_read(self):
        self.build(FakeBackend(seconds_per_character=0.0005))  # the voice is done in a few milliseconds
        self.controller.say("Hello there")
        QTest.qWait(300)
        self.assertIs(self.controller.state, State.TALKING)  # the 0.6 s reading time has not passed yet
        self.assertGreaterEqual(0.3 + self.seconds_until_idle(), 0.55)

    def test_without_a_voice_the_reading_time_decides(self):
        self.build(enabled=False)
        self.controller.say("Hello there")
        elapsed = self.seconds_until_idle()
        self.assertGreaterEqual(elapsed, 0.55)
        self.assertLess(elapsed, 1.5)
        self.assertEqual(self.backend.calls, [])

    def test_a_broken_voice_still_gives_a_bubble_that_ends_normally(self):
        with self.assertLogs("water_assistant.speech_service", "WARNING"):
            self.build(FakeBackend(fail_on_open=RuntimeError("no audio device")))
        self.controller.say("Hello there")
        self.assertTrue(self.bubble.isVisible())
        self.assertLess(self.seconds_until_idle(), 1.5)

    def test_without_any_speech_service_it_behaves_as_before(self):
        window = CharacterWindow(self.sprites)
        bubble = SpeechBubble()
        controller = AnimationController(window, bubble, FAST, random.Random(1))  # no speech=
        window.set_wandering_enabled(False)
        window.show()
        self.addCleanup(window.close)
        self.addCleanup(bubble.close)
        self.addCleanup(controller.stop)
        controller.say("Hello there")
        self.assertTrue(self.wait_until(lambda: controller.state is State.IDLE, 3000))

    def test_the_explicit_minimum_time_is_respected_even_if_the_voice_is_quicker(self):
        self.build(FakeBackend(seconds_per_character=0.001))
        self.controller.say("Hello there", seconds=2.0)  # at least 2.0 s * 0.2 = 0.4 s
        self.assertGreaterEqual(self.seconds_until_idle(), 0.35)


class InterruptionTest(SpeakingControllerTestCase):
    def test_a_new_state_silences_the_old_speech(self):
        self.build(FakeBackend(seconds_per_character=0.2))
        self.controller.say("Hello there my friend")  # 20 characters: 4 s of talking
        self.assertTrue(self.wait_until(lambda: self.speech.speaking))
        self.controller.drink()
        self.assertTrue(self.wait_until(lambda: self.backend.cancelled_texts == ["Hello there my friend"], 1500))
        self.assertTrue(self.wait_until(lambda: not self.bubble.isVisible()))
        self.assertIs(self.controller.state, State.DRINKING)

    def test_stopping_the_controller_silences_the_voice(self):
        self.build(FakeBackend(seconds_per_character=0.2))
        self.controller.say("Hello there my friend")
        self.assertTrue(self.wait_until(lambda: self.speech.speaking))
        self.controller.stop()
        self.assertTrue(self.wait_until(lambda: self.backend.cancelled_texts == ["Hello there my friend"], 1500))

    def test_the_end_of_an_old_sentence_does_not_cut_the_new_one_short(self):
        self.build(FakeBackend(seconds_per_character=0.1))
        self.controller.say("a" * 10)  # 1 s
        self.assertTrue(self.wait_until(lambda: self.speech.speaking))
        started = time.monotonic()
        self.controller.say("b" * 20)  # 2 s; the first sentence is cancelled and reports "finished" later
        self.seconds_until_idle()
        self.assertGreaterEqual(time.monotonic() - started, 2.0)  # the "finished" of the old sentence was ignored
        self.assertEqual(self.backend.finished_texts, ["b" * 20])
        self.assertEqual(self.backend.cancelled_texts, ["a" * 10])


class SafetyNetTest(SpeakingControllerTestCase):
    def test_a_voice_that_never_answers_cannot_trap_the_character_in_the_talking_state(self):
        self.build(FakeBackend(ignore_cancel=True), timing=IMPATIENT)  # the engine hangs: it never reports "finished"
        self.controller.say("Hello there")
        elapsed = self.seconds_until_idle(timeout_ms=4000)
        self.assertGreaterEqual(elapsed, 0.9)  # waited for the voice: (3.0 s reading time + 2.0 s) * 0.2 = 1.0 s
        self.assertLess(elapsed, 2.5)
        self.assertTrue(self.wait_until(lambda: not self.bubble.isVisible()))


class WhatIsSpokenTest(SpeakingControllerTestCase):
    def test_the_greeting_is_spoken_at_start_up(self):
        self.build()
        self.controller.start(greet=True)
        self.assertTrue(self.wait_until(lambda: self.backend.calls))
        self.assertEqual(self.backend.calls[0]["text"], messages.GREETING)

    def test_the_reminder_speaks_its_message_after_the_attention_hop(self):
        self.build(FakeBackend(seconds_per_character=0.002))
        self.controller.remind("Drink up!")
        QTest.qWait(200)  # still hopping and pointing (the attention part lasts 2.6 s * 0.2 = 0.52 s)
        self.assertIs(self.controller.state, State.REMINDER)
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(self.wait_until(lambda: self.controller.state is State.TALKING))
        self.assertTrue(self.wait_until(lambda: self.backend.calls))
        self.assertEqual(self.backend.calls[0]["text"], "Drink up!")
        self.assertEqual(self.bubble.text, "Drink up!")

    def test_the_settings_of_the_voice_are_used(self):
        self.build(rate=225, volume=0.4)
        self.controller.say("Hello there")
        self.assertTrue(self.wait_until(lambda: self.backend.calls))
        self.assertEqual((self.backend.calls[0]["rate"], self.backend.calls[0]["volume"]), (225, 0.4))

    def test_celebrating_with_a_message_speaks_it_and_lasts_until_it_is_done(self):
        text = "Good job! Keep drinking water regularly."
        self.build(FakeBackend(seconds_per_character=0.05))  # 40 characters: 2 s
        self.controller.celebrate(text)
        self.assertTrue(self.wait_until(lambda: self.backend.calls))
        self.assertEqual(self.backend.calls[0]["text"], text)
        QTest.qWait(1300)  # the 3.4 s * 0.2 = 0.68 s celebration is over, but the voice is not
        self.assertIs(self.controller.state, State.CELEBRATING)
        self.assertTrue(self.wait_until(lambda: self.controller.state is State.IDLE, 4000))
        self.assertEqual(self.backend.finished_texts, [text])

    def test_celebrating_without_a_message_is_silent(self):
        self.build()
        self.controller.celebrate()
        self.assertTrue(self.wait_until(lambda: self.controller.state is State.IDLE, 3000))
        self.assertEqual(self.backend.calls, [])

    def test_the_talk_preview_in_the_menu_is_spoken_too(self):
        self.build(FakeBackend(seconds_per_character=0.002))
        self.window.previewRequested.emit("talk")
        self.assertTrue(self.wait_until(lambda: self.backend.calls))
        self.assertEqual(self.backend.calls[0]["text"], messages.REMINDER_MESSAGES[0])

    def test_drinking_and_walking_do_not_speak(self):
        self.build()
        self.controller.drink()
        QTest.qWait(150)
        self.controller.walk_to(self.window.x() - 200)
        QTest.qWait(150)
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
